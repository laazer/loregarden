"""Tool grants: what reaches argv, and what the operator is warned about."""

from __future__ import annotations

from loregarden.agents.cli_tool_ids import CHAT_ADVISORY_CLI_TOOLS, CHAT_INTERACTIVE_CLI_TOOLS
from loregarden.agents.mcp_context import CLAUDE_MCP_TOOL_PREFIX
from loregarden.agents.tool_grants import (
    analyze_tool_grants,
    claude_tool_flags,
    effective_allowlist,
    parse_tool_grants,
)
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS
from loregarden.models.domain.enums import (
    ChatSurface,
    CliTool,
    ToolGrantWarningCode,
    ToolPosture,
)
from loregarden.models.domain.schemas import StudioAgentToolGrants

MCP_TOOLS = ["loregarden_get_ticket", "loregarden_append_checkpoint", "loregarden_complete_stage"]


def _allowlist(**kwargs) -> StudioAgentToolGrants:
    return StudioAgentToolGrants(posture=ToolPosture.ALLOWLIST, **kwargs)


def _codes(warnings) -> set[ToolGrantWarningCode]:
    return {warning.code for warning in warnings}


class TestEffectiveAllowlist:
    def test_inherit_constrains_nothing(self):
        grants = StudioAgentToolGrants()
        assert (
            effective_allowlist(
                grants, mcp_tools=MCP_TOOLS, mcp_enabled=True, surface=ChatSurface.HOME
            )
            == []
        )

    def test_unrestricted_constrains_nothing(self):
        grants = StudioAgentToolGrants(posture=ToolPosture.UNRESTRICTED)
        assert (
            effective_allowlist(
                grants, mcp_tools=MCP_TOOLS, mcp_enabled=True, surface=ChatSurface.HOME
            )
            == []
        )

    def test_allowlist_derives_from_the_rail_and_the_agents_mcp_tools(self):
        names = effective_allowlist(
            _allowlist(), mcp_tools=MCP_TOOLS, mcp_enabled=True, surface=ChatSurface.HOME
        )
        for tool in CHAT_INTERACTIVE_CLI_TOOLS:
            assert tool.value in names
        for tool in MCP_TOOLS:
            assert f"{CLAUDE_MCP_TOOL_PREFIX}{tool}" in names

    def test_advisory_turn_starts_from_the_smaller_set(self):
        names = effective_allowlist(
            _allowlist(),
            mcp_tools=[],
            mcp_enabled=False,
            surface=ChatSurface.TICKET_TRIAGE,
            interactive=False,
        )
        assert set(names) == {tool.value for tool in CHAT_ADVISORY_CLI_TOOLS}
        assert CliTool.BASH.value not in names

    def test_allowed_tools_only_narrows_cli_tools(self):
        names = effective_allowlist(
            _allowlist(allowed_tools=[CliTool.READ]),
            mcp_tools=MCP_TOOLS,
            mcp_enabled=True,
            surface=ChatSurface.HOME,
        )
        cli_names = [name for name in names if not name.startswith("mcp__")]
        assert cli_names == [CliTool.READ.value]
        # Narrowing the CLI tools must not silently drop MCP, which is governed
        # by mcp_tools / mcp_servers instead.
        assert f"{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket" in names

    def test_an_operator_cannot_widen_past_the_rails_offer(self):
        names = effective_allowlist(
            _allowlist(allowed_tools=list(CliTool)),
            mcp_tools=[],
            mcp_enabled=False,
            surface=ChatSurface.HOME,
            interactive=False,
        )
        assert set(names) <= {tool.value for tool in CHAT_ADVISORY_CLI_TOOLS}

    def test_disallowed_tools_are_removed(self):
        names = effective_allowlist(
            _allowlist(disallowed_tools=[CliTool.BASH]),
            mcp_tools=[],
            mcp_enabled=False,
            surface=ChatSurface.HOME,
        )
        assert CliTool.BASH.value not in names

    def test_granted_servers_expand_to_a_wildcard(self):
        names = effective_allowlist(
            _allowlist(mcp_servers=["github"]),
            mcp_tools=[],
            mcp_enabled=True,
            surface=ChatSurface.HOME,
        )
        assert "mcp__github__*" in names


