"""The PreToolUse hook that runs the PR UX check before `gh pr create`.

Each test builds a real repository with the real check script and a branch
that touches a surface (or not), then feeds the hook the JSON Claude Code sends.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.git_subprocess import scrubbed_git_env
from tests.repo_paths import REPO_ROOT
from tests.repo_templates import from_template

_ROOT = REPO_ROOT
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
    return from_template(
        tmp_path / "repo",
        "pr_ux_precheck-" + touched.replace("/", "_"),
        lambda repo: _build_repo(repo, touched),
    )


def _build_repo(repo: Path, touched: str) -> None:
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


@pytest.mark.parametrize(
    "command",
    [
        "B={dir}/body.md; gh pr create --body-file $B",
        'B="{dir}/body.md" && gh pr create --body-file "$B"',
        "B='{dir}/body.md'\ngh pr create --body-file \"${{B}}\"",
        "D={dir}; B=$D/body.md; gh pr create -F $B",
        'D={dir}; gh pr create --body-file "$D/body.md"',
        "export B={dir}/body.md; gh pr create --body-file=$B",
        "gh pr create --body-file $PRECHECK_BODY",
    ],
)
def test_a_body_file_in_a_variable_is_read(ui_repo, tmp_path, command):
    body = tmp_path / "body.md"
    body.write_text(BARE)
    command = command.format(dir=tmp_path)
    with mock.patch.dict(os.environ, {"PRECHECK_BODY": str(body)}):
        decision, reason = _decide(command, ui_repo)
        assert decision == "deny"
        assert "would fail on this description" in reason
        body.write_text(FILLED)
        assert _decide(command, ui_repo) is None


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --body-file $NOT_SET_ANYWHERE",
        "B=$(mktemp); gh pr create --body-file $B",
        "B=`mktemp`; gh pr create --body-file $B",
        "B={dir}/body.md; B={dir}/other.md; gh pr create --body-file $B",
        "D=$(pwd); B=$D/body.md; gh pr create --body-file $B",
        'gh pr create --body-file "$(mktemp)"',
        "PRECHECK_BODY=$(mktemp); gh pr create --body-file $PRECHECK_BODY",
        "gh pr create --body-file $B; B={dir}/body.md",
        "gh pr edit 5 --body-file $NOT_SET_ANYWHERE",
    ],
)
def test_a_body_file_only_the_shell_can_name_is_refused(ui_repo, tmp_path, command):
    """It used to look for a file named `$B`, find none, and let the PR open unchecked."""
    (tmp_path / "body.md").write_text(FILLED)
    environment = {"PRECHECK_BODY": str(tmp_path / "body.md")}
    with mock.patch.dict(os.environ, environment):
        os.environ.pop("B", None)
        decision, reason = _decide(command.format(dir=tmp_path), ui_repo)
    assert decision == "deny"
    assert "literal path" in reason


def test_a_repository_without_the_check_is_not_refused_for_a_variable(tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    assert _decide("gh pr create --body-file $NOT_SET_ANYWHERE", repo) is None


def test_a_repository_without_the_check_is_ignored(tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    assert _decide(f"gh pr create --body '{BARE}'", repo) is None


def test_a_repository_without_the_check_gets_no_note_even_when_the_body_is_unreadable(tmp_path):
    """The plugin runs this hook in every repository; only one carrying the check hears from it."""
    repo = tmp_path / "other"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    assert _decide("gh pr create --fill", repo) is None


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


# ---- the fix coverage check, run by the same hook ---------------------------

FIX_FILLED = """## Summary
x

## Fix coverage
- **Existing:** 0 rows: `select count(*) from tickets where block_kind is null` on live
- **Re-entry:** `every_block_is_settled` reports any block written around settle_block
- **Prevention:** server/tests/test_x.py::test_no_writer_skips_the_guard
- **Learning:** a block written outside settle_block is indistinguishable from a handled one
"""


@pytest.fixture(name="fix_repo")
def fix_repo_fixture(tmp_path: Path) -> Path:
    repo = _repo(tmp_path, "server/tests/test_x.py")
    (repo / "server" / "tests" / "test_x.py").write_text(
        "def test_no_writer_skips_the_guard():\n    pass\n"
    )
    shutil.copy(
        _ROOT / ".github" / "scripts" / "pr_fix_coverage_check.py", repo / ".github" / "scripts"
    )
    registry = repo / "server" / "loregarden" / "services" / "invariant_registry.py"
    registry.parent.mkdir(parents=True)
    registry.write_text('X = Invariant(name="every_block_is_settled")\n')
    _git(["add", "."], repo)
    _git(["commit", "-m", "fix check"], repo)
    return repo


def test_a_fix_titled_create_without_fix_coverage_is_denied(fix_repo):
    decision, reason = _decide(f"gh pr create --title 'fix(x): y' --body '{BARE}'", fix_repo)
    assert decision == "deny"
    assert "'PR fix coverage'" in reason
    assert "no '## Fix coverage' section" in reason


def test_a_fix_titled_create_with_fix_coverage_runs(fix_repo):
    (fix_repo / "body.md").write_text(FIX_FILLED)
    assert _decide("gh pr create --title 'fix(x): y' --body-file body.md", fix_repo) is None


def test_a_feature_title_needs_no_fix_coverage(fix_repo):
    assert _decide(f"gh pr create --title 'feat: y' --body '{BARE}'", fix_repo) is None
