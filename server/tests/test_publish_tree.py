"""A finished tree's integration branch reaches the base through the publish chain (771)."""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import git_automation, tree_publish_scheduler
from loregarden.services.git_automation_config import enabled_steps, resolve_git_automation
from loregarden.services.land_ticket import land_ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.publish_tree import publish_tree
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session, select
from tests.worktree_helpers import at_terminal_stage, blocking_text, commit_on, git, make_repo


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
    ws = Workspace(slug="publish-tree", name="publish", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session, workspace, external_id, *, parent=None, kind=WorkItemType.TASK, **fields):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=parent.id if parent else None,
        state=TicketState.IN_PROGRESS,
        work_item_type=kind,
        **fields,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD"):
    return git(cwd, "rev-parse", ref).stdout.strip()


def _remote_sha(repo, branch):
    out = git(repo, "ls-remote", "origin", f"refs/heads/{branch}").stdout.split()
    return out[0] if out else ""


def _published(repo, branch) -> str:
    """The commit the remote holds as ``branch``, asserted to be one commit cut
    from the base tip whose tree is the base and the branch merged (958)."""
    head = _remote_sha(repo, branch)
    assert head, f"{branch} was not published"
    git(repo, "fetch", "-q", "origin", f"refs/heads/{branch}")
    parents = git(repo, "rev-list", "--parents", "-n1", head).stdout.split()[1:]
    assert parents == [_sha(repo, "main")], "one commit, on the base tip"
    merged = git(repo, "merge-tree", "--write-tree", "main", branch).stdout.split()[0]
    assert _sha(repo, f"{head}^{{tree}}") == merged, "the base and the branch, merged"
    return head


def _landed_tree(session, workspace, repo, policy=None):
    """A milestone with one child whose work has landed on the integration branch."""
    fields = {"git_automation_json": json.dumps(policy)} if policy else {}
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE, **fields)
    child = _ticket(session, workspace, "c1", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, "c1.txt", "child work\n")
    assert land_ticket(session, child, workspace).ok
    return ms, target


PUSH_ONLY = {"commit": True, "push": True, "open_pr": False}


