"""Places this change refuses to fail quietly.

Each test here pins a behaviour, not a message: the requirement is that the
failure reaches somebody, not that it is worded a particular way.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    StudioAgent,
    StudioAgentCreate,
    StudioAgentToolGrants,
    StudioAgentUpdate,
    StudioAgentVersion,
)
from loregarden.models.domain.enums import ToolGrantWarningCode, ToolPosture
from loregarden.services.studio_service import StudioService, seed_builtin_agents
from sqlmodel import select


def _clear_seeded_agents(db_session) -> None:
    """App startup has already seeded; seeding is skip-when-present by slug."""
    for version in db_session.exec(select(StudioAgentVersion)).all():
        db_session.delete(version)
    for agent in db_session.exec(select(StudioAgent)).all():
        db_session.delete(agent)
    db_session.commit()


class TestSeedRefusesALobotomisedAgent:
    def test_unreadable_role_file_raises_instead_of_seeding_an_empty_role(self, db_session):
        """An empty role body is invisible from the outside — the agent just
        answers with no character. A broken checkout should fail the seed."""
        _clear_seeded_agents(db_session)
        with (
            patch("loregarden.services.studio_service.load_role_body", return_value=("", "")),
            pytest.raises(ValueError, match="role_file"),
        ):
            seed_builtin_agents(db_session)

    def test_a_root_with_no_agent_context_seeds_with_a_warning(self, db_session, caplog):
        """Distinct from a broken checkout, and expected: `init_db` initialises
        databases for roots that carry no agent assets yet. Seed, but say so."""
        _clear_seeded_agents(db_session)
        missing = Path("/nonexistent-agent-context-root")
        with (
            patch("loregarden.services.studio_service.load_role_body", return_value=("", "")),
            patch.object(settings, "agent_context_dir", missing),
            caplog.at_level(logging.WARNING, logger="loregarden.services.studio_service"),
        ):
            seeded = seed_builtin_agents(db_session)
        assert seeded
        assert caplog.records

    def test_a_readable_role_file_seeds_normally(self, db_session):
        # The guard must not fire on the real agent_context tree.
        _clear_seeded_agents(db_session)
        seed_builtin_agents(db_session)
        assert StudioService(db_session).get_agent("triage") is not None


class TestWarningsAreComputedOnRead:
    def test_an_agent_configured_long_ago_warns_when_it_is_opened(self, db_session):
        """Computed in the view, not on save, so a stale misconfiguration
        surfaces the moment someone looks at it."""
        service = StudioService(db_session)
        service.create_agent(StudioAgentCreate(slug="warned", name="Warned", adapter="cursor"))
        service.update_agent(
            "warned",
            StudioAgentUpdate(tool_grants=StudioAgentToolGrants(posture=ToolPosture.ALLOWLIST)),
        )

        # A fresh read, as if opening the agent in a later session.
        codes = {w.code for w in StudioService(db_session).get_agent("warned").tool_grant_warnings}
        assert ToolGrantWarningCode.ADAPTER_IGNORES_GRANTS in codes

    def test_warnings_appear_in_the_list_view_too(self, db_session):
        service = StudioService(db_session)
        service.create_agent(StudioAgentCreate(slug="listed", name="Listed", adapter="codex"))
        service.update_agent(
            "listed",
            StudioAgentUpdate(tool_grants=StudioAgentToolGrants(posture=ToolPosture.ALLOWLIST)),
        )
        listed = next(a for a in service.list_agents() if a.slug == "listed")
        assert listed.tool_grant_warnings

    def test_a_default_agent_reports_an_empty_list_not_a_missing_one(self, db_session):
        service = StudioService(db_session)
        service.create_agent(StudioAgentCreate(slug="plain", name="Plain", adapter="claude"))
        view = service.get_agent("plain")
        assert view.tool_grant_warnings == []

    def test_warnings_never_block_a_save(self, db_session):
        """Narrowing on purpose is legitimate; the API must accept it."""
        service = StudioService(db_session)
        service.create_agent(StudioAgentCreate(slug="narrow", name="Narrow", adapter="cursor"))
        saved = service.update_agent(
            "narrow",
            StudioAgentUpdate(tool_grants=StudioAgentToolGrants(posture=ToolPosture.ALLOWLIST)),
        )
        assert saved.tool_grants.posture is ToolPosture.ALLOWLIST
        assert saved.tool_grant_warnings


class TestGrantedServerFilteringIsAnnounced:
    def test_withheld_servers_are_logged(self, caplog):
        from loregarden.agents.mcp_context import loregarden_mcp_cli_config_json

        entries = {"github": {"type": "http", "url": "x"}, "linear": {"type": "http", "url": "y"}}
        with (
            patch("loregarden.agents.mcp_context.cli_server_entries", return_value=entries),
            caplog.at_level(logging.INFO, logger="loregarden.agents.mcp_context"),
        ):
            payload = loregarden_mcp_cli_config_json(object(), granted_servers=["github"])
        assert "linear" not in payload
        assert "github" in payload
        assert "linear" in caplog.text

    def test_no_grant_keeps_every_server(self):
        from loregarden.agents.mcp_context import loregarden_mcp_cli_config_json

        entries = {"github": {"type": "http", "url": "x"}, "linear": {"type": "http", "url": "y"}}
        with patch("loregarden.agents.mcp_context.cli_server_entries", return_value=entries):
            payload = loregarden_mcp_cli_config_json(object())
        assert "github" in payload
        assert "linear" in payload
