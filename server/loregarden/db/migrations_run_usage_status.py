"""Say why a run has no usage figures, rather than leaving it to be guessed.

NULL token columns already mean "not measured", which is what keeps an unknown
run from being summed in as a free one. But NULL says nothing about *why*, and
two different facts were landing on the same value: a run whose adapter has no
usage surface at all, and a run whose adapter should have reported and did not.
The first is a known limitation; the second is a defect, and it was invisible.

AC1 of lg-workflow-integrity-496 asks for "an explicit marker that the figure was
unavailable". This is that marker.

Empty-string default rather than a backfilled guess: every row that predates this
column genuinely has no recorded reason, and inventing one would make the
historical gap look like a measurement. `RunUsageStatus.UNKNOWN` reads it back.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy.engine import Connection


def m_run_usage_status(conn: Connection) -> None:
    if not table_exists(conn, "agent_runs"):
        return
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "usage_status": (
                "ALTER TABLE agent_runs ADD COLUMN usage_status TEXT NOT NULL DEFAULT ''"
            ),
        },
    )
