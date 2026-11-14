"""The doctor check that a done ticket's added files reached the base's history.

It replaces a measurement written by hand on 2026-11-14, which found
lg-milestone-that-1316's six files missing from main though the ticket read
delivered. Each case builds the repository shape the check has to tell apart.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import DoctorCheck, DoctorStatus, TicketState
from loregarden.services.doctor import CHECKS, run_checks
from loregarden.services.doctor_landings import check_landed_files_reach_base
from sqlmodel import Session
from tests.tree_publish_helpers import make_workspace
from tests.worktree_helpers import commit_on, git, make_repo, make_tree_ticket

BRANCH = "integration/lg-tree-1"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    return make_workspace(session, repo, "doctor-landings")


def _done(session, workspace, external_id, landed_sha, *, branch=BRANCH, on_base_sha=""):
    ticket = make_tree_ticket(
        session,
        workspace,
        external_id,
        landed_sha=landed_sha,
        landed_branch=branch,
        on_base_sha=on_base_sha,
    )
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    return ticket


def _landing_on_tree(repo, filename):
    git(repo, "branch", "-f", BRANCH, "main")
    return commit_on(repo, BRANCH, filename, "work\n")


def _check(session, workspace, repo):
    return check_landed_files_reach_base(session, workspace, repo)


def test_a_landing_on_the_base_passes(session, workspace, repo):
    sha = commit_on(repo, "main", "src/feature.ts", "work\n")
    _done(session, workspace, "lg-on-main-1", sha, branch="main")

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert "1 delivered landing" in finding.finding


def test_a_landing_its_branch_dropped_fails_and_names_the_files(session, workspace, repo):
    """The 1316 shape: landed on the tree, the tree moved off it, the base never got it."""
    sha = _landing_on_tree(repo, "client/src/lib/toastWrap.ts")
    _done(session, workspace, "lg-dropped-1316", sha)
    git(repo, "branch", "-f", BRANCH, "main")

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "lg-dropped-1316" in finding.finding
    assert "client/src/lib/toastWrap.ts" in finding.finding


def test_a_false_delivery_record_does_not_hide_the_loss(session, workspace, repo):
    """`base_delivery` once recorded 1316 delivered; the check reads git, not the record."""
    main_tip = git(repo, "rev-parse", "main").stdout.strip()
    sha = _landing_on_tree(repo, "src/lost.py")
    _done(session, workspace, "lg-recorded-1", sha, on_base_sha=main_tip)
    git(repo, "branch", "-f", BRANCH, "main")

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "lg-recorded-1" in finding.finding


def test_a_delivery_record_is_checked_even_while_the_tree_still_holds_the_landing(
    session, workspace, repo
):
    """Waiting-to-publish is only a reason to skip when nothing claims the base has it."""
    main_tip = git(repo, "rev-parse", "main").stdout.strip()
    sha = _landing_on_tree(repo, "src/claimed.py")
    _done(session, workspace, "lg-claimed-1", sha, on_base_sha=main_tip)

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "lg-claimed-1" in finding.finding


def test_a_landing_waiting_on_its_tree_is_not_lost(session, workspace, repo):
    sha = _landing_on_tree(repo, "src/pending.py")
    _done(session, workspace, "lg-pending-1", sha)

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert "0 delivered landing" in finding.finding


def test_a_file_the_base_added_then_deleted_was_still_delivered(session, workspace, repo):
    sha = commit_on(repo, "main", "src/retired.py", "work\n")
    _done(session, workspace, "lg-retired-1", sha, branch="main")
    git(repo, "rm", "-q", "src/retired.py")
    git(repo, "commit", "-q", "-m", "retire it")
    # Later base history that adds other files: the whole history is read, not its tip.
    commit_on(repo, "main", "src/later.py", "later\n")

    assert _check(session, workspace, repo).status is DoctorStatus.PASS


def test_a_landing_git_cannot_read_is_named_not_cleared(session, workspace, repo):
    _done(session, workspace, "lg-collected-1", "0" * 40, branch="main")

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert "lg-collected-1" in finding.finding


def test_no_base_to_compare_against_is_a_warning_not_a_crash(session, workspace, repo):
    git(repo, "branch", "-m", "main", "trunk")

    finding = _check(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert "no landing was examined" in finding.finding


def test_it_runs_with_the_other_checks(session, workspace, repo):
    sha = _landing_on_tree(repo, "src/lost.py")
    _done(session, workspace, "lg-dropped-2", sha)
    git(repo, "branch", "-f", BRANCH, "main")

    assert DoctorCheck.LANDED_FILES_REACH_BASE in CHECKS
    (finding,) = run_checks(session, workspace, repo, checks=(DoctorCheck.LANDED_FILES_REACH_BASE,))
    assert finding.status is DoctorStatus.FAIL
