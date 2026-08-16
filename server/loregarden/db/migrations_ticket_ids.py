"""Restructure the human-facing ticket id into workspace + milestone + number.

Before this migration a ticket read ``456-one-dispatch-decision-instead-of-thr``
— a workspace-wide counter welded to a 48-character slice of the title. It was
unique and unshareable: too long to say, truncated mid-word, and stale the
moment the title was edited.

After it, the same ticket reads ``lor-queue-execution-456``: the workspace's
prefix, its milestone's code, and the number it has always had. The spelling is
derived by ``services.ticket_ids``, which this migration reuses so the backfill
and every later ticket agree on the rules.

The old id is not discarded. It is written to ``legacy_external_id``, because it
is already sitting in git branch names, PR titles, handoff artifacts, and the
learning vault — none of which a database migration can reach — and resolution
accepts it forever.
"""

from __future__ import annotations

from collections import defaultdict

from loregarden.db.migration_utils import add_columns_if_missing, table_columns, table_exists
from loregarden.models.domain import WorkItemType
from loregarden.services.ticket_ids import (
    NO_MILESTONE_SEGMENT,
    derive_milestone_code,
    derive_workspace_prefix,
    spell_external_id,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection

# What re-spelling a row needs to read. A schema old enough to be missing any of
# them predates the hierarchy the id is derived from.
_BACKFILL_COLUMNS = frozenset(
    {"workspace_id", "external_id", "title", "work_item_type", "parent_ticket_id", "created_at"}
)

# Prefixes chosen by hand for the workspaces that existed when this ran. They are
# not derivable — `lg` skips letters and `blob` takes four — and the prefix has to
# be right *before* the backfill spells anything, because changing it afterwards
# re-spells only future ids and leaves the issued ones reading the old one.
# Any workspace not named here derives its prefix from its slug, as new ones do.
_CHOSEN_PREFIXES = {"loregarden": "lg", "blobert": "blob"}


def _add_columns(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "workspaces",
        {
            "ticket_prefix": (
                "ALTER TABLE workspaces ADD COLUMN ticket_prefix TEXT NOT NULL DEFAULT ''"
            ),
            "last_ticket_number": (
                "ALTER TABLE workspaces ADD COLUMN last_ticket_number INTEGER NOT NULL DEFAULT 0"
            ),
        },
    )
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "ticket_number": (
                "ALTER TABLE tickets ADD COLUMN ticket_number INTEGER NOT NULL DEFAULT 0"
            ),
            "milestone_code": (
                "ALTER TABLE tickets ADD COLUMN milestone_code TEXT NOT NULL DEFAULT ''"
            ),
            "legacy_external_id": (
                "ALTER TABLE tickets ADD COLUMN legacy_external_id TEXT NOT NULL DEFAULT ''"
            ),
        },
    )
    # `CREATE INDEX IF NOT EXISTS` still fails on a table that does not exist, and
    # this runs against databases at every prior point in history — including ones
    # where these tables have not been created yet.
    if table_exists(conn, "tickets"):
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tickets_ticket_number ON tickets (ticket_number)")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tickets_legacy_external_id "
                "ON tickets (legacy_external_id)"
            )
        )
    if table_exists(conn, "workspaces"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workspaces_ticket_prefix "
                "ON workspaces (ticket_prefix)"
            )
        )


