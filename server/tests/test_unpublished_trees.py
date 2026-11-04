"""Landed work the base lacks always has a publish, a published tip, or a card behind it.

`integration/lg-run-durability-314` held 679's tests unpublished from
2026-10-12 until an audit found it on 2026-11-03: no publish running, none of
its tip, no card. `landed_work_reaches_the_base` detects that and publishes.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    InvariantReentry,
    MergeQueueAdmission,
)
from loregarden.services import git_automation, merge_queue, tree_publish_scheduler
from loregarden.services.invariant_registry import INVARIANTS, LANDED_WORK_REACHES_THE_BASE
from loregarden.services.landing import land_before_done
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.services.tree_publish_scheduler import publish_card_title
from loregarden.services.unpublished_trees import (
    publish_trees,
    unpublished_trees,
    violations,
)
from sqlmodel import Session, select
from tests.tree_publish_helpers import (
    NO_PUSH_CHAIN,
    clear_process_state,
    fake_gh,
    landed_tree,
    make_workspace,
    repo_with_remote,
    squash_onto_remote_main,
    tree_with_child_branch,
)
from tests.worktree_helpers import commit_on, git


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
    return make_workspace(session, repo, "unpublished-trees")


def _cards(session, root) -> list[Approval]:
    session.expire_all()
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == publish_card_title(root),
            )
        ).all()
    )


def _refuse_publish_pushes(tmp_path) -> None:
    """The remote takes the base but refuses every publish ref, as a pull-only account does."""
    hook = tmp_path / "remote.git" / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\nwhile read old new ref; do\n"
        '  case "$ref" in refs/heads/publish/*) echo "denied" >&2; exit 1;; esac\n'
        "done\n"
    )
    hook.chmod(0o755)


def _conflict_with_base(repo) -> None:
    commit_on(repo, "main", "c1.txt", "the base's own c1\n")
    git(repo, "push", "-q", "origin", "main")


def _crash(*_args, **_kwargs):
    raise RuntimeError("publish blew up")


_QUEUE_REFUSES = merge_queue.Admission(MergeQueueAdmission.NO_PULL_REQUEST, "no pull request")

#: ending -> (arrange the repo, the fake `gh`, (object, attribute, patch kwargs) or None)
ENDINGS = {
    "published": (lambda tmp_path, repo: None, fake_gh(), None),
    "auto_merge refused": (
        lambda tmp_path, repo: None,
        fake_gh(),
        (merge_queue, "enqueue", {"return_value": _QUEUE_REFUSES}),
    ),
    "open_pr refused": (lambda tmp_path, repo: None, fake_gh(failing="create"), None),
    "push refused": (lambda tmp_path, repo: _refuse_publish_pushes(tmp_path), fake_gh(), None),
    "cut conflicts": (lambda tmp_path, repo: _conflict_with_base(repo), fake_gh(), None),
    "publish crashed": (
        lambda tmp_path, repo: None,
        fake_gh(),
        (tree_publish_scheduler, "publish_tree", {"side_effect": _crash}),
    ),
}


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_every_way_a_landing_ends_leaves_its_tree_delivered_or_carded(
    session, workspace, repo, tmp_path, ending
):
    """The guard: each way a landing's publish can end, through `land_before_done`.

    A landing schedules its tree's publish; the publish pushes the tip or files a
    card. None of the endings may leave the tree in the invariant's state.
    """
    arrange, gh, patch = ENDINGS[ending]
    ms, child, _ = tree_with_child_branch(session, workspace, repo)
    arrange(tmp_path, repo)

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(git_automation, "_gh", side_effect=gh))
        if patch is not None:
            target, attribute, kwargs = patch
            stack.enter_context(mock.patch.object(target, attribute, **kwargs))
        assert land_before_done(session, child, mock.Mock(), [], stage_key="done")

    session.expire_all()
    assert unpublished_trees(session) == []
    if ending != "published":
        assert len(_cards(session, ms)) == 1, f"{ending} left no card"


def test_a_landing_nothing_published_is_found_and_published(session, workspace, repo):
    """314's shape: landed, never published, no card."""
    ms, _, branch = landed_tree(session, workspace, repo)

    [found] = violations(session)
    assert found.subject_id == ms.id
    assert branch in found.detail

    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh()):
        assert publish_trees(session, [found]) == 1

    session.expire_all()
    assert violations(session) == []
    session.refresh(ms)
    assert ms.landed_sha == git(repo, "rev-parse", branch).stdout.strip()


def test_a_failed_repair_leaves_a_card_and_stops_reporting(session, workspace, repo, tmp_path):
    ms, _, _ = landed_tree(session, workspace, repo)
    _refuse_publish_pushes(tmp_path)

    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh()):
        assert publish_trees(session, violations(session)) == 1

    assert len(_cards(session, ms)) == 1
    assert violations(session) == [], "the card is in front of a person now"


def test_a_card_closed_around_the_guard_is_found_again(session, workspace, repo, tmp_path):
    """A card closed with the work still unpublished was how 854 and 133 were lost."""
    ms, _, _ = landed_tree(session, workspace, repo)
    _refuse_publish_pushes(tmp_path)
    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh()):
        publish_trees(session, violations(session))
    [card] = _cards(session, ms)
    card.status = ApprovalStatus.IGNORED
    session.add(card)
    session.commit()

    assert [v.subject_id for v in violations(session)] == [ms.id]


def test_a_tip_the_base_holds_by_squash_is_not_a_violation(session, workspace, repo):
    landed_tree(session, workspace, repo)
    squash_onto_remote_main(repo)

    assert violations(session) == []


def test_a_tip_already_published_is_waiting_on_its_pr(session, workspace, repo):
    ms, _, _ = landed_tree(session, workspace, repo)
    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh()):
        tree_publish_scheduler.publish_and_settle(session, ms, workspace)

    assert violations(session) == []


def test_a_publish_in_flight_is_not_a_violation(session, workspace, repo):
    ms, _, _ = landed_tree(session, workspace, repo)
    tree_publish_scheduler._IN_FLIGHT.add(ms.id)

    assert violations(session) == []


def test_a_chain_that_never_pushes_is_not_read(session, workspace, repo):
    landed_tree(session, workspace, repo, chain=NO_PUSH_CHAIN)

    assert violations(session) == []


def test_a_branch_no_root_is_keyed_by_is_not_read(session, workspace, repo):
    landed_tree(session, workspace, repo)
    git(repo, "branch", "integration/ms-signed-rebuild", "integration/ms")
    commit_on(repo, "integration/ms-signed-rebuild", "rebuilt.txt", "x\n")

    assert [v.detail.split(" ")[0] for v in violations(session)] == ["integration/ms"]


def test_the_same_tip_is_not_published_again_within_the_hour(session, workspace, repo):
    """A publish that returns without recording itself must not become a push per tick."""
    landed_tree(session, workspace, repo)
    found = violations(session)

    with mock.patch.object(tree_publish_scheduler, "publish_tree") as publish:
        publish.return_value = tree_publish_scheduler.PublishOutcome(
            ok=True, branch="", skipped=True
        )
        assert publish_trees(session, found) == 1
        assert publish_trees(session, found) == 0
    assert publish.call_count == 1


def test_it_is_registered_as_a_defect_and_scheduled():
    assert LANDED_WORK_REACHES_THE_BASE in INVARIANTS
    assert LANDED_WORK_REACHES_THE_BASE.reentry is InvariantReentry.DEFECT
    assert LANDED_WORK_REACHES_THE_BASE.name in [step.name for step in PERIODIC_STEPS]
