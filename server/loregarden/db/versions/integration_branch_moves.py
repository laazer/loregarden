"""The branch-move ledger (`IntegrationBranchMove`).

`integration_branch_moves.move_integration_branch` appends a row for every
branch it moves, so "who moved X" is a query rather than a reflog that went
with the branch (lg-milestone-that-1316). New table; nothing to backfill.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261115_integration_branch_moves", after="20261114_landing_delivered_in_part")
def m_integration_branch_moves(conn: Connection) -> None:
    if table_exists(conn, "integration_branch_moves") or not table_exists(conn, "workspaces"):
        return
    conn.execute(
        text(
            "CREATE TABLE integration_branch_moves ( id VARCHAR NOT NULL, workspace_id VARCHAR "
            "NOT NULL, branch VARCHAR NOT NULL, old_tip VARCHAR NOT NULL, new_tip VARCHAR NOT "
            "NULL, writer VARCHAR NOT NULL, why VARCHAR NOT NULL, run_id VARCHAR, moved_at "
            "DATETIME NOT NULL, PRIMARY KEY (id), "
            "FOREIGN KEY(workspace_id) REFERENCES workspaces (id) )"
        )
    )
    for column in ("workspace_id", "branch", "moved_at"):
        conn.execute(
            text(
                f"CREATE INDEX ix_integration_branch_moves_{column} "
                f"ON integration_branch_moves ({column})"
            )
        )
