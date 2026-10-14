"""A stage run's turn may not end until its stage report is out."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import stage_report_stop_hook as hook
from loregarden.agents.cli_adapters import (
    build_interactive_invocation,
    resolve_cli_invocation,
    stage_report_hook_settings,
)

REPORT = (
    "<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.9, "reroute_to_stage": null, "reroute_context": ""}\n'
    "<<<END_STAGE_REPORT>>>"
)

SUITE = "capacity-run.sh server-tests --footprint heavy -- uv run pytest -n 4"


def _assistant(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _tool_result(tool_use_id: str, *, is_error: bool = False, content: str = "ok") -> str:
    block = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "is_error": is_error,
        "content": content,
    }
    return json.dumps({"type": "user", "message": {"content": [block]}})


def _input(last: str = "", *, running: bool = False, session_id: str = "s-1") -> hook.StopHookInput:
    tasks = (
        [{"id": "bb88i40mb", "type": "shell", "status": "running", "command": SUITE}]
        if running
        else []
    )
    return hook.StopHookInput.model_validate(
        {
            "session_id": session_id,
            "last_assistant_message": last,
            "background_tasks": tasks,
        }
    )


@pytest.fixture
def state_dir(tmp_path: Path):
    with mock.patch.object(hook, "_STATE_DIR", tmp_path / "state"):
        yield tmp_path / "state"


# ---- what counts as having reported ---------------------------------------


def test_the_report_in_the_final_message_counts():
    # The final message is not yet in the transcript file when Stop fires; it
    # arrives only as `last_assistant_message`.
    assert hook.has_reported(_input(f"Done.\n{REPORT}"), []) is True


def test_a_report_in_an_earlier_assistant_message_counts():
    lines = [_assistant({"type": "text", "text": REPORT})]
    assert hook.has_reported(_input("Handoff written."), lines) is True


def test_the_contracts_example_outside_the_agents_own_text_is_not_a_report():
    # The prompt, a tool's output and a stop-hook reason can all quote the block.
    prompt = {"type": "user", "message": {"content": [{"type": "text", "text": REPORT}]}}
    lines = [
        json.dumps(prompt),
        _assistant({"type": "tool_use", "id": "t1", "name": "Read", "input": {}}),
        _tool_result("t1", content=REPORT),
    ]
    assert hook.has_reported(_input("Reading the contract."), lines) is False


def test_waiting_on_a_background_suite_is_not_a_report():
    last = "Full server suite is running under a capacity lease; I'll report once it lands."
    assert hook.has_reported(_input(last, running=True), []) is False


@pytest.mark.parametrize(
    "call",
    [
        {"name": "mcp__loregarden__loregarden_complete_stage", "input": {}},
        {
            "name": "Bash",
            "input": {"command": "./scripts/loregarden-cli.sh mcp call loregarden_complete_stage"},
        },
    ],
    ids=["mcp", "cli"],
)
def test_a_complete_stage_call_that_succeeded_counts(call):
    lines = [_assistant({"type": "tool_use", "id": "t9", **call}), _tool_result("t9")]
    assert hook.has_reported(_input(""), lines) is True


def test_a_complete_stage_call_that_errored_does_not_count():
    call = {"type": "tool_use", "id": "t9", "name": "mcp__loregarden__loregarden_complete_stage"}
    lines = [_assistant({**call, "input": {}}), _tool_result("t9", is_error=True)]
    assert hook.has_reported(_input(""), lines) is False


def test_an_odd_tool_argument_does_not_drop_the_text_beside_it():
    line = _assistant(
        {"type": "tool_use", "id": "t1", "name": "x", "input": {"command": ["a", 1]}},
        {"type": "text", "text": REPORT},
    )
    assert hook.has_reported(_input("Handoff written."), [line]) is True


def test_transcript_lines_it_does_not_model_are_skipped():
    lines = ["not json", json.dumps({"type": "summary", "summary": "x"}), _assistant()]
    assert hook.has_reported(_input(REPORT), lines) is True


# ---- the decision -----------------------------------------------------------


def test_a_reported_run_may_stop(state_dir):
    assert hook.decide(_input(REPORT, running=True), []) is None


def test_an_unreported_run_is_refused_and_told_what_is_still_running(state_dir):
    reason = hook.decide(_input("Waiting for it.", running=True), [])
    assert reason is not None
    assert "bb88i40mb" in reason
    assert SUITE in reason


def test_refusals_stop_after_the_cap_so_a_run_cannot_loop_forever(state_dir):
    decisions = [hook.decide(_input("nothing"), []) for _ in range(hook.MAX_BLOCKS + 1)]
    assert all(reason is not None for reason in decisions[: hook.MAX_BLOCKS])
    assert decisions[hook.MAX_BLOCKS] is None


def test_the_cap_is_counted_per_session(state_dir):
    for _ in range(hook.MAX_BLOCKS + 1):
        hook.decide(_input("nothing", session_id="a"), [])
    assert hook.decide(_input("nothing", session_id="b"), []) is not None


def test_unreadable_hook_input_lets_the_turn_end(state_dir):
    assert hook.main("{not json") == ""


def test_main_emits_claude_codes_block_decision(state_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_assistant({"type": "text", "text": "working"}) + "\n")
    payload = {"session_id": "s-main", "transcript_path": str(transcript)}
    out = json.loads(hook.main(json.dumps(payload)))
    assert out["decision"] == "block"


def test_a_missing_transcript_still_decides_from_the_final_message(state_dir, tmp_path):
    payload = {
        "session_id": "s-gone",
        "transcript_path": str(tmp_path / "gone.jsonl"),
        "last_assistant_message": REPORT,
    }
    assert hook.main(json.dumps(payload)) == ""


# ---- wiring -------------------------------------------------------------------


def _hook_command(argv: list[str]) -> str:
    settings = json.loads(argv[argv.index("--settings") + 1])
    (entry,) = settings["hooks"]["Stop"]
    (command,) = entry["hooks"]
    return command["command"]


def test_the_installed_command_runs_the_hook_as_claude_code_would(tmp_path):
    # Executed exactly as Claude Code does: a shell command, JSON on stdin.
    session_id = f"test-{uuid.uuid4()}"
    command = _hook_command(["--settings", stage_report_hook_settings()])
    payload = {"session_id": session_id, "last_assistant_message": "waiting for it"}
    try:
        done = subprocess.run(
            shlex.split(command),
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    finally:
        hook._counter_path(session_id).unlink(missing_ok=True)
    assert json.loads(done.stdout)["decision"] == "block"


@pytest.mark.parametrize("bypass", ["1", None], ids=["print", "interactive"])
def test_every_claude_stage_run_carries_the_hook(tmp_path, bypass):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
        os.environ.pop("LOREGARDEN_ALLOW_PERMISSION_BYPASS", None)
        if bypass:
            os.environ["LOREGARDEN_ALLOW_PERMISSION_BYPASS"] = bypass
        inv = resolve_cli_invocation(
            agent_id="backend_implementer",
            adapter="claude",
            prompt="stage task",
            prompt_file=prompt_file,
            skill_name="",
            workspace_root=tmp_path,
        )
    assert "stage_report_stop_hook" in _hook_command(inv.argv)


def test_a_chat_turn_carries_no_stage_report_hook(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("chat", encoding="utf-8")
    inv = build_interactive_invocation(
        adapter="claude",
        prompt_file=prompt_file,
        workspace_root=tmp_path,
        orchestrated=False,
    )
    assert "--settings" not in inv.argv
