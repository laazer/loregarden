"""The receipt is a premise of removing a checkout, and forging it does not work.

lg-external-harness-1026. Every destructive act in this tree used to be reached
from an *observation of shape* — the directory exists, the tree looks clean, the
branch looks empty. None of those says the work the checkout held actually
finished; they say what a crash looks like from the outside.

So removing a worktree now has one more premise, consulted before any premise
about the shape of the tree: a recorded receipt that the work landed. The
receipt is the record we already write — `tickets.landed_sha` / `landed_branch`,
written by `land_ticket._record_landing` and `publish_tree` — re-verified
against the repository on every read. That last clause is the whole point: a
receipt the database records but git cannot confirm is not a receipt, and the
three forgeries below are the proof.

Everything here runs against a real git fixture. A receipt test that stubs
`is_ancestor` is a test of the stub.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Workspace,
    Worktree,
)
from loregarden.services import worktree_receipt
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import KeepReason
from loregarden.services.worktree_receipt import Receipt, Unresolved, resolve_receipt
from sqlmodel import Session, select
from tests.worktree_helpers import (
    git,
    grant_receipt,
    land_branch_onto,
    make_repo,
)
from tests.worktree_helpers import (
    make_ticket_on_id_branch as _ticket,
)

FORGED_SHA = "0123456789abcdef0123456789abcdef01234567"


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


def _tree_for(session, workspace, ticket) -> Path:
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return resolve_execution_root(session, run, ticket, workspace)


def _row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


def _commit_in(path: Path, name: str = "work.txt", body: str = "real work\n") -> str:
    (path / name).write_text(body)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"work: {name}")
    return git(path, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# AC1 / AC2 — the shape of the answer
# ---------------------------------------------------------------------------


def test_the_answer_is_a_receipt_or_an_unresolved_and_never_a_bool_or_none(session, workspace):
    """`resolve_receipt` must not be able to say "no" the way a predicate does.

    A bool collapses "the work did not land" with "I could not find out", and
    this gate's whole argument is that those two are different: one is an
    answer, the other is a question nobody answered. `None` is worse still,
    because `if not answer:` reads the same for both.
    """
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)

    unlanded = resolve_receipt(session, _row(session, ticket))
    assert isinstance(unlanded, Unresolved)
    assert unlanded is not False and unlanded is not None
    assert bool(unlanded) is True, "an Unresolved must not be falsy; `if not answer` must not work"

    grant_receipt(session, ticket, Path(workspace.repo_path))
    landed = resolve_receipt(session, _row(session, ticket))
    assert isinstance(landed, Receipt)
    assert landed.sha == ticket.landed_sha


def test_a_landed_ticket_tree_resolves_a_receipt_naming_the_commit_that_landed_it(
    session, workspace
):
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    sha = grant_receipt(session, ticket, Path(workspace.repo_path))

    answer = resolve_receipt(session, _row(session, ticket))

    assert isinstance(answer, Receipt)
    assert answer.sha == sha
    assert answer.branch == "main"


def test_the_owner_arm_is_chosen_by_a_column_read_not_by_the_row_s_python_type(session, workspace):
    """AC2 forbids `isinstance` for the owner dispatch, and the py-organization
    gate forbids it independently. The observable consequence: three rows of the
    *same* class take three different arms, decided by `ticket_id` /
    `chat_session_id` / neither."""
    source = inspect.getsource(worktree_receipt)
    assert "isinstance(" not in source, "the owner arm is a column read, never a type switch"


def test_worktree_receipt_never_imports_land_ticket_s_merge_drivers(session):
    """AC2's import fence.

    `land_ticket` *writes* receipts and merges refs. A module whose whole job is
    to read one must not be able to reach a driver that moves a ref — and must
    not drag the landing machinery into every retirement either.
    """
    source = inspect.getsource(worktree_receipt)
    assert "land_ticket" not in source
    for forbidden in ("merge_tips", "advance_tips", "squash_onto", "merge_without_checkout"):
        assert forbidden not in source, f"{forbidden} moves or writes refs; a reader must not"


# ---------------------------------------------------------------------------
# AC3 — forging the completion does not release ownership
# ---------------------------------------------------------------------------


def test_a_plausible_sha_no_ref_contains_is_refused_and_the_directory_stays(session, workspace):
    """AC3(a). The forgery the surveyed harness's battery runs: the record says
    the work finished, and the repository has never heard of the commit."""
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    ticket.landed_sha = FORGED_SHA
    ticket.landed_branch = "main"
    session.add(ticket)
    session.commit()

    answer = resolve_receipt(session, _row(session, ticket))

    assert isinstance(answer, Unresolved)
    assert answer.reason is KeepReason.NO_RECEIPT
    assert tree.is_dir(), "a forged receipt must not have released the checkout"


def test_a_real_sha_recorded_against_another_target_is_refused(session, workspace):
    """AC3(b). The landing is real, and it is not this tree's landing.

    `landed_branch` has to match the branch this tree would land on, or a
    ticket that landed on some integration branch would read as a receipt for
    a tree whose work is nowhere near `main`.
    """
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    sha = land_branch_onto(repo, ticket.branch, "main")

    ticket.landed_sha = sha
    ticket.landed_branch = "integration/somewhere-else"
    session.add(ticket)
    session.commit()

    answer = resolve_receipt(session, _row(session, ticket))

    assert isinstance(answer, Unresolved)
    assert answer.reason is KeepReason.NO_RECEIPT
    assert tree.is_dir()


def test_a_real_receipt_that_does_not_contain_this_tree_s_live_tip_is_refused(session, workspace):
    """AC3(c). The commit landed; the tree then kept working.

    This is the case the stored-tip design could not see and the live read can:
    the receipt is genuine, it is on the right target, and it does not cover the
    commit sitting in this checkout right now. Removing the checkout would take
    that commit's only reference with it.
    """
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    grant_receipt(session, ticket, repo)

    later = _commit_in(tree, "after-the-landing.txt", "work the receipt never saw\n")

    answer = resolve_receipt(session, _row(session, ticket))

    assert isinstance(answer, Unresolved)
    assert answer.reason is KeepReason.RECEIPT_DOES_NOT_COVER
    assert tree.is_dir()
    assert git(tree, "rev-parse", "HEAD").stdout.strip() == later


def test_the_tip_is_read_live_from_the_tree_and_never_from_a_stored_column(session, workspace):
    """No `claim_tip_sha` (plan D2). Advancing the branch must change the answer
    with nothing written to the row in between — which is only true if the tip
    is read from the repository at the moment the question is asked."""
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    grant_receipt(session, ticket, repo)
    row = _row(session, ticket)
    assert isinstance(resolve_receipt(session, row), Receipt)

    _commit_in(tree, "more.txt", "one commit later\n")

    assert isinstance(resolve_receipt(session, row), Unresolved)
    assert not hasattr(row, "claim_tip_sha"), "the claimed tip is read live, not stored"


# ---------------------------------------------------------------------------
# AC2 — a git call that failed is Unresolved, never a "no"
# ---------------------------------------------------------------------------


def test_a_receipt_probe_whose_repository_cannot_be_read_answers_unresolved(session, workspace):
    """The repository is gone from under us mid-sweep.

    An `is_ancestor` that cannot run answers False, and False here would read as
    "this did not land" — a refusal, which is safe — but the *reason* recorded
    would be a lie about what happened. Uncertainty records itself as
    uncertainty; it never borrows a verdict.
    """
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    grant_receipt(session, ticket, repo)
    row = _row(session, ticket)
    assert isinstance(resolve_receipt(session, row), Receipt)

    # Break the repository the way a half-deleted checkout does: the directory
    # is there and git refuses to answer anything about it.
    (repo / ".git" / "HEAD").unlink()

    answer = resolve_receipt(session, row)

    assert isinstance(answer, Unresolved)
    # Which member a failed *probe* takes is the one thing R1 leaves open — it
    # is unreadability, not absence, and the spec ties UNREADABLE to
    # `directory_state`. Either is defensible; what is not is answering
    # "it did not land", so the test pins the refusal and the said-so rather
    # than a member the spec never chose.
    assert answer.reason in (KeepReason.NO_RECEIPT, KeepReason.UNREADABLE)
    assert answer.detail, "an unresolved probe says what it could not do"


# ---------------------------------------------------------------------------
# AC2 — the fan-out arm (neither ticket nor chat owns the row)
# ---------------------------------------------------------------------------


def test_a_fan_out_loser_the_keeper_already_contains_resolves_a_receipt(session, workspace):
    """The third arm. A losing attempt whose tip the keeper's branch reaches has
    had its work chosen — under another name — so the checkout holds nothing the
    repository will lose."""
    repo = Path(workspace.repo_path)
    keeper, loser = _fanout_pair(session, workspace, repo)

    assert isinstance(resolve_receipt(session, loser), Receipt)
    del keeper


def test_a_fan_out_loser_holding_a_commit_of_its_own_is_refused(session, workspace):
    """The case `_discard_attempts` used to force-delete: a losing attempt that
    went somewhere the keeper never did. Nobody chose it, and nobody said it
    could go."""
    repo = Path(workspace.repo_path)
    _keeper, loser = _fanout_pair(session, workspace, repo, loser_diverges=True)

    answer = resolve_receipt(session, loser)

    assert isinstance(answer, Unresolved)
    assert answer.reason in (KeepReason.NO_RECEIPT, KeepReason.RECEIPT_DOES_NOT_COVER)


def _fanout_pair(session, workspace, repo: Path, *, loser_diverges: bool = False):
    """A settled fan-out group: one promoted keeper, one losing attempt.

    Both worktree rows carry neither `ticket_id` nor `chat_session_id`, which is
    the third owner arm. The keeper's branch contains the loser's tip unless
    `loser_diverges`, which is the only difference between the two tests above.
    """
    from loregarden.models.domain import (
        StageFanoutAttempt,
        StageFanoutAttemptStatus,
        StageFanoutGroup,
    )
    from loregarden.services.worktree_service import WorktreeService

    ticket = _ticket(session, workspace, external_id="LG-FANOUT")
    group = StageFanoutGroup(
        workspace_id=workspace.id, ticket_id=ticket.id, stage_key="implement", attempt_count=2
    )
    session.add(group)
    session.commit()
    session.refresh(group)

    service = WorktreeService(session, repo_path=str(repo))
    attempts = {}
    for index, name in enumerate(("keeper", "loser")):
        run = AgentRun(
            run_code=f"run-{name}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        row = service.create_worktree(
            workspace.id, run.id, parent_branch="main", branch=f"fanout/{name}"
        )
        assert row is not None, f"fixture: {name}'s worktree was not created"
        attempt = StageFanoutAttempt(
            group_id=group.id,
            attempt_index=index,
            attempt_name=name,
            agent_run_id=run.id,
            worktree_id=row.id,
            branch=row.branch,
            status=StageFanoutAttemptStatus.RUNNING,
        )
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        attempts[name] = (attempt, row)

    (keeper_attempt, keeper) = attempts["keeper"]
    (loser_attempt, loser) = attempts["loser"]

    # The shared work: written in the loser's tree, then taken by the keeper.
    # Both branches live in the one repository, so the commit is already in the
    # shared object store and the keeper can fast-forward straight onto it.
    shared = _commit_in(Path(loser.worktree_path), "shared.txt", "both attempts wrote this\n")
    git(Path(keeper.worktree_path), "merge", "-q", "--ff-only", shared)
    if loser_diverges:
        _commit_in(Path(loser.worktree_path), "only-mine.txt", "nobody else has this\n")
    _commit_in(Path(keeper.worktree_path), "keeper.txt", "the chosen attempt\n")

    keeper_attempt.status = StageFanoutAttemptStatus.PROMOTED
    group.winner_attempt_id = keeper_attempt.id
    session.add(keeper_attempt)
    session.add(group)
    session.commit()
    session.refresh(loser)
    del loser_attempt
    return keeper, loser