class TestClaudeToolFlags:
    def test_no_grants_means_no_flags(self):
        assert (
            claude_tool_flags(None, mcp_tools=MCP_TOOLS, mcp_enabled=True, surface=ChatSurface.HOME)
            == []
        )

    def test_inherit_emits_no_flags_so_the_change_ships_inert(self):
        assert (
            claude_tool_flags(
                StudioAgentToolGrants(),
                mcp_tools=MCP_TOOLS,
                mcp_enabled=True,
                surface=ChatSurface.HOME,
            )
            == []
        )

    def test_tool_list_is_one_comma_joined_token(self):
        flags = claude_tool_flags(
            _allowlist(), mcp_tools=MCP_TOOLS, mcp_enabled=True, surface=ChatSurface.HOME
        )
        assert flags[0] == "--allowedTools"
        # The flag is variadic in the CLI: space-separated values would swallow
        # the positional prompt that follows it.
        assert len(flags) == 2
        assert "," in flags[1]
        assert " " not in flags[1]

    def test_disallowed_tools_get_their_own_flag(self):
        flags = claude_tool_flags(
            _allowlist(disallowed_tools=[CliTool.BASH, CliTool.WRITE]),
            mcp_tools=[],
            mcp_enabled=False,
            surface=ChatSurface.HOME,
        )
        assert "--disallowedTools" in flags
        assert flags[flags.index("--disallowedTools") + 1] == "Bash,Write"


class TestAutoApproveSupersetInvariant:
    def test_every_granted_auto_approved_tool_survives_a_default_allowlist(self):
        """An allowlist must stay a superset of what the bridge could approve.

        A tool the bridge would auto-approve but the CLI never offers fails
        inside the turn with no approval row and no telemetry — the exact
        invisible failure the derive-then-narrow rule exists to prevent.
        """
        auto_names = [tool.value for tool in AUTO_APPROVED_MCP_TOOLS]
        names = set(
            effective_allowlist(
                _allowlist(), mcp_tools=auto_names, mcp_enabled=True, surface=ChatSurface.HOME
            )
        )
        for tool in auto_names:
            assert f"{CLAUDE_MCP_TOOL_PREFIX}{tool}" in names


class TestWarnings:
    def test_default_agent_produces_no_warnings(self):
        assert (
            analyze_tool_grants(
                StudioAgentToolGrants(),
                adapter="claude",
                mcp_tools=MCP_TOOLS,
                mcp_enabled=True,
                registered_servers=frozenset(),
            )
            == []
        )

    def test_non_claude_adapter_is_told_the_grant_does_nothing(self):
        warnings = analyze_tool_grants(
            _allowlist(),
            adapter="cursor",
            mcp_tools=MCP_TOOLS,
            mcp_enabled=True,
            registered_servers=frozenset(),
        )
        assert ToolGrantWarningCode.ADAPTER_IGNORES_GRANTS in _codes(warnings)

    def test_claude_adapter_does_not_get_the_adapter_warning(self):
        warnings = analyze_tool_grants(
            _allowlist(),
            adapter="claude",
            mcp_tools=MCP_TOOLS,
            mcp_enabled=True,
            registered_servers=frozenset(),
        )
        assert ToolGrantWarningCode.ADAPTER_IGNORES_GRANTS not in _codes(warnings)

    def test_unregistered_server_grant_is_flagged(self):
        warnings = analyze_tool_grants(
            _allowlist(mcp_servers=["ghost"]),
            adapter="claude",
            mcp_tools=MCP_TOOLS,
            mcp_enabled=True,
            registered_servers=frozenset({"github"}),
        )
        unknown = [w for w in warnings if w.code == ToolGrantWarningCode.UNKNOWN_MCP_SERVER]
        assert unknown and unknown[0].tools == ["ghost"]

    def test_excluding_every_auto_approved_tool_is_flagged_with_names(self):
        warnings = analyze_tool_grants(
            _allowlist(
                allowed_tools=[CliTool.READ],
                disallowed_tools=[],
                mcp_servers=[],
            ),
            adapter="claude",
            mcp_tools=["loregarden_get_ticket"],
            mcp_enabled=False,  # MCP off drops the granted tools from the allowlist
            registered_servers=frozenset(),
        )
        auto = [w for w in warnings if w.code == ToolGrantWarningCode.AUTO_APPROVED_EXCLUDED]
        assert auto
        assert f"{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket" in auto[0].tools

    def test_losing_all_mcp_is_flagged(self):
        warnings = analyze_tool_grants(
            _allowlist(),
            adapter="claude",
            mcp_tools=[],
            mcp_enabled=True,
            registered_servers=frozenset(),
        )
        assert ToolGrantWarningCode.ALL_MCP_EXCLUDED in _codes(warnings)

    def test_an_allowlist_resolving_to_nothing_is_flagged(self):
        warnings = analyze_tool_grants(
            _allowlist(disallowed_tools=list(CliTool)),
            adapter="claude",
            mcp_tools=[],
            mcp_enabled=False,
            registered_servers=frozenset(),
        )
        assert ToolGrantWarningCode.EMPTY_ALLOWLIST in _codes(warnings)


class TestParsing:
    def test_a_row_written_before_grants_existed_reads_as_the_default(self):
        assert parse_tool_grants("{}").posture is ToolPosture.INHERIT
        assert parse_tool_grants("").posture is ToolPosture.INHERIT

    def test_round_trips_through_json(self):
        grants = _allowlist(allowed_tools=[CliTool.READ], mcp_servers=["github"])
        assert parse_tool_grants(grants.model_dump_json()) == grants
