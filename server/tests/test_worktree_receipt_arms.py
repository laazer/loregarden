"""Adversarial cover for the three owner arms of the receipt, and the third conjunct.

lg-external-harness-1026, AC2/AC3. `tests/test_worktree_receipt.py` pins the
shape of the answer and the three forgeries the ticket names. This module is
the test-break pass over what those leave standing, and each test here was
chosen because a specific mutation of `resolve_receipt` survives the existing
suite:

* **Drop the third conjunct** — `is_ancestor(landed_sha, refs/heads/<target>)`.
  Every existing ticket-arm test still passes, because none of them has a
  landing the target no longer contains. A rewound or force-updated target is
  exactly how a real landing stops being one.
* **Make the chat arm answer `Receipt` unconditionally.** AC2 names the arm and
  nothing exercises it: the existing coverage is one `inspect.getsource` check
  that the dispatch is not an `isinstance`.
* **Compare two empty strings.** A half-written receipt (`landed_sha` with no
  `landed_branch`, or the reverse) is the empty-string-compares-equal bug AC4
  spends its whole docstring on, one column family over.
* **Return `False`/`None` from the chat or fan-out arm when git fails.** AC2's
  "any arm whose git call FAILED answers Unresolved" is currently asserted for
  the ticket arm alone.

Everything runs against a real git fixture, for the reason the sibling module
gives: a receipt test that stubs `is_ancestor` is a test of the stub.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import (
    BaxterChatSession,
    Workspace,
    Worktree,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import KeepReason
from loregarden.services.worktree_receipt import Receipt, Unresolved, resolve_receipt
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import (
    git,
    land_branch_onto,
    make_repo,
    make_run,
)
from tests.worktree_helpers import (
    make_ticket_on_id_branch as _ticket,
)


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
    return resolve_execution_root(
        session,
        make_run(session, workspace, ticket, f"run-{ticket.external_id}"),
        ticket,
        workspace,
    )


def _row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


def _contains(repo: Path, outer: str, inner: str) -> bool:
    """Does `outer` reach `inner`? `merge-base --is-ancestor` exits 1 for "no"."""
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", inner, outer],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _commit_in(path: Path, name: str = "work.txt", body: str = "real work\n") -> str:
    (path / name).write_text(body)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"work: {name}")
    return git(path, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# AC2 / AC3 — the third conjunct: the target has to still contain the landing
# ---------------------------------------------------------------------------


def test_a_landing_the_target_no_longer_contains_is_not_a_receipt(session, workspace):
    """The landing happened, and then `main` was rewound off it.

    This is the one ticket-arm conjunct no existing test can see. After the
    rewind the recorded `landed_sha` is still a real commit, it still names the
    right `landed_branch`, and it still contains this tree's tip — the first
    two conjuncts both hold. What is no longer true is the thing the receipt
    actually claims: that the work is reachable from the branch everyone else
    reads. Deleting the checkout here deletes the only remaining reference to
    work that is no longer anywhere.

    A gate that checks only "the record names a real commit that covers this
    tree" accepts a landing into a branch that was force-updated out from under
    it, which is the surveyed harness's whole point: the record is a claim, and
    the repository is the authority.
    """
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)

    before_landing = git(repo, "rev-parse", "refs/heads/main").stdout.strip()
    sha = land_branch_onto(repo, ticket.branch, "main")
    ticket.landed_sha = sha
    ticket.landed_branch = "main"
    session.add(ticket)
    session.commit()
    assert isinstance(resolve_receipt(session, _row(session, ticket)), Receipt)

    # The force-update. The merge commit survives in the object store (that is
    # what makes this adversarial rather than a repeat of AC3(a)) and `main`
    # does not reach it any more.
    git(repo, "reset", "--hard", "-q", before_landing)
    assert git(repo, "cat-file", "-t", sha).stdout.strip() == "commit", (
        "the landing commit must still exist, or this is AC3(a) again"
    )
    assert not _contains(repo, "refs/heads/main", sha), "main must no longer reach the landing"
    assert _contains(repo, sha, git(repo, "rev-parse", ticket.branch).stdout.strip()), (
        "the first two conjuncts must still hold, so only the third can decide"
    )

    answer = resolve_receipt(session, _row(session, ticket))

    assert isinstance(answer, Unresolved), (
        "a landing the target no longer reaches is not a receipt; "
        "the third conjunct (landed_sha is an ancestor of refs/heads/<target>) is missing"
    )
    assert answer.reason in (KeepReason.NO_RECEIPT, KeepReason.RECEIPT_DOES_NOT_COVER)
    assert tree.is_dir()


@pytest.mark.parametrize(
    ("landed_sha", "landed_branch"),
    [
        ("", ""),
        ("", "main"),
        ("@REAL@", ""),
    ],
    ids=["neither-column", "branch-without-sha", "sha-without-branch"],
)
def test_a_half_written_receipt_is_never_a_receipt(session, workspace, landed_sha, landed_branch):
    """Two empty strings compare equal, and that must not add up to a landing.

    AC4 spends its whole rationale on this shape one column family over: an
    unset column reads back as `""`, so a match written as
    `landed_branch == target` is one unlucky default away from passing on a row
    nobody ever landed. The same trap is here — a ticket whose `landed_branch`
    is unset against a tree whose target resolves to `""`.

    All three rows below are records of a landing that did not finish being
    written. None of them is evidence of anything.
    """
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    real = land_branch_onto(repo, ticket.branch, "main")

    ticket.landed_sha = real if landed_sha == "@REAL@" else landed_sha
    ticket.landed_branch = landed_branch
    session.add(ticket)
    session.commit()

    answer = resolve_receipt(session, _row(session, ticket))

    assert isinstance(answer, Unresolved), (
        f"landed_sha={ticket.landed_sha!r} landed_branch={ticket.landed_branch!r} "
        "is a half-written record, not a receipt"
    )
    assert answer.reason is KeepReason.NO_RECEIPT
    assert tree.is_dir()


# ---------------------------------------------------------------------------
# AC2 — the chat arm, which nothing exercised
# ---------------------------------------------------------------------------


def _chat_tree(session, workspace, repo: Path) -> tuple[Worktree, Path]:
    """A chat thread with a real checkout holding one commit of its own."""
    chat = BaxterChatSession(workspace_id=workspace.id, title="Rename the pane editor")
    session.add(chat)
    session.commit()
    session.refresh(chat)

    service = WorktreeService(session, str(repo))
    row = service.get_or_create_for_chat_session(
        chat, make_run(session, workspace, None, "chat-1").id
    )
    assert row is not None
    path = Path(row.worktree_path)
    _commit_in(path, "chat-work.txt", "what the thread wrote\n")
    session.refresh(row)
    return row, path


def test_a_chat_tree_whose_branch_has_landed_resolves_a_receipt(session, workspace):
    """The chat arm's positive case, which decides whether the arm exists at all.

    `branch_work_has_landed` is the chat equivalent of the ticket arm's three
    conjuncts: the branch's content is already in base, so the ref — and the
    checkout holding it — is keeping nothing reachable that would otherwise be
    lost.
    """
    repo = Path(workspace.repo_path)
    row, path = _chat_tree(session, workspace, repo)
    land_branch_onto(repo, row.branch, "main")

    answer = resolve_receipt(session, row)

    assert isinstance(answer, Receipt), (
        "a chat thread whose work is already in base has a receipt; the chat arm is missing"
    )
    assert path.is_dir()


def test_a_chat_tree_whose_work_is_nowhere_else_is_refused(session, workspace):
    """The arm's negative case, and the mutation it kills.

    An arm that answers `Receipt` unconditionally passes the test above and
    every other test in this suite, while releasing the checkout of a thread
    whose work exists in exactly one place.
    """
    repo = Path(workspace.repo_path)
    row, path = _chat_tree(session, workspace, repo)

    answer = resolve_receipt(session, row)

    assert isinstance(answer, Unresolved)
    assert answer.reason is KeepReason.NO_RECEIPT
    assert answer.detail
    assert path.is_dir()


# ---------------------------------------------------------------------------
# AC2 — the fan-out arm with nothing to compare against
# ---------------------------------------------------------------------------


def test_a_fan_out_tree_with_no_promoted_keeper_is_unresolved_rather_than_an_error(
    session, workspace
):
    """The third arm asked before anybody won.

    `is_ancestor(this tree's tip, the keeper attempt's tip)` has no second
    operand until an attempt is promoted, and a sweep can reach a fan-out tree
    at any moment — including the moment before the group settles, and
    including a group that was abandoned and never will. The two wrong answers
    are a `Receipt` (nobody chose this work) and a raise (one unsettled group
    ends the sweep for every other tree, the failure AC11 forbids).
    """
    repo = Path(workspace.repo_path)
    service = WorktreeService(session, str(repo))
    run = make_run(session, workspace, None, "fanout-unsettled")
    row = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="fanout/attempt-a",
    )
    assert row is not None
    assert row.ticket_id is None and row.chat_session_id is None
    _commit_in(Path(row.worktree_path), "attempt.txt", "an attempt nobody judged\n")

    answer = resolve_receipt(session, row)

    assert isinstance(answer, Unresolved), (
        "an unsettled fan-out group has chosen nothing, so nothing released its losers"
    )
    assert answer.reason in (KeepReason.NO_RECEIPT, KeepReason.RECEIPT_DOES_NOT_COVER)
    assert Path(row.worktree_path).is_dir()


# ---------------------------------------------------------------------------
# AC2 — "any arm whose git call FAILED answers Unresolved", for every arm
# ---------------------------------------------------------------------------


def _arm_rows(session, workspace, repo: Path) -> dict[str, Worktree]:
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    land_branch_onto(repo, ticket.branch, "main")
    ticket.landed_sha = git(repo, "rev-parse", "refs/heads/main").stdout.strip()
    ticket.landed_branch = "main"
    session.add(ticket)
    session.commit()

    chat_row, _ = _chat_tree(session, workspace, repo)
    land_branch_onto(repo, chat_row.branch, "main")

    service = WorktreeService(session, str(repo))
    run = make_run(session, workspace, None, "fanout-probe")
    neither = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="fanout/probe",
    )
    assert neither is not None

    return {"ticket": _row(session, ticket), "chat": chat_row, "neither": neither}


@pytest.mark.parametrize("arm", ["ticket", "chat", "neither"])
def test_no_arm_turns_a_failed_git_call_into_a_verdict(session, workspace, arm):
    """AC2's last sentence, applied to all three arms rather than one.

    A `git` that cannot run answers `False` through every predicate in this
    tree, and `False` here reads as "this did not land" — a *refusal*, so the
    directory survives either way. What does not survive is the truth: the row
    records a verdict about the work when what happened was that nobody could
    look. `Unresolved` is the only honest answer, and the existing suite pins
    it for the ticket arm alone.
    """
    repo = Path(workspace.repo_path)
    rows = _arm_rows(session, workspace, repo)
    row = rows[arm]
    assert not isinstance(resolve_receipt(session, row), Unresolved), (
        f"the {arm} arm must resolve before the repository is broken, "
        "or this test proves nothing about the failure path"
    )

    # Broken the way a half-deleted checkout breaks it: the directory is there
    # and git refuses to answer anything about it. Not a patched predicate —
    # a stubbed `is_ancestor` would only prove the stub returns what it was told.
    (repo / ".git" / "HEAD").unlink()

    answer = resolve_receipt(session, row)

    assert isinstance(answer, Unresolved), f"the {arm} arm answered a verdict it could not reach"
    assert answer.detail, "an unresolved probe says what it could not do"
    assert Path(row.worktree_path).is_dir()