def test_the_root_pushes_its_integration_branch_and_records_the_tip(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    tip = _sha(repo, target)

    outcome = publish_tree(session, ms, workspace)

    assert outcome.ok and not outcome.skipped
    assert _published(repo, target) != tip, "the cut is published, not the branch's history"
    assert outcome.pr_url == "", "open_pr is off: the PR stays a person's decision"
    session.refresh(ms)
    assert (ms.landed_branch, ms.landed_sha) == (target, tip)


def test_publishing_runs_once_per_tip(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    first = publish_tree(session, ms, workspace)
    assert first.ok and not first.skipped

    with mock.patch.object(git_automation, "_push", wraps=git_automation._push) as push:
        second = publish_tree(session, ms, workspace)

    assert second.ok and second.skipped
    push.assert_not_called()


def test_a_non_root_ticket_publishes_nothing(session, workspace, repo):
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    child = _ticket(session, workspace, "c1", parent=ms)
    outcome = publish_tree(session, child, workspace)
    assert outcome.ok and outcome.skipped and outcome.branch == ""


def test_a_root_finishing_its_workflow_publishes_and_marks_done(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    at_terminal_stage(session, ms)

    OrchestrationService(session).advance_stage(ms)

    session.refresh(ms)
    assert ms.state == TicketState.DONE
    _published(repo, target)


def test_a_push_failure_blocks_the_root_with_the_reason(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    git(repo, "remote", "set-url", "origin", str(repo.parent / "no-such-remote.git"))
    at_terminal_stage(session, ms)

    OrchestrationService(session).advance_stage(ms)

    session.refresh(ms)
    assert ms.state == TicketState.BLOCKED
    recorded = blocking_text(session, ms)
    assert "push failed" in recorded and "no-such-remote" in recorded, "git's own words"
    assert ms.landed_sha == ""


def test_with_open_pr_on_a_single_pr_is_opened_from_the_integration_branch(
    session, workspace, repo
):
    ms, target = _landed_tree(
        session, workspace, repo, {"commit": True, "push": True, "open_pr": True}
    )
    calls: list[list[str]] = []

    def fake_gh(args, cwd):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")
        return subprocess.CompletedProcess(
            args, 0, stdout="https://github.com/example/repo/pull/1\n", stderr=""
        )

    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh):
        outcome = publish_tree(session, ms, workspace)

    assert outcome.ok
    assert outcome.pr_url == "https://github.com/example/repo/pull/1"
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    assert create[create.index("--head") + 1] == target
    assert create[create.index("--base") + 1] == "main"
    assert "ms" in create[create.index("--title") + 1]
    assert not any(c[:2] == ["pr", "merge"] for c in calls), "auto_merge stays off"


def test_a_milestone_under_an_initiative_publishes_its_own_branch_on_completion(
    session, workspace, repo
):
    """An initiative never completes as a tree; its milestones still must."""
    initiative = Ticket(
        external_id="init-pub",
        title="init",
        workspace_id=None,
        work_item_type=WorkItemType.INITIATIVE,
    )
    session.add(initiative)
    session.commit()
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    ms.parent_ticket_id = initiative.id
    session.add(ms)
    session.commit()
    assert target == "integration/ms"
    at_terminal_stage(session, ms)

    OrchestrationService(session).advance_stage(ms)

    session.refresh(ms)
    assert ms.state == TicketState.DONE
    _published(repo, target)
    assert (ms.landed_branch, ms.landed_sha) == (target, _sha(repo, target))


def test_a_ticket_with_no_tree_publishes_its_own_branch_on_completion(session, workspace, repo):
    """Its target is the base, so nothing lands on an integration branch for it."""
    ticket = _ticket(session, workspace, "solo", git_automation_json=json.dumps(PUSH_ONLY))
    git(repo, "branch", ticket.branch, "main")
    tip = commit_on(repo, ticket.branch, "solo.txt", "solo work\n")
    at_terminal_stage(session, ticket)

    OrchestrationService(session).advance_stage(ticket)

    session.refresh(ticket)
    assert ticket.state == TicketState.DONE
    _published(repo, ticket.branch)
    assert (ticket.landed_branch, ticket.landed_sha) == (ticket.branch, tip)


def test_a_ticket_whose_branch_adds_nothing_publishes_nothing(session, workspace, repo):
    """A ticket that committed nothing has no branch worth a push, and a PR would fail."""
    ticket = _ticket(session, workspace, "solo", git_automation_json=json.dumps(PUSH_ONLY))
    git(repo, "branch", ticket.branch, "main")
    at_terminal_stage(session, ticket)

    OrchestrationService(session).advance_stage(ticket)

    session.refresh(ticket)
    assert ticket.state == TicketState.DONE
    assert _remote_sha(repo, ticket.branch) == ""


# --- 958: publish after every landing, not when the root finishes -------------------
#
# Publishing only at completion kept an integration branch alive as long as its
# milestone; 491's had 29 commits unpublished since 09-21 when it last
# conflicted with main.


@pytest.fixture(autouse=True)
def _fresh_scheduler():
    tree_publish_scheduler._IN_FLIGHT.clear()
    tree_publish_scheduler._AGAIN.clear()
    yield
    tree_publish_scheduler._IN_FLIGHT.clear()
    tree_publish_scheduler._AGAIN.clear()


def _child_at_terminal_stage(session, workspace, repo, policy):
    """A milestone (carrying ``policy``) whose child is about to finish and land."""
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    ms.git_automation_json = json.dumps(policy)
    session.add(ms)
    session.commit()
    child = _ticket(session, workspace, "c1", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, "c1.txt", "child work\n")
    at_terminal_stage(session, child)
    return ms, child, target


def _publish_cards(session, root):
    session.expire_all()
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == tree_publish_scheduler.publish_card_title(root),
            )
        ).all()
    )


