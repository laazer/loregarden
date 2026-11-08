"""The PreToolUse hook that sends `gh pr merge` to loregarden's merge queue instead."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / ".claude" / "hooks" / "pr_merge_queue_precheck.py"
_spec = importlib.util.spec_from_file_location("pr_merge_queue_precheck", _HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _decide(command: str):
    return hook.decide({"tool_input": {"command": command}, "cwd": str(_ROOT)})


@pytest.mark.parametrize(
    ("command", "number"),
    [
        ("gh pr merge 812 --auto --squash", "812"),
        ("gh pr merge --squash --subject 'x 9' 813", "813"),
        ("cd /tmp && gh pr merge https://github.com/laazer/loregarden/pull/814 --auto", "814"),
        ("GH_TOKEN=$(gh auth token -u laazer) gh pr merge --auto", "<the PR's number>"),
    ],
)
def test_a_merge_is_denied_naming_the_enqueue_call(command, number):
    decision, reason = _decide(command)
    assert decision == "deny"
    assert f"loregarden_enqueue_merge pr_number={number} workspace_slug=loregarden" in reason


@pytest.mark.parametrize(
    "command",
    ["gh pr merge 5 --disable-auto", "gh pr view 5", "gh pr create --fill", "git merge main"],
)
def test_cancelling_auto_merge_and_other_commands_pass(command):
    assert _decide(command) is None


def test_the_hook_is_registered_for_bash():
    settings = (_ROOT / ".claude" / "settings.json").read_text()
    assert "pr_merge_queue_precheck.py" in settings
