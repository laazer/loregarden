"""Triage, static QA and repair are pinned to Sonnet, never over a person's choice."""

from __future__ import annotations

import json

from loregarden.db.versions.sonnet_for_mechanical_lanes import (
    AGENTS,
    MODEL,
    STAGE,
    TEMPLATE,
    m_sonnet_for_mechanical_lanes,
)
from loregarden.models.domain import StudioAgent, WorkflowTemplate
from loregarden.services.studio_service import seed_builtin_agents
from sqlmodel import Session, select


def _agent(session: Session, slug: str) -> StudioAgent:
    return session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()


def _template(session: Session, triage_model: str = "") -> WorkflowTemplate:
    template = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == TEMPLATE)
    ).first()
    if template is None:
        template = WorkflowTemplate(slug=TEMPLATE, name="v3", transitions_json="[]")
    template.stages_json = json.dumps(
        [
            {
                "key": STAGE,
                "name": "Triage",
                "agent_id": "ticket_scoper",
                "order": 1,
                "model": triage_model,
            }
        ]
    )
    session.add(template)
    session.commit()
    return template


def _triage_model(session: Session) -> str:
    template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == TEMPLATE)).one()
    session.refresh(template)
    return next(s for s in json.loads(template.stages_json) if s["key"] == STAGE).get("model", "")


def _clear(session: Session, slug: str, model: str = "") -> int:
    """Seed the agents, then set `slug`'s pin as the live row has it (fresh seeds carry Sonnet)."""
    seed_builtin_agents(session)
    agent = _agent(session, slug)
    agent.default_model = model
    session.add(agent)
    session.commit()
    return agent.version


def test_unpinned_lanes_move_to_sonnet_once(isolated_db):
    with Session(isolated_db) as session:
        before = {slug: _clear(session, slug) for slug in AGENTS}
        _template(session)
    with isolated_db.begin() as conn:
        m_sonnet_for_mechanical_lanes(conn)
        m_sonnet_for_mechanical_lanes(conn)
    with Session(isolated_db) as session:
        for slug in AGENTS:
            agent = _agent(session, slug)
            assert agent.default_model == MODEL
            assert agent.version == before[slug] + 1
        assert _triage_model(session) == MODEL
        assert _agent(session, "ticket_scoper").default_model == "", (
            "Ticket Studio shares ticket_scoper; only the triage stage is pinned"
        )


def test_a_model_a_person_chose_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        _clear(session, "static_qa", model="opus")
        _template(session, triage_model="haiku")
    with isolated_db.begin() as conn:
        m_sonnet_for_mechanical_lanes(conn)
    with Session(isolated_db) as session:
        assert _agent(session, "static_qa").default_model == "opus"
        assert _triage_model(session) == "haiku"