def test_a_child_landing_publishes_the_tree_before_the_root_finishes(session, workspace, repo):
    ms, child, target = _child_at_terminal_stage(session, workspace, repo, PUSH_ONLY)

    OrchestrationService(session).advance_stage(child)

    session.refresh(child)
    session.refresh(ms)
    assert child.state == TicketState.DONE
    assert ms.state != TicketState.DONE, "the root is still open; only its branch went out"
    tip = _sha(repo, target)
    _published(repo, target)
    assert (ms.landed_branch, ms.landed_sha) == (target, tip)


def test_tree_switches_open_and_auto_merge_the_trees_pr_and_leave_chat_push_only(
    session, workspace, repo
):
    policy = {"commit": True, "push": True, "tree_open_pr": True, "tree_auto_merge": True}
    ms, target = _landed_tree(session, workspace, repo, policy)
    calls: list[list[str]] = []

    def fake_gh(args, cwd):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")
        return subprocess.CompletedProcess(
            args, 0, stdout="https://github.com/example/repo/pull/7\n", stderr=""
        )

    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh):
        outcome = publish_tree(session, ms, workspace)

    assert outcome.ok and outcome.pr_url.endswith("/pull/7")
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    assert create[create.index("--head") + 1] == target
    merge = next(c for c in calls if c[:2] == ["pr", "merge"])
    assert "--auto" in merge and "--squash" in merge
    # The chain a chat turn runs is the plain one: no PR, no merge.
    assert enabled_steps(resolve_git_automation(workspace, ms)) == ["commit", "push"]


# --- what is published: one signed commit cut from the base (958) ---------------------
#
# 2026-10-14: 917's ticket branch, cut before 491's branch moved to main, landed
# and brought back seven unsigned commits from September. Publishing pushed the
# branch's history, refused it for those commits, and held every later landing
# in the tree back with them.


def _has_signature(repo, sha) -> bool:
    return any(
        line.startswith("gpgsig")
        for line in git(repo, "cat-file", "commit", sha).stdout.split("\n\n", 1)[0].splitlines()
    )


@pytest.fixture
def signing(repo, tmp_path):
    """Sign with a real SSH key, as the live repository does (`gpg.format ssh`)."""
    key = tmp_path / "signing_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    git(repo, "config", "gpg.format", "ssh")
    git(repo, "config", "user.signingkey", str(key))
    git(repo, "config", "commit.gpgsign", "true")
    return key


def test_unsigned_history_is_published_as_one_signed_commit(session, workspace, repo, signing):
    git(repo, "config", "commit.gpgsign", "false")
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    landed = git(repo, "rev-list", f"main..{target}").stdout.split()
    assert landed and not any(_has_signature(repo, sha) for sha in landed), "unsigned history"
    git(repo, "config", "commit.gpgsign", "true")

    outcome = publish_tree(session, ms, workspace)

    assert outcome.ok, outcome.detail
    head = _published(repo, target)
    assert _has_signature(repo, head)
    assert git(repo, "rev-list", head, "^main").stdout.split() == [head], (
        "none of the branch's own commits reach the remote"
    )


