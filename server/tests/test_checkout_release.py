"""The one path -> row -> receipt-gate decision, as its own seam.

lg-external-harness-1452. `branch_triage_service._release_checkout` was the
only place that turned a filesystem path into a gate answer, and it was
private to a 1150-line operator-facing service — so the checkout self-repair
on the live stage-dispatch path, which needed exactly the same decision, ran
`git worktree remove --force` instead and destroyed uncommitted work.

Moving it to `services/checkout_release.py` is what gives both callers one
answer. These tests are about that seam's contract, not about branch triage:
the fields three operator surfaces read are unchanged, and a path the
`worktrees` table does not know about is REPORTED rather than performed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    RemovalOutcome,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo


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


@pytest.fixture(name="stray")
def stray_fixture(repo: Path) -> Path:
    """A checkout git has registered and the `worktrees` table has never heard of.

    Measured on live 2026-10-23: 44 git-registered worktrees against 54 ACTIVE
    rows, with 11 registered paths holding no ACTIVE row at all. Eleven trees
    the gate refuses where the self-repair used to force-remove.
    """
    tree = repo.parent / ".worktrees" / "stray"
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", "feature/stray", str(tree))
    (tree / "draft.txt").write_text("nobody has seen this\n")
    return tree


def _receipted(session, workspace, repo: Path, branch: str = "loregarden/lg-1") -> Worktree:
    """A checkout with everything the gate asks for: a row, an owner, a landing."""
    ticket = Ticket(
        external_id="lg-1",
        workspace_id=workspace.id,
        title="Add the thing",
        branch=branch,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code="run-lg-1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    tree = repo.parent / ".worktrees" / "lg-1"
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(tree))
    (tree / "work.txt").write_text("real work\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "work")

    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=str(tree),
        branch=branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    grant_receipt(session, ticket, repo, branch=branch)
    return row


def test_a_path_with_no_active_row_is_refused_with_no_receipt(session, workspace, repo, stray):
    """AC2. The premise is a *record*, so with no record there is nothing to perform.

    There is no owner to resolve a receipt for, no claim to read and no row to
    write the refusal on. Removing it anyway is the defect this ticket closes.
    """
    from loregarden.services.checkout_release import release_checkout

    report = release_checkout(session, repo, str(stray))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert report.path == str(stray)
    assert report.detail, "a refusal that says nothing cannot be acted on"
    assert stray.is_dir()
    assert (stray / "draft.txt").read_text() == "nobody has seen this\n"
    assert session.exec(select(Worktree)).all() == [], "a refusal writes no row"


def test_a_receipted_checkout_is_released_and_its_row_settled(session, workspace, repo):
    """The other side. A gate that refuses everything is not a gate."""
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    report = release_checkout(session, repo, str(tree))

    assert report.outcome is RemovalOutcome.DELETED
    assert report.reason == ""
    assert not tree.exists()
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value


def test_uncommitted_work_is_refused_even_with_a_receipt_on_record(session, workspace, repo):
    """The premise the self-repair never asked: is there work in there?"""
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    (tree / "in-flight.txt").write_text("an agent was mid-stage here\n")

    report = release_checkout(session, repo, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert tree.is_dir()
    assert (tree / "in-flight.txt").read_text() == "an agent was mid-stage here\n"


def test_branch_triage_and_the_self_repair_read_one_definition(session, workspace, repo, stray):
    """A move, not a redesign.

    `branch_triage_service` imports the names back and keeps calling them by
    the same name, because three operator surfaces read `WorktreeRemoval`'s
    fields and `as_payload`. Two modules holding two spellings of "no
    worktrees row" is how the next drift starts.
    """
    from loregarden.services import branch_triage_service, checkout_release

    assert branch_triage_service.WorktreeRemoval is checkout_release.WorktreeRemoval

    through_triage = branch_triage_service.remove_branch_worktree(
        session, workspace, "feature/stray", str(stray)
    )
    through_the_seam = checkout_release.release_checkout(session, repo, str(stray))

    assert through_triage.outcome is through_the_seam.outcome
    assert through_triage.reason == through_the_seam.reason
    assert through_triage.detail == through_the_seam.detail


def test_the_payload_three_surfaces_read_is_unchanged(session, workspace, repo, stray):
    """`as_payload`'s keys are a wire contract; the move may not touch them."""
    from loregarden.services.checkout_release import release_checkout

    payload = release_checkout(session, repo, str(stray)).as_payload("feature/stray")

    assert set(payload) == {
        "branch",
        "removed_path",
        "outcome",
        "reason",
        "detail",
        "parked_path",
    }
    assert payload["outcome"] == RemovalOutcome.REFUSED.value


