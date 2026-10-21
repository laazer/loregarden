"""Every migration after `0151_ticket_criteria_checked`, one per module.

Add a migration by adding a module here — no shared list to append to:

    from loregarden.db.versions import migration
    from sqlalchemy import Connection

    @migration("20261002_workspace_flag", after="<the id it follows>")
    def m_workspace_flag(conn: Connection) -> None:
        ...

- The id is today's date and a name. It is permanent once merged: the database
  records it, and renaming it runs the body again under the new name.
- `after` is the newest migration on main when you write yours — the frozen
  list's last id while this package is empty. Another branch merging first
  with the same `after` is fine: that is a fork, and nothing needs re-pointing
  unless `test_migration_forks_commute` fails, which means the two interact.
  Then make yours follow theirs, since it really does depend on it.
- Guard your own changes (`add_columns_if_missing`, `table_exists`, …). The
  suite runs every migration twice.

`migrations.py` imports every module here and appends the chain to its frozen
list. See `migration_registry` for why.
"""

from __future__ import annotations

from loregarden.db.migration_registry import MigrationRegistry

REGISTRY = MigrationRegistry()
migration = REGISTRY.migration

__all__ = ["REGISTRY", "migration"]
