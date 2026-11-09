"""The gate that refuses report markdown an agent writes into the tree (lg-milestone-that-2488).

Loregarden reads none of these files and the orchestrator sweeps them into an
unrelated ticket's commit. The rule was prompt-only, and loregarden's history
holds 46 report files it never stopped; blobert's holds 36 checkpoint logs added
after its own CLAUDE.md froze that directory.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from loregarden.services.organization_gate_service import OrganizationScope, check_repo

_SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"
_GATE = _SCRIPTS / "md_report_check.py"
_DISPATCHER = _SCRIPTS / "workspace-gates.sh"


def _load_gate():
    spec = importlib.util.spec_from_file_location("md_report_check", _GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_env())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed repository on main, holding one tracked report-shaped file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("# Demo\n")
    (root / "OLD_REPORT.md").write_text("# Old report\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _write(repo: Path, rel: str, text: str = "notes\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_GATE), *args],
        capture_output=True,
        text=True,
        cwd=repo,
        env=_env(),
        check=False,
    )


def _worktree(repo: Path) -> subprocess.CompletedProcess[str]:
    return _run(repo, "--repo", str(repo), "--scope", "worktree")


# ---- refused, naming the tool that takes it ---------------------------------


@pytest.mark.parametrize(
    ("rel", "text", "tool"),
    [
        ("TEST_BREAK_REPORT.md", "x\n", "loregarden_attach_artifact"),
        ("server/tests/TICKET_39_QA_SIGN_OFF.md", "x\n", "loregarden_attach_artifact"),
        ("client/TestDesignSummary.md", "x\n", "loregarden_attach_artifact"),
        ("TEST_BREAK_STAGE_COMPLETION.md", "x\n", "<<<LOREGARDEN_STAGE_REPORT>>>"),
        ("spec-latest.md", "x\n", "loregarden_update_ticket"),
        ("UPGRADE_PLAN.md", "x\n", "loregarden_write_document"),
        ("project_board/CHECKPOINTS.md", "x\n", "loregarden_append_checkpoint"),
        ("LEARNINGS.md", "x\n", "loregarden_append_learning"),
        ("notes.md", "Intro\n\n# Test break findings\n", "loregarden_attach_artifact"),
    ],
)
def test_a_new_report_file_is_refused_naming_its_tool(repo, rel, text, tool):
    _write(repo, rel, text)

    result = _worktree(repo)

    assert result.returncode == 1, result.stdout
    [finding] = [line for line in result.stdout.splitlines() if line.startswith(" - ")]
    assert finding.startswith(f" - {rel}: ")
    assert tool in finding


def test_a_staged_report_is_refused_at_pre_commit(repo):
    _write(repo, "FINDINGS.md")
    _git(repo, "add", "FINDINGS.md")

    result = _run(repo, "FINDINGS.md")

    assert result.returncode == 1, result.stdout
    assert "pre-commit: examined 1 file(s)" in result.stdout


def test_outside_a_repository_every_listed_file_counts_as_new(tmp_path):
    _write(tmp_path, "REPORT.md")

    result = _run(tmp_path, "REPORT.md")

    assert result.returncode == 1, result.stdout
    assert " - REPORT.md: " in result.stdout


def test_a_report_committed_on_the_branch_is_refused(repo):
    _git(repo, "checkout", "-qb", "work")
    _write(repo, "IMPLEMENTATION_SUMMARY.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")

    result = _run(repo, "--repo", str(repo), "--scope", "branch", "--base", "main")

    assert result.returncode == 1, result.stdout
    assert "IMPLEMENTATION_SUMMARY.md" in result.stdout


# ---- allowed --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("docs/qa-report.md", "# QA report\n"),
        ("client/docs/findings.md", "x\n"),
        ("agent_context/skills/x/SKILL.md", "# Summary\n"),
        (".loregarden/handoffs/t-1/run_abc-plan.md", "x\n"),
        (".github/pull_request_template.md", "## Summary\n"),
        ("server/README.md", "# Test report tooling\n"),
        ("client/DESIGN.md", "# Design system\n"),
        ("execution.md", "# Execution Plan\n"),
        ("notes.md", "```\n# Findings\n```\n# Hive sprites\n"),
    ],
)
def test_a_doc_or_a_file_that_is_not_report_shaped_passes(repo, rel, text):
    _write(repo, rel, text)

    result = _worktree(repo)

    assert result.returncode == 0, result.stdout
    assert "examined 1 file(s)" in result.stdout


def test_only_markdown_is_graded(repo):
    _write(repo, "report.py", "a = 1\n")

    result = _worktree(repo)

    assert result.returncode == 0, result.stdout
    assert "examined 0 file(s)" in result.stdout


def test_editing_a_report_already_in_the_tree_is_not_this_gates_to_refuse(repo):
    _write(repo, "OLD_REPORT.md", "# Old report\n\nmore\n")

    result = _worktree(repo)

    assert result.returncode == 0, result.stdout
    assert "examined 1 file(s)" in result.stdout


# ---- waived -----------------------------------------------------------------------


def test_a_waiver_with_a_reason_lets_the_file_through(repo):
    _write(
        repo,
        "fixtures/REPORT.md",
        "<!-- report-ok: golden output the report renderer's tests compare against -->\n",
    )

    assert _worktree(repo).returncode == 0


def test_a_waiver_without_a_real_reason_is_refused(repo):
    _write(repo, "REPORT.md", "<!-- report-ok: needed -->\n")

    result = _worktree(repo)

    assert result.returncode == 1
    assert "REPORT.md: the report-ok: waiver needs a reason" in result.stdout


# ---- where it runs -----------------------------------------------------------------


def test_the_dispatcher_runs_it_on_a_stage_transition(repo):
    _write(repo, "TEST_BREAK_REPORT.md")

    result = subprocess.run(
        ["bash", str(_DISPATCHER), "--repo", str(repo), "--scope", "worktree"],
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )

    assert "md_report_check.py — failed (exit 1)" in result.stdout, result.stdout
    assert "loregarden_attach_artifact" in result.stdout
    assert result.returncode == 1


def test_the_dispatcher_hands_it_only_markdown_at_pre_commit(repo):
    _write(repo, "FINDINGS.md")
    _write(repo, "a.py", "a = 1\n")
    _git(repo, "add", "-A")

    result = subprocess.run(
        ["bash", str(_DISPATCHER), "FINDINGS.md", "a.py"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )

    assert "md_report_check.py — failed (exit 1)" in result.stdout, result.stdout
    assert "pre-commit: examined 1 file(s)" in result.stdout


def test_check_organization_reports_it(repo):
    _write(repo, "TEST_BREAK_REPORT.md")

    [result] = [r for r in check_repo(repo, OrganizationScope.WORKTREE) if r.checker == _GATE.name]

    assert result.ok is False
    assert [f.split(":")[0] for f in result.findings] == ["TEST_BREAK_REPORT.md"]


# ---- reading names and headings ---------------------------------------------------


def test_names_split_on_separators_and_case_with_adjacent_pairs_joined():
    assert "signoff" in gate.words("TICKET_39_QA_SIGN_OFF")
    assert gate.words("TestBreakReport")[:3] == ["test", "break", "report"]


def test_a_heading_needs_an_unambiguous_report_word():
    assert gate.report_shape("notes.md", ["# Spec Exit Gate"]) is None
    assert gate.report_shape("notes.md", ["# Spec findings"]).word == "findings"
