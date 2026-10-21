"""The repair agent's 900s default is raised to 2400s, never over a person's choice."""

from __future__ import annotations

from loregarden.agents.registry import REPAIR_AGENT_ID, get_agent
from loregarden.db.versions.repair_timeout_for_merges import (
    NEW_TIMEOUT,
    OLD_TIMEOUT,
    m_repair_timeout_for_merges,
)
from loregarden.models.domain import StudioAgent
from loregarden.services.studio_service import seed_builtin_agents
from sqlmodel import Session, select


def _repair(session: Session) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == REPAIR_AGENT_ID)).one()


def _set_timeout(session: Session, timeout: int) -> int:
    """Seed the agents, then give `repair` the timeout the live row has."""
    seed_builtin_agents(session)
    agent = _repair(session)
    agent.timeout = timeout
    session.add(agent)
    session.commit()
    return agent.version


def test_the_old_default_is_raised_once(isolated_db):
    with Session(isolated_db) as session:
        before = _set_timeout(session, OLD_TIMEOUT)
    with isolated_db.begin() as conn:
        m_repair_timeout_for_merges(conn)
        m_repair_timeout_for_merges(conn)
    with Session(isolated_db) as session:
        agent = _repair(session)
        assert (agent.timeout, agent.version) == (NEW_TIMEOUT, before + 1)


def test_a_timeout_a_person_chose_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        before = _set_timeout(session, 1200)
    with isolated_db.begin() as conn:
        m_repair_timeout_for_merges(conn)
    with Session(isolated_db) as session:
        agent = _repair(session)
        assert (agent.timeout, agent.version) == (1200, before)


def test_a_fresh_seed_already_has_the_new_timeout(isolated_db):
    with Session(isolated_db) as session:
        seed_builtin_agents(session)
        assert _repair(session).timeout == NEW_TIMEOUT
    assert get_agent(REPAIR_AGENT_ID)["timeout"] == NEW_TIMEOUT
