"""Five TEXT columns on `worktrees`: the durable claim, and what a refusal learned.

lg-external-harness-1026, R4. Three carry the claim a checkout is held under
(`claim_pid`, `claim_birth`, `claim_boot_id`) and two carry what the gate
decided about it (`unresolved_reason`, `parked_at`).

There is deliberately **no receipt column**. The receipt is
`tickets.landed_sha` / `landed_branch`, which `land_ticket._record_landing` and
`publish_tree` already maintain; a second record of "did this land" drifts from
the first the day one of those writers changes (plan D1). Nor is there a
`claim_tip_sha`: the tree's tip is read live from the tree at retirement time
(plan D2).

Every column defaults to `''` and not NULL. An existing checkout has to come
through this migration with an *empty* claim — "nobody recorded one", which
`parse_claim` reads as no claimant — rather than a NULL a `!=` comparison would
make truthy or a default that looks like a claim.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled out again in the decorator below, deliberately. The shared-database
#: guard reads this package's SOURCE with `ids_declared_in_source` to decide
#: which migrations `origin/main` ships, and it only sees a string literal — a
#: decorator given a name instead is a migration the guard refuses nowhere.
MIGRATION_ID = "20261020_worktree_claim_identity"

#: Every column is TEXT NOT NULL DEFAULT '' so an unwritten row reads back as
#: "nobody said" rather than as a value.
_COLUMNS = {
    name: f"ALTER TABLE worktrees ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
    for name in ("claim_pid", "claim_birth", "claim_boot_id", "unresolved_reason", "parked_at")
}


@migration("20261020_worktree_claim_identity", after="20261020_fix_coverage_lanes")
def m_worktree_claim_identity(conn: Connection) -> None:
    """Add the claim and evidence columns, guarding each of them.

    `add_columns_if_missing` makes this re-runnable as a no-op, which the
    migration suite requires and a ledger repair replay depends on. A partial
    schema with no `worktrees` table at all is left alone: `create_all` adds
    the table whole, and a migration that assumed its table exists fails the
    suite's partial builds.
    """
    if not table_exists(conn, "worktrees"):
        return
    add_columns_if_missing(conn, "worktrees", _COLUMNS)
