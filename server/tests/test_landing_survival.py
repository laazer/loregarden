"""A landing on an integration branch survives every move of the branch until the base holds it.

`integration/lg-milestone-that-491` lost lg-milestone-that-1316's landing, the
tree was published without it, and the publish recorded it as delivered
anyway (lg-milestone-that-2624).
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest
from loregarden.db.versions.landing_lost_delivery_cleared import m_landing_lost_delivery_cleared
from loregarden.models.domain import Delivery, Ticket, TicketState
from loregarden.services.base_delivery import delivery_of, record_base_holdings
from loregarden.services.branch_triage_service import delete_branch
from loregarden.services.doctor_landings import DELIVERED_IN_PART, DeliveredInPart
from loregarden.services.git_merge_noco import read_branch_tips
from loregarden.services.integration_branch_moves import mover
from loregarden.services.invariant_registry import LANDINGS_SURVIVE_THEIR_BRANCH
from loregarden.services.land_ticket import land_ticket
from loregarden.services.landing_survival import LandingsWouldBeLost
from loregarden.services.landing_survival_checks import landings_lost_from_their_branch
from loregarden.services.pull_request_merge import _delete_local_branch
from loregarden.services.settled_refs import settled_tips
from loregarden.services.target_branch import _fast_forward_superseded
from sqlmodel import Session
from tests.tree_publish_helpers import (
    clear_process_state,
    landed_tree,
    make_workspace,
    repo_with_remote,
    squash_onto_remote_main,
)
from tests.worktree_helpers import commit_on, delete_on, git, make_tree_ticket

PACKAGE = Path(__file__).resolve().parents[1] / "loregarden"


@pytest.fixture(autouse=True)
def _fresh_process_state():
    clear_process_state()
    yield
    clear_process_state()


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return repo_with_remote(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    return make_workspace(session, repo, "landing-survival")


def _sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def _reset(session, workspace, repo: Path, branch: str) -> bool:
    """The real reset to the base, through the owner, as `resolve_target_branch` runs it."""
    move = mover(session, workspace, writer="test.reset", why="reset to main", repo_root=repo)
    return _fast_forward_superseded(
        repo, read_branch_tips(repo, branch, "main"), branch, "main", move=move
    )


def _land_second(session, workspace, repo, ms: Ticket, branch: str) -> Ticket:
    second = make_tree_ticket(session, workspace, "ms-c2", parent=ms)
    git(repo, "branch", second.branch, branch)
    commit_on(repo, second.branch, "c2.txt", "second\n")
    assert land_ticket(session, second, workspace).ok
    session.refresh(second)
    return second


# --- every writer -------------------------------------------------------------------------


#: Every module that moves or deletes a branch ref, and why it cannot drop an
#: integration branch's landing. A new writer fails the enumeration below until
#: it is listed here with its reason.
KNOWN_REF_WRITERS = {
    "services/integration_branch_moves.py": "the owner: guards, keeps the tip it leaves, records",
    "services/git_merge_noco.py": "never an integration branch: those need the owner's mover",
    "services/branch_triage_service.py": "non-integration branches; integration ones go to the owner",
    "services/branch_takes_target.py": "ticket branches only, never an integration branch",
    "services/root_integration_sync.py": "its own fix-base refs, not the branch",
    "services/merge_queue_pull_heads.py": "the queue's pull refs",
    "services/change_pr.py": "deletes the branch it created a moment before",
    "services/chat_branch_sweep.py": "chat branches",
    "services/stage_fanout_service.py": "parallel attempt branches",
}

_WRITES_A_REF = re.compile(r'"update-ref"|"branch",\s*"-[Dd]"|\["branch",\s*"-[Dd]"')


def test_every_module_that_moves_a_branch_ref_is_accounted_for():
    found = {
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if _WRITES_A_REF.search(path.read_text())
    }
    assert found == set(KNOWN_REF_WRITERS)


def test_no_writer_moves_an_integration_branch_off_a_landing_the_base_lacks(
    session, workspace, repo
):
    ms, child, branch = landed_tree(session, workspace, repo)
    tip = _sha(repo, branch)

    # Deletion, with and without force, and the PR cleanup that calls it. The
    # child's own branch keeps its commits, so git's commit check alone passes.
    for force in (False, True):
        with pytest.raises(LandingsWouldBeLost, match=child.external_id):
            delete_branch(session, workspace, branch, force=force)
    step = _delete_local_branch(session, workspace, branch)
    assert not step.ok and child.external_id in step.detail

    # The reset to the base: refused while the base lacks the tip.
    assert not _reset(session, workspace, repo, branch)

    assert _sha(repo, branch) == tip


def test_a_published_tree_deletes_as_before(session, workspace, repo):
    _ms, _child, branch = landed_tree(session, workspace, repo)
    squash_onto_remote_main(repo)

    assert delete_branch(session, workspace, branch, force=True).deleted


def test_a_landing_after_the_publish_keeps_the_branch(session, workspace, repo):
    ms, child, branch = landed_tree(session, workspace, repo)
    squash_onto_remote_main(repo)
    second = _land_second(session, workspace, repo, ms, branch)

    with pytest.raises(LandingsWouldBeLost) as refused:
        delete_branch(session, workspace, branch, force=True)
    assert second.external_id in str(refused.value)
    assert child.external_id not in str(refused.value)


def test_the_reset_keeps_the_tip_it_leaves(session, workspace, repo):
    _ms, _child, branch = landed_tree(session, workspace, repo)
    tip = _sha(repo, branch)
    squash_onto_remote_main(repo)

    assert _reset(session, workspace, repo, branch)

    assert settled_tips(repo, branch) == [tip]


# --- the record ---------------------------------------------------------------------------


def _done(session: Session, ticket: Ticket) -> Ticket:
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _lose(repo: Path, branch: str, landed_sha: str) -> None:
    """What happened to 491: the branch moved back past a landing, by a writer nothing recorded."""
    git(repo, "update-ref", f"refs/heads/{branch}", f"{landed_sha}^1")


def test_a_landing_the_branch_lost_is_not_recorded_by_its_trees_publish(session, workspace, repo):
    ms, child, branch = landed_tree(session, workspace, repo)
    _lose(repo, branch, child.landed_sha)
    git(repo, "push", "-q", "origin", f"{branch}:main", "--force")  # the tree, published without it

    record_base_holdings(session)

    session.refresh(child)
    assert child.on_base_sha == ""
    assert delivery_of(session, child).state is Delivery.LANDED_NOT_ON_BASE


def test_the_invariant_reports_a_landing_on_neither_its_branch_nor_the_base(
    session, workspace, repo
):
    _ms, child, branch = landed_tree(session, workspace, repo)
    _done(session, child)
    assert landings_lost_from_their_branch(session) == []

    _lose(repo, branch, child.landed_sha)

    [violation] = landings_lost_from_their_branch(session)
    assert violation.subject_id == child.id
    assert LANDINGS_SURVIVE_THEIR_BRANCH.detect is landings_lost_from_their_branch


def test_the_invariant_reports_a_landing_whose_files_its_branch_deleted(session, workspace, repo):
    """8ee19bc68: a merge deleted 1316's files; the branch kept the landing as an ancestor."""
    _ms, child, branch = landed_tree(session, workspace, repo)
    _done(session, child)
    delete_on(repo, branch, "c1.txt")

    [violation] = landings_lost_from_their_branch(session)

    assert violation.subject_id == child.id
    assert "c1.txt" in violation.detail


