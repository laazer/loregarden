"""An agent cannot start a parallel suite the capacity ledger does not know about.

A verifier ran `pytest -n 6` over the whole suite beside a leased one: the load
average was 200 on 10 cores while the ledger counted 5 of 7 cpus in use.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import unleased_suite_guard
from loregarden.agents.cli_adapters import (
    build_triage_invocation,
    chat_hook_settings,
    resolve_cli_invocation,
    stage_run_hook_settings,
)
from loregarden.agents.executors.tool_auto_approve import denied_cli_tool_message
from loregarden.agents.unleased_suite_guard import denial
from tests.repo_paths import REPO_ROOT

#: What the verifier sent, verbatim.
INCIDENT = "cd server && env -u GIT_DIR -u GIT_WORK_TREE uv run pytest -n 6 -q 2>&1 | tail -30"

REFUSED = [
    INCIDENT,
    "pytest -n 2 tests/test_a.py",
    "pytest -n4",
    "pytest -n=4",
    "pytest -n auto",
    "pytest -n logical",
    "pytest --numprocesses 4",
    "pytest --numprocesses=auto",
    "python -m pytest tests -q -n 3",
    ".venv/bin/pytest -q -n 4 $(cat /tmp/sel.txt)",
    "poetry run pytest tests/unit -q -n auto 2>&1 | tail -50",
    "timeout 570 uv run pytest -q \\\n  -n 4 \\\n  tests/test_a.py",
    "bash -c 'pytest -n 4'",
    "out=$(pytest -n 4)",
    "bash <<'EOF'\npytest -n 4\nEOF",
    "capacity-run.sh x --footprint heavy -- true; pytest -n 4",
    "npx jest",
    "npx jest --maxWorkers=3",
    "npx jest --maxWorkers=50%",
    "npx jest --coverage 2>&1 | tail",
]

ALLOWED = [
    "pytest tests/test_a.py",
    "pytest -n 0 tests",
    "pytest -n 1 tests",
    "pytest -q",  # serial, however long: one process
    'pytest -n "$N" tests',
    "capacity-run.sh suite --footprint heavy -- sh -c 'pytest -n $LOREGARDEN_CAPACITY_WORKERS'",
    "capacity-run.sh suite --footprint heavy -- sh -c 'pytest -n 3'",
    "/r/.lefthook/scripts/capacity-run.sh suite --footprint heavy -- uv run pytest -n 4 tests",
    "./scripts/loregarden-cli.sh capacity run --label x --footprint heavy -- pytest -n 4",
    "npx jest src/a.test.ts --maxWorkers=2",
    "npx jest --changedSince=origin/main --maxWorkers=4",
    "npx jest --changedSince origin/main",
    "npx jest -t name",
    "npx jest -i",
    "npx jest --maxWorkers=1",
    "npx jest --listTests",
    "npm test",  # whatever the workspace's script runs
    "npm install jest",
    "grep -n pytest server-tests.sh",
    "grep pytest -n 4 x",
    "git commit -m 'run pytest -n 4'",
    "echo 'pytest -n auto'",
    "cat <<'EOF'\npytest -n 4\nEOF",
    "tail -n 4 pytest.log",
]


@pytest.mark.parametrize("command", REFUSED)
def test_a_parallel_suite_without_a_lease_is_refused(command):
    assert denial(command)


@pytest.mark.parametrize("command", ALLOWED)
def test_what_is_serial_narrow_leased_or_only_text_is_allowed(command):
    assert denial(command) == ""


def test_the_refusal_says_what_to_run_instead():
    reason = denial(INCIDENT)
    assert "`pytest -n 6`" in reason
    assert "serially" in reason
    wrapper = REPO_ROOT / ".lefthook" / "scripts" / "capacity-run.sh"
    assert f'{wrapper} "<what>" --footprint heavy' in reason
    assert wrapper.is_file()


@pytest.mark.parametrize("command", ["pytest -n 4", "npx jest"])
def test_the_command_it_offers_is_one_it_allows(command):
    offered = denial(command).split("queue for it: ")[1].replace("<what>", "x")
    assert denial(offered) == ""


# --- where it is installed ----------------------------------------------------------


def _answer(argv: list[str], payload: dict) -> str:
    done = subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True, timeout=60, check=True
    )
    return done.stdout


def _installed(settings_json: str) -> list[str]:
    return [
        hook["command"]
        for entry in json.loads(settings_json)["hooks"]["PreToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
        if "unleased_suite_guard" in hook["command"]
    ]


def _system_pythons() -> list[str]:
    """What a hook's `python3` may be: this interpreter, and macOS's own 3.9 where it exists."""
    system = "/usr/bin/python3"
    return [sys.executable, *([system] if Path(system).exists() else [])]


@pytest.mark.parametrize("python", _system_pythons())
def test_it_runs_as_a_script_under_the_hooks_python(python):
    script = str(Path(unleased_suite_guard.__file__))
    denied = _answer([python, script], {"tool_input": {"command": INCIDENT}})
    assert json.loads(denied)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _answer([python, script], {"tool_input": {"command": "pytest tests/a.py"}}) == ""


@pytest.mark.parametrize(
    "settings_json",
    [
        stage_run_hook_settings(stage_report=True),
        stage_run_hook_settings(stage_report=False),
        chat_hook_settings(),
    ],
)
def test_the_command_claude_is_given_denies(settings_json):
    (command,) = _installed(settings_json)
    assert '"deny"' in _answer(shlex.split(command), {"tool_input": {"command": INCIDENT}})


def _cursor_stage(prompt: Path, root: Path) -> list[str]:
    return resolve_cli_invocation(
        agent_id="qa",
        prompt="p",
        prompt_file=prompt,
        skill_name="",
        workspace_root=root,
        run_id="r",
    ).argv


def _cursor_chat(prompt: Path, root: Path) -> list[str]:
    return build_triage_invocation(
        agent_id="triage",
        adapter="cursor",
        prompt="p",
        prompt_file=prompt,
        skill_name="",
        workspace_root=root,
    ).argv


@pytest.mark.parametrize("build", [_cursor_stage, _cursor_chat])
def test_every_cursor_agent_loads_a_plugin_whose_hook_denies(build, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hi", encoding="utf-8")
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "cursor"}):
        argv = build(prompt, tmp_path)
    plugin = argv[argv.index("--plugin-dir") + 1]
    config = json.loads((Path(plugin) / "hooks" / "hooks.json").read_text())
    (hook,) = [
        hook
        for hook in config["hooks"]["beforeShellExecution"]
        if "unleased_suite_guard" in hook["command"]
    ]
    command = hook["command"].replace("${CURSOR_PLUGIN_ROOT}", plugin)
    verdict = json.loads(_answer(["sh", "-c", command], {"command": INCIDENT}))
    assert verdict["permission"] == "deny"
    assert "capacity lease" in verdict["agent_message"]


def test_the_bridge_denies_what_the_guard_refuses():
    assert "capacity lease" in denied_cli_tool_message("Bash", {"command": INCIDENT})
    assert denied_cli_tool_message("Bash", {"command": "pytest tests/test_a.py"}) == ""


def test_a_persons_session_is_not_guarded():
    """Their own CLAUDE.md asks them to queue; nothing refuses them."""
    project = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    assert _installed(json.dumps(project)) == []
