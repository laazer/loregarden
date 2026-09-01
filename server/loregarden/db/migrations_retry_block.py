"""Backfill the structural mark for tickets the retry breaker currently blocks.

`blocked_on_stage_retry_budget` used to fall back to reading `"retry budget"`
out of `tickets.blocking_issues` when no `stage_retry_block` artifact existed.
That fallback is gone: an agent at its budget can call `loregarden_block_ticket`
with any prose it likes, and the phrase was enough to earn the stage a fresh
counter on the next start — skipping the human's force decision and the
`stage_dispatch_override` audit row it writes.

Deleting the fallback would have stranded the tickets already blocked by the
breaker before the mark existed: their block would read as somebody else's, so
re-entering the stage would keep the exhausted counter and refuse forever. This
writes the mark those blocks should always have had, once, from the same
evidence the fallback used — and then nothing reads prose again.

Deliberately narrow. Only a ticket that is *blocked now*, whose blocking text
carries the whole sentence the breaker wrote (not merely the words "retry
budget", which a block about something else may well use), and whose current
stage has a dispatch counter at all gets a mark — a stage with no recorded
dispatches has nothing the missing mark could strand, so marking it would only
hand out a reset nobody is owed.

Idempotent: a stage that already has its mark is skipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_utils import table_exists
from loregarden.models.domain import StageBudgetArtifactKind, TicketState
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: The sentence `stage_retry_block_message` wrote into `blocking_issues`, as a
#: SQL LIKE pattern. It lives here, in the one-shot that retires it, rather than
#: in the service that no longer reads it.
#:
#: The wildcard stands where the stage's configured budget went ("...retry
#: budget of 5 dispatches..."). Matching the whole sentence rather than the bare
#: words "retry budget" is what keeps the backfill from marking a ticket blocked
#: for something else that merely mentions the budget — a mark it never earned
#: is a free counter reset on that stage's next block, which is the failure this
#: migration exists to avoid, not to cause.
_RETRY_BUDGET_BLOCK_PHRASE = "reached its retry budget of%dispatches"

#: A stage needs at least this many recorded dispatches for the missing mark to
#: have stranded anything. The workspace's configured budget is deliberately not
#: resolved here — a migration must not reach into the orchestration profile
#: layer, and a ticket the breaker blocked is by definition at *its* budget,
#: whatever that number was when the block was written.
_MIN_DISPATCHES = 1


def _dispatch_count(conn: Connection, ticket_id: str, stage_key: str) -> int:
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM artifacts "
            "WHERE ticket_id = :ticket_id AND kind = :kind AND title = :title"
        ),
        {
            "ticket_id": ticket_id,
            "kind": StageBudgetArtifactKind.DISPATCH.value,
            "title": f"stage-dispatch:{stage_key}",
        },
    ).scalar()
    return int(row or 0)


def _already_marked(conn: Connection, ticket_id: str, stage_key: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM artifacts "
            "WHERE ticket_id = :ticket_id AND kind = :kind AND title = :title LIMIT 1"
        ),
        {
            "ticket_id": ticket_id,
            "kind": StageBudgetArtifactKind.RETRY_BLOCK.value,
            "title": f"stage-retry-block:{stage_key}",
        },
    ).first()
    return row is not None


def m_backfill_stage_retry_block(conn: Connection) -> None:
    if not table_exists(conn, "tickets") or not table_exists(conn, "artifacts"):
        return

    candidates = conn.execute(
        text(
            "SELECT id, workflow_stage_key FROM tickets "
            "WHERE state = :blocked AND workflow_stage_key != '' "
            "AND LOWER(blocking_issues) LIKE :phrase"
        ),
        {
            "blocked": TicketState.BLOCKED.value,
            "phrase": f"%{_RETRY_BUDGET_BLOCK_PHRASE}%",
        },
    ).fetchall()

    now = datetime.now(timezone.utc)
    for ticket_id, stage_key in candidates:
        if _dispatch_count(conn, ticket_id, stage_key) < _MIN_DISPATCHES:
            continue
        if _already_marked(conn, ticket_id, stage_key):
            continue
        conn.execute(
            text(
                "INSERT INTO artifacts "
                "(id, ticket_id, run_id, kind, title, content_json, evidence_kind, "
                "commit_sha, created_at) "
                "VALUES (:id, :ticket_id, NULL, :kind, :title, '{}', '', '', :created_at)"
            ),
            {
                "id": str(uuid4()),
                "ticket_id": ticket_id,
                "kind": StageBudgetArtifactKind.RETRY_BLOCK.value,
                "title": f"stage-retry-block:{stage_key}",
                "created_at": now,
            },
        )
