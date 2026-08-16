"""Migration for the view store: composed views and the sidebar they rank in.

Two tables land together because one feature drives both, and because the
ordering they share is why they cannot be added separately — a view's position
in the sidebar lives on ``sidebar_entries``, never on ``views``. Pinned built-in
pages rank in that same list, so a page can sit between two views.

Both halves of a sidebar entry are nullable, and the unused one is NULL. That is
what lets ``UNIQUE (workspace_id, page_key)`` and ``UNIQUE (workspace_id,
view_id)`` be plain constraints: SQLite counts NULLs as distinct, so entries of
one kind do not all collide on the other kind's blank half. ``view_id`` also
declares a foreign key, and that is enforced — ``db.session`` sets ``PRAGMA
foreign_keys=ON`` on every connection — so an entry can only ever name a view
that exists. The flat, never-null wire shape is the service's job, not the
column's.

``CHECK ((page_key IS NULL) <> (view_id IS NULL))`` is what makes "an entry is a
page or a view" a fact about the table: without it a row with neither half set
renders as nothing, and a row with both set is an entry whose two columns
disagree about what kind it is.

``UNIQUE (workspace_id, position)`` makes "one entry per rank" structural rather
than a convention every write path has to remember, and its backing index is the
composite the queries want: they filter by workspace, then order by position.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

_INDEXES = {
    "ix_views_workspace_id": "CREATE INDEX ix_views_workspace_id ON views (workspace_id)",
    "ix_sidebar_entries_workspace_id": (
        "CREATE INDEX ix_sidebar_entries_workspace_id ON sidebar_entries (workspace_id)"
    ),
}


def m_view_store(conn: Connection) -> None:
    if not table_exists(conn, "views"):
        conn.execute(
            text(
                """
                CREATE TABLE views (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind VARCHAR NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    icon TEXT NOT NULL DEFAULT '',
                    layout_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                )
                """
            )
        )

    if not table_exists(conn, "sidebar_entries"):
        conn.execute(
            text(
                """
                CREATE TABLE sidebar_entries (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    entry_kind VARCHAR NOT NULL,
                    page_key TEXT,
                    view_id TEXT,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
                    FOREIGN KEY(view_id) REFERENCES views(id),
                    UNIQUE (workspace_id, page_key),
                    UNIQUE (workspace_id, position),
                    UNIQUE (workspace_id, view_id),
                    CHECK ((page_key IS NULL) <> (view_id IS NULL))
                )
                """
            )
        )

    for name, statement in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(statement))


def m_sidebar_entry_pinned(conn: Connection) -> None:
    """Whether a view's tab sits in the sidebar's Pinned section.

    A column rather than a third ``entry_kind``: pinning is a property of an
    entry, not a different sort of entry, and folding it into the kind would put
    "which half of the row is set" and "which section draws it" behind one value
    that the CHECK constraint already speaks for.

    ``DEFAULT 0`` is what makes the backfill a no-op worth stating: every entry
    that exists today predates pinned views, so every one of them belongs in
    Tabs.
    """
    add_columns_if_missing(
        conn,
        "sidebar_entries",
        {"pinned": "ALTER TABLE sidebar_entries ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0"},
    )
