"""Every role's outcome line names `tickets` as a block kind, with `waiting_on`, once migrated."""

from __future__ import annotations

from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions import tickets_block_kind_in_roles as migration
from loregarden.models.domain import StudioAgent
from loregarden.services.studio_service import seed_builtin_agents
from sqlmodel import Session, select


def test_every_seed_that_lists_the_kinds_names_tickets():
    """The seeds were rewritten in the same change; a seed left on the old line
    would put it back on the next reseed."""
    stale = [
        slug
        for slug, cfg in AGENTS.items()
        if migration.OLD_SENTENCE
        in (settings.agent_context_dir / cfg["role_file"]).read_text(encoding="utf-8")
    ]
    assert stale == []


def test_the_old_line_is_rewritten_once_and_the_rest_of_the_body_kept(isolated_db):
    with Session(isolated_db) as session:
        seed_builtin_agents(session)
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "planner")).one()
        agent.role_body = f"A person's own note.\n\n{migration.OLD_SENTENCE} more text."
        session.add(agent)
        session.commit()
        before = agent.version

    with isolated_db.begin() as conn:
        migration.m_tickets_block_kind_in_roles(conn)
        migration.m_tickets_block_kind_in_roles(conn)

    with Session(isolated_db) as session:
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == "planner")).one()
        assert agent.role_body == f"A person's own note.\n\n{migration.NEW_SENTENCE} more text."
        assert agent.version == before + 1
