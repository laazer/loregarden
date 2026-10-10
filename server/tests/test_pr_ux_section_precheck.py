"""The PreToolUse hook that runs the PR UX check before `gh pr create`.

Each test builds a real repository with the real check script and a branch
that touches a surface (or not), then feeds the hook the JSON Claude Code sends.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from loregarden.services.git_subprocess import scrubbed_git_env

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / ".claude" / "hooks" / "pr_ux_section_precheck.py"
_spec = importlib.util.spec_from_file_location("pr_ux_section_precheck", _HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

FILLED = """## Summary
x

## User-facing surfaces
- **Question:** which tickets need me right now, and why?
- **Action:** each ticket links to its detail pane
- **Real data:** 138 findings on the sandbox; 53 on live tickets

## Verification
"""
BARE = "## Summary\nx\n"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=scrubbed_git_env())


def _repo(tmp_path: Path, touched: str) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github" / "scripts").mkdir(parents=True)
    shutil.copy(
        _ROOT / ".github" / "scripts" / "pr_ux_section_check.py", repo / ".github" / "scripts"
    )
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["add", "."], repo)
    _git(["commit", "-m", "init"], repo)
    _git(["switch", "-c", "feature"], repo)
    path = repo / touched
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "change"], repo)
    return repo


@pytest.fixture(name="ui_repo")
def ui_repo_fixture(tmp_path: Path) -> Path:
    return _repo(tmp_path, "client/src/pages/MemoryPage.tsx")


def _decide(command: str, cwd: Path):
    return hook.decide({"tool_input": {"command": command}, "cwd": str(cwd)})


def test_a_create_without_the_section_is_denied_with_the_checks_reason(ui_repo):
    decision, reason = _decide(f"gh pr create --title t --body '{BARE}'", ui_repo)
    assert decision == "deny"
    assert "no '## User-facing surfaces' section" in reason
    assert "client/src/pages/MemoryPage.tsx" in reason


def test_a_create_with_the_section_runs(ui_repo):
    (ui_repo / "body.md").write_text(FILLED)
    assert _decide("gh pr create --title t --body-file body.md", ui_repo) is None


def test_a_heredoc_body_is_read(ui_repo):
    command = f"gh pr create --title t --body-file - <<'EOF'\n{BARE}It's fine\nEOF"
    decision, _ = _decide(command, ui_repo)
    assert decision == "deny"
    assert _decide(f"gh pr create --title t --body-file - <<'EOF'\n{FILLED}\nEOF", ui_repo) is None


def test_a_cd_before_gh_decides_which_repository_is_checked(ui_repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    decision, _ = _decide(f"cd {ui_repo} && GH_TOKEN=x gh pr create --body '{BARE}'", elsewhere)
    assert decision == "deny"


def test_a_branch_with_no_surface_needs_no_section(tmp_path):
    repo = _repo(tmp_path, "server/loregarden/x.py")
    assert _decide(f"gh pr create --body '{BARE}'", repo) is None


def test_an_edit_that_sets_a_body_is_checked_and_one_that_does_not_is_left_alone(ui_repo):
    decision, _ = _decide(f"gh pr edit 506 --body '{BARE}'", ui_repo)
    assert decision == "deny"
    assert _decide("gh pr edit 506 --title 'New title'", ui_repo) is None


def test_an_unreadable_body_is_not_blocked_but_the_agent_is_told(ui_repo):
    for command in (
        "gh pr create --fill",
        'gh pr create --body "$(cat notes.md)"',
        "gh pr create --body-file missing.md",
    ):
        decision, message = _decide(command, ui_repo)
        assert decision is None, command
        assert "precheck did not run" in message


def test_a_repository_without_the_check_is_ignored(tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    assert _decide(f"gh pr create --body '{BARE}'", repo) is None


def test_other_commands_are_ignored(ui_repo):
    assert _decide("gh pr view 506", ui_repo) is None
    assert _decide("git status", ui_repo) is None


def test_the_script_speaks_the_hook_protocol(ui_repo):
    """End to end, under the interpreter hooks actually get."""
    payload = json.dumps(
        {"tool_input": {"command": f"gh pr create --body '{BARE}'"}, "cwd": str(ui_repo)}
    )
    python = shutil.which("python3") or sys.executable
    out = subprocess.run(
        [python, str(_HOOK)], input=payload, capture_output=True, text=True, check=True
    )
    decision = json.loads(out.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