# ---------------------------------------------------------------------------
# lg-external-harness-1452, test-break. The lookup key, adversarially.
#
# A1 states path equality with no normalization layer, measured: 0 of 184 live
# `worktrees.worktree_path` values differ from their own realpath. A2 states
# that a `locked_at` spelled some other way refuses, "which is the safe
# direction". Both are assumptions written in prose on the ticket, and neither
# had a test. The plausible follow-up change — normalize until a row matches —
# re-opens the hole from the other side, and would pass every test above.
# ---------------------------------------------------------------------------


def test_a_path_spelled_through_a_symlinked_parent_finds_no_row(session, workspace, repo):
    """A2. The row holds one spelling, and another spelling is not that row.

    Refusing is the safe direction: the tree stays, the operator is told, and
    the remedy still frees the branch (R7). Removing it would mean resolving a
    path until it matched *something* and then deleting what it matched.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    link = repo.parent / "linked-worktrees"
    link.symlink_to(repo.parent / ".worktrees")

    report = release_checkout(session, repo, str(link / tree.name))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert tree.is_dir(), "the tree the row does speak for was removed under another name"


def test_a_trailing_separator_is_a_different_path_and_refuses(session, workspace, repo):
    """A1, at its boundary. `str(tree) + "/"` is not `str(tree)`.

    Pinned rather than assumed: a caller that joined a path and left the
    separator on gets a refusal, not somebody else's receipt.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    report = release_checkout(session, repo, str(tree) + "/")

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert tree.is_dir()


def test_a_row_that_is_no_longer_active_speaks_for_nothing(session, workspace, repo):
    """The `state == ACTIVE` half of the lookup, which the design never breaks.

    A settled row is the control plane's record that it is *done* with that
    checkout. Reading it as a receipt would let a tree be released twice — the
    second time on the strength of the first release's own bookkeeping.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    row.state = WorktreeState.CLEANUP
    session.add(row)
    session.commit()

    report = release_checkout(session, repo, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert tree.is_dir()


def test_a_receipt_for_one_tree_does_not_license_another_on_the_same_branch(
    session, workspace, repo, stray
):
    """The key is the path, not the branch.

    git lets only one checkout hold a branch, so the two trees here cannot be
    on one ref — but the `worktrees` row carries its own `branch` column, and
    a row recording `feature/stray` at some other path is a row that exists.
    A lookup that matched on that column would hand the stray tree a receipt
    granted for a different directory, which is the defect with one extra step.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    row.branch = "feature/stray"
    session.add(row)
    session.commit()

    report = release_checkout(session, repo, str(stray))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert stray.is_dir()
    assert (stray / "draft.txt").read_text() == "nobody has seen this\n"
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE.value, "and the other row is untouched"


def test_an_ignored_file_nothing_regenerates_refuses_a_fully_receipted_tree(
    session, workspace, repo
):
    """The premise `git status --porcelain` cannot see.

    An `.env` is ignored, so every shape check calls the tree clean; it is
    also the file somebody keeps in a worktree and nowhere else. The receipt
    says the *branch's* work landed and says nothing about this. A gate that
    stopped asking would pass every other test in this module.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    (tree / ".gitignore").write_text(".env\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "ignore .env")
    (tree / ".env").write_text("TOKEN=keep-me\n")

    report = release_checkout(session, repo, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert ".env" in report.detail, "the operator learns which file is holding the branch"
    assert (tree / ".env").read_text() == "TOKEN=keep-me\n"


def test_asking_twice_about_a_refused_tree_answers_twice_the_same(session, workspace, repo, stray):
    """Determinism. A refusal writes nothing, so it has nothing to drift on."""
    from loregarden.services.checkout_release import release_checkout

    first = release_checkout(session, repo, str(stray))
    second = release_checkout(session, repo, str(stray))

    assert (first.outcome, first.reason, first.detail) == (
        second.outcome,
        second.reason,
        second.detail,
    )
    assert session.exec(select(Worktree)).all() == []
    assert stray.is_dir()
