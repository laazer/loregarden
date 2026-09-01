"""Environment checks, against real repositories rather than mocked git.

Each check exists because the trap it names cost a real run, so each one is
exercised by reproducing that trap in a throwaway repo — a mocked `core.bare`
would prove only that the mock works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    DoctorCheck,
    DoctorStatus,
    PortabilityState,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services import doctor
from loregarden.services.doctor import (
    CHECKS,
    DISPATCH_PREFLIGHT_CHECKS,
    check_backend_reload_sentinel,
    check_git_core_bare,
    check_git_env_leak,
    check_repo_has_commit,
    portability_state,
    preflight_run,
    preflight_summary,
    run_checks,
)
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo


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


# -- individual checks --------------------------------------------------------


def test_core_bare_on_a_working_checkout_fails(session, workspace, repo):
    """The trap: every work-tree operation afterwards dies with an exit-128
    checkout error naming a path that is perfectly fine."""
    git(repo, "config", "--local", "core.bare", "true")

    finding = check_git_core_bare(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "core.bare false" in finding.remediation


def test_a_normal_checkout_passes_the_bare_check(session, workspace, repo):
    finding = check_git_core_bare(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert finding.remediation == ""


def test_a_leaked_git_dir_fails_and_names_the_variables(session, workspace, repo, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))

    finding = check_git_env_leak(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "GIT_DIR" in finding.finding
    assert "GIT_DIR" in finding.remediation


def test_a_clean_environment_passes_the_leak_check(session, workspace, repo, monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)

    assert check_git_env_leak(session, workspace, repo).status is DoctorStatus.PASS


def test_a_repo_with_no_commit_fails(session, workspace, tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(fresh, "init", "-q", "-b", "main")

    finding = check_repo_has_commit(session, workspace, fresh)

    assert finding.status is DoctorStatus.FAIL


def test_a_directory_that_is_not_a_repo_fails_the_commit_check(session, workspace, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    assert check_repo_has_commit(session, workspace, plain).status is DoctorStatus.FAIL


def test_a_workspace_without_a_server_tree_skips_the_sentinel(session, workspace, repo):
    """The sentinel is a loregarden dev-loop convention. A workspace without one
    is not broken, and the check must not invent a finding for it."""
    finding = check_backend_reload_sentinel(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert "not applicable" in finding.finding


def test_a_source_newer_than_the_sentinel_warns(session, workspace, repo):
    server = repo / "server"
    server.mkdir()
    (server / ".self-improve-restart").touch()
    source = server / "late.py"
    source.write_text("x = 1\n", encoding="utf-8")
    os.utime(source, (10**9, 10**9 + 500))
    os.utime(server / ".self-improve-restart", (10**9, 10**9))

    finding = check_backend_reload_sentinel(session, workspace, repo)

    # WARN, not FAIL: it only bites when a dev server is up, and that is not this
    # code's business to know.
    assert finding.status is DoctorStatus.WARN
    assert "late.py" in finding.finding


def test_a_sentinel_newer_than_every_source_passes(session, workspace, repo):
    server = repo / "server"
    server.mkdir()
    (server / "early.py").write_text("x = 1\n", encoding="utf-8")
    os.utime(server / "early.py", (10**9, 10**9))
    sentinel = server / ".self-improve-restart"
    sentinel.touch()
    os.utime(sentinel, (10**9 + 500, 10**9 + 500))

    assert check_backend_reload_sentinel(session, workspace, repo).status is DoctorStatus.PASS


# -- portability --------------------------------------------------------------


def test_a_repo_with_no_upstream_is_local_only(repo):
    assert portability_state(repo) is PortabilityState.LOCAL_ONLY


def test_a_branch_level_with_its_upstream_is_remote_ready(repo, tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")

    assert portability_state(repo) is PortabilityState.REMOTE_READY


def test_an_unpushed_commit_is_push_required(repo, tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "local only")

    assert portability_state(repo) is PortabilityState.PUSH_REQUIRED


def test_a_rewritten_history_is_remote_diverged(repo, tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    (repo / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "theirs")
    git(repo, "push", "-q", "-u", "origin", "main")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    (repo / "ours.txt").write_text("ours\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ours")

    assert portability_state(repo) is PortabilityState.REMOTE_DIVERGED


def test_portability_never_fetches(repo, tmp_path):
    """A diagnostic that touches the network answers a different question each
    time it runs. Point the remote at nothing and the check must still answer."""
    git(repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))

    assert portability_state(repo) is PortabilityState.LOCAL_ONLY


# -- the registry -------------------------------------------------------------


def test_every_check_id_has_an_implementation():
    assert set(CHECKS) == set(DoctorCheck)


def test_the_preflight_subset_is_small_and_all_registered():
    """A doctor that adds a second to every dispatch gets turned off."""
    assert set(DISPATCH_PREFLIGHT_CHECKS) <= set(CHECKS)
    assert len(DISPATCH_PREFLIGHT_CHECKS) < len(CHECKS)


def test_running_a_subset_runs_only_that_subset(session, workspace, repo):
    findings = run_checks(session, workspace, repo, checks=DISPATCH_PREFLIGHT_CHECKS)

    assert [f.check for f in findings] == list(DISPATCH_PREFLIGHT_CHECKS)


def test_a_check_that_raises_becomes_one_failure_and_the_rest_still_run(
    session, workspace, repo, monkeypatch
):
    """Reporting six results and one broken check beats reporting a traceback."""

    def explode(session, workspace, repo_root):
        raise RuntimeError("boom")

    monkeypatch.setitem(CHECKS, DoctorCheck.GIT_CORE_BARE, explode)

    findings = run_checks(session, workspace, repo)

    assert len(findings) == len(CHECKS)
    broken = next(f for f in findings if f.check is DoctorCheck.GIT_CORE_BARE)
    assert broken.status is DoctorStatus.FAIL
    assert "RuntimeError" in broken.finding


def test_checks_do_not_write_to_the_repository(session, workspace, repo):
    """A diagnostic that changes what it is diagnosing is not one."""
    before = sorted(p.relative_to(repo) for p in repo.rglob("*") if ".git" not in p.parts)

    run_checks(session, workspace, repo)

    after = sorted(p.relative_to(repo) for p in repo.rglob("*") if ".git" not in p.parts)
    assert before == after
    assert git(repo, "status", "--porcelain").stdout == ""


def test_the_one_check_that_writes_leaves_the_git_directory_as_it_found_it(
    session, workspace, repo
):
    """The exception, asserted rather than excluded.

    `check_git_writable` has to write: `os.access` reports the mode bits, and the
    failure it exists to catch is a sandbox that denies the write while the bits
    still allow it. So the invariant it must hold is not "never writes" but
    "leaves nothing behind" — and the test above cannot say so, because it
    excludes `.git` from its comparison and `git status` never reports anything
    inside it. Asserted here directly, or the probe could leak forever unseen.
    """
    git_dir = repo / ".git"
    before = sorted(p.relative_to(git_dir) for p in git_dir.rglob("*"))

    run_checks(session, workspace, repo)

    after = sorted(p.relative_to(git_dir) for p in git_dir.rglob("*"))
    assert after == before
    assert not list(git_dir.glob(".loregarden-write-probe*"))


def test_no_finding_leaks_a_credential_value(session, workspace, repo, monkeypatch):
    """Presence is the answer; the value is never read back out."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-secret-do-not-print")

    findings = run_checks(session, workspace, repo)

    assert all("sk-secret-do-not-print" not in f.model_dump_json() for f in findings)


