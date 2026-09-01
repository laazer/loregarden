"""Durable rework-feedback ledger for stage reroutes.

When a stage rejects work and reroutes it upstream, the *full* fix direction
must reach the re-run agent. It could not before: the single-stage reroute path
funnelled the agent's ``reroute_context`` through ``record_blocking_issue``,
which caps ``ticket.blocking_issues`` at 200 chars and files the overflow as an
error artifact the re-run agent never reads (``build_orchestration_context``
reads only ``ticket.blocking_issues``). So a verifier could hand back a precise
fix direction and the implementer would receive only
``"Stage 'verify' hit a blocking issue — see the Errors tab for details."`` —
and re-guess the fix every round.

This ledger stores each reroute's *full* context as its own artifact so:

* the re-run agent sees every prior round's feedback in full — no truncation,
  and the union across rounds rather than only the latest framing;
* a runaway reroute loop can be capped durably. The count is artifact-backed,
  so it survives a server reload or a fresh orchestration run — unlike a
  function-local counter, which resets every ``execute()`` and lets a stuck
  stage cycle forever (the same trap ``_persisted_gate_fix_attempts`` documents
  for the gate path).

``ticket.blocking_issues`` is left untouched: it stays the short pointer the
workflow pane renders. This ledger is a separate, agent-facing channel.
"""

from __future__ import annotations

import json

from loregarden.models.domain import Artifact, ReworkArtifactKind, Ticket
from sqlmodel import Session, select

# Feedback context, not a failure — kept off the Errors tab. The re-run agent's
# context is assembled from these; the human-facing error artifact that
# ``record_blocking_issue`` already files is a separate, unchanged concern.
#
# A dedicated kind, not the shared ``context`` bucket it used until migration
# 0103 backfilled these rows. Both readers below filter on it, so it is the one
# seam between what this module writes and what it counts.
REWORK_FEEDBACK_KIND = ReworkArtifactKind.FEEDBACK

# Reroute a single target stage this many times without the work sticking, and
# the next rejection blocks for a human instead of bouncing again. Mirrors the
# gate autofix budget (``GatesConfig.autofix_max_agent_attempts``) but for the
# review/verify rework loop, which previously had no cap at all.
MAX_REWORK_REROUTES = 3


def _ledger_title(target_stage: str) -> str:
    """Deterministic title so the ledger for one target is countable and
    distinct from unrelated ``context`` artifacts."""
    return f"Rework feedback — {target_stage}"


def record_rework_feedback(
    session: Session,
    ticket: Ticket,
    *,
    target_stage: str,
    from_stage: str,
    context: str,
    run_id: str | None = None,
) -> None:
    """Append one round of rework feedback for ``target_stage``.

    ``context`` is stored verbatim (no truncation) — this is what the re-run
    agent reads. One artifact per reroute, so the row count doubles as the
    durable reroute budget for the loop cap.
    """
    session.add(
        Artifact(
            ticket_id=ticket.id,
            run_id=run_id,
            kind=REWORK_FEEDBACK_KIND,
            title=_ledger_title(target_stage),
            content_json=json.dumps(
                {"from_stage": from_stage, "target_stage": target_stage, "context": context}
            ),
        )
    )
    session.commit()


def _entries(session: Session, ticket: Ticket, target_stage: str) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket.id)
            .where(Artifact.kind == REWORK_FEEDBACK_KIND)
            .where(Artifact.title == _ledger_title(target_stage))
            .order_by(Artifact.created_at)
        ).all()
    )


def rework_reroute_count(session: Session, ticket: Ticket, target_stage: str) -> int:
    """How many times ``target_stage`` has already been rerouted to (durable)."""
    return len(_entries(session, ticket, target_stage))


def record_reroute_exhausts_budget(
    session: Session,
    ticket: Ticket,
    *,
    target_stage: str,
    from_stage: str,
    context: str,
    run_id: str | None = None,
) -> bool:
    """Record this reroute and report whether the loop cap is now exhausted.

    Returns ``True`` when ``target_stage`` had already been rerouted to
    ``MAX_REWORK_REROUTES`` times before this one — the caller should then block
    for a human instead of bouncing the work again. The count is read *before*
    recording, so the budget is "how many reroutes already happened". An empty
    ``target_stage`` records nothing and never exhausts.
    """
    if not target_stage:
        return False
    prior = rework_reroute_count(session, ticket, target_stage)
    record_rework_feedback(
        session,
        ticket,
        target_stage=target_stage,
        from_stage=from_stage,
        context=context,
        run_id=run_id,
    )
    return prior >= MAX_REWORK_REROUTES


def render_rework_feedback(session: Session, ticket: Ticket, target_stage: str) -> str:
    """The full accumulated feedback for ``target_stage``, oldest round first,
    or ``""`` when there is none.

    Exact-duplicate rounds are collapsed (a recurring identical finding reads
    once, not N times) while distinct rounds are all kept — so the agent sees
    the union of everything asked of it, not just the latest framing.
    """
    entries = _entries(session, ticket, target_stage)
    if not entries:
        return ""

    rounds: list[tuple[str, str]] = []
    seen: set[str] = set()
    for artifact in entries:
        try:
            payload = json.loads(artifact.content_json or "{}")
        except json.JSONDecodeError:
            continue
        context = (payload.get("context") or "").strip()
        if not context or context in seen:
            continue
        seen.add(context)
        rounds.append((payload.get("from_stage") or "", context))

    if not rounds:
        return ""
    if len(rounds) == 1:
        return rounds[0][1]

    blocks = []
    for index, (from_stage, context) in enumerate(rounds, start=1):
        source = f" (from `{from_stage}`)" if from_stage else ""
        blocks.append(f"### Round {index}{source}\n{context}")
    return "\n\n".join(blocks)
