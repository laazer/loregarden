"""A role does not declare a CLI. Drop studio_agents.adapter.

Harness selection is env → ticket → workspace → settings.cli_adapter. Remaining
model pins are Claude-authored until lg-protocol-executors-981.
"""

from __future__ import annotations

from loregarden.db.migration_utils import drop_columns_if_present
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261027_drop_studio_agents_adapter", after="20261023_disk_usage")
def m_drop_studio_agents_adapter(conn: Connection) -> None:
    drop_columns_if_present(conn, "studio_agents", ["adapter"])
