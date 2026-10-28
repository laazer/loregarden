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

import dataclasses
import logging
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    BaxterChatMessage,
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
from loregarden.services.worktree_lifecycle import (
    ChatWorktreeHold,
    KeepReason,
    release_chat_worktree,
)
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
    note = outcome.as_note()
    assert "Committed to" in note
    assert chat_session_branch(chat_session) in note
    # Not "published": push is off, the branch is local, and a reader who
    # believed otherwise would go looking for a pull request that is not there.
    assert "Published" not in note


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


def test_deleting_a_thread_whose_head_strands_a_commit_refuses_too(
    session, workspace, chat_session
):
    """The refusal a thread's operator meets, for the other kind of unseen work.

    The branch holds its commit, so the published-work path above would drop
    this thread's row and remove the checkout — and the commit HEAD moved off
    the branch to make would go with it, reachable from nothing. The thread is
    what owns that directory, so the refusal has to reach this far: a row
    deleted here leaves a checkout on disk belonging to nobody.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "published.txt").write_text("committed on the branch\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    git(root, "checkout", "-q", "--detach")
    (root / "interrupted.txt").write_text("committed where no ref reaches\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "interrupted rebase")
    stranded = git(root, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    assert str(root) in str(caught.value)
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()
    assert (root / "interrupted.txt").exists()
    assert git(root, "rev-parse", "HEAD").stdout.strip() == stranded


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
    assert held.worktree.worktree_path == str(root)
    # Nothing was attempted here — there was no service to attempt it with — so
    # this is a hold, not a failed removal, and the operator gets today's
    # wording rather than advice about clearing a path.
    assert held.removal_failed is False


# --------------------------------------------------------------------------
# A checkout the thread owns and the server cannot remove — lg-external-harness-954
#
# Three of the four ways `release_chat_worktree` declines are deliberate: no
# service to act through, or one of `retire_worktree`'s premises refusing. The
# fourth is not a decision at all — `git worktree remove` and the `shutil.rmtree`
# fallback both raised, and the directory is still there. The thread's row is the
# only thing that owns that directory and no sweeper reaps a chat checkout, so
# the row has to survive either way; what must differ is what the operator is
# told, because "commit or discard your work" cannot clear a permission error.
# --------------------------------------------------------------------------


def _committed_thread_tree(session, workspace, chat_session) -> Path:
    """A thread whose work is committed on its branch — every premise satisfied,
    so the only thing left to refuse the removal is the removal itself."""
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "published.txt").write_text("committed by the turn\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    return root


def test_a_checkout_that_will_not_go_is_held_and_says_the_removal_failed(
    session, workspace, chat_session
):
    """AC17 — the hold carries the reason, because a bare worktree cannot.

    Returning the `Worktree` alone satisfies "the row survives" and loses the
    only fact the caller needs to pick its words: whether anything was tried.
    """
    root = _committed_thread_tree(session, workspace, chat_session)

    with mock.patch.object(WorktreeService, "cleanup_worktree", return_value=False):
        held = release_chat_worktree(session, chat_session)

    assert held is not None
    assert held.worktree.worktree_path == str(root)
    assert held.removal_failed is True
    assert root.is_dir()


def test_a_premise_that_refuses_is_held_without_claiming_a_removal_failed(
    session, workspace, chat_session
):
    """AC17 — the other hold, and the one that keeps today's wording. Nothing was
    attempted: the tree holds work, so the removal was never reached."""
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work nobody has seen\n")

    held = release_chat_worktree(session, chat_session)

    assert held is not None
    assert held.worktree.worktree_path == str(root)
    assert held.removal_failed is False


def test_a_thread_whose_checkout_went_is_released_outright(session, workspace, chat_session):
    """AC17 — the success case still answers None, so `delete_chat_session`
    proceeds. Pinned beside the holds because "no hold" is the one answer that
    drops the row, and a wrong field read reaches it from the failure above."""
    _committed_thread_tree(session, workspace, chat_session)

    assert release_chat_worktree(session, chat_session) is None


def test_a_thread_that_never_committed_has_its_clean_checkout_released(
    session, workspace, chat_session
):
    """A thread that only read code leaves a clean checkout of an empty branch.

    The ticket path keeps such a tree ("no commits beyond its base"), and the
    delete then refused every read-only thread as holding unpublished work.
    Nothing is lost by removing it, so the delete goes through.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)

    assert release_chat_worktree(session, chat_session) is None
    assert not root.exists()


