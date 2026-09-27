"""The `instance_templates` table: launch templates defined in the UI, per workspace.

See `models.domain.instance_template_tables`.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_instance_templates(conn: Connection) -> None:
    if not table_exists(conn, "instance_templates"):
        conn.execute(
            text(
                """
                CREATE TABLE instance_templates (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    workspace_id VARCHAR NOT NULL REFERENCES workspaces(id),
                    name VARCHAR NOT NULL,
                    spec_json VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE (workspace_id, name)
                )
                """
            )
        )
    if not index_exists(conn, "ix_instance_templates_workspace_id"):
        conn.execute(
            text("CREATE INDEX ix_instance_templates_workspace_id ON instance_templates (workspace_id)")
        )
