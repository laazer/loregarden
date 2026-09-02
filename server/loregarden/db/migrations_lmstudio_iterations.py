"""Per-workspace cap on LM Studio fresh-context iterations.

The runner drove local models with one ever-growing conversation, so a small
model drowned in accumulated context long before a stage was finished. Restarting
with a prompt rebuilt from the database each iteration fixes that, and the number
of restarts has to be bounded — an unbounded loop is the failure being replaced,
not an improvement on it.

Nullable-with-default-0 rather than the real default: 0 means "this workspace has
not chosen", which is what lets `resolve_lmstudio_max_iterations` fall through to
the global setting. Storing 4 here would freeze today's default into every
existing row and make a later change to it invisible.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy.engine import Connection


def m_lmstudio_max_iterations(conn: Connection) -> None:
    if not table_exists(conn, "workspaces"):
        return
    add_columns_if_missing(
        conn,
        "workspaces",
        {
            "lmstudio_max_iterations": (
                "ALTER TABLE workspaces ADD COLUMN lmstudio_max_iterations "
                "INTEGER NOT NULL DEFAULT 0"
            ),
        },
    )
