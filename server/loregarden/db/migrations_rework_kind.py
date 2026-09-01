"""Move the rework-feedback ledger onto its own artifact kind.

`rework_feedback.py` wrote its rows as `kind='context'`, the same bucket as run
context and stage reports. That made the ledger unqueryable: the count of
reroutes for a target stage *is* the loop metric `MAX_REWORK_REROUTES` caps, and
asking for it returned zero — loregarden's own `absorb-adapt` skill documents
`kind='rework_feedback'` and would have read "no reroute loop ever happened",
the exact opposite of the truth.

The service now writes `ReworkArtifactKind.FEEDBACK`. This backfills the rows it
already wrote, and doing so is load-bearing rather than cosmetic: both readers
filter on kind *and* title, so an unmigrated row is invisible to them. Left
behind, every in-flight ticket's reroute count would silently reset to zero and
the cap would stop capping the loop it exists to cap.

Selection is by the ledger's own deterministic title prefix, not by a count.
Nothing else in `context` carries it — the other rework-titled artifacts are
`evidence`, `review` and `log` rows, which this leaves alone.

Idempotent: rows already on the new kind no longer match the `context`
predicate, so a re-run moves nothing.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.models.domain import ReworkArtifactKind
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: The prefix `_ledger_title` builds every ledger title from, as a SQL LIKE
#: pattern. Spelled out here rather than imported from the service: a migration
#: describes the rows as they were written, and must keep matching them even if
#: the service later renames what it writes.
_LEDGER_TITLE_PREFIX = "Rework feedback — %"

#: The kind these rows shared before they had one of their own.
_PREVIOUS_KIND = "context"


def m_rework_feedback_kind(conn: Connection) -> None:
    if not table_exists(conn, "artifacts"):
        return

    conn.execute(
        text(
            "UPDATE artifacts SET kind = :new_kind "
            "WHERE kind = :old_kind AND title LIKE :title_prefix"
        ),
        {
            "new_kind": ReworkArtifactKind.FEEDBACK.value,
            "old_kind": _PREVIOUS_KIND,
            "title_prefix": _LEDGER_TITLE_PREFIX,
        },
    )