def _backfill_workspace_prefixes(conn: Connection) -> dict[str, str]:
    if not table_exists(conn, "workspaces"):
        return {}
    rows = conn.execute(
        text("SELECT id, slug, ticket_prefix FROM workspaces ORDER BY created_at, id")
    ).fetchall()

    prefixes: dict[str, str] = {}
    taken: set[str] = {row[2] for row in rows if row[2]}
    # Chosen prefixes are claimed first, so a derived one cannot take `lg` from
    # under a workspace that is spelled later in this loop.
    taken.update(_CHOSEN_PREFIXES[slug] for _id, slug, _p in rows if slug in _CHOSEN_PREFIXES)
    for workspace_id, slug, existing in rows:
        prefix = (
            existing
            or _CHOSEN_PREFIXES.get(slug)
            or derive_workspace_prefix(slug, taken=frozenset(taken))
        )
        taken.add(prefix)
        prefixes[workspace_id] = prefix
        if not existing:
            conn.execute(
                text("UPDATE workspaces SET ticket_prefix = :prefix WHERE id = :id"),
                {"prefix": prefix, "id": workspace_id},
            )
    return prefixes


def _milestone_ancestor(
    ticket_id: str, parents: dict[str, str | None], milestones: set[str]
) -> str | None:
    """Walk up to the milestone owning ``ticket_id``, tolerating a broken chain."""
    seen: set[str] = set()
    current: str | None = ticket_id
    while current and current not in seen:
        if current in milestones:
            return current
        seen.add(current)
        current = parents.get(current)
    return None


def m_structured_ticket_ids(conn: Connection) -> None:
    """Add the id columns and re-spell every existing ticket."""
    _add_columns(conn)
    if not table_exists(conn, "tickets"):
        return
    # A tickets table old enough to predate any of these columns cannot be
    # re-spelled — there is no workspace to take a prefix from, and no hierarchy
    # to take a milestone code from. The columns above are still added, so the
    # schema converges; the rows keep the ids they have.
    if not _BACKFILL_COLUMNS.issubset(table_columns(conn, "tickets")):
        return

    prefixes = _backfill_workspace_prefixes(conn)
    rows = conn.execute(
        text(
            "SELECT id, workspace_id, external_id, title, work_item_type, "
            "parent_ticket_id, ticket_number FROM tickets ORDER BY created_at, id"
        )
    ).fetchall()
    # Re-running against an already-migrated database must not renumber anything.
    if not rows or all(row[6] for row in rows):
        return

    parents = {row[0]: row[5] for row in rows}
    milestones = {row[0] for row in rows if row[4] == WorkItemType.MILESTONE.value}

    codes: dict[str, str] = {}
    taken_codes: dict[str, set[str]] = defaultdict(set)
    for ticket_id, workspace_id, _external_id, title, work_item_type, _parent, _number in rows:
        if work_item_type != WorkItemType.MILESTONE.value:
            continue
        code = derive_milestone_code(title, taken=frozenset(taken_codes[workspace_id]))
        taken_codes[workspace_id].add(code)
        codes[ticket_id] = code
        conn.execute(
            text("UPDATE tickets SET milestone_code = :code WHERE id = :id"),
            {"code": code, "id": ticket_id},
        )

    # created_at order, so the numbers run in the order the work was filed.
    counters: dict[str, int] = defaultdict(int)
    for ticket_id, workspace_id, external_id, _title, _type, _parent, _number in rows:
        counters[workspace_id] += 1
        milestone = _milestone_ancestor(ticket_id, parents, milestones)
        conn.execute(
            text(
                "UPDATE tickets SET ticket_number = :number, legacy_external_id = :legacy, "
                "external_id = :external_id WHERE id = :id"
            ),
            {
                "number": counters[workspace_id],
                "legacy": external_id,
                "external_id": spell_external_id(
                    prefix=prefixes.get(workspace_id, ""),
                    milestone_code=codes.get(milestone or "", NO_MILESTONE_SEGMENT),
                    number=counters[workspace_id],
                ),
                "id": ticket_id,
            },
        )

    # Seed the high-water mark so the first ticket created after this migration
    # continues the sequence instead of colliding with the tail of it.
    for workspace_id, issued in counters.items():
        conn.execute(
            text(
                "UPDATE workspaces SET last_ticket_number = :issued WHERE id = :id "
                "AND last_ticket_number < :issued"
            ),
            {"issued": issued, "id": workspace_id},
        )