def test_a_file_a_person_excused_is_not_reported(session, workspace, repo):
    _ms, child, branch = landed_tree(session, workspace, repo)
    _done(session, child)
    delete_on(repo, branch, "c1.txt")
    excused = DeliveredInPart(files=frozenset({"c1.txt"}), decided_in="#test")

    with mock.patch.dict(DELIVERED_IN_PART, {child.external_id: excused}):
        assert landings_lost_from_their_branch(session) == []


def test_a_file_the_base_already_has_may_leave_the_branch(session, workspace, repo):
    _ms, child, branch = landed_tree(session, workspace, repo)
    _done(session, child)
    squash_onto_remote_main(repo)
    delete_on(repo, branch, "c1.txt")

    assert landings_lost_from_their_branch(session) == []


def test_a_landing_the_base_holds_is_not_reported_though_its_branch_moved(session, workspace, repo):
    _ms, child, branch = landed_tree(session, workspace, repo)
    _done(session, child)
    squash_onto_remote_main(repo)
    _lose(repo, branch, child.landed_sha)

    assert landings_lost_from_their_branch(session) == []


def test_a_landing_a_later_sibling_rewrote_is_held_after_the_reset(session, workspace, repo):
    """Asked on its own, the first child's landing conflicts with the base: the
    second rewrote its line. Only the tip the reset left still says it was delivered."""
    ms, first, branch = landed_tree(session, workspace, repo)
    second = make_tree_ticket(session, workspace, "ms-c2", parent=ms)
    git(repo, "branch", second.branch, branch)
    commit_on(repo, second.branch, "c1.txt", "rewritten\n")
    assert land_ticket(session, second, workspace).ok
    commit_on(repo, "main", "c1.txt", "rewritten\n")  # the tree's squash: its net change only
    git(repo, "push", "-q", "origin", "main")
    assert _reset(session, workspace, repo, branch)

    record_base_holdings(session)

    session.refresh(first)
    assert first.on_base_sha


def test_a_landing_lost_before_does_not_hold_the_branch(session, workspace, repo):
    """Deleting a branch that no longer holds a landing loses nothing more; the
    invariant, not every later cleanup, is where the loss is reported."""
    _ms, child, branch = landed_tree(session, workspace, repo)
    _lose(repo, branch, child.landed_sha)

    assert delete_branch(session, workspace, branch, force=True).deleted


# --- the row already in the state ---------------------------------------------------------


def test_the_migration_clears_only_the_false_record(session, workspace):
    false = make_tree_ticket(
        session,
        workspace,
        "lg-milestone-that-1316",
        landed_sha="9b4974188",
        on_base_sha="ed8f3851c763d10bbf97659f5c5722240ad0cd6e",
    )
    other = make_tree_ticket(session, workspace, "lg-other-1", landed_sha="abc", on_base_sha="def")

    m_landing_lost_delivery_cleared(session.connection())
    session.commit()

    session.refresh(false)
    session.refresh(other)
    assert (false.on_base_sha, other.on_base_sha) == ("", "def")
