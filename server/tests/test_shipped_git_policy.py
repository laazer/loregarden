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


def test_loregarden_commits_a_chat_threads_work():
    """The policy behind `chat_publish.publish_chat_turn` actually committing.

    Without `commit`, an acting turn leaves its work uncommitted on the thread's
    branch — a branch whose contents exist only in a working tree, which is the
    failure the worktree sandbox was built to end rather than relocate.
    """
    config = resolve_git_automation(_workspace("loregarden"))

    assert config.commit is True
    assert config.worktree is True


def test_loregarden_stops_at_commit_and_publishes_nothing_outward():
    """Commit, and no further — deliberately, not by omission.

    `enabled_steps` is a chain: `push` off disables `open_pr` and `auto_merge`
    whatever their own flags say. This repository has no branch protection, so
    `gh pr merge --auto` lands a PR immediately rather than on green. Nothing
    should leave this machine without a person deciding to.
    """
    config = resolve_git_automation(_workspace("loregarden"))

    assert enabled_steps(config) == ["commit"]
    assert config.push is False
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


def test_a_chat_turns_work_is_committed_under_the_shipped_policy(tmp_path, isolated_db):
    """End to end on the real policy, with nothing about git automation patched.

    The sandbox tests build their own `GitAutomationConfig`, so they would still
    pass with the profile's `git:` block deleted. This one resolves the policy
    the way a live turn does, and asserts the commit exists.
    """
    from loregarden.models.domain import AgentRun, BaxterChatSession, RunStatus
    from loregarden.services.chat_publish import publish_chat_turn
    from loregarden.services.chat_worktree import resolve_chat_execution_root
    from sqlmodel import Session
    from tests.worktree_helpers import git, make_repo

    repo = make_repo(tmp_path)
    with Session(isolated_db) as session:
        # Slug `loregarden`, because the profile is resolved by slug and that is
        # precisely what is under test.
        workspace = Workspace(slug="loregarden", name="loregarden", repo_path=str(repo))
        session.add(workspace)
        session.commit()
        session.refresh(workspace)

        chat = BaxterChatSession(workspace_id=workspace.id, title="Rename the pane editor")
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

        root = resolve_chat_execution_root(session, run, chat, workspace)
        (root / "what_the_turn_wrote.txt").write_text("real work\n")

        outcome = publish_chat_turn(session, run.id, chat, workspace)

    assert outcome is not None
    assert outcome.automation is not None and outcome.automation.ok
    assert git(root, "status", "--porcelain").stdout.strip() == ""
    assert "what_the_turn_wrote.txt" in git(root, "show", "--name-only", "--format=", "HEAD").stdout
    # Committed, and nowhere else: push is off, so the branch is local.
    assert [s.step for s in outcome.automation.steps] == ["commit"]


def test_the_note_for_a_commit_only_policy_does_not_claim_a_push():
    """What the operator is told when the branch is still local.

    `commit` alone is now the shipped policy, so this is the sentence most turns
    produce. Calling it "published" would send a reader looking for a pull
    request that does not exist.
    """
    from pathlib import Path

    from loregarden.services.chat_publish import TurnPublishOutcome
    from loregarden.services.git_automation import AutomationResult, StepResult

    note = TurnPublishOutcome(
        repo_root=Path("/tmp/tree"),
        branch="chat/rename-the-pane-editor-abcd1234",
        dirty=True,
        automation=AutomationResult(steps=[StepResult("commit", True, "msg")]),
    ).as_note()

    assert "Committed to `chat/rename-the-pane-editor-abcd1234`" in note
    assert "Not pushed" in note
    assert "Published" not in note
