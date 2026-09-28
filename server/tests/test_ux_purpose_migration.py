"""0147: the design and visual-QA roles ask what a surface is for, not only its shape."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.migrations_ux_purpose import _ROLES, m_ux_purpose_in_design_lanes
from loregarden.models.domain import StudioAgent, WorkflowTemplate
from loregarden.services.studio_service import seed_builtin_agents
from sqlalchemy import text
from sqlmodel import Session, select


@pytest.mark.parametrize(("slug", "role_file", "marker"), _ROLES)
def test_each_seed_file_carries_its_marker_and_is_the_registered_role(slug, role_file, marker):
    assert AGENTS[slug]["role_file"] == role_file
    assert marker in (settings.agent_context_dir / role_file).read_text(encoding="utf-8")


def _strip(session: Session, slug: str, marker: str, *, edited_by: str | None = None) -> int:
    seed_builtin_agents(session)
    agent = session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()
    agent.role_body = agent.role_body.replace(marker, "")
    session.add(agent)
    session.commit()
    if edited_by:
        session.execute(
            text(
                "INSERT INTO studio_agent_versions (id, agent_id, version, snapshot_json, "
                "created_by, change_note, created_at) VALUES (:id, :a, 99, '{}', :by, 'x', "
                "'2026-01-01')"
            ),
            {"id": str(uuid4()), "a": agent.id, "by": edited_by},
        )
        session.commit()
    return agent.version


def _body(session: Session, slug: str) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()


@pytest.mark.parametrize(("slug", "_file", "marker"), _ROLES)
def test_an_unedited_role_is_refreshed_from_its_seed(isolated_db, slug, _file, marker):
    with Session(isolated_db) as session:
        before = _strip(session, slug, marker)
    with isolated_db.begin() as conn:
        m_ux_purpose_in_design_lanes(conn)
    with Session(isolated_db) as session:
        agent = _body(session, slug)
        assert marker in agent.role_body
        assert agent.version == before + 1


def test_a_hand_edited_role_is_left_alone(isolated_db):
    slug, _file, marker = _ROLES[1]
    with Session(isolated_db) as session:
        _strip(session, slug, marker, edited_by="jacob")
    with isolated_db.begin() as conn:
        m_ux_purpose_in_design_lanes(conn)
    with Session(isolated_db) as session:
        assert marker not in _body(session, slug).role_body


def _design_brief(conn) -> str | None:
    row = conn.execute(
        text("SELECT stages_json FROM workflow_templates WHERE slug='studio-loregarden-tdd-v3'")
    ).fetchone()
    if row is None:
        return None
    stages = json.loads(row[0])
    return next(s.get("stage_brief") or "" for s in stages if s.get("key") == "ui-design")


@pytest.fixture
def v3_template(isolated_db):
    """The v3 template as 0123 left it: a design stage carrying the five-states brief."""
    with Session(isolated_db) as session:
        session.add(
            WorkflowTemplate(
                slug="studio-loregarden-tdd-v3",
                name="v3",
                stages_json=json.dumps(
                    [
                        {
                            "key": "ui-design",
                            "name": "UI design",
                            "order": 1,
                            "stage_brief": "Name the loading, empty and error states.",
                        },
                        {"key": "done", "name": "Done", "order": 2, "terminal": True},
                    ]
                ),
            )
        )
        session.commit()
    return isolated_db


def test_the_design_brief_gains_the_three_questions_once(v3_template):
    with v3_template.begin() as conn:
        m_ux_purpose_in_design_lanes(conn)
        once = _design_brief(conn)
        m_ux_purpose_in_design_lanes(conn)
        twice = _design_brief(conn)

    assert once.startswith("Name the loading, empty and error states.")
    assert "question it answers" in once
    assert "largest realistic volume" in once
    assert twice == once
