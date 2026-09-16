"""0137: every built-in role's outcome section names `blocked_kind` (749).

Role bodies are DB-authoritative, so the seed files alone change nothing for
an existing install. Two guards, as `0123` had: a body already carrying the
sentence is untouched, and a body a person edited is left alone and reported.
"""

from __future__ import annotations

from uuid import uuid4

from loregarden.agents.registry import AGENTS
from loregarden.db.migrations_block_kind_prompts import (
    BLOCK_KIND_SENTENCE,
    m_block_kind_in_role_prompts,
)
from loregarden.models.domain import StudioAgent
from loregarden.services.studio_service import seed_builtin_agents
from sqlalchemy import text
from sqlmodel import Session, select


def test_every_seed_file_carries_the_sentence():
    from loregarden.config import settings

    missing = [
        slug
        for slug, cfg in AGENTS.items()
        if BLOCK_KIND_SENTENCE
        not in (settings.agent_context_dir / cfg["role_file"]).read_text(encoding="utf-8")
    ]
    assert missing == [], f"role files whose outcome section does not name blocked_kind: {missing}"


def _stale(session: Session, slug: str, *, edited_by: str | None = None) -> StudioAgent:
    seed_builtin_agents(session)
    agent = session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()
    agent.role_body = agent.role_body.replace(BLOCK_KIND_SENTENCE, "")
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
    return agent


def test_a_seeded_body_without_the_sentence_is_refreshed(isolated_db):
    with Session(isolated_db) as session:
        before = _stale(session, "planner").version
    with isolated_db.begin() as conn:
        m_block_kind_in_role_prompts(conn)
    with Session(isolated_db) as session:
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "planner")).one()
        assert BLOCK_KIND_SENTENCE in agent.role_body
        assert agent.version == before + 1


def test_a_reconciler_written_version_is_not_a_person(isolated_db):
    """`reconcile` is the studio-agent reconciler; counting it as an editor
    skipped 25 of 27 bodies on the first live run of 0137."""
    with Session(isolated_db) as session:
        before = _stale(session, "planner", edited_by="reconcile").version
    with isolated_db.begin() as conn:
        m_block_kind_in_role_prompts(conn)
    with Session(isolated_db) as session:
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "planner")).one()
        assert BLOCK_KIND_SENTENCE in agent.role_body
        assert agent.version == before + 1


def test_a_hand_edited_body_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        _stale(session, "spec", edited_by="jacob")
    with isolated_db.begin() as conn:
        m_block_kind_in_role_prompts(conn)
    with Session(isolated_db) as session:
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "spec")).one()
        assert BLOCK_KIND_SENTENCE not in agent.role_body


def test_a_current_body_is_not_touched(isolated_db):
    with Session(isolated_db) as session:
        seed_builtin_agents(session)
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "planner")).one()
        before = agent.version
    with isolated_db.begin() as conn:
        m_block_kind_in_role_prompts(conn)
    with Session(isolated_db) as session:
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "planner")).one()
        assert agent.version == before
