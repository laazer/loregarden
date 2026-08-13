"""Schema for the git boundary a run executes against.

Lives apart from ``migrations.py`` for the reason the other ``migrations_*``
modules do — that file is at its line ceiling, and a topic with more than one
migration ahead of it (the boundary verdict a receiving stage records is the
next) is better off owning its own module than adding to the pile.

See ``models.domain.schemas.GitBoundary`` for what the columns mean and
``services.git_boundary`` for how they are read and written.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing
from sqlalchemy.engine import Connection


def m_agent_run_git_boundary(conn: Connection) -> None:
    """Record the checkout, branch, commit, and inherited dirty paths a run
    started from.

    Empty defaults rather than NULL: rows written before these columns existed
    have no boundary, and *unknown* is a state every reader already handles.
    Making it also mean *NULL* would buy a second empty case for nothing.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "start_repo_path": (
                "ALTER TABLE agent_runs ADD COLUMN start_repo_path TEXT NOT NULL DEFAULT ''"
            ),
            "start_branch": (
                "ALTER TABLE agent_runs ADD COLUMN start_branch TEXT NOT NULL DEFAULT ''"
            ),
            "start_head_sha": (
                "ALTER TABLE agent_runs ADD COLUMN start_head_sha TEXT NOT NULL DEFAULT ''"
            ),
            "start_dirty_paths_json": (
                "ALTER TABLE agent_runs ADD COLUMN start_dirty_paths_json TEXT NOT NULL "
                "DEFAULT '[]'"
            ),
        },
    )
