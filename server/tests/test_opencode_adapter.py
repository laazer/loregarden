"""The OpenCode adapter — invocation shape, MCP-by-environment, and discovery.

OpenCode is the one adapter whose MCP config does not travel in argv, so these
cover the environment channel as carefully as the flags.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from loregarden.agents.cli_adapters import (
    CliInvocation,
    build_triage_invocation,
    invocation_env,
    render_terminal_handoff_command,
    resolve_cli_invocation,
    resolve_terminal_handoff_invocation,
)
from loregarden.models.domain import Workspace
from loregarden.services.cli_settings import (
    OPENCODE_EFFORT_OPTIONS,
    adapter_available,
    resolve_effort_for_adapter,
    resolve_model_for_adapter,
)
from loregarden.services.opencode_discovery import (
    DISCOVERY_TIMEOUT_SECONDS,
    _parse_models_output,
    list_opencode_models,
    opencode_model_options,
    reset_model_cache,
)
from loregarden.services.run_log_stream import format_stream_payload


@pytest.fixture(autouse=True)
def _workspace_picks_the_adapter(monkeypatch):
    """conftest pins LOREGARDEN_CLI_ADAPTER=local session-wide, and the env tier
    outranks everything — clear it so these tests exercise the workspace pin."""
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)


@pytest.fixture(autouse=True)
def _no_memoized_catalog():
    """Discovery memoizes across calls; a leaked entry would answer the next test."""
    reset_model_cache()
    yield
    reset_model_cache()


def _workspace(**kwargs) -> Workspace:
    return Workspace(slug="ws", name="ws", cli_adapter="opencode", **kwargs)


def _stage_invocation(tmp_path: Path, workspace: Workspace) -> CliInvocation:
    return resolve_cli_invocation(
        agent_id="implementer",
        adapter="opencode",
        prompt="do the stage",
        prompt_file=tmp_path / "prompt.md",
        skill_name="implement",
        workspace_root=tmp_path,
        workspace=workspace,
    )


def test_stage_invocation_streams_json_and_feeds_the_prompt_on_stdin(tmp_path):
    invocation = _stage_invocation(tmp_path, _workspace())

    assert invocation.adapter == "opencode"
    assert invocation.argv[1] == "run"
    assert "--format" in invocation.argv
    assert invocation.argv[invocation.argv.index("--format") + 1] == "json"
    assert invocation.argv[invocation.argv.index("--dir") + 1] == str(tmp_path)
    # A stage prompt runs tens of KB; it must not become an argv token.
    assert invocation.stdin_prompt == "do the stage"
    assert "do the stage" not in invocation.argv


def test_model_and_effort_pins_become_model_and_variant(tmp_path):
    workspace = _workspace(opencode_model="opencode/nemotron", opencode_effort="high")

    argv = _stage_invocation(tmp_path, workspace).argv

    assert argv[argv.index("--model") + 1] == "opencode/nemotron"
    assert argv[argv.index("--variant") + 1] == "high"


def test_effort_pin_is_per_adapter(tmp_path):
    workspace = _workspace(claude_effort="xhigh", opencode_effort="max")

    assert resolve_effort_for_adapter("opencode", workspace) == "max"
    assert resolve_effort_for_adapter("claude", workspace) == "xhigh"


def test_unknown_effort_level_is_dropped_rather_than_forwarded():
    # `xhigh` is a Claude rung with no OpenCode counterpart.
    workspace = _workspace(opencode_effort="xhigh")

    assert resolve_effort_for_adapter("opencode", workspace) == ""
    assert "xhigh" not in {opt["id"] for opt in OPENCODE_EFFORT_OPTIONS}


def test_model_resolution_walks_the_shared_precedence_chain():
    workspace = _workspace(opencode_model="ws/model")

    assert resolve_model_for_adapter("opencode", workspace) == "ws/model"
    assert (
        resolve_model_for_adapter("opencode", workspace, ticket_model="ticket/model")
        == "ticket/model"
    )
    with mock.patch.dict(os.environ, {"LOREGARDEN_OPENCODE_MODEL": "env/model"}):
        assert (
            resolve_model_for_adapter("opencode", workspace, ticket_model="ticket/model")
            == "env/model"
        )


def test_mcp_config_rides_in_the_environment_not_argv(tmp_path):
    invocation = _stage_invocation(tmp_path, _workspace())

    # OpenCode has no --mcp-config flag; the config is only reachable via env.
    assert not any(token.startswith("--mcp") for token in invocation.argv)
    config = json.loads(invocation.env["OPENCODE_CONFIG_CONTENT"])
    assert config["mcp"]["loregarden"]["type"] == "remote"
    assert config["mcp"]["loregarden"]["enabled"] is True


def test_stage_runs_are_marked_orchestrated_and_chat_turns_are_not(tmp_path):
    stage = _stage_invocation(tmp_path, _workspace())
    triage = build_triage_invocation(
        agent_id="triage",
        adapter="opencode",
        prompt="what is going on",
        prompt_file=tmp_path / "prompt.md",
        skill_name="",
        workspace_root=tmp_path,
        workspace=_workspace(),
    )

    stage_entry = json.loads(stage.env["OPENCODE_CONFIG_CONTENT"])["mcp"]["loregarden"]
    triage_entry = json.loads(triage.env["OPENCODE_CONFIG_CONTENT"])["mcp"]["loregarden"]

    assert stage_entry["headers"] == {"X-Loregarden-Orchestrated": "1"}
    assert "headers" not in triage_entry


def test_disabling_mcp_injection_clears_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_DISABLE_MCP_CLI", "1")

    invocation = _stage_invocation(tmp_path, _workspace())

    assert invocation.env == {}
    assert invocation_env(invocation) is None


def test_invocation_env_overlays_rather_than_replaces_the_environment(tmp_path):
    invocation = _stage_invocation(tmp_path, _workspace())

    resolved = invocation_env(invocation)

    assert "OPENCODE_CONFIG_CONTENT" in resolved
    # Everything the supervising process exports must survive the overlay, or the
    # agent loses PATH and the CLI cannot spawn its own tools.
    assert resolved["PATH"] == os.environ["PATH"]


def test_permission_bypass_is_opt_in(tmp_path, monkeypatch):
    """`--auto` is opencode's own flag (opencode.ai/docs/cli). These assertions
    named Claude Code's `--dangerously-skip-permissions` until it was noticed
    that opencode has no such flag — so the bypass never took, and the tests
    were green because they agreed with the bug."""
    monkeypatch.delenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", raising=False)
    assert "--auto" not in _stage_invocation(tmp_path, _workspace()).argv

    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    assert "--auto" in _stage_invocation(tmp_path, _workspace()).argv


def test_read_only_triage_never_skips_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")

    invocation = build_triage_invocation(
        agent_id="triage",
        adapter="opencode",
        prompt="just look",
        prompt_file=tmp_path / "prompt.md",
        skill_name="",
        workspace_root=tmp_path,
        workspace=_workspace(),
        read_only=True,
    )

    assert "--auto" not in invocation.argv


def test_terminal_handoff_attaches_the_prompt_file_and_carries_its_env(tmp_path):
    prompt_file = tmp_path / "prompt.md"

    invocation = resolve_terminal_handoff_invocation(
        agent_id="implementer",
        adapter="opencode",
        prompt="do the stage",
        prompt_file=prompt_file,
        skill_name="implement",
        workspace_root=tmp_path,
        workspace=_workspace(),
    )

    # A pasted command has no stdin producer, so the prompt must reach the CLI by
    # path — and the caller only writes that file when use_prompt_file is set.
    assert invocation.use_prompt_file is True
    assert invocation.stdin_prompt is None
    assert invocation.argv[invocation.argv.index("--file") + 1] == str(prompt_file)

    command = render_terminal_handoff_command(invocation)
    assert command.startswith("OPENCODE_CONFIG_CONTENT=")


def test_parse_models_output_keeps_provider_qualified_ids_only():
    raw = "\n".join(
        [
            "opencode/nemotron-3.5-lightning-free",
            "  anthropic/claude-opus-5  ",
            "opencode/nemotron-3.5-lightning-free",  # duplicate
            "not a model id",
            "bare-id-without-provider",
            "",
        ]
    )

    assert _parse_models_output(raw) == [
        "opencode/nemotron-3.5-lightning-free",
        "anthropic/claude-opus-5",
    ]


def test_discovery_returns_nothing_when_the_cli_is_absent():
    with patch("loregarden.services.opencode_discovery.resolve_opencode_binary", return_value=None):
        assert list_opencode_models() == []
        # The picker still offers the "use the profile default" row.
        assert opencode_model_options() == [{"id": "", "label": "Default (OpenCode profile)"}]


def test_discovery_survives_a_failing_cli():
    with (
        patch(
            "loregarden.services.opencode_discovery.resolve_opencode_binary",
            return_value="/bin/opencode",
        ),
        patch(
            "loregarden.services.opencode_discovery.subprocess.run",
            side_effect=OSError("boom"),
        ),
    ):
        assert list_opencode_models() == []


def test_discovery_reads_the_cli_catalog():
    completed = MagicMock(stdout="opencode/a\nopencode/b\n")
    with (
        patch(
            "loregarden.services.opencode_discovery.resolve_opencode_binary",
            return_value="/bin/opencode",
        ),
        patch("loregarden.services.opencode_discovery.subprocess.run", return_value=completed),
    ):
        assert opencode_model_options() == [
            {"id": "", "label": "Default (OpenCode profile)"},
            {"id": "opencode/a", "label": "opencode/a"},
            {"id": "opencode/b", "label": "opencode/b"},
        ]


def test_discovery_waits_long_enough_for_the_cli_to_answer():
    # `opencode models` refreshes every authenticated provider over the network
    # first; it measured ~15s locally, and the old 12s budget expired every time,
    # leaving the picker permanently empty on a perfectly healthy install.
    assert DISCOVERY_TIMEOUT_SECONDS > 15.0


def test_discovery_runs_the_cli_once_and_reuses_the_catalog():
    completed = MagicMock(stdout="opencode/a\n")
    with (
        patch(
            "loregarden.services.opencode_discovery.resolve_opencode_binary",
            return_value="/bin/opencode",
        ),
        patch(
            "loregarden.services.opencode_discovery.subprocess.run", return_value=completed
        ) as run,
    ):
        assert list_opencode_models() == ["opencode/a"]
        assert list_opencode_models() == ["opencode/a"]

    # A ~15s subprocess per runtime-options request would stall the settings modal.
    assert run.call_count == 1


def test_cached_catalog_cannot_be_mutated_by_a_caller():
    completed = MagicMock(stdout="opencode/a\n")
    with (
        patch(
            "loregarden.services.opencode_discovery.resolve_opencode_binary",
            return_value="/bin/opencode",
        ),
        patch("loregarden.services.opencode_discovery.subprocess.run", return_value=completed),
    ):
        first = list_opencode_models()
        first.append("opencode/injected")

        assert list_opencode_models() == ["opencode/a"]


def test_a_failed_probe_is_retried_sooner_than_a_successful_one(monkeypatch):
    monkeypatch.setattr("loregarden.services.opencode_discovery.FAILURE_CACHE_TTL_SECONDS", 0.0)
    with (
        patch(
            "loregarden.services.opencode_discovery.resolve_opencode_binary",
            return_value="/bin/opencode",
        ),
        patch(
            "loregarden.services.opencode_discovery.subprocess.run",
            side_effect=[
                subprocess.TimeoutExpired(cmd="opencode", timeout=1),
                MagicMock(stdout="opencode/a\n"),
            ],
        ),
    ):
        # Authenticating a provider must not take five minutes to show up.
        assert list_opencode_models() == []
        assert list_opencode_models() == ["opencode/a"]


def test_adapter_availability_follows_the_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("LOREGARDEN_OPENCODE_BIN", str(tmp_path / "nope"))
    assert adapter_available("opencode") is False

    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("LOREGARDEN_OPENCODE_BIN", str(binary))
    assert adapter_available("opencode") is True


def test_run_log_reads_opencode_events_not_claude_field_names():
    text_event = {
        "type": "text",
        "sessionID": "ses_1",
        "part": {"type": "text", "text": "the answer"},
    }
    tool_event = {
        "type": "tool_use",
        "sessionID": "ses_1",
        "part": {"type": "tool", "tool": "read"},
    }

    assert format_stream_payload(text_event) == ("OUT", "the answer")
    assert format_stream_payload(tool_event)[0] == "TOOL"
    assert "read" in format_stream_payload(tool_event)[1]


def test_claude_tool_events_still_use_their_own_shape():
    # `tool_use` is a type name both CLIs spend; only OpenCode's carries sessionID.
    claude_event = {"type": "tool_use", "tool_name": "Bash"}

    assert format_stream_payload(claude_event)[1].endswith("Bash")
