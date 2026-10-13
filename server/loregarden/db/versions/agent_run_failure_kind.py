"""Record why a failed agent run failed: the work, or the harness around it.

See `models.domain.tables.AgentRun.failure_kind`. Nullable and not backfilled:
the kind was never recorded before, and a stderr string is not a reliable
enough reading to rewrite history from. The integration merge turn's budget
counts only `work` failures, so the null rows it leaves behind stop counting,
which is the point: on 2026-10-13 four of them were CLI usage-limit failures
that had spent two trees' budgets before any agent ran (lg-milestone-that-959).
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261013_agent_run_failure_kind", after="20261012_ci_run_head_sha")
def m_agent_run_failure_kind(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "agent_runs",
        {"failure_kind": "ALTER TABLE agent_runs ADD COLUMN failure_kind VARCHAR"},
    )
