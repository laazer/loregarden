"""Stop instant pre-push holds from teaching the wait estimator.

Until the hook started its held command from its own directory, every
capacity-gated pre-push waited its turn and then died at once with `No such
file or directory`, releasing cleanly. Those holds are a fraction of a second
each, and they made up 16 of the 34 recorded `heavy` host holds, so the median
for a test suite read as seconds and the board told seven waiters "≈ 0s" to
"≈ 2m" for an hour-long line.

New runs record a command that never started as `command_not_run`. This
re-tags the old ones the same way: a pre-push hold under ten seconds cannot be a
real run, because ruff and tsc alone take longer than that. Idempotent — a
re-run finds nothing still tagged `released` to change.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

#: Shorter than any real pre-push run: `ruff check` and `tsc -b` alone exceed it.
INSTANT_HOLD_SECONDS = 10


@migration("20261003_capacity_unrun_holds", after="20261003_capacity_lease_slots")
def m_capacity_unrun_holds(conn: Connection) -> None:
    if not table_exists(conn, "docker_leases"):
        return
    conn.execute(
        text(
            """
            UPDATE docker_leases
            SET end_reason = 'command_not_run'
            WHERE status = 'released'
              AND end_reason = 'released'
              AND pool = 'host'
              AND holder_label LIKE 'pre-push %'
              AND granted_at IS NOT NULL
              AND released_at IS NOT NULL
              AND (julianday(released_at) - julianday(granted_at)) * 86400 < :instant
            """
        ),
        {"instant": INSTANT_HOLD_SECONDS},
    )
