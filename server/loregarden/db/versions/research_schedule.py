"""Saved research questions re-run on a schedule, and claims keyed to the run.

Adds to `survey_research_questions`: ``last_run_turn_id`` (so one research turn
is one run however many record calls it makes), ``last_scheduled_at`` (when a
re-run was last asked for, so a turn that records nothing is not re-posted every
tick) and ``schedule_error`` (why the last scheduled re-run could not start).
Adds ``survey_capability_claims.run``: the question run a claim was recorded in,
0 for a person's correction and for claims recorded before this migration.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261112_research_schedule", after="20261111_reference_research")
def m_research_schedule(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "survey_research_questions",
        {
            "last_run_turn_id": (
                "ALTER TABLE survey_research_questions "
                "ADD COLUMN last_run_turn_id VARCHAR NOT NULL DEFAULT ''"
            ),
            "last_scheduled_at": (
                "ALTER TABLE survey_research_questions ADD COLUMN last_scheduled_at DATETIME"
            ),
            "schedule_error": (
                "ALTER TABLE survey_research_questions "
                "ADD COLUMN schedule_error VARCHAR NOT NULL DEFAULT ''"
            ),
        },
    )
    add_columns_if_missing(
        conn,
        "survey_capability_claims",
        {"run": ("ALTER TABLE survey_capability_claims ADD COLUMN run INTEGER NOT NULL DEFAULT 0")},
    )
