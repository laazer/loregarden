"""Migrations that repair the migration ledger itself.

Separate from `migrations_runner`, which prunes RENUMBERED orphans on every
apply without anyone deciding: those rows record nothing the new id does not.
A DELETED orphan is different — its row is the only trace of a body the build
no longer has, so removing it is a decision, and a decision belongs in a
migration that names what it removed and why.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: Ledger ids applied to the live database by branches that never merged, and
#: whose bodies main has never had. Each is retired here with what it left
#: behind and why that is left alone. Adding to this list is how a later
#: DELETED orphan gets retired — by name, with its reason, not by a sweep.
#:
#: 0085_ticket_blocked_kind — added `tickets.blocked_kind`. Main has zero
#:   references to the column; it is dead schema. Left in place: two rows carry
#:   the value 'environment', and dropping a column with data to tidy a ledger
#:   is the wrong trade. Main's own sequence skips 0085 entirely, so the number
#:   was never reclaimed.
#:
#: 0116_stage_timeout_budgets — wrote `timeout_seconds` into template stage
#:   definitions. Main's stage model has no such field and nothing reads it; the
#:   only remnant is an orphaned comment block in `migrations_templates`. Left
#:   in place: the key is inert in `stages_json` and `WorkflowStageDef` ignores
#:   it. Main's own 0116 is `human_verification_brief`.
_RETIRED_UNMERGED_IDS = (
    "0085_ticket_blocked_kind",
    "0116_stage_timeout_budgets",
)


def m_retire_unmerged_branch_ledger_ids(conn: Connection) -> None:
    """Remove the ledger rows of two migrations that ran here and never merged.

    Their bodies are not in this build, so `unknown_migration_ids` reported them
    on every CLI call and `refuse_stale_write` refused every write — correctly,
    by its own rule, and uselessly, because the divergence was inert. The
    schema each left behind is documented on `_RETIRED_UNMERGED_IDS` and stays.

    Guarded and idempotent: a database that never applied them has nothing to
    delete, and re-running finds nothing either.
    """
    if not table_exists(conn, "schema_migrations"):
        return
    for retired in _RETIRED_UNMERGED_IDS:
        conn.execute(text("DELETE FROM schema_migrations WHERE id = :id"), {"id": retired})


__all__ = ["m_retire_unmerged_branch_ledger_ids"]
