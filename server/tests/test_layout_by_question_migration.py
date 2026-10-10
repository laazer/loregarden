"""The design and visual-QA roles carry the list-vs-table rules once migrated."""

from __future__ import annotations

import pytest
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions.layout_by_question import ROLES, m_layout_by_question
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker


@pytest.mark.parametrize(("slug", "role_file", "marker"), ROLES)
def test_each_seed_file_carries_its_marker_and_is_the_registered_role(slug, role_file, marker):
    assert AGENTS[slug]["role_file"] == role_file
    assert marker in (settings.agent_context_dir / role_file).read_text(encoding="utf-8")


def _agent(session: Session, slug: str) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()


@pytest.mark.parametrize(("slug", "_file", "marker"), ROLES)
def test_an_unedited_role_is_refreshed_once(isolated_db, slug, _file, marker):
    with Session(isolated_db) as session:
        before = strip_role_marker(session, slug, marker)
    with isolated_db.begin() as conn:
        m_layout_by_question(conn)
        m_layout_by_question(conn)
    with Session(isolated_db) as session:
        agent = _agent(session, slug)
        assert marker in agent.role_body
        assert agent.version == before + 1


def test_a_hand_edited_role_is_left_alone(isolated_db):
    slug, _file, marker = ROLES[0]
    with Session(isolated_db) as session:
        strip_role_marker(session, slug, marker, edited_by="jacob")
    with isolated_db.begin() as conn:
        m_layout_by_question(conn)
    with Session(isolated_db) as session:
        assert marker not in _agent(session, slug).role_body
