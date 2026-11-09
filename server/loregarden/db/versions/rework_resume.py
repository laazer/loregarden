"""A rework round records the session it resumed, or why it ran fresh (`rework_resume`).

Nothing is backfilled: which earlier runs were rework rounds, and whether any
could have resumed, was never recorded. `cli_session_id` stays "" on old runs;
the resume decision reads an old run's session from its stdout instead.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection

_COLUMNS = {
    name: f"ALTER TABLE agent_runs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
    for name in (
        "cli_session_id",
        "rework_feedback_id",
        "resumed_from_run_id",
        "resume_skip_reason",
    )
}


@migration("20261108_rework_resume", after="20261108_stage_required_inputs")
def m_rework_resume(conn: Connection) -> None:
    add_columns_if_missing(conn, "agent_runs", _COLUMNS)
