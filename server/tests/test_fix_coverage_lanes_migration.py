"""The spec and architecture-review roles are told what a bug fix owes, once migrated."""

from __future__ import annotations

import pytest
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions import fix_coverage_lanes
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker

ROLES = pytest.mark.parametrize(
    ("slug", "role_file", "marker"), fix_coverage_lanes.ROLES, ids=lambda v: str(v)[:24]
)


def _agent(session: Session, slug: str) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()


@ROLES
def test_the_seed_carries_the_marker_and_names_the_record(slug, role_file, marker):
    assert AGENTS[slug]["role_file"] == role_file
    seed = (settings.agent_context_dir / role_file).read_text(encoding="utf-8")
    assert marker in seed
    assert "fix_coverage" in seed


@ROLES
def test_an_unedited_role_is_refreshed_once(isolated_db, slug, role_file, marker):
    with Session(isolated_db) as session:
        before = strip_role_marker(session, slug, marker)
    with isolated_db.begin() as conn:
        fix_coverage_lanes.m_fix_coverage_lanes(conn)
        fix_coverage_lanes.m_fix_coverage_lanes(conn)
    with Session(isolated_db) as session:
        agent = _agent(session, slug)
        assert marker in agent.role_body
        assert agent.version == before + 1


@ROLES
def test_a_hand_edited_role_is_left_alone(isolated_db, slug, role_file, marker):
    with Session(isolated_db) as session:
        strip_role_marker(session, slug, marker, edited_by="jacob")
    with isolated_db.begin() as conn:
        fix_coverage_lanes.m_fix_coverage_lanes(conn)
    with Session(isolated_db) as session:
        assert marker not in _agent(session, slug).role_body
