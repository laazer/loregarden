"""A publish card says how far the work got, and closes once the base holds it (1032).

463's #620, 2026-10-14: the publish pushed and opened a mergeable PR, and only
enabling auto-merge was refused. The card said the work was "not on its way to
the base", suggested a push that had already succeeded, and stayed pending after
a person merged the PR by hand, because only another publish could close it and
the tree had nothing left to land.
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, WorkItemType, Workspace
from loregarden.services import git_automation
from loregarden.services.invariant_registry import PUBLISH_CARDS_CLOSE
from loregarden.services.land_ticket import land_ticket
from loregarden.services.publish_tree import PublishOutcome
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.tree_publish_scheduler import (
    close_published_cards,
    publish_and_settle,
    publish_card_title,
    report_publish_failure,
)
from loregarden.services.tree_ux_section import missing_card_title, report_missing_ux_answers
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, make_repo, make_tree_ticket

PR = "https://github.com/example/repo/pull/7"
FULL_CHAIN = {"commit": True, "push": True, "tree_open_pr": True, "tree_auto_merge": True}


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="publish-card", name="publish card", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="tree")
def tree_fixture(session, workspace, repo):
    """A milestone whose one child's work has landed on its integration branch."""
    ms = make_tree_ticket(
        session,
        workspace,
        "ms",
        kind=WorkItemType.MILESTONE,
        git_automation_json=json.dumps(FULL_CHAIN),
    )
    child = make_tree_ticket(session, workspace, "c1", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, "c1.txt", "child work\n")
    assert land_ticket(session, child, workspace).ok
    return ms, target


def _gh(*, merge_refused: bool):
    def fake(args, cwd):
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")
        if args[:2] == ["pr", "merge"] and merge_refused:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="GraphQL: Auto merge is not allowed for this repository"
            )
        return subprocess.CompletedProcess(args, 0, stdout=f"{PR}\n", stderr="")

    return fake


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


def _refused_auto_merge(session, workspace, ms) -> Approval:
    with mock.patch.object(git_automation, "_gh", side_effect=_gh(merge_refused=True)):
        outcome = publish_and_settle(session, ms, workspace)
    assert not outcome.ok and outcome.failed_step == "auto_merge"
    [card] = _cards(session, ms)
    return card


def test_an_auto_merge_refusal_names_the_open_pr_and_how_to_merge_it(session, workspace, tree):
    ms, target = tree

    card = _refused_auto_merge(session, workspace, ms)

    assert PR in card.impact
    assert "not on its way" not in card.impact
    assert card.tool_name == f"gh pr merge {PR} --squash"


@pytest.mark.parametrize("step", ["push", "open_pr", ""])
def test_no_failure_suggests_pushing_the_branch_by_hand(session, workspace, tree, step):
    """A hand push sends the branch's history, which the publish exists not to (958)."""
    ms, target = tree

    card = report_publish_failure(
        session, ms, PublishOutcome(ok=False, branch=target, detail="boom", failed_step=step)
    )

    assert "git push" not in card.tool_name
    assert f"loregarden_land_ticket ticket_id={ms.external_id}" in card.tool_name


def test_a_later_report_replaces_the_cards_action(session, workspace, tree):
    ms, target = tree
    report_publish_failure(
        session, ms, PublishOutcome(ok=False, branch=target, detail="boom", failed_step="push")
    )

    card = _refused_auto_merge(session, workspace, ms)

    assert card.tool_name == f"gh pr merge {PR} --squash"


def test_a_publish_from_any_path_closes_the_card(session, workspace, tree):
    ms, _ = tree
    _refused_auto_merge(session, workspace, ms)

    with mock.patch.object(git_automation, "_gh", side_effect=_gh(merge_refused=False)):
        assert publish_and_settle(session, ms, workspace).ok

    assert _cards(session, ms) == []


def test_the_sweep_closes_the_card_once_the_pr_is_merged_by_hand(session, workspace, tree, repo):
    ms, target = tree
    _refused_auto_merge(session, workspace, ms)
    assert close_published_cards(session) == 0, "the base does not hold the work yet"
    assert len(_cards(session, ms)) == 1

    # A squash merge on GitHub: the branch's content, as one commit on the base.
    commit_on(repo, "main", "c1.txt", "child work\n")
    git(repo, "push", "-q", "origin", "main")

    assert close_published_cards(session) == 1
    assert _cards(session, ms) == []


def test_the_sweep_leaves_a_card_whose_work_is_not_on_the_base(session, workspace, tree, repo):
    ms, _ = tree
    _refused_auto_merge(session, workspace, ms)
    commit_on(repo, "main", "other.txt", "unrelated\n")

    assert close_published_cards(session) == 0
    assert len(_cards(session, ms)) == 1


def _pending_titled(session, root, title) -> list[Approval]:
    session.expire_all()
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == title,
            )
        ).all()
    )


def test_the_sweep_closes_a_ux_answers_card_once_the_pr_is_merged(session, workspace, tree, repo):
    """#661, #692 and #733 merged with their cards pending: only a publish closed one,
    and a merged tree never publishes again."""
    ms, _ = tree
    with mock.patch.object(git_automation, "_gh", side_effect=_gh(merge_refused=False)):
        assert publish_and_settle(session, ms, workspace).ok
    report_missing_ux_answers(session, ms, (), PR)
    assert close_published_cards(session) == 0, "the base does not hold the work yet"
    assert len(_pending_titled(session, ms, missing_card_title(ms))) == 1

    commit_on(repo, "main", "c1.txt", "child work\n")
    git(repo, "push", "-q", "origin", "main")

    assert close_published_cards(session) == 1
    assert _pending_titled(session, ms, missing_card_title(ms)) == []


def test_the_sweep_is_a_reconcile_step():
    assert PUBLISH_CARDS_CLOSE.name in [step.name for step in PERIODIC_STEPS]
