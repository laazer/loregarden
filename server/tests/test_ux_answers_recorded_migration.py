"""The design role is told to record the answers a tree's PR needs, once migrated."""

from __future__ import annotations

from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions.ux_answers_recorded import (
    MARKER,
    ROLE_FILE,
    SLUG,
    m_ux_answers_recorded,
)
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker


def _agent(session: Session) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == SLUG)).one()


def test_the_seed_carries_the_marker_and_names_the_tool():
    assert AGENTS[SLUG]["role_file"] == ROLE_FILE
    seed = (settings.agent_context_dir / ROLE_FILE).read_text(encoding="utf-8")
    assert MARKER in seed
    assert "loregarden_record_ux_answers" in seed


def test_an_unedited_role_is_refreshed_once(isolated_db):
    with Session(isolated_db) as session:
        before = strip_role_marker(session, SLUG, MARKER)
    with isolated_db.begin() as conn:
        m_ux_answers_recorded(conn)
        m_ux_answers_recorded(conn)
    with Session(isolated_db) as session:
        agent = _agent(session)
        assert MARKER in agent.role_body
        assert agent.version == before + 1


def test_a_hand_edited_role_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        strip_role_marker(session, SLUG, MARKER, edited_by="jacob")
    with isolated_db.begin() as conn:
        m_ux_answers_recorded(conn)
    with Session(isolated_db) as session:
        assert MARKER not in _agent(session).role_body
