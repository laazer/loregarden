"""Visual QA is told to hand the human gate its screenshots and pages, once migrated."""

from __future__ import annotations

from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions import visual_qa_review_kit as module
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker


def _agent(session: Session) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == module.SLUG)).one()


def test_the_seed_carries_the_marker():
    assert AGENTS[module.SLUG]["role_file"] == module.ROLE_FILE
    seed = (settings.agent_context_dir / module.ROLE_FILE).read_text(encoding="utf-8")
    assert module.MARKER in seed


def test_an_unedited_role_is_refreshed_once(isolated_db):
    with Session(isolated_db) as session:
        before = strip_role_marker(session, module.SLUG, module.MARKER)
    with isolated_db.begin() as conn:
        module.m_visual_qa_review_kit(conn)
        module.m_visual_qa_review_kit(conn)
    with Session(isolated_db) as session:
        agent = _agent(session)
        assert module.MARKER in agent.role_body
        assert agent.version == before + 1


def test_a_hand_edited_role_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        strip_role_marker(session, module.SLUG, module.MARKER, edited_by="jacob")
    with isolated_db.begin() as conn:
        module.m_visual_qa_review_kit(conn)
    with Session(isolated_db) as session:
        assert module.MARKER not in _agent(session).role_body
