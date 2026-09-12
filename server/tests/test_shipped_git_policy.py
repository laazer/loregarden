"""What the shipped orchestration profiles actually authorise git to do.

`git_automation` is exercised everywhere with a hand-built `GitAutomationConfig`,
which proves the chain works and proves nothing about the policy real runs get.
That gap has a cost: a chat thread only commits because `loregarden.yaml` says
so, and deleting those two lines turns the publish step back into a no-op that
every other test still passes. The reply would go on saying where the work is,
so it would not even be silent — just useless.

So these read the files as shipped.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import Workspace
from loregarden.services.git_automation_config import enabled_steps, resolve_git_automation


def _workspace(slug: str) -> Workspace:
    return Workspace(slug=slug, name=slug, repo_path=".")


def test_loregarden_commits_and_pushes_a_chat_threads_work():
    """The policy behind `chat_publish.publish_chat_turn` reaching the remote.

    Without `commit`, an acting turn leaves its work uncommitted on the thread's
    branch — a branch whose contents exist only in a working tree, which is the
    failure the worktree sandbox was built to end rather than relocate. Without
    `push`, they exist only on this machine.
    """
    config = resolve_git_automation(_workspace("loregarden"))

    assert config.commit is True
    assert config.push is True
    assert config.worktree is True


def test_loregarden_stops_at_push_and_opens_no_pull_request():
    """Push, and no further — deliberately, not by omission.

    `enabled_steps` is a chain: `open_pr` off disables `auto_merge` whatever its
    own flag says. This repository has no branch protection, so
    `gh pr merge --auto` lands a PR immediately rather than on green — opening
    one has to stay a person's decision.
    """
    config = resolve_git_automation(_workspace("loregarden"))

    assert enabled_steps(config) == ["commit", "push"]
    assert config.open_pr is False
    assert config.auto_merge is False


@pytest.mark.parametrize(
    "slug", ["blobert", "lore-eden", "loremaker", "a-workspace-with-no-profile"]
)
def test_every_other_workspace_still_publishes_nothing(slug: str):
    """The change was scoped to loregarden, and stays that way until asked.

    A workspace falling through to `default.yaml` writes into another repository
    on this machine. Turning commit on there is a separate decision, and this is
    what makes taking it deliberate rather than incidental.
    """
    config = resolve_git_automation(_workspace(slug))

    assert enabled_steps(config) == []
    assert config.worktree is True


def _acting_turn(session, repo, title="Rename the pane editor"):
    """A workspace, a thread and a finished run, wired to the real profile.

    Slug `loregarden`, because the policy is resolved by slug and that is
    precisely what is under test.
    """
    from loregarden.models.domain import AgentRun, BaxterChatSession, RunStatus

    workspace = Workspace(slug="loregarden", name="loregarden", repo_path=str(repo))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    chat = BaxterChatSession(workspace_id=workspace.id, title=title)
    run = AgentRun(
        run_code="r1",
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key="home-chat",
        status=RunStatus.RUNNING,
    )
    session.add(chat)
    session.add(run)
    session.commit()
    session.refresh(chat)
    session.refresh(run)
    return workspace, chat, run


def test_a_chat_turns_work_is_committed_and_pushed_under_the_shipped_policy(tmp_path, isolated_db):
    """End to end on the real policy, with nothing about git automation patched.

    The sandbox tests build their own `GitAutomationConfig`, so they would still
    pass with the profile's `git:` block deleted. This one resolves the policy
    the way a live turn does, against a real remote, and asserts the branch
    actually arrives there.
    """
    from loregarden.services.chat_publish import publish_chat_turn
    from loregarden.services.chat_worktree import resolve_chat_execution_root
    from loregarden.services.git_branch import chat_session_branch
    from sqlmodel import Session
    from tests.worktree_helpers import git, make_repo

    repo = make_repo(tmp_path)
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(repo, "remote", "add", "origin", str(remote))

    with Session(isolated_db) as session:
        workspace, chat, run = _acting_turn(session, repo)
        root = resolve_chat_execution_root(session, run, chat, workspace)
        (root / "what_the_turn_wrote.txt").write_text("real work\n")

        outcome = publish_chat_turn(session, run.id, chat, workspace)

    assert outcome is not None
    assert outcome.automation is not None and outcome.automation.ok
    assert [step.step for step in outcome.automation.steps] == ["commit", "push"]
    assert git(root, "status", "--porcelain").stdout.strip() == ""

    branch = chat_session_branch(chat)
    assert "what_the_turn_wrote.txt" in git(root, "show", "--name-only", "--format=", "HEAD").stdout
    # The branch is on the remote, not merely committed locally.
    assert branch in git(remote, "branch", "--format=%(refname:short)").stdout
    assert (
        git(remote, "rev-parse", branch).stdout.strip()
        == git(root, "rev-parse", "HEAD").stdout.strip()
    )


def test_a_push_that_cannot_reach_a_remote_says_so_and_keeps_the_commit(tmp_path, isolated_db):
    """The failure path, which is the one an operator will actually hit.

    Commit succeeds, push does not, and the chain stops there. What matters is
    that the work is not lost — it is committed on the thread's branch — and
    that the operator is told which step failed rather than being left to
    discover an unpushed branch later.
    """
    from loregarden.services.chat_publish import publish_chat_turn
    from loregarden.services.chat_worktree import resolve_chat_execution_root
    from sqlmodel import Session
    from tests.worktree_helpers import git, make_repo

    repo = make_repo(tmp_path)  # deliberately no remote
    with Session(isolated_db) as session:
        workspace, chat, run = _acting_turn(session, repo)
        root = resolve_chat_execution_root(session, run, chat, workspace)
        (root / "what_the_turn_wrote.txt").write_text("real work\n")

        outcome = publish_chat_turn(session, run.id, chat, workspace)

    assert outcome.automation is not None
    assert not outcome.automation.ok
    assert outcome.automation.failure.step == "push"
    # The commit still happened, so the work survives the failed push.
    assert git(root, "status", "--porcelain").stdout.strip() == ""
    assert "what_the_turn_wrote.txt" in git(root, "show", "--name-only", "--format=", "HEAD").stdout

    summary = outcome.summary()
    assert "`push`" in summary
    assert "origin" in summary


def test_the_summary_for_a_commit_only_policy_does_not_claim_a_push():
    """Kept for a workspace configured to commit without pushing.

    Not loregarden's policy any more, but `summary()` still has to get this
    right: calling a local branch "published" sends a reader looking for a pull
    request that does not exist.
    """
    from pathlib import Path

    from loregarden.services.chat_publish import TurnPublishOutcome
    from loregarden.services.git_automation import AutomationResult, StepResult

    summary = TurnPublishOutcome(
        repo_root=Path("/tmp/tree"),
        branch="chat/rename-the-pane-editor-abcd1234",
        dirty=True,
        automation=AutomationResult(steps=[StepResult("commit", True, "msg")]),
    ).summary()

    assert "Committed to `chat/rename-the-pane-editor-abcd1234`" in summary
    assert "Not pushed" in summary
    assert "Published" not in summary


def test_loregarden_prunes_its_landed_chat_branches():
    """The switch behind the startup sweep actually being on.

    Without it `sweep_chat_branches` returns before it looks at anything, and a
    rail that pushes on every acting turn accumulates a branch per conversation
    on the remote with nothing to clear them.
    """
    config = resolve_git_automation(_workspace("loregarden"))

    assert config.prune_landed_chat_branches is True


@pytest.mark.parametrize(
    "slug", ["blobert", "lore-eden", "loremaker", "a-workspace-with-no-profile"]
)
def test_no_other_workspace_has_its_branches_pruned(slug: str):
    """Deleting refs from another repository's remote stays opt-in.

    `push` must not imply it, and neither must the default profile: these three
    are separate repositories on this machine, and a sweep that widened to them
    by inheritance would be removing branches nobody asked it to touch.
    """
    assert resolve_git_automation(_workspace(slug)).prune_landed_chat_branches is False