def test_a_branch_behind_the_base_does_not_revert_what_the_base_gained(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    commit_on(repo, "main", "later.txt", "on main after the branch was cut\n")

    assert publish_tree(session, ms, workspace).ok

    head = _published(repo, target)
    files = git(repo, "ls-tree", "--name-only", head).stdout.split()
    assert {"later.txt", "c1.txt"} <= set(files)


def test_a_branch_that_conflicts_with_the_base_is_not_published(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    commit_on(repo, "main", "c1.txt", "main's own c1\n")

    outcome = publish_tree(session, ms, workspace)

    assert not outcome.ok
    assert "conflicts with main in c1.txt" in outcome.detail
    assert _remote_sha(repo, target) == ""


def test_a_new_landing_republishes_over_the_last_cut(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    assert publish_tree(session, ms, workspace).ok
    first = _published(repo, target)
    commit_on(repo, target, "more.txt", "a second landing\n")

    assert publish_tree(session, ms, workspace).ok

    second = _published(repo, target)
    assert second != first
    assert "more.txt" in git(repo, "ls-tree", "--name-only", second).stdout.split()


def test_a_push_that_races_the_publish_is_not_overwritten(session, workspace, repo):
    """The publish replaces what the remote held when it looked, and nothing newer."""
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    real = git_automation._git

    def racing(args, cwd):
        result = real(args, cwd)
        if args[:2] == ["ls-remote", "origin"]:
            # Someone pushes to the PR branch between the lease's read and the push.
            git(repo, "push", "-q", "origin", f"main:refs/heads/{target}")
        return result

    with mock.patch.object(git_automation, "_git", side_effect=racing):
        outcome = publish_tree(session, ms, workspace)

    assert not outcome.ok and "push failed" in outcome.detail
    assert _remote_sha(repo, target) == _sha(repo, "main"), "the racing push is kept"


def test_a_failed_publish_after_a_landing_files_a_card_and_a_later_one_closes_it(
    session, workspace, repo
):
    ms, child, target = _child_at_terminal_stage(session, workspace, repo, PUSH_ONLY)
    remote = git(repo, "remote", "get-url", "origin").stdout.strip()
    git(repo, "remote", "set-url", "origin", str(repo.parent / "no-such-remote.git"))

    OrchestrationService(session).advance_stage(child)

    session.refresh(child)
    assert child.state == TicketState.DONE, "the landing succeeded; the publish is the tree's"
    [card] = _publish_cards(session, ms)
    assert "push failed" in card.impact and "no-such-remote" in card.impact

    git(repo, "remote", "set-url", "origin", remote)
    tree_publish_scheduler.schedule_tree_publish(ms.id, workspace.id)

    assert _publish_cards(session, ms) == []
    _published(repo, target)


def test_a_landing_during_a_publish_publishes_again_after_it(session, workspace, repo):
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    publishes: list[str] = []
    real = tree_publish_scheduler.publish_tree

    def publish_and_land_again(session_, root, workspace_):
        publishes.append(root.id)
        if len(publishes) == 1:
            # Another child lands while this publish runs.
            tree_publish_scheduler.schedule_tree_publish(root.id, workspace_.id)
        return real(session_, root, workspace_)

    with mock.patch.object(
        tree_publish_scheduler, "publish_tree", side_effect=publish_and_land_again
    ):
        tree_publish_scheduler.schedule_tree_publish(ms.id, workspace.id)

    assert publishes == [ms.id, ms.id]
    assert tree_publish_scheduler._IN_FLIGHT == set()


def test_a_branch_whose_work_reached_the_base_by_a_squash_publishes_nothing(
    session, workspace, repo
):
    """Right after a tree's PR squash-merges, the branch is not an ancestor of the
    base, but it adds nothing to it; a push would open an empty PR."""
    ms, target = _landed_tree(session, workspace, repo, PUSH_ONLY)
    commit_on(repo, "main", "c1.txt", "child work\n")  # the squash of the tree's PR

    outcome = publish_tree(session, ms, workspace)

    assert outcome.ok and outcome.skipped
    assert _remote_sha(repo, target) == ""


def test_a_merged_pr_on_the_branch_is_not_taken_for_an_open_one(session, workspace, repo):
    """lg-initiatives-cross-724: #411 merged weeks earlier from the same branch."""
    ms, target = _landed_tree(
        session, workspace, repo, {"commit": True, "push": True, "tree_open_pr": True}
    )
    calls: list[list[str]] = []

    def fake_gh(args, cwd):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            assert "--state" in args and args[args.index("--state") + 1] == "open"
            return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")
        if args[:2] == ["pr", "view"]:  # what the merged PR would have answered
            return subprocess.CompletedProcess(
                args, 0, stdout="https://github.com/example/repo/pull/411\n", stderr=""
            )
        return subprocess.CompletedProcess(
            args, 0, stdout="https://github.com/example/repo/pull/412\n", stderr=""
        )

    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh):
        outcome = publish_tree(session, ms, workspace)

    assert outcome.ok and outcome.pr_url.endswith("/pull/412")
    assert any(call[:2] == ["pr", "create"] for call in calls)
