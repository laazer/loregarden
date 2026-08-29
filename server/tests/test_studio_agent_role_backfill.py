"""Migration 0100's role-body refresh, and the decision it announces either way.

The guard matters more than the refresh: an operator who has edited Baxter in
Studio must keep their text. On the machine this was written for, the refresh
correctly does nothing.
"""

from __future__ import annotations

import logging

from loregarden.db.migrations_agent_grants import m_agent_tool_grants
from sqlalchemy import text


def _agent_row(conn, slug: str = "triage"):
    return conn.execute(
        text("SELECT id, version, role_body FROM studio_agents WHERE slug = :s"), {"s": slug}
    ).fetchone()


def _seed_agent(conn, *, version: int, creators: list[str], role_body: str = "old body") -> str:
    conn.execute(
        text(
            "INSERT INTO studio_agents (id, slug, name, description, role_body, adapter, "
            "default_model, timeout, default_skill, mcp_enabled, mcp_tools_json, "
            "gate_checks_json, handoff_checks_json, tool_grants_json, version, built_in, "
            "created_at, updated_at) VALUES ('agent-1', 'triage', 'Baxter', '', :body, "
            "'claude', '', 1800, '', 1, '[]', '[]', '[]', '{}', :version, 1, "
            "'2026-01-01', '2026-01-01')"
        ),
        {"body": role_body, "version": version},
    )
    for index, creator in enumerate(creators, start=1):
        conn.execute(
            text(
                "INSERT INTO studio_agent_versions (id, agent_id, version, snapshot_json, "
                "created_by, change_note, created_at) VALUES (:id, 'agent-1', :v, '{}', "
                ":by, '', '2026-01-01')"
            ),
            {"id": f"v{index}", "v": index, "by": creator},
        )
    return "agent-1"


class TestRefreshGuard:
    def test_fires_on_a_pristine_seeded_row(self, isolated_db):
        with isolated_db.begin() as conn:
            _seed_agent(conn, version=1, creators=["seed"])
            m_agent_tool_grants(conn)
            row = _agent_row(conn)
        assert row[1] == 2
        assert "Baxter" in row[2]
        assert row[2] != "old body"

    def test_does_not_fire_once_the_operator_has_edited(self, isolated_db):
        with isolated_db.begin() as conn:
            _seed_agent(conn, version=4, creators=["seed", "studio-ui", "studio-ui", "studio-ui"])
            m_agent_tool_grants(conn)
            row = _agent_row(conn)
        assert row[1] == 4
        assert row[2] == "old body"

    def test_does_not_fire_when_a_studio_edit_exists_at_version_one(self, isolated_db):
        with isolated_db.begin() as conn:
            _seed_agent(conn, version=1, creators=["studio-ui"])
            m_agent_tool_grants(conn)
            row = _agent_row(conn)
        assert row[2] == "old body"

    def test_records_a_restorable_version_when_it_fires(self, isolated_db):
        with isolated_db.begin() as conn:
            _seed_agent(conn, version=1, creators=["seed"])
            m_agent_tool_grants(conn)
            rows = conn.execute(
                text(
                    "SELECT created_by, change_note FROM studio_agent_versions "
                    "WHERE agent_id = 'agent-1' AND version = 2"
                )
            ).fetchall()
        assert rows and rows[0][0] == "migration"
        assert "0100" in rows[0][1]


class TestTheDecisionIsAnnounced:
    def test_a_skip_says_why(self, isolated_db, caplog):
        """A no-op migration that says nothing is indistinguishable from one
        that never ran, and this one's decision depends on invisible data."""
        with caplog.at_level(logging.INFO, logger="loregarden.db.migrations_agent_grants"):
            with isolated_db.begin() as conn:
                _seed_agent(conn, version=4, creators=["seed", "studio-ui"])
                m_agent_tool_grants(conn)
        assert caplog.records
        assert "4" in caplog.text

    def test_a_refresh_says_so(self, isolated_db, caplog):
        with caplog.at_level(logging.INFO, logger="loregarden.db.migrations_agent_grants"):
            with isolated_db.begin() as conn:
                _seed_agent(conn, version=1, creators=["seed"])
                m_agent_tool_grants(conn)
        assert caplog.records


class TestColumnIsAdded:
    def test_tool_grants_column_exists_after_the_migration(self, isolated_db):
        with isolated_db.begin() as conn:
            m_agent_tool_grants(conn)
            columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(studio_agents)")).fetchall()
            }
        assert "tool_grants_json" in columns
