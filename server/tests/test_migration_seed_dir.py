"""A migration reads the seed files that ship with it, wherever `repo_root` points.

The CLI points `repo_root` at the primary checkout to reach the shared database.
Run from a worktree, a migration that read seeds from there copied a primary
checkout's older files, or found none, and was recorded as applied anyway.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from loregarden.config import settings
from loregarden.db.versions.person_judged_criteria import ROLES
from loregarden.db.versions.person_judgment_roles_again import m_person_judgment_roles_again
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.repo_paths import REPO_ROOT
from tests.role_refresh_helpers import strip_role_marker

CODE_CHECKOUT = REPO_ROOT


def test_seeds_come_from_the_running_code_not_repo_root(tmp_path):
    env = os.environ | {"LOREGARDEN_REPO_ROOT": str(tmp_path)}
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from loregarden.config import settings; "
            "print(settings.agent_context_dir); print(settings.seed_context_dir)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=CODE_CHECKOUT / "server",
    )
    agent_context, seed = out.stdout.split()
    assert Path(agent_context) == tmp_path / "agent_context"
    assert Path(seed) == CODE_CHECKOUT / "agent_context"


def test_a_role_refresh_ignores_an_agent_context_without_the_change(isolated_db, tmp_path):
    with Session(isolated_db) as session:
        for slug, (_, marker) in ROLES.items():
            strip_role_marker(session, slug, marker)
    with mock.patch.object(settings, "agent_context_dir", tmp_path), isolated_db.begin() as conn:
        m_person_judgment_roles_again(conn)
    with Session(isolated_db) as session:
        for slug, (_, marker) in ROLES.items():
            agent = session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()
            assert marker in agent.role_body, slug