def test_a_thread_with_no_active_worktree_is_released_outright(session, chat_session):
    """AC17 — nothing to hold. Distinct from the failure above only in the
    database, which is why it is asserted rather than assumed."""
    assert release_chat_worktree(session, chat_session) is None


def test_a_failed_removal_refuses_the_delete_without_blaming_the_operators_work(
    session, workspace, chat_session
):
    """AC8/AC18 — the words a person actually meets, and the two they must not.

    This reaches the operator verbatim as a 409 detail in an error toast. The
    premise wording says the thread "still has unpublished work" and to "commit
    or discard it": for a busy file or a permission error there may be no
    uncommitted work at all, and that instruction cannot clear the condition. So
    the failed-removal detail names the three things the operator can act on —
    the removal failed, the path, and that the row was kept on purpose — then the
    one action that clears it.

    The string is exact because its length is load-bearing: `.toast__message`
    clamps at 5.8em / 69.6px, four visible lines at 12px/1.45, with no ellipsis
    and no scrollbar (ToastHost.css). AC18's wording renders 4.02 lines against
    that clamp with the real 86-char chat path, measured in headless Chromium
    with IBM Plex Sans loaded; the wording it replaces rendered 5.98 and lost its
    instruction mid-phrase at "Clear the". Lengthening this message silently cuts
    its tail, so any change re-runs AC22's measurement rather than counting
    characters. The log pointer is gone for the same reason: it was the least
    actionable clause and the most expensive, and `cleanup_worktree` keeps the
    cause in its own `logger.error` either way.
    """
    root = _committed_thread_tree(session, workspace, chat_session)
    session.add(BaxterChatMessage(session_id=chat_session.id, role="user", content="do a thing"))
    session.commit()

    with (
        mock.patch.object(WorktreeService, "cleanup_worktree", return_value=False),
        pytest.raises(ChatSessionHasUnpublishedWork) as caught,
    ):
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    assert detail == (
        f"Could not remove this chat's checkout at {root}. Chat kept on purpose. "
        "Clear the path, then delete again."
    )
    assert "unpublished work" not in detail
    assert "Commit or discard" not in detail
    # The point of refusing: the row that owns this directory, and the thread
    # the operator retries the delete on, both survive — as does the directory,
    # which is the thing the refusal exists to keep owned.
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert (
        session.exec(
            select(BaxterChatMessage).where(BaxterChatMessage.session_id == chat_session.id)
        ).all()
        != []
    )
    assert root.is_dir()
    assert (root / "published.txt").exists()


