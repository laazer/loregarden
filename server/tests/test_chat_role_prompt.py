"""The prompt blocks Home chat and ticket triage share.

The point of the shared module is that the two rails cannot drift, so the
parity assertions here are the load-bearing ones.
"""

from __future__ import annotations

import logging

from loregarden.agents.chat_role_prompt import (
    chat_posture_blocks,
    chat_role_blocks,
    chat_ui_primitives_blocks,
)
from loregarden.agents.prompt_blocks import AGENT_ROLE_HEADING, ROLE_BODY_CAP
from loregarden.models.domain.enums import ChatSurface


class TestRoleBlocks:
    def test_role_body_renders_under_the_same_heading_a_stage_uses(self):
        blocks = chat_role_blocks({"role_body": "You are Baxter."}, surface=ChatSurface.HOME)
        assert AGENT_ROLE_HEADING in blocks
        assert "You are Baxter." in blocks

    def test_role_body_is_capped(self):
        blocks = chat_role_blocks(
            {"role_body": "x" * (ROLE_BODY_CAP + 500)}, surface=ChatSurface.HOME
        )
        assert len(blocks[-1]) == ROLE_BODY_CAP

    def test_empty_role_body_renders_nothing(self):
        assert chat_role_blocks({}, surface=ChatSurface.HOME) == []

    def test_empty_role_body_is_logged_rather_than_passing_silently(self, caplog):
        """A rail running without its configured identity must leave a trace.

        Rendering nothing is the right output, but an operator whose Studio edit
        never arrived has no other way to find out.
        """
        with caplog.at_level(logging.WARNING, logger="loregarden.agents.chat_role_prompt"):
            chat_role_blocks({"slug": "triage", "role_body": ""}, surface=ChatSurface.HOME)
        assert any(record.levelno >= logging.WARNING for record in caplog.records)
        assert "triage" in caplog.text


class TestPostureParityAcrossRails:
    def test_advisory_text_is_identical_on_both_rails(self):
        home = chat_posture_blocks(surface=ChatSurface.HOME, interactive=False)
        triage = chat_posture_blocks(surface=ChatSurface.TICKET_TRIAGE, interactive=False)
        assert home == triage

    def test_interactive_text_differs_only_in_home_scoped_lines(self):
        """Home says two things triage must not: that git is in scope, and that
        no ticket is implied. Everything else is one shared copy."""
        home = chat_posture_blocks(surface=ChatSurface.HOME, interactive=True, approval_bridge=True)
        triage = chat_posture_blocks(
            surface=ChatSurface.TICKET_TRIAGE, interactive=True, approval_bridge=True
        )
        only_home = [line for line in home if line not in triage]
        assert len(only_home) == 2
        assert any("Git is in scope" in line for line in only_home)
        assert any("not scoped to a work item" in line for line in only_home)
        assert all(line in home for line in triage)

    def test_advisory_reason_is_named_when_supplied(self):
        blocks = chat_posture_blocks(
            surface=ChatSurface.HOME, interactive=False, advisory_reason="codex has no inbox"
        )
        assert any("codex has no inbox" in line for line in blocks)

    def test_advisory_turn_keeps_mcp_available(self):
        blocks = chat_posture_blocks(surface=ChatSurface.HOME, interactive=False)
        assert any("MCP" in line for line in blocks)

    def test_bridge_and_non_bridge_executing_turns_say_different_things(self):
        bridged = chat_posture_blocks(
            surface=ChatSurface.HOME, interactive=True, approval_bridge=True
        )
        direct = chat_posture_blocks(
            surface=ChatSurface.HOME, interactive=True, approval_bridge=False
        )
        assert bridged != direct


class TestUiPrimitives:
    def test_contract_names_the_execute_prefix_the_client_posts(self):
        from loregarden.agents.chat_role_prompt import AGENT_PLAN_EXECUTE_PREFIX

        rendered = "\n".join(chat_ui_primitives_blocks())
        assert AGENT_PLAN_EXECUTE_PREFIX in rendered

    def test_contract_is_identical_wherever_it_is_rendered(self):
        assert chat_ui_primitives_blocks() == chat_ui_primitives_blocks()

    def test_plan_id_stability_rule_is_present(self):
        rendered = "\n".join(chat_ui_primitives_blocks())
        assert "plan_id" in rendered
