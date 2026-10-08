"""Set up a built-in agent whose role body predates a seed-file refresh."""

from __future__ import annotations

from uuid import uuid4

from loregarden.models.domain import StudioAgent
from loregarden.services.studio_service import seed_builtin_agents
from sqlalchemy import text
from sqlmodel import Session, select


def strip_role_marker(
    session: Session, slug: str, marker: str, *, edited_by: str | None = None
) -> int:
    """Seed the agents, drop `marker` from `slug`'s body, and return its version.

    `edited_by` records a version written by that person, which a refresh must respect.
    """
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
