"""The AC gatekeeper role tells it to settle each acceptance criterion once migrated."""

from __future__ import annotations

from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.migration_utils import table_exists
from loregarden.db.versions.ac_gatekeeper_settles_criteria import (
    MARKER,
    ROLE_FILE,
    SLUG,
    m_ac_gatekeeper_settles_criteria,
)
from loregarden.models.domain import StudioAgent
from sqlalchemy import create_engine
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker


def _agent(session: Session) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == SLUG)).one()


def test_the_seed_file_carries_the_marker_and_is_the_registered_role():
    assert AGENTS[SLUG]["role_file"] == ROLE_FILE
    assert MARKER in (settings.agent_context_dir / ROLE_FILE).read_text(encoding="utf-8")


def test_an_unedited_role_is_refreshed_once(isolated_db):
    with Session(isolated_db) as session:
        before = strip_role_marker(session, SLUG, MARKER)
    with isolated_db.begin() as conn:
        m_ac_gatekeeper_settles_criteria(conn)
        m_ac_gatekeeper_settles_criteria(conn)
    with Session(isolated_db) as session:
        agent = _agent(session)
        assert MARKER in agent.role_body
        assert agent.version == before + 1


def test_a_hand_edited_role_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        strip_role_marker(session, SLUG, MARKER, edited_by="jacob")
    with isolated_db.begin() as conn:
        m_ac_gatekeeper_settles_criteria(conn)
    with Session(isolated_db) as session:
        assert MARKER not in _agent(session).role_body


def test_a_database_with_no_agent_tables_is_left_alone():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        m_ac_gatekeeper_settles_criteria(conn)
        assert not table_exists(conn, "studio_agents")
