"""Record how each agent run's process was detached.

Nullable, and deliberately not backfilled: 1,449 existing rows carry no process
identity at all, so NULL — "this run never recorded one" — is the only truthful
value for them. The run log modal renders that as an em dash rather than
guessing `file`, which would assert something the row does not say.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from loregarden.db.versions import migration
from sqlalchemy import Connection


@migration("20261008_agent_run_transport", after="20261009_motion_guide")
def m_agent_run_transport(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "agent_runs",
        {"agent_transport": "ALTER TABLE agent_runs ADD COLUMN agent_transport VARCHAR"},
    )
