"""argv shape for the one-shot chat invocation.

This path runs `--permission-mode bypassPermissions` with no permission bridge
on the other end, so its argv is the only thing constraining the turn. It is
also the path that shipped duplicated flags, which is why the shape is pinned
here rather than left to review.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from loregarden.agents.cli_adapters import build_interactive_invocation, build_triage_invocation
from loregarden.models.domain.enums import ChatSurface, CliTool, ToolPosture
from loregarden.models.domain.schemas import StudioAgentToolGrants

PROMPT_FILE = Path("/tmp/loregarden-test-prompt.md")
USER_PROMPT = "Answer the operator."


def _triage_argv(**kwargs) -> list[str]:
    # The suite pins LOREGARDEN_CLI_ADAPTER=local; these assertions are about
    # claude's argv specifically, so resolve past the pin.
    with patch("loregarden.agents.cli_adapters.resolve_effective_adapter", return_value="claude"):
        invocation = build_triage_invocation(
            agent_id="triage",
            adapter="claude",
            prompt="system prompt",
            prompt_file=PROMPT_FILE,
            skill_name="",
            workspace_root=Path("/tmp"),
            user_prompt=USER_PROMPT,
            **kwargs,
        )
    return invocation.argv


class TestNoDuplicatedArguments:
    def test_system_prompt_file_is_passed_once(self):
        argv = _triage_argv()
        assert argv.count("--append-system-prompt-file") == 1
        assert argv.count(str(PROMPT_FILE)) == 1

    def test_user_prompt_positional_appears_once(self):
        assert _triage_argv().count(USER_PROMPT) == 1

    def test_still_true_with_extra_dirs_and_streaming(self):
        argv = _triage_argv(extra_dirs=[Path("/tmp/ref")], stream_json=True)
        assert argv.count("--append-system-prompt-file") == 1
        assert argv.count(USER_PROMPT) == 1

    def test_streaming_flags_are_present_and_ordered_after_p(self):
        argv = _triage_argv(stream_json=True)
        assert "--verbose" in argv
        assert "--include-partial-messages" in argv
        assert argv.index("-p") < argv.index("--verbose")


class TestPromptPositionalPrecedesMcpConfig:
    def test_prompt_comes_before_the_flag_block(self):
        """Claude binds --mcp-config to the LAST bare positional in argv.

        A prompt appended after that flag is mistaken for the config path, so
        the positional must stay ahead of it.
        """
        argv = _triage_argv()
        assert argv.index(USER_PROMPT) < argv.index("--mcp-config")


class TestToolGrantsReachBothBuilders:
    def test_oneshot_builder_emits_allowlist(self):
        argv = _triage_argv(
            tool_grants=StudioAgentToolGrants(
                posture=ToolPosture.ALLOWLIST, allowed_tools=[CliTool.READ]
            ),
            mcp_tools=["loregarden_get_ticket"],
            surface=ChatSurface.TICKET_TRIAGE,
        )
        assert "--allowedTools" in argv
        assert argv.count("--allowedTools") == 1

    def test_interactive_builder_emits_allowlist(self):
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=PROMPT_FILE,
            workspace_root=Path("/tmp"),
            tool_grants=StudioAgentToolGrants(
                posture=ToolPosture.ALLOWLIST, allowed_tools=[CliTool.READ]
            ),
            mcp_tools=["loregarden_get_ticket"],
            surface=ChatSurface.HOME,
        )
        assert "--allowedTools" in invocation.argv

    def test_default_agent_adds_no_tool_flags_to_either_builder(self):
        assert "--allowedTools" not in _triage_argv()
        invocation = build_interactive_invocation(
            adapter="claude", prompt_file=PROMPT_FILE, workspace_root=Path("/tmp")
        )
        assert "--allowedTools" not in invocation.argv

    def test_allowlist_value_is_a_single_token_so_it_cannot_eat_the_prompt(self):
        argv = _triage_argv(
            tool_grants=StudioAgentToolGrants(posture=ToolPosture.ALLOWLIST),
            mcp_tools=["loregarden_get_ticket"],
        )
        value = argv[argv.index("--allowedTools") + 1]
        assert " " not in value
        assert argv.count(USER_PROMPT) == 1
