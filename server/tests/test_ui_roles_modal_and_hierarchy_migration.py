"""Visual QA and the UI design agent carry the modal rule and the hierarchy
checklist once migrated, and a hand-edited role is left alone."""

from __future__ import annotations

import pytest
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions.ui_roles_modal_and_hierarchy import (
    REFRESHES,
    m_ui_roles_modal_and_hierarchy,
)
from loregarden.models.domain import StudioAgent
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker


def _agent(session: Session, slug: str) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()


@pytest.mark.parametrize(("slug", "role_file", "marker"), REFRESHES)
def test_each_seed_file_carries_its_marker_and_is_the_registered_role(slug, role_file, marker):
    assert AGENTS[slug]["role_file"] == role_file
    assert marker in (settings.agent_context_dir / role_file).read_text(encoding="utf-8")


def test_unedited_roles_are_refreshed_once(isolated_db):
    with Session(isolated_db) as session:
        before = {slug: strip_role_marker(session, slug, marker) for slug, _, marker in REFRESHES}
    with isolated_db.begin() as conn:
        m_ui_roles_modal_and_hierarchy(conn)
        m_ui_roles_modal_and_hierarchy(conn)
    with Session(isolated_db) as session:
        for slug, _, marker in REFRESHES:
            agent = _agent(session, slug)
            assert marker in agent.role_body
            assert agent.version == before[slug] + 1


def test_a_hand_edited_role_is_left_alone_and_the_other_still_refreshes(isolated_db):
    (edited, _, edited_marker), (other, _, other_marker) = REFRESHES
    with Session(isolated_db) as session:
        strip_role_marker(session, edited, edited_marker, edited_by="jacob")
        strip_role_marker(session, other, other_marker)
    with isolated_db.begin() as conn:
        m_ui_roles_modal_and_hierarchy(conn)
    with Session(isolated_db) as session:
        assert edited_marker not in _agent(session, edited).role_body
        assert other_marker in _agent(session, other).role_body
