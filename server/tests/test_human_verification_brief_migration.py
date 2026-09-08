"""Migration 0115: the brief skill is seeded, and the two askers point at it.

The link is the load-bearing part. A common asset reaches an agent only when its
role body says to read it, so a skill file on disk with no reference from
`visual_qa` / `ac_gatekeeper` is a document nobody opens. The guard is the same
one 0100 established: an operator's edited agent keeps its text.
"""

from __future__ import annotations

import logging

from loregarden.db.migrations_human_verification import (
    REFRESH_ROLE_FILES,
    SKILL_SLUG,
    m_human_verification_brief,
)
from sqlalchemy import text

SKILL_PATH = "agent_context/skills/human-verification-brief/SKILL.md"


def _seed_agent(conn, *, slug: str, version: int, creators: list[str]) -> None:
    conn.execute(
        text(
            "INSERT INTO studio_agents (id, slug, name, description, role_body, adapter, "
            "default_model, timeout, default_skill, mcp_enabled, mcp_tools_json, "
            "gate_checks_json, handoff_checks_json, tool_grants_json, version, built_in, "
            "created_at, updated_at) VALUES (:id, :slug, :slug, '', 'old body', "
            "'claude', '', 900, '', 1, '[]', '[]', '[]', '{}', :version, 1, "
            "'2026-01-01', '2026-01-01')"
        ),
        {"id": f"agent-{slug}", "slug": slug, "version": version},
    )
    for index, creator in enumerate(creators, start=1):
        conn.execute(
            text(
                "INSERT INTO studio_agent_versions (id, agent_id, version, snapshot_json, "
                "created_by, change_note, created_at) VALUES (:id, :agent, :v, '{}', "
                ":by, '', '2026-01-01')"
            ),
            {"id": f"{slug}-v{index}", "agent": f"agent-{slug}", "v": index, "by": creator},
        )


def _agent_row(conn, slug: str):
    return conn.execute(
        text("SELECT version, role_body FROM studio_agents WHERE slug = :s"), {"s": slug}
    ).fetchone()


def _skill_rows(conn):
    return conn.execute(
        text("SELECT id, name, description, body, built_in FROM skills WHERE slug = :s"),
        {"s": SKILL_SLUG},
    ).fetchall()


class TestTheSkillIsSeeded:
    def test_the_row_arrives_with_its_frontmatter(self, isolated_db):
        with isolated_db.begin() as conn:
            m_human_verification_brief(conn)
            rows = _skill_rows(conn)
        assert len(rows) == 1
        _, name, description, body, built_in = rows[0]
        assert name == SKILL_SLUG
        assert description.strip()
        assert body.strip()
        assert built_in

    def test_a_restorable_version_is_recorded(self, isolated_db):
        with isolated_db.begin() as conn:
            m_human_verification_brief(conn)
            skill_id = _skill_rows(conn)[0][0]
            versions = conn.execute(
                text("SELECT version, created_by FROM skill_versions WHERE skill_id = :i"),
                {"i": skill_id},
            ).fetchall()
        assert versions == [(1, "migration")]

    def test_running_twice_does_not_duplicate_or_overwrite(self, isolated_db):
        with isolated_db.begin() as conn:
            m_human_verification_brief(conn)
            conn.execute(
                text("UPDATE skills SET body = 'operator text' WHERE slug = :s"),
                {"s": SKILL_SLUG},
            )
            m_human_verification_brief(conn)
            rows = _skill_rows(conn)
        assert len(rows) == 1
        assert rows[0][3] == "operator text"


class TestTheAskersPointAtIt:
    def test_both_role_bodies_are_refreshed_and_reference_the_skill(self, isolated_db):
        with isolated_db.begin() as conn:
            for slug in REFRESH_ROLE_FILES:
                _seed_agent(conn, slug=slug, version=1, creators=["seed"])
            m_human_verification_brief(conn)
            rows = {slug: _agent_row(conn, slug) for slug in REFRESH_ROLE_FILES}
        for slug, row in rows.items():
            assert row[0] == 2, slug
            assert SKILL_PATH in row[1], slug

    def test_an_edited_agent_keeps_its_text(self, isolated_db):
        with isolated_db.begin() as conn:
            _seed_agent(conn, slug="visual_qa", version=3, creators=["seed", "studio-ui"])
            m_human_verification_brief(conn)
            row = _agent_row(conn, "visual_qa")
        assert row == (3, "old body")

    def test_a_missing_agent_row_is_not_an_error(self, isolated_db, caplog):
        with caplog.at_level(logging.INFO, logger="loregarden.db.migrations_human_verification"):
            with isolated_db.begin() as conn:
                m_human_verification_brief(conn)
                rows = conn.execute(text("SELECT slug FROM studio_agents")).fetchall()
        assert rows == []
        assert caplog.records
