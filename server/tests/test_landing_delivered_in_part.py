"""lg-milestone-that-1316 is recorded delivered, in part, by its rescue (#865).

The rescue restored four of the six files 1316's lost landing added; a person
left out the other two. The migration points the delivery record at the rescue,
and the doctor check excuses exactly those two files — out loud.
"""

from __future__ import annotations

import pytest
from loregarden.db.versions.landing_delivered_in_part import m_landing_delivered_in_part
from loregarden.models.domain import DoctorStatus, TicketState
from loregarden.services.doctor_landings import DELIVERED_IN_PART, check_landed_files_reach_base
from sqlmodel import Session
from tests.tree_publish_helpers import make_workspace
from tests.worktree_helpers import git, make_repo, make_tree_ticket

TICKET = "lg-milestone-that-1316"
RESCUE = "6f3c328e7492fad918d38e0ea3da7de270f333ab"
WRONG = "ed8f3851c763d10bbf97659f5c5722240ad0cd6e"
BRANCH = "integration/lg-tree-1"
EXCUSED = sorted(DELIVERED_IN_PART[TICKET].files)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    return make_workspace(session, repo, "delivered-in-part")


# ---- the migration ----


@pytest.mark.parametrize("before", ["", WRONG], ids=["cleared", "false-record"])
def test_the_record_points_at_the_rescue(session, workspace, before):
    ticket = make_tree_ticket(
        session, workspace, TICKET, landed_sha="9b4974188", on_base_sha=before
    )

    m_landing_delivered_in_part(session.connection())
    session.commit()

    session.refresh(ticket)
    assert ticket.on_base_sha == RESCUE


def test_any_other_record_is_left_alone(session, workspace):
    """A delivery someone recorded since is theirs; so is every other ticket."""
    ticket = make_tree_ticket(session, workspace, TICKET, landed_sha="9b4974188", on_base_sha="abc")
    other = make_tree_ticket(session, workspace, "lg-other-1", landed_sha="x", on_base_sha="")

    m_landing_delivered_in_part(session.connection())
    session.commit()

    session.refresh(ticket)
    session.refresh(other)
    assert (ticket.on_base_sha, other.on_base_sha) == ("abc", "")


# ---- the doctor exemption ----


def _dropped_landing(repo, files: list[str]) -> str:
    """A landing adding ``files`` on the tree, which then moved off it."""
    git(repo, "branch", "-f", BRANCH, "main")
    tree = repo.parent / "tmp-landing"
    git(repo, "worktree", "add", "-q", str(tree), BRANCH)
    for name in files:
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("work\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "landing")
    sha = git(tree, "rev-parse", "HEAD").stdout.strip()
    git(repo, "worktree", "remove", "--force", str(tree))
    git(repo, "branch", "-f", BRANCH, "main")
    return sha


def _done(session, workspace, external_id: str, sha: str) -> None:
    ticket = make_tree_ticket(
        session, workspace, external_id, landed_sha=sha, landed_branch=BRANCH, on_base_sha=RESCUE
    )
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()


def test_the_excused_files_pass_and_are_named(session, workspace, repo):
    _done(session, workspace, TICKET, _dropped_landing(repo, EXCUSED))

    finding = check_landed_files_reach_base(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert TICKET in finding.finding
    assert all(path in finding.finding for path in EXCUSED)
    assert "#865" in finding.finding


def test_a_file_the_exemption_does_not_name_still_fails(session, workspace, repo):
    _done(session, workspace, TICKET, _dropped_landing(repo, [*EXCUSED, "client/src/lost.ts"]))

    finding = check_landed_files_reach_base(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "client/src/lost.ts" in finding.finding


def test_the_exemption_belongs_to_its_ticket_alone(session, workspace, repo):
    _done(session, workspace, "lg-someone-else-1", _dropped_landing(repo, EXCUSED))

    finding = check_landed_files_reach_base(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "lg-someone-else-1" in finding.finding
