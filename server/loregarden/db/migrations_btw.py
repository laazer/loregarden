"""Migrations for the "by the way" aside channel.

Split out of `migrations.py` on the commit that pushed it past the organization
gate's 1500-line cap. The cut is by feature, not by size: `btw_exchanges` and
its later `deleted_at` column are one table's history, and they were the only
pair in that module that read as a unit.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_btw_exchanges(conn: Connection) -> None:
    """Somewhere to keep a question asked while a run is still working.

    Not a column on ``run_messages``: that channel is imperative, one-way, and
    keyed to a run that must exist and be steerable. An aside expects an answer,
    is answered by a different agent than the one it is about, and stays valid
    when nothing is running at all.
    """
    if table_exists(conn, "btw_exchanges"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE btw_exchanges (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                observed_run_id TEXT,
                question TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '',
                escalated_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                answered_at TEXT
            )
            """
        )
    )
    conn.execute(text("CREATE INDEX ix_btw_exchanges_ticket ON btw_exchanges (ticket_id)"))
    conn.execute(text("CREATE INDEX ix_btw_exchanges_status ON btw_exchanges (status)"))
    conn.execute(text("CREATE INDEX ix_btw_exchanges_run ON btw_exchanges (observed_run_id)"))
    conn.execute(text("CREATE INDEX ix_btw_exchanges_created ON btw_exchanges (created_at)"))


def m_btw_exchange_deleted_at(conn: Connection) -> None:
    """Somewhere to record that the operator dismissed an aside.

    A column rather than a fourth ``BtwStatus``: dismissal is orthogonal to
    whether the observer answered, the same way ``escalated_at`` is, and folding
    it into the status line would make "answered" and "dismissed" mutually
    exclusive when an operator most often dismisses one *because* it was
    answered.
    """
    # Guarded: a database built up to an earlier id has no `btw_exchanges` yet.
    if not table_exists(conn, "btw_exchanges"):
        return
    add_columns_if_missing(
        conn,
        "btw_exchanges",
        {"deleted_at": "ALTER TABLE btw_exchanges ADD COLUMN deleted_at TEXT"},
    )


__all__ = ["m_btw_exchange_deleted_at", "m_btw_exchanges"]
