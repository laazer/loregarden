"""A pipeline agent's Bash cannot reach this control plane's HTTP API.

lg-milestone-that-1315 was filed by a review lane that, refused
`loregarden_create_ticket`, curled `POST /api/tickets` instead. The hook denies
the command text naming this server's address; other ports stay open.
"""

from __future__ import annotations

import json
import shlex
import subprocess

import pytest
from loregarden.agents.cli_adapters import build_interactive_invocation, stage_run_hook_settings
from loregarden.agents.control_plane_http_hook import (
    HookInput,
    control_plane_pattern,
    denies,
)

BASE = "http://127.0.0.1:8000"


def _bash(command: str) -> HookInput:
    return HookInput.model_validate({"tool_name": "Bash", "tool_input": {"command": command}})


@pytest.mark.parametrize(
    "command",
    [
        "curl -s -X POST http://127.0.0.1:8000/api/tickets -d @body.json",
        "curl localhost:8000/mcp",
        "wget -qO- 'http://0.0.0.0:8000/api/tickets'",
        "python -c \"import httpx; httpx.post('http://[::1]:8000/api/tickets')\"",
        "http POST LOCALHOST:8000/api/tickets title=x",
    ],
)
def test_a_command_naming_the_control_plane_is_denied(command):
    assert denies(_bash(command), control_plane_pattern(BASE))


@pytest.mark.parametrize(
    "command",
    [
        # A sandbox instance or the agent's own dev server, on another port.
        "curl http://127.0.0.1:8001/api/tickets",
        "curl http://localhost:80001/",
        "uv run pytest tests/test_api.py -q",
        "grep -rn '127.0.0.1' server/",
    ],
)
def test_other_addresses_and_commands_are_left_alone(command):
    assert not denies(_bash(command), control_plane_pattern(BASE))


def test_only_bash_is_judged():
    other = HookInput.model_validate(
        {"tool_name": "Read", "tool_input": {"command": "curl 127.0.0.1:8000"}}
    )
    assert not denies(other, control_plane_pattern(BASE))


def test_a_configured_host_is_matched_as_well_as_loopback():
    pattern = control_plane_pattern("http://devbox.local:9100")
    assert denies(_bash("curl devbox.local:9100/api/tickets"), pattern)
    assert denies(_bash("curl 127.0.0.1:9100/api/tickets"), pattern)


def _hook_command(settings_json: str) -> str:
    settings = json.loads(settings_json)
    (entry,) = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    (command,) = entry["hooks"]
    return command["command"]


def test_every_supervised_run_installs_the_hook_and_stage_runs_keep_their_stop_hook():
    assert "Stop" not in json.loads(stage_run_hook_settings(stage_report=False))["hooks"]
    assert "Stop" in json.loads(stage_run_hook_settings(stage_report=True))["hooks"]
    _hook_command(stage_run_hook_settings(stage_report=False))


def test_a_chat_turn_gets_no_hook(tmp_path):
    """Chat surfaces are not orchestrated and may use the API as a person would."""
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hi", encoding="utf-8")
    invocation = build_interactive_invocation(
        adapter="claude", prompt_file=prompt, workspace_root=tmp_path, orchestrated=False
    )
    assert "--settings" not in invocation.argv


def test_the_installed_command_denies_as_claude_code_runs_it():
    command = _hook_command(stage_run_hook_settings(stage_report=False))
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "curl -X POST 127.0.0.1:8000/api/tickets"},
    }
    done = subprocess.run(
        shlex.split(command),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    decision = json.loads(done.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "mcp__loregarden" in decision["permissionDecisionReason"]


def test_an_unreadable_input_does_not_wedge_bash():
    command = _hook_command(stage_run_hook_settings(stage_report=False))
    done = subprocess.run(
        shlex.split(command), input="not json", capture_output=True, text=True, timeout=60
    )
    # Exit 1 is Claude Code's non-blocking hook error: reported, and the tool runs.
    assert done.returncode == 1
    assert done.stdout == ""
