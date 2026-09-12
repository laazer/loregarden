"""A chat turn that acts gets its own worktree, and says where its work went.

Acting turns on the Home and ticket-triage rails used to execute in the shared
workspace checkout: `agent_turn_runner` had no owner to resolve a tree from, so
`resolve_workspace_root` was its only answer. The observable result was edits
sitting uncommitted on the operator's `main`, attributed to no branch and no
run, where the next orchestration's tree sweep would fold them into an unrelated
ticket's commit.

These pin both halves of the fix: the turn runs somewhere of its own, and
whatever the workspace's git policy does or does not publish, the operator is
told where the work is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    RunStatus,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.agent_turn_runner import AgentTurnRequest, _resolve_turn_root
from loregarden.services.baxter_chat_service import (
    ChatSessionHasUnpublishedWork,
    delete_chat_session,
)
from loregarden.services.chat_publish import TurnPublishOutcome, publish_chat_turn
from loregarden.services.chat_worktree import (
    resolve_chat_execution_root,
    resolve_chat_thread_root,
)
from loregarden.services.git_automation import (
    AutomationResult,
    StepResult,
    subject_for_chat_session,
)
from loregarden.services.git_branch import chat_session_branch
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.triage_service import TRIAGE_CLI_PROFILE
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, head_branch, make_repo, make_ticket


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


@pytest.fixture(name="chat_session")
def chat_session_fixture(session, workspace):
    chat = BaxterChatSession(workspace_id=workspace.id, title="Rename the pane editor")
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return chat


def _run(session, workspace, code="r1") -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=None,
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key="home-chat",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# --------------------------------------------------------------------------
# The thread owns a tree, and keeps it across turns
# --------------------------------------------------------------------------


def test_branch_is_derived_from_the_thread_id_not_its_title(chat_session):
    original = chat_session_branch(chat_session)

    chat_session.title = "Something else entirely"

    # The id half is what makes the branch stable; a rename may redecorate it
    # but must not move the commits somewhere else.
    assert original.startswith("chat/")
    assert chat_session.id[:8] in original
    assert chat_session.id[:8] in chat_session_branch(chat_session)


def test_an_acting_turn_gets_its_own_worktree_not_the_shared_checkout(
    session, workspace, chat_session, repo
):
    run = _run(session, workspace)

    root = resolve_chat_execution_root(session, run, chat_session, workspace)

    assert root != repo
    assert root.is_dir()
    assert head_branch(root) == chat_session_branch(chat_session)
    # The run has to carry it: git_automation and resolve_run_root both start
    # from run.worktree_id.
    session.refresh(run)
    assert run.worktree_id is not None


def test_later_turns_in_the_same_thread_land_in_the_same_tree(session, workspace, chat_session):
    first = resolve_chat_execution_root(
        session, _run(session, workspace, "r1"), chat_session, workspace
    )
    (first / "from_turn_one.txt").write_text("one\n")

    second = resolve_chat_execution_root(
        session, _run(session, workspace, "r2"), chat_session, workspace
    )

    assert second == first
    assert (second / "from_turn_one.txt").exists()


def test_a_second_thread_gets_a_second_tree(session, workspace, chat_session):
    other = BaxterChatSession(workspace_id=workspace.id, title="Unrelated")
    session.add(other)
    session.commit()
    session.refresh(other)

    mine = resolve_chat_execution_root(
        session, _run(session, workspace, "r1"), chat_session, workspace
    )
    theirs = resolve_chat_execution_root(session, _run(session, workspace, "r2"), other, workspace)

    assert mine != theirs


def test_a_thread_with_no_tree_reads_the_shared_checkout(session, workspace, chat_session, repo):
    # Read-only counterpart: it must never conjure a tree, or an advisory turn
    # would cut a branch just by being asked a question.
    assert resolve_chat_thread_root(session, chat_session, workspace) == repo
    assert session.exec(select(Worktree)).first() is None


def test_a_thread_that_has_acted_is_read_in_its_own_tree(session, workspace, chat_session):
    acted_in = resolve_chat_execution_root(
        session, _run(session, workspace), chat_session, workspace
    )

    assert resolve_chat_thread_root(session, chat_session, workspace) == acted_in


def test_a_vanished_tree_is_replaced_rather_than_handed_back(
    session, workspace, chat_session, repo
):
    service = WorktreeService(session, repo_path=str(repo))
    first = service.get_or_create_for_chat_session(chat_session, _run(session, workspace, "r1").id)
    git(repo, "worktree", "remove", "--force", first.worktree_path)

    second = service.get_or_create_for_chat_session(chat_session, _run(session, workspace, "r2").id)

    assert second is not None
    assert second.id != first.id
    assert Path(second.worktree_path).is_dir()
    session.refresh(first)
    assert first.state == WorktreeState.CLEANUP


def test_the_worktree_switch_can_be_turned_off(session, workspace, chat_session, repo, monkeypatch):
    monkeypatch.setattr(
        "loregarden.services.chat_worktree.resolve_git_automation",
        lambda _workspace: GitAutomationConfig(worktree=False),
    )

    root = resolve_chat_execution_root(session, _run(session, workspace), chat_session, workspace)

    assert root == repo


# --------------------------------------------------------------------------
# The runner picks the owner's tree
# --------------------------------------------------------------------------


def _request(session, workspace, **kwargs) -> AgentTurnRequest:
    return AgentTurnRequest(
        session=session,
        workspace=workspace,
        prompt="p",
        profile=TRIAGE_CLI_PROFILE,
        agent={},
        intent="execute",
        **kwargs,
    )


def test_the_runner_sends_an_acting_home_turn_to_the_thread_tree(
    session, workspace, chat_session, repo
):
    run = _run(session, workspace)

    root = _resolve_turn_root(_request(session, workspace, chat_session_id=chat_session.id), run)

    assert root != repo
    assert head_branch(root) == chat_session_branch(chat_session)


def test_the_runner_sends_an_acting_ticket_turn_to_the_ticket_tree(session, workspace, repo):
    ticket = make_ticket(session, workspace)
    run = _run(session, workspace)

    root = _resolve_turn_root(_request(session, workspace, ticket=ticket), run)

    assert root != repo
    assert head_branch(root) == ticket.branch


def test_an_explicit_checkout_still_wins(session, workspace, chat_session, tmp_path):
    # Branch triage resolves its own checkout and passes it; the owner-derived
    # tree must not override it.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    root = _resolve_turn_root(
        _request(session, workspace, chat_session_id=chat_session.id, workspace_root=elsewhere),
        _run(session, workspace),
    )

    assert root == elsewhere


def test_a_turn_with_no_owner_answers_from_the_shared_checkout(session, workspace, repo):
    assert _resolve_turn_root(_request(session, workspace), _run(session, workspace)) == repo


def test_a_turn_with_no_run_never_cuts_a_tree(session, workspace, chat_session, repo):
    # `run is None` is the read-only path: reuse what exists, create nothing.
    root = _resolve_turn_root(_request(session, workspace, chat_session_id=chat_session.id), None)

    assert root == repo


# --------------------------------------------------------------------------
# Whatever the policy does, the operator is told
# --------------------------------------------------------------------------


def _publish_config(**kwargs) -> GitAutomationConfig:
    return GitAutomationConfig(**kwargs)


def test_a_turn_that_changed_nothing_says_nothing(session, workspace, chat_session):
    run = _run(session, workspace)
    resolve_chat_execution_root(session, run, chat_session, workspace)

    outcome = publish_chat_turn(session, run.id, chat_session, workspace)

    assert outcome is not None
    assert outcome.dirty is False
    assert outcome.as_note() == ""


def test_uncommitted_work_names_its_branch_when_the_policy_commits_nothing(
    session, workspace, chat_session, monkeypatch
):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work\n")
    monkeypatch.setattr(
        "loregarden.services.chat_publish.resolve_git_automation",
        lambda _workspace: _publish_config(commit=False),
    )

    outcome = publish_chat_turn(session, run.id, chat_session, workspace)

    note = outcome.as_note()
    assert chat_session_branch(chat_session) in note
    assert "uncommitted" in note
    assert "commit" in note


def test_work_is_committed_to_the_thread_branch_when_the_policy_commits(
    session, workspace, chat_session, monkeypatch
):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work\n")
    monkeypatch.setattr(
        "loregarden.services.chat_publish.resolve_git_automation",
        lambda _workspace: _publish_config(commit=True),
    )

    outcome = publish_chat_turn(session, run.id, chat_session, workspace)

    assert outcome.automation is not None
    assert outcome.automation.ok
    assert git(root, "status", "--porcelain").stdout.strip() == ""
    subject = subject_for_chat_session(session, session.get(AgentRun, run.id), chat_session)
    assert git(root, "log", "-1", "--format=%s").stdout.strip() == subject.commit_message
    assert "`commit`" in outcome.as_note()


def test_a_failed_publish_step_reaches_the_reply(session, workspace, chat_session, monkeypatch):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work\n")
    failed = AutomationResult(
        steps=[StepResult("commit", True, "ok"), StepResult("push", False, "no remote")]
    )
    monkeypatch.setattr(
        "loregarden.services.chat_publish.publish_run",
        lambda *_args, **_kwargs: failed,
    )

    note = publish_chat_turn(session, run.id, chat_session, workspace).as_note()

    assert "`push`" in note
    assert "no remote" in note


def test_an_unreadable_status_is_reported_rather_than_read_as_clean(
    session, workspace, chat_session, monkeypatch
):
    # "clean" is what suppresses the note. A git that would not answer must not
    # be able to silence it.
    run = _run(session, workspace)
    resolve_chat_execution_root(session, run, chat_session, workspace)
    monkeypatch.setattr("loregarden.services.chat_publish._is_dirty", lambda _root: None)

    outcome = publish_chat_turn(session, run.id, chat_session, workspace)

    assert outcome.dirty is True
    assert outcome.as_note() != ""


def test_a_turn_with_no_run_has_nothing_to_publish(session, workspace, chat_session):
    assert publish_chat_turn(session, "", chat_session, workspace) is None


def test_a_shared_checkout_fallback_says_so(tmp_path):
    # The degraded path from `resolve_chat_execution_root`: no branch, so the
    # note has to name the directory instead of pretending there is one.
    note = TurnPublishOutcome(repo_root=tmp_path, branch="", dirty=True, automation=None).as_note()

    assert str(tmp_path) in note
    assert "shared checkout" in note


# --------------------------------------------------------------------------
# Deleting the thread has to deal with the tree it owns
# --------------------------------------------------------------------------


def test_deleting_a_thread_removes_its_published_worktree(session, workspace, chat_session):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")

    delete_chat_session(session, chat_session)

    assert not root.is_dir()
    assert session.get(BaxterChatSession, chat_session.id) is None


def test_deleting_a_thread_with_uncommitted_work_refuses_and_says_where(
    session, workspace, chat_session
):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work nobody has seen\n")

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    assert chat_session_branch(chat_session) in str(caught.value)
    assert str(root) in str(caught.value)
    # And the thread survives, so the operator can still open it and publish.
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()


def test_deleting_a_thread_that_never_acted_is_unchanged(session, workspace, chat_session):
    delete_chat_session(session, chat_session)

    assert session.get(BaxterChatSession, chat_session.id) is None


def test_a_bridge_turn_that_cannot_resolve_a_checkout_settles_its_run(
    session, workspace, chat_session, tmp_path, monkeypatch
):
    """A raise after the run exists must not leave it RUNNING.

    `_start_run` moved ahead of the checkout resolution so the worktree has a
    run to hang off. That put a raise between "the run exists" and the try block
    that settles it — and a run stuck RUNNING both shows as live in the UI and
    blocks the thread's next turn on the already-running check.
    """
    from loregarden.services.agent_turn_runner import _run_permission_bridge

    missing = tmp_path / "not-a-directory"
    request = _request(
        session,
        workspace,
        chat_session_id=chat_session.id,
        workspace_root=missing,
        stage_key="home-chat",
        agent_id="triage",
        manage_run=True,
    )

    with pytest.raises(ValueError, match="does not exist"):
        _run_permission_bridge(request)

    run = session.exec(select(AgentRun).where(AgentRun.workspace_id == workspace.id)).one()
    assert run.status == RunStatus.FAILED
    assert str(missing) in (run.stderr or "")


def test_an_unresolvable_workspace_reports_the_tree_it_leaves_behind(
    session, workspace, chat_session, monkeypatch
):
    """The degenerate case must not read as "nothing to release".

    Deriving the worktree from the service made a chat session whose workspace
    could not be resolved return None — so the delete went through, the foreign
    key set the column NULL, and the checkout was left on disk owned by nothing
    and reaped by no sweeper.
    """
    from loregarden.services import worktree_lifecycle

    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    monkeypatch.setattr(worktree_lifecycle, "_service_for", lambda *_args: None)

    held = worktree_lifecycle.release_chat_worktree(session, chat_session)

    assert held is not None
    assert held.worktree_path == str(root)
