"""Refresh the three roles `20261125_person_judged_criteria` could not.

That migration read its seed files from `repo_root`, which the CLI points at the
primary checkout to reach the shared database. Run from a worktree the moment
its branch merged, it found a primary checkout two commits behind, whose seeds
lacked the markers, and was recorded as applied with the ac_gatekeeper,
verifier and spec roles unchanged. Seeds are now read from the running code's
own checkout (`settings.seed_context_dir`); this repeats the refresh, which
is marker-gated and so writes nothing where the first one worked.
"""

from __future__ import annotations

from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from loregarden.db.versions.person_judged_criteria import ROLES
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261126_person_judgment_roles_again"


@migration("20261126_person_judgment_roles_again", after="20261125_person_judged_criteria")
def m_person_judgment_roles_again(conn: Connection) -> None:
    for slug, (role_file, marker) in ROLES.items():
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
