"""Schema for what a stage run actually consumed.

Its own module for the reason the other ``migrations_*`` modules have one:
``migrations.py`` is at its line ceiling, and cost accounting is a topic with
more migrations ahead of it than behind it.

See ``models.domain.schemas.RunUsage`` for what the columns mean,
``agents.run_usage`` for how each CLI's numbers are read, and
``services.run_token_usage`` for how they are aggregated.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from sqlalchemy.engine import Connection


def m_agent_run_token_usage(conn: Connection) -> None:
    """Record tokens, model, and reasoning effort per agent run.

    Every column is nullable with **no default**, which is the whole point.
    Every row that exists today was written by an executor that measured
    nothing and never will, and a backfilled zero would be indistinguishable
    from a run that genuinely spent nothing — averaged into a cost report, it
    understates it silently. NULL is the marker: ``SUM`` skips it, ``COUNT(col)``
    counts only the measured rows, and an unmeasured run drops out of an
    aggregate instead of deflating it.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "input_tokens": "ALTER TABLE agent_runs ADD COLUMN input_tokens INTEGER",
            "output_tokens": "ALTER TABLE agent_runs ADD COLUMN output_tokens INTEGER",
            "cache_read_tokens": "ALTER TABLE agent_runs ADD COLUMN cache_read_tokens INTEGER",
            "cache_write_tokens": "ALTER TABLE agent_runs ADD COLUMN cache_write_tokens INTEGER",
            "model": "ALTER TABLE agent_runs ADD COLUMN model VARCHAR",
            "effort": "ALTER TABLE agent_runs ADD COLUMN effort VARCHAR",
        },
    )
