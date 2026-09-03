"""Record which channel carried a stage verdict.

Two channels say the same thing — the typed `loregarden_complete_stage` call and
the `<<<LOREGARDEN_STAGE_REPORT>>>` text sentinel — and nothing recorded which an
agent used. So adherence could only be measured by grepping stdout, which is both
fragile and how lg-workflow-integrity-95 came to cite 7.1% adherence when the
real figure was 56.5%: the sentinel appears in prose whenever an agent quotes the
contract document, and a regex cannot tell a report from a discussion of one.

Empty-string default, because every existing row genuinely has no recorded
channel and backfilling a guess would turn "unmeasured" into a measurement — the
same mistake this column exists to stop making.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy.engine import Connection


def m_verdict_channel(conn: Connection) -> None:
    if not table_exists(conn, "agent_runs"):
        return
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "verdict_channel": (
                "ALTER TABLE agent_runs ADD COLUMN verdict_channel TEXT NOT NULL DEFAULT ''"
            ),
        },
    )
