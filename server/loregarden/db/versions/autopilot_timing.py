"""Four columns on `initiative_plans`: the autopilot's timer and schedule.

`autopilot_stops_json` and `autopilot_schedule_json` hold the person's settings
(`models.domain.autopilot_timing`); `autopilot_window_seen` and
`autopilot_window_owns` are the schedule's own bookkeeping, kept out of the
JSON so saving the settings can never overwrite them.

Defaults read back as "no timer, no schedule", which is every existing plan.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection

MIGRATION_ID = "20261105_autopilot_timing"

_COLUMNS = {
    "autopilot_stops_json": (
        "ALTER TABLE initiative_plans ADD COLUMN autopilot_stops_json TEXT NOT NULL DEFAULT '[]'"
    ),
    "autopilot_schedule_json": (
        "ALTER TABLE initiative_plans ADD COLUMN autopilot_schedule_json TEXT NOT NULL DEFAULT '{}'"
    ),
    "autopilot_window_seen": "ALTER TABLE initiative_plans ADD COLUMN autopilot_window_seen DATETIME",
    "autopilot_window_owns": (
        "ALTER TABLE initiative_plans ADD COLUMN autopilot_window_owns BOOLEAN NOT NULL DEFAULT 0"
    ),
}


@migration("20261105_autopilot_timing", after="20261104_survey_pass_workspaces")
def m_autopilot_timing(conn: Connection) -> None:
    """Add the timer and schedule columns, each guarded, so a re-run is a no-op."""
    if not table_exists(conn, "initiative_plans"):
        return
    add_columns_if_missing(conn, "initiative_plans", _COLUMNS)
