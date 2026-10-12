"""The commit a CI result judged, so one commit is one result.

See `models.domain.git_tables.CIRunResult.head_sha`. The poller observes the
same pull request every tick; keyed on the head commit, a tick that sees the
same checks updates its row instead of adding one, and an auto-fix is armed
once per failing commit rather than once per tick. Existing rows (none in the
live database when this was written) keep an empty sha.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261012_ci_run_head_sha", after="20261010_baxter_chat_global_scope")
def m_ci_run_head_sha(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "ci_run_results",
        {
            "head_sha": "ALTER TABLE ci_run_results ADD COLUMN head_sha TEXT NOT NULL DEFAULT ''",
            "pr_number": "ALTER TABLE ci_run_results ADD COLUMN pr_number INTEGER",
        },
    )
    if not index_exists(conn, "ix_ci_run_results_ticket_head"):
        conn.execute(
            text(
                "CREATE INDEX ix_ci_run_results_ticket_head ON ci_run_results (ticket_id, head_sha)"
            )
        )