def test_an_empty_database_warns_rather_than_passing(session, workspace, repo, monkeypatch):
    """The worktree trap: ticket queries answer a silent zero, which reads as
    "no such ticket" rather than "wrong database"."""
    monkeypatch.setattr(doctor, "resolved_database_path", lambda: Path(__file__))

    finding = doctor.check_db_resolution(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert "LOREGARDEN_REPO_ROOT" in finding.remediation


def test_a_populated_database_passes(session, workspace, repo, monkeypatch):
    monkeypatch.setattr(doctor, "resolved_database_path", lambda: Path(__file__))
    session.add(Ticket(external_id="t1", workspace_id=workspace.id, title="demo"))
    session.commit()

    assert doctor.check_db_resolution(session, workspace, repo).status is DoctorStatus.PASS


# -- the preflight ------------------------------------------------------------


def _run(session, workspace, ticket) -> AgentRun:
    run = AgentRun(
        run_code="r-preflight",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key=ticket.workflow_stage_key,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_healthy_preflight_records_no_failures(session, workspace, repo, monkeypatch):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    ticket = Ticket(external_id="t1", workspace_id=workspace.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = _run(session, workspace, ticket)

    findings = preflight_run(session, run, workspace, repo)

    assert all(f.status is not DoctorStatus.FAIL for f in findings)
    session.refresh(run)
    assert json.loads(run.start_preflight_failures_json) == []


def test_the_preflight_records_which_checks_failed(session, workspace, repo):
    git(repo, "config", "--local", "core.bare", "true")
    ticket = Ticket(external_id="t1", workspace_id=workspace.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = _run(session, workspace, ticket)

    preflight_run(session, run, workspace, repo)

    session.refresh(run)
    assert json.loads(run.start_preflight_failures_json) == [DoctorCheck.GIT_CORE_BARE.value]


def test_the_preflight_runs_only_the_fast_subset(session, workspace, repo, monkeypatch):
    """A doctor that adds a second to every dispatch gets turned off. The
    informational checks — portability, the reload sentinel — must not run here."""
    ran: list[DoctorCheck] = []
    for check_id, original in list(CHECKS.items()):

        def spy(session, workspace, repo_root, _check=check_id, _original=original):
            ran.append(_check)
            return _original(session, workspace, repo_root)

        monkeypatch.setitem(CHECKS, check_id, spy)

    ticket = Ticket(external_id="t1", workspace_id=workspace.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    preflight_run(session, _run(session, workspace, ticket), workspace, repo)

    assert ran == list(DISPATCH_PREFLIGHT_CHECKS)
    assert DoctorCheck.GIT_PORTABILITY not in ran


def test_the_summary_carries_the_remediation_not_just_the_diagnosis(session, workspace, repo):
    """The remediation is the part that otherwise lives only in someone's memory."""
    git(repo, "config", "--local", "core.bare", "true")
    findings = run_checks(session, workspace, repo, checks=DISPATCH_PREFLIGHT_CHECKS)

    summary = preflight_summary(findings)

    assert "core.bare false" in summary
    # Approving is not a fix, and must not read as one.
    assert "does not fix" in summary
