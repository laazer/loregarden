"""The design role is told to record the answers a tree's PR needs, and that its
stage does not pass without them, once migrated."""

from __future__ import annotations

from types import ModuleType

import pytest
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions import ux_answers_recorded, ux_answers_required
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker

MIGRATIONS = pytest.mark.parametrize(
    ("module", "apply"),
    [
        (ux_answers_recorded, ux_answers_recorded.m_ux_answers_recorded),
        (ux_answers_required, ux_answers_required.m_ux_answers_required),
    ],
    ids=["recorded", "required"],
)


def _agent(session: Session, slug: str) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()


@MIGRATIONS
def test_the_seed_carries_the_marker(module: ModuleType, apply):
    assert AGENTS[module.SLUG]["role_file"] == module.ROLE_FILE
    seed = (settings.agent_context_dir / module.ROLE_FILE).read_text(encoding="utf-8")
    assert module.MARKER in seed
    assert "loregarden_record_ux_answers" in seed


@MIGRATIONS
def test_an_unedited_role_is_refreshed_once(isolated_db, module: ModuleType, apply):
    with Session(isolated_db) as session:
        before = strip_role_marker(session, module.SLUG, module.MARKER)
    with isolated_db.begin() as conn:
        apply(conn)
        apply(conn)
    with Session(isolated_db) as session:
        agent = _agent(session, module.SLUG)
        assert module.MARKER in agent.role_body
        assert agent.version == before + 1


@MIGRATIONS
def test_a_hand_edited_role_is_left_alone(isolated_db, module: ModuleType, apply):
    with Session(isolated_db) as session:
        strip_role_marker(session, module.SLUG, module.MARKER, edited_by="jacob")
    with isolated_db.begin() as conn:
        apply(conn)
    with Session(isolated_db) as session:
        assert module.MARKER not in _agent(session, module.SLUG).role_body
