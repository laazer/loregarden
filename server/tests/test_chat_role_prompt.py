"""The prompt blocks Home chat and ticket triage share.

The point of the shared module is that the two rails cannot drift, so the
parity assertions here are the load-bearing ones.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from loregarden.agents.chat_role_prompt import (
    chat_posture_blocks,
    chat_role_blocks,
    chat_ui_primitives_blocks,
)
from loregarden.agents.prompt_blocks import AGENT_ROLE_HEADING, ROLE_BODY_CAP
from loregarden.models.domain.enums import ChatSurface
from loregarden.services.chat_primitives import parse_primitive_parts

HONEST_BACKGROUND_SENTENCE = (
    "I started X in the background; it will not report back here — "
    "ask me again, or watch `<log path>`."
)


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

    def test_honest_background_sentence_is_on_all_three_interactive_surfaces(self):
        """R1.1 / R1.4. The sentence lives once in chat_posture_blocks."""
        for surface in (ChatSurface.HOME, ChatSurface.TICKET_TRIAGE, ChatSurface.BRANCH_TRIAGE):
            blocks = chat_posture_blocks(surface=surface, interactive=True)
            joined = "\n".join(blocks)
            assert HONEST_BACKGROUND_SENTENCE in joined
            assert "waiter will wake me" not in joined.lower()
            assert "waiter will wake" not in joined.lower()

    def test_advisory_turns_do_not_carry_the_background_sentence(self):
        for surface in (ChatSurface.HOME, ChatSurface.TICKET_TRIAGE, ChatSurface.BRANCH_TRIAGE):
            joined = "\n".join(chat_posture_blocks(surface=surface, interactive=False))
            assert HONEST_BACKGROUND_SENTENCE not in joined

    def test_branch_triage_interactive_keeps_git_guidance(self):
        """R1.2. Unification must not drop branch git rules."""
        blocks = chat_posture_blocks(surface=ChatSurface.BRANCH_TRIAGE, interactive=True)
        joined = "\n".join(blocks)
        assert "force-push" in joined.lower() or "force push" in joined.lower()
        assert "delete" in joined.lower()
        assert HONEST_BACKGROUND_SENTENCE in joined


class TestUiPrimitives:
    def test_contract_names_the_execute_prefix_the_client_posts(self):
        from loregarden.agents.chat_role_prompt import AGENT_PLAN_EXECUTE_PREFIX

        rendered = "\n".join(chat_ui_primitives_blocks())
        assert AGENT_PLAN_EXECUTE_PREFIX in rendered

    def test_contract_is_identical_wherever_it_is_rendered(self):
        assert chat_ui_primitives_blocks() == chat_ui_primitives_blocks()

    def test_every_example_card_parses_as_the_card_it_shows(self):
        # The model copies these shapes. One the parser rejects degrades to raw
        # JSON in the thread, which is what an unspecified `qa` shape produced.
        rendered = "\n".join(chat_ui_primitives_blocks())
        examples = re.findall(r"```loregarden\\n(.*?)\\n```", rendered)
        assert {json.loads(body)["primitive"] for body in examples} >= {"todo_list", "qa"}
        for body in examples:
            parts = parse_primitive_parts(f"```loregarden\n{body}\n```")
            assert [part.primitive for part in parts] == [json.loads(body)["primitive"]]

    def test_plan_id_stability_rule_is_present(self):
        rendered = "\n".join(chat_ui_primitives_blocks())
        assert "plan_id" in rendered


class TestPromptBuildersUseSharedPosture:
    def test_branch_triage_prompt_calls_shared_blocks_and_keeps_git_guidance(self, db_session):
        """R1.2. Local interactive/advisory copy is gone; git guidance remains."""
        from loregarden.models.domain import Workspace
        from loregarden.services.branch_triage_chat_service import build_branch_triage_prompt
        from sqlmodel import select

        workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        src = Path(
            __import__("loregarden.services.branch_triage_chat_service", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "chat_posture_blocks" in src
        assert "You have real file, shell, git" not in src

        prompt = build_branch_triage_prompt(
            workspace,
            "main",
            None,
            [],
            "push this",
            ticket=None,
            interactive=True,
        )
        assert HONEST_BACKGROUND_SENTENCE in prompt
        assert "force-push" in prompt.lower() or "force push" in prompt.lower()
        assert "waiter will wake" not in prompt.lower()

    def test_home_acting_prompt_names_this_sessions_chat_session_id(self, db_session):
        """R1.3. Every acting turn, not only a feature loop."""
        from loregarden.models.domain import Workspace
        from loregarden.services.baxter_chat_service import build_baxter_chat_prompt
        from sqlmodel import select

        workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        prompt = build_baxter_chat_prompt(
            workspace=workspace,
            history=[],
            latest_user_message="push it",
            approvals=[],
            tickets=[],
            interactive=True,
            chat_session_id="sess-wait-home",
        )
        assert "sess-wait-home" in prompt
        assert "chat_session_id" in prompt
        assert HONEST_BACKGROUND_SENTENCE in prompt