def test_a_premise_hold_still_names_the_work_and_what_to_do_with_it(
    session, workspace, chat_session
):
    """AC18 — the wording the failure case must not borrow, kept where it is true.

    Pinned from this side too: a single message covering both cases would pass
    the refusal tests above and still hand a permission error the advice written
    for uncommitted work.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work nobody has seen\n")

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    assert "unpublished work" in detail
    assert "Commit or discard" in detail
    assert str(root) in detail
    assert KeepReason.UNCOMMITTED.value not in detail


# --------------------------------------------------------------------------
# The hold itself, and the two holds nothing above routes through the delete
# — lg-external-harness-954
# --------------------------------------------------------------------------


def test_a_hold_cannot_be_rewritten_after_the_fact(session, workspace, chat_session):
    """AC17 — frozen, for the same reason `Retirement` is.

    This answer decides whether a row that owns a directory on disk gets
    dropped. A mutable one is a `hold.removal_failed = False` away from an
    intermediate layer turning a permission error back into the advice written
    for uncommitted work, and nothing else in this module would notice.
    """
    hold = ChatWorktreeHold(
        worktree=Worktree(
            workspace_id=workspace.id,
            chat_session_id=chat_session.id,
            worktree_path="/nowhere",
            branch="loregarden/chat",
            state=WorktreeState.ACTIVE,
        ),
        removal_failed=True,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        hold.removal_failed = False  # type: ignore[misc]


def test_an_unresolvable_workspace_still_says_which_tree_it_left(
    session, workspace, chat_session, monkeypatch, caplog
):
    """AC17 — the hold that attempted nothing keeps the warning it already has.

    The hold's `removal_failed=False` tells `delete_chat_session` which words to
    use; it tells nobody that a checkout was abandoned. Only this record does,
    and it is the one place a workspace that cannot be resolved is reported at
    all — the caller raises about the thread, not about the configuration.
    Migrating the return type is where a log line gets dropped, because no
    assertion above this one reads it.
    """
    from loregarden.services import worktree_lifecycle

    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    monkeypatch.setattr(worktree_lifecycle, "_service_for", lambda *_args: None)

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        held = release_chat_worktree(session, chat_session)

    assert held is not None
    assert held.removal_failed is False
    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.worktree_lifecycle"
    ]
    assert len(warnings) == 1
    assert str(root) in warnings[0].getMessage()


def test_an_unresolvable_workspace_refuses_the_delete_in_todays_words(
    session, workspace, chat_session, monkeypatch
):
    """AC18 — the third hold through the delete, and it is not a failed removal.

    Nothing was attempted, so "clear the path, then delete the chat again" would
    be advice about a condition the operator cannot see and did not cause: the
    workspace row is unresolvable, and clearing the directory would strand the
    branch instead. The two-message branch has to key on `removal_failed`, not
    on "the hold came back", which is the shape that passes both failure tests
    above by routing every hold to the new wording.
    """
    from loregarden.services import worktree_lifecycle

    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "published.txt").write_text("committed by the turn\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    monkeypatch.setattr(worktree_lifecycle, "_service_for", lambda *_args: None)

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    assert "unpublished work" in detail
    assert str(root) in detail
    assert "Could not remove" not in detail
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()


# --------------------------------------------------------------------------
# Adversarial: what the refused delete leaves for the retry
# — lg-external-harness-954, test-break
#
# The tests above pin the words and that the chat row survives. Neither asks
# what owns the directory afterwards, or whether the retry the message tells the
# operator to perform actually completes — and the retry is the only thing that
# ever clears this state: `reconcile_worktrees` skips a worktree with no ticket,
# and `chat_branch_sweep` runs at startup only.
# --------------------------------------------------------------------------


def test_the_refused_delete_leaves_the_worktree_row_owning_its_directory(
    session, workspace, chat_session
):
    """AC3/AC17 — the row stays ACTIVE and still points at the thread.

    The refusal exists so the directory keeps an owner. The chat row surviving
    is half of that; the other half is the `worktrees` row, which is what names
    the path, the branch and the thread. Stamped CLEANUP, or detached from the
    session, it is a directory no query reaches — the orphan this ticket's whole
    argument is about — and every assertion in the test above still passes.
    """
    root = _committed_thread_tree(session, workspace, chat_session)

    with (
        mock.patch.object(WorktreeService, "cleanup_worktree", return_value=False),
        pytest.raises(ChatSessionHasUnpublishedWork),
    ):
        delete_chat_session(session, chat_session)

    row = session.exec(select(Worktree).where(Worktree.chat_session_id == chat_session.id)).one()
    assert row.state == WorktreeState.ACTIVE
    assert row.worktree_path == str(root)
    assert row.branch == chat_session_branch(chat_session)
    assert row.cleaned_at is None


def test_the_retry_the_message_asks_for_completes_once_the_path_is_removable(
    session, workspace, chat_session
):
    """AC9/AC18 — "Clear the path, then delete again" has to be true.

    The instruction is the message's only action, and nothing but the operator
    performs it. So the failed attempt must leave the thread deletable: no row
    half-stamped, no message rows dropped before the raise, no second-delete
    path that now raises for a different reason. This is the one assertion in
    the module that exercises the failure and the recovery in one order, which
    is the only way a leftover from the first attempt is observable at all.
    """
    root = _committed_thread_tree(session, workspace, chat_session)
    session.add(BaxterChatMessage(session_id=chat_session.id, role="user", content="do a thing"))
    session.commit()

    with (
        mock.patch.object(WorktreeService, "cleanup_worktree", return_value=False),
        pytest.raises(ChatSessionHasUnpublishedWork),
    ):
        delete_chat_session(session, chat_session)

    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()

    # The operator clears what was holding it and deletes again. Same call, same
    # row, no refusal this time: the thread, its messages and the checkout all go.
    delete_chat_session(session, chat_session)

    assert session.get(BaxterChatSession, chat_session.id) is None
    assert (
        session.exec(
            select(BaxterChatMessage).where(BaxterChatMessage.session_id == chat_session.id)
        ).all()
        == []
    )
    assert not root.exists()
    assert (
        session.exec(select(Worktree).where(Worktree.chat_session_id == chat_session.id)).all()
        == []
    )
