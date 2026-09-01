"""The four environment assertions a run should make before it spends an agent.

Each one cost a real run on the blobert milestone 14 sweep (2026-08-15), and
each was discovered by dispatching an agent, failing a gate, and rerouting a
ticket for rework it could not perform. They are properties of the machine, not
of the ticket.

The membership question is as much the subject as the checks themselves.
`DISPATCH_PREFLIGHT_CHECKS` carries a rule in its own comment — a check belongs
there only when nothing downstream reports it well — and one of these four fails
that test on purpose.
"""

from __future__ import annotations

import pytest
from loregarden.agents.mcp_context import (
    STAGE_REPORT_SECTION_TITLE,
    WORKFLOW_ENFORCEMENT_DOC_REL,
)
from loregarden.models.domain import DoctorCheck, DoctorStatus, Workspace
from loregarden.services.doctor import (
    CHECKS,
    DISPATCH_PREFLIGHT_CHECKS,
    check_gate_commands_resolve,
    check_git_writable,
    check_stage_report_contract,
    check_toolchain_installed,
)
from sqlmodel import Session
from tests.worktree_helpers import make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _write_contract(repo, body: str) -> None:
    """The workflow-enforcement doc as the loader expects to find it: dividers
    splitting the file into alternating title and body chunks."""
    path = repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"intro\n\n{'-' * 30}\n{STAGE_REPORT_SECTION_TITLE}\n{'-' * 30}\n{body}\n",
        encoding="utf-8",
    )


# -- AC2: the stage-report contract -------------------------------------------


def test_a_workspace_with_no_contract_doc_fails(session, workspace, repo):
    """Blobert measured 0 characters against loregarden's 3303, and nothing said
    so — every stage there failed on a report it was never told how to write.

    `make_repo` seeds the doc, because a fixture without one is a workspace that
    could not run; this test removes it to describe the workspace that could not.
    """
    (repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL).unlink()

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert WORKFLOW_ENFORCEMENT_DOC_REL.name in finding.remediation


def test_a_contract_doc_missing_its_section_fails_the_same_way(session, workspace, repo):
    """The second, quieter half: the doc is present, so a human looking for it
    finds it, but the loader matches on the section title and returns "" when it
    is absent. Both routes to empty must fail, or the check only covers the one
    that is easy to spot."""
    path = repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a doc with no such section\n", encoding="utf-8")

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert STAGE_REPORT_SECTION_TITLE in finding.remediation


def test_a_populated_contract_passes_and_reports_its_size(session, workspace, repo):
    _write_contract(repo, "Report your stage like this.")

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert finding.remediation == ""


# -- AC3: toolchains declared but not installed --------------------------------


def test_a_declared_toolchain_that_is_not_installed_fails(session, workspace, repo):
    """The worktree case. A worktree is created without ignored directories, so
    `node_modules` is exactly what it lacks while the checkout beside it has one."""
    (repo / "package.json").write_text("{}", encoding="utf-8")

    finding = check_toolchain_installed(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "node_modules" in finding.finding


def test_a_tree_declaring_no_toolchain_is_not_judged_for_missing_one(session, workspace, repo):
    """The check derives the requirement from the tree rather than assuming
    loregarden's shape: a repo with no `package.json` is not missing one."""
    finding = check_toolchain_installed(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


def test_a_declared_toolchain_that_is_installed_passes(session, workspace, repo):
    (repo / "package.json").write_text("{}", encoding="utf-8")
    (repo / "node_modules").mkdir()

    finding = check_toolchain_installed(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


# -- AC4: the git directory must accept a write --------------------------------


def test_a_writable_git_directory_passes(session, workspace, repo):
    finding = check_git_writable(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


def test_an_unwritable_git_directory_fails(session, workspace, repo):
    """Asked by writing, not by reading a permission bit: the case this exists
    for is a sandbox that denies the write while the mode bits still allow it."""
    git_dir = repo / ".git"
    original = git_dir.stat().st_mode
    git_dir.chmod(0o500)
    try:
        finding = check_git_writable(session, workspace, repo)
    finally:
        git_dir.chmod(original)

    assert finding.status is DoctorStatus.FAIL
    assert "not writable" in finding.finding


def test_the_write_probe_leaves_nothing_behind(session, workspace, repo):
    """A check that littered the git directory would be its own bug report."""
    check_git_writable(session, workspace, repo)

    assert not list((repo / ".git").glob(".loregarden-write-probe"))


# -- AC5: gate commands, and what this check cannot see ------------------------


def test_a_gate_command_that_does_not_resolve_fails(session, workspace, repo, monkeypatch):
    from loregarden.services import doctor
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    profile = OrchestrationProfile(slug="proj")
    profile.gates = GatesConfig(enabled=True, commands=["./scripts/nope.sh check"])
    monkeypatch.setattr(doctor, "resolve_orchestration_profile", lambda _ws: profile)

    finding = check_gate_commands_resolve(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "./scripts/nope.sh" in finding.finding


def test_a_gate_command_present_in_the_tree_resolves(session, workspace, repo, monkeypatch):
    from loregarden.services import doctor
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    script = repo / "scripts" / "gate.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    profile = OrchestrationProfile(slug="proj")
    profile.gates = GatesConfig(enabled=True, commands=["./scripts/gate.sh check"])
    monkeypatch.setattr(doctor, "resolve_orchestration_profile", lambda _ws: profile)

    finding = check_gate_commands_resolve(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


# -- membership, which is the design decision ----------------------------------


def test_the_checks_nothing_else_reports_run_before_dispatch():
    """`DISPATCH_PREFLIGHT_CHECKS` earns members by one test, stated in its own
    comment: nothing downstream reports the failure well."""
    assert DoctorCheck.STAGE_REPORT_CONTRACT in DISPATCH_PREFLIGHT_CHECKS
    assert DoctorCheck.GIT_WRITABLE in DISPATCH_PREFLIGHT_CHECKS


def test_the_toolchain_check_cannot_park_this_very_workspace():
    """It reads loregarden's own root `package.json` as a missing toolchain.

    That manifest is the Tauri desktop host — devDependencies it never installs,
    with the real `node_modules` under `client/`. A manifest does not say whether
    the run needs what it declares, so parking on the difference would stop every
    dispatch in the workspace this control plane runs from. On demand it is still
    worth having: a human reading the finding knows which manifests matter.
    """
    assert DoctorCheck.TOOLCHAIN_INSTALLED not in DISPATCH_PREFLIGHT_CHECKS
    assert DoctorCheck.TOOLCHAIN_INSTALLED in CHECKS


def test_gate_command_resolution_is_deliberately_not_a_dispatch_check():
    """`gate_runner` already catches the exec failure and reports
    `GateOutcome.UNAVAILABLE` with the OS error and the command. Parking would
    replace a precise message with an approval a human has to clear — the same
    reason REPO_HAS_COMMIT is excluded.
    """
    assert DoctorCheck.GATE_COMMANDS_RESOLVE not in DISPATCH_PREFLIGHT_CHECKS
    assert DoctorCheck.REPO_HAS_COMMIT not in DISPATCH_PREFLIGHT_CHECKS


def test_every_check_is_reachable_on_demand(session, workspace, repo):
    """AC6: the checks are usable through the doctor, not only before a dispatch."""
    for check in DoctorCheck:
        assert check in CHECKS, f"{check.value} has no checker registered"
