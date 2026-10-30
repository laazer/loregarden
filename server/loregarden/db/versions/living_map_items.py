"""The living map's items: one row per item on a chat's or a ticket's map.

See `models.domain.living_map_tables` and `services.living_map`.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261027_living_map_items", after="20261023_disk_usage")
def m_living_map_items(conn: Connection) -> None:
    if table_exists(conn, "living_map_items"):
        return
    conn.execute(
        text(
            "CREATE TABLE living_map_items ( id VARCHAR NOT NULL, scope VARCHAR(6) NOT NULL, "
            "scope_id VARCHAR NOT NULL, seq INTEGER NOT NULL, kind VARCHAR(10) NOT NULL, "
            "state VARCHAR(7) NOT NULL, source VARCHAR(6) NOT NULL, source_ref VARCHAR NOT NULL, "
            "title VARCHAR NOT NULL, detail VARCHAR NOT NULL, topic_id VARCHAR, "
            "happened_at DATETIME NOT NULL, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL, PRIMARY KEY (id), "
            "CONSTRAINT uq_living_map_items_source UNIQUE (scope, scope_id, source_ref), "
            "CONSTRAINT uq_living_map_items_seq UNIQUE (scope, scope_id, seq), "
            "FOREIGN KEY(topic_id) REFERENCES living_map_items (id) )"
        )
    )
    conn.execute(text("CREATE INDEX ix_living_map_items_scope_id ON living_map_items (scope_id)"))
