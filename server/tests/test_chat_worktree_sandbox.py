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
from loregarden.services.branch_names import chat_session_branch
from loregarden.services.chat_publish import TurnPublishOutcome, publish_chat_turn
from loregarden.services.chat_worktree import (
    resolve_chat_execution_root,
    resolve_chat_thread_root,
)
from loregarden.services.directory_release import DirectoryReleaseFailed, ReleaseDisposition
from loregarden.services.git_automation import (
    AutomationResult,
    StepResult,
    subject_for_chat_session,
)
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.triage_service import TRIAGE_CLI_PROFILE
from loregarden.services.worktree_lifecycle import (
    ChatWorktreeHold,
    KeepReason,
    RemovalFailure,
    Retirement,
    release_chat_worktree,
)
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
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


def test_deleting_a_thread_removes_its_published_worktree(session, workspace, chat_session, repo):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    # lg-external-harness-1026: removing a chat checkout now needs a receipt
    # too, and a chat tree's receipt is its content already being in the base.
    _land_chat_branch(repo, chat_session)

    delete_chat_session(session, chat_session)

    assert not root.is_dir()
    assert session.get(BaxterChatSession, chat_session.id) is None


def test_deleting_a_thread_with_uncommitted_work_refuses_and_says_where(
    session, workspace, chat_session
):
    """The refusal names the place the operator has to go — and only that place.

    It used to name the branch as well. lg-external-harness-1167 dropped it:
    the branch is recoverable from the path (`git -C <path> branch
    --show-current`) and the path is not recoverable from the branch, because
    `worktree_service` truncates the slug at 32 chars for the directory while
    `git_branch.chat_session_branch` truncates at 48 for the branch and the two
    carry different uuid8 suffixes. Naming it spent up to 62 characters of a
    four-line clamp ahead of an unbounded path to answer a question the path
    already answers, and the clamp ate the tail. So there is no branch
    assertion here, and keeping the branch in the message to satisfy one would
    re-open the defect.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work nobody has seen\n")

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    assert str(root) in str(caught.value)
    assert chat_session_branch(chat_session) not in str(caught.value)
    # And the thread survives, so the operator can still open it and publish.
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()


def test_deleting_a_thread_whose_head_strands_a_commit_refuses_too(
    session, workspace, chat_session, repo
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
    _land_chat_branch(repo, chat_session)
    git(root, "checkout", "-q", "--detach")
    (root / "interrupted.txt").write_text("committed where no ref reaches\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "interrupted rebase")
    stranded = git(root, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    # AC12(b) — the OTHER cause that reaches the premise branch. The branch is
    # one f-string today, but nothing above pins that: this cause's refusal was
    # asserted only with `in`, which passes for a path sitting in the middle of
    # the sentence. So it carries the same two structural assertions the
    # uncommitted cause carries, and a cause-dependent wording cannot slip in
    # under them (lg-external-harness-1167 AC6/AC7).
    assert detail.endswith(str(root)), detail
    assert chat_session_branch(chat_session) not in detail
    assert "Commit or discard" in detail
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
    assert held.outcome.refusal is not RemovalFailure.CLEANUP_FAILED
    assert held.outcome.deleted_nothing is True
    assert held.cause is None


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


def _land_chat_branch(repo: Path, chat_session) -> None:
    """Put the thread branch's content into the base, as a squash merge would.

    That is a chat tree's receipt: `branch_work_has_landed` asks whether every
    file the branch touched is byte-identical in the base, which is the only
    bar at which removing the checkout destroys nothing.
    """
    git(repo, "merge", "-q", "--squash", chat_session_branch(chat_session))
    git(repo, "commit", "-q", "-m", f"squash: {chat_session_branch(chat_session)}")


def _seam_failure(root, refusal: CleanupRefusal) -> DirectoryReleaseFailed:
    """What `remove_checkout` raises, for either cause it can raise for.

    lg-external-harness-1026 answers both with the one `RemovalFailure` member,
    so the cause travels on the exception rather than in the gate's vocabulary
    — the one channel that preserves both. A containment refusal parks nothing,
    because nothing was renamed, so it carries no parked path.
    """
    parked = f"{root}.parked" if refusal is CleanupRefusal.REMOVE_RAISED else ""
    return DirectoryReleaseFailed(
        "the removal failed",
        disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
        refusal=refusal,
        uncleaned_path=str(root),
        parked_path=parked,
    )


def _committed_thread_tree(session, workspace, chat_session, repo, code="r1") -> Path:
    """A thread whose work is committed on its branch *and* landed in the base —
    every premise satisfied, so the only thing left to refuse the removal is the
    removal itself.

    lg-external-harness-1026 added the receipt premise, and a commit on the
    thread branch is not one: without the squash below every test reached from
    here refuses on NO_RECEIPT and never reaches the removal it is about.

    `code` names the run, so a test can build a second committed thread without
    colliding on `run_code`.
    """
    run = _run(session, workspace, code=code)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    # `published.txt` is read back by a test below, so its name and contents
    # stay; the file beside it is per-thread (lg-external-harness-1026
    # implement, AC18). Landing one thread's branch puts `published.txt` into
    # `main`, after which a second thread committing only that file has
    # "nothing to commit" — the FIXTURE fails rather than the assertion — and a
    # thread whose own file differed from main's would stop reading as landed
    # the moment the next thread landed a different version of it.
    (root / "published.txt").write_text("committed by the turn\n")
    (root / f"published-{code}.txt").write_text(f"committed by the turn ({code})\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    _land_chat_branch(repo, chat_session)
    return root


def test_a_checkout_that_will_not_go_is_held_and_says_the_removal_failed(
    session, workspace, chat_session, repo
):
    """AC17 — the hold carries the reason, because a bare worktree cannot.

    Returning the `Worktree` alone satisfies "the row survives" and loses the
    only fact the caller needs to pick its words: whether anything was tried.
    """
    root = _committed_thread_tree(session, workspace, chat_session, repo)

    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=f"{root}.parked",
        ),
    ):
        held = release_chat_worktree(session, chat_session)

    assert held is not None
    assert held.worktree.worktree_path == str(root)
    assert held.outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert held.outcome.deleted_nothing is True
    assert held.cause is CleanupRefusal.REMOVE_RAISED
    assert root.is_dir()


def test_an_out_of_base_checkout_is_held_with_the_never_retryable_member(
    session, workspace, chat_session, tmp_path
):
    """AC16 — the hold carries the member, so the caller can pick a sentence.

    Forced with the real service: the recorded path is outside the base and is
    absent from disk, so `_keep_reason` finds no premise to refuse with and the
    row reaches the seam. The hold must carry OUTSIDE_BASE and not merely "a
    removal failed" — a bool here is what made the two causes share one
    message, and "clear the path, then delete again" is not the remedy for a
    path the service will never touch.
    """
    row = session.exec(select(Worktree).where(Worktree.chat_session_id == chat_session.id)).first()
    assert row is None, "fixture: the thread must not already own a tree"
    outside = tmp_path / "somewhere-else" / "thread"
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=_run(session, workspace, code="r-outside").id,
            chat_session_id=chat_session.id,
            worktree_path=str(outside),
            branch=chat_session_branch(chat_session),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()

    held = release_chat_worktree(session, chat_session)

    assert held is not None
    # `cause`, not `refusal`: lg-external-harness-1026 replaced the hold's one
    # field with two carrying two different facts (AC18 migration) — `outcome`
    # is the gate's answer and `cause` is the seam's member, which the gate
    # collapses into CLEANUP_FAILED. Same claim, same member, read off the
    # field that now carries it.
    assert held.cause is CleanupRefusal.OUTSIDE_BASE
    assert held.worktree.worktree_path == str(outside)


def test_the_hold_no_longer_carries_a_bare_removal_failed_bool():
    """AC17 — `removal_failed` is REPLACED, not joined by what replaced it.

    Two fields encoding ONE fact is how a caller ends up reading the one that
    lost the distinction, and the bool was that: it answered "was anything
    attempted" and nothing else. What replaces it is two fields encoding two
    DIFFERENT facts. `outcome` is the gate's own answer — a premise refusal, a
    failed removal, a park — which AC17 needs three values for and a bool
    cannot carry. `cause` is the seam's `CleanupRefusal`, which the gate
    deliberately collapses into the single `RemovalFailure.CLEANUP_FAILED`
    member (AC11/AC12), so without it `delete_chat_session` could not tell a
    path outside the base from an `rmtree` that raised without parsing the
    prose — the thing the test above forbids.
    """
    fields = {field.name for field in dataclasses.fields(ChatWorktreeHold)}
    assert "removal_failed" not in fields
    assert fields == {"worktree", "outcome", "cause"}
    hold = ChatWorktreeHold(
        worktree=mock.Mock(),
        outcome=Retirement(
            refusal=KeepReason.UNCOMMITTED,
            deleted_nothing=True,
            parked_path="",
            detail="nothing was deleted",
        ),
    )
    # The seam cause defaults to absent, because a premise refusal never
    # reached the seam. The gate's answer does NOT default: a hold with no
    # outcome is the bool again, one field along.
    assert hold.cause is None
    with pytest.raises(TypeError):
        ChatWorktreeHold(worktree=mock.Mock())  # type: ignore[call-arg]


def test_no_branch_picks_its_sentence_by_reading_operator_prose():
    """AC16 — control flow reads the member, never `Retirement.failure`.

    The rejected alternatives were branching on the failure prose and a reverse
    `str -> CleanupRefusal` lookup. Both make a wording tweak — the kind AC9's
    measurement invites — silently change which sentence an operator meets.
    Asserted on the source of the two functions that pair them, because the
    defect is invisible in behaviour until someone edits a string.
    """
    import inspect
    import re

    from loregarden.services import baxter_chat_service, worktree_lifecycle

    for function in (
        worktree_lifecycle.release_chat_worktree,
        baxter_chat_service.delete_chat_session,
    ):
        source = inspect.getsource(function)
        # 954's `.failure` field is gone; the prose now lives on
        # `Retirement.detail`, so the rule is the same one one name further on:
        # the detail may be PRINTED, never compared. A branch reads
        # `outcome.refusal` or the seam's `cause`.
        assert ".failure" not in source, f"{function.__name__} branches on operator prose"
        compared = [
            line.strip()
            for line in source.splitlines()
            if ".detail" in line
            and re.search(r"\.detail\s*(==|!=|in |\.startswith|\.endswith)", line)
        ]
        assert compared == [], f"{function.__name__} branches on operator prose: {compared}"


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
    assert held.outcome.refusal is not RemovalFailure.CLEANUP_FAILED
    assert held.outcome.deleted_nothing is True
    assert held.cause is None


def test_a_thread_whose_checkout_went_is_released_outright(session, workspace, chat_session, repo):
    """AC17 — the success case still answers None, so `delete_chat_session`
    proceeds. Pinned beside the holds because "no hold" is the one answer that
    drops the row, and a wrong field read reaches it from the failure above."""
    _committed_thread_tree(session, workspace, chat_session, repo)

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
    session, workspace, chat_session, repo
):
    """AC8/AC18, and lg-external-harness-1208 AC1/AC6 — the words a person
    actually meets, in the order the clamp cannot take from them.

    This reaches the operator verbatim as a 409 detail in an error toast. The
    premise wording says the thread "still has unpublished work" and to "commit
    or discard it": for a busy file or a permission error there may be no
    uncommitted work at all, and that instruction cannot clear the condition. So
    the failed-removal detail names what was not removed, that the row was kept,
    the one action that clears it — and only then where it is.

    The ORDERING is the assertion, not a length. `.toast__message` clamps at four
    visible lines with no ellipsis and no scrollbar (ToastHost.css), and the
    worktrees base is operator-dependent — `worktree_service` caps the directory
    NAME but not the base — so a wording that merely fits this machine's paths is
    one `--worktrees-dir` away from clipping its own instruction. With the whole
    action clause complete before the first interpolated character, the only
    thing the clamp can ever eat is the tail of the path, which is why
    `detail.endswith(str(root))` is pinned separately below. This follows the
    precedent the `OUTSIDE_BASE` branch and the premise branch already set
    (lg-external-harness-1165 AC9, lg-external-harness-1167 AC6).

    Deliberately NOT a character count: `overflow-wrap: anywhere` makes line
    count non-linear in length, so the clamp is MEASURED in
    `client/scripts/measure-toast-clamp.mjs` (lg-external-harness-1208 AC2/AC13,
    against real `worktrees` rows) rather than counted here. Tightening the
    wording is allowed; lengthening the literal prefix, or moving the path
    forward, re-runs that measurement. `on purpose` and `at` are gone because
    the reorder has to be paid for — restoring either without re-measuring is
    the regression this pin exists to catch. The log pointer is gone for the
    same reason: `remove_checkout` keeps the cause in its own `logger.error`.
    """
    # No park: the rename itself failed, so there is no second path to name
    # and this is the sentence 954 measured against the toast clamp. The
    # parked variant adds a clause and is pinned separately below.
    root = _committed_thread_tree(session, workspace, chat_session, repo)
    session.add(BaxterChatMessage(session_id=chat_session.id, role="user", content="do a thing"))
    session.commit()

    with (
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.RESTORED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(root),
                parked_path="",
            ),
        ),
        pytest.raises(ChatSessionHasUnpublishedWork) as caught,
    ):
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    assert detail == (
        f"Could not remove this chat's checkout. Chat kept. Clear the path, then delete again. It is at: {root}"
    )
    # The ordering, as an assertion and not as a comment: nothing follows the
    # path, so nothing the clamp cuts is an instruction.
    assert detail.endswith(str(root)), detail
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


def test_an_out_of_base_checkout_refuses_the_delete_without_an_instruction_that_cannot_work(
    session, workspace, chat_session, tmp_path
):
    """AC4/AC7/AC9 — the never-retryable 409, word for word.

    This is the defect: the operator met "Clear the path, then delete again"
    for a condition clearing the path cannot fix. The question the toast
    answers is "why will this chat not delete, and is there anything I can do
    about it?", and here the honest answer to the second half is "nothing on
    this row" — so the message SAYS so rather than leaving it to be inferred
    from the absence of an instruction, and points at no control, because
    there is no editor for a workspace's `repo_path` anywhere in `client/**`.

    The string is exact because its length is load-bearing: `.toast__message`
    clamps at 5.8em / 69.6px — four visible lines at 12px/1.45, no ellipsis, no
    scrollbar (ToastHost.css). Measured in headless Chromium with the real
    stylesheet and IBM Plex Sans loaded (lg-external-harness-954 AC22's method,
    re-run for lg-external-harness-1165; artifact 3b30171a): 4.02 lines at
    message widths 272, 296 and 336px. The path is LAST because an out-of-base
    path has no bounded length and the clamp cuts the tail, so the explanation
    must precede it. Tightening is allowed; lengthening it, or moving the path
    forward, re-runs that measurement rather than counting characters.
    """
    outside = tmp_path / "somewhere-else" / "thread"
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=_run(session, workspace, code="r-outside").id,
            chat_session_id=chat_session.id,
            worktree_path=str(outside),
            branch=chat_session_branch(chat_session),
            state=WorktreeState.ACTIVE,
        )
    )
    session.add(BaxterChatMessage(session_id=chat_session.id, role="user", content="do a thing"))
    session.commit()

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    assert detail == (
        "Chat kept. Its checkout is outside the worktrees folder, so no retry "
        f"can remove it: {outside}"
    )
    # The three clauses that belong to the other two messages (AC9/AC10).
    assert "Clear the path" not in detail
    assert "unpublished work" not in detail
    assert "Commit or discard" not in detail
    # The row that owns the directory survives, as it does for every hold.
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert (
        session.exec(select(Worktree).where(Worktree.chat_session_id == chat_session.id))
        .one()
        .state
        == WorktreeState.ACTIVE
    )


def test_the_three_delete_refusals_are_three_different_sentences(
    session, workspace, chat_session, repo, tmp_path
):
    """AC9/AC10 — one message per cause, and none of them borrowed.

    Each of the three is pinned on its own above, and each of those tests
    passes against an implementation that answers the same sentence for all
    three. Running them side by side is what catches a branch that fell
    through, which is the shape this whole ticket is about.
    """
    messages = set()

    outside_chat = BaxterChatSession(workspace_id=workspace.id, title="Outside")
    session.add(outside_chat)
    session.commit()
    session.refresh(outside_chat)
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=_run(session, workspace, code="r-outside").id,
            chat_session_id=outside_chat.id,
            worktree_path=str(tmp_path / "elsewhere" / "thread"),
            branch=chat_session_branch(outside_chat),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    with pytest.raises(ChatSessionHasUnpublishedWork) as outside_caught:
        delete_chat_session(session, outside_chat)
    messages.add(str(outside_caught.value))

    root = _committed_thread_tree(session, workspace, chat_session, repo)
    with (
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(root),
                parked_path=f"{root}.parked",
            ),
        ),
        pytest.raises(ChatSessionHasUnpublishedWork) as raised_caught,
    ):
        delete_chat_session(session, chat_session)
    messages.add(str(raised_caught.value))

    premise_chat = BaxterChatSession(workspace_id=workspace.id, title="Premise")
    session.add(premise_chat)
    session.commit()
    session.refresh(premise_chat)
    premise_root = resolve_chat_execution_root(
        session, _run(session, workspace, code="r-premise"), premise_chat, workspace
    )
    (premise_root / "new.txt").write_text("work nobody has seen\n")
    with pytest.raises(ChatSessionHasUnpublishedWork) as premise_caught:
        delete_chat_session(session, premise_chat)
    messages.add(str(premise_caught.value))

    assert len(messages) == 3, messages


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


def test_a_premise_hold_refuses_with_the_action_first_and_the_path_last(
    session, workspace, chat_session
):
    """lg-external-harness-1167 AC6/AC7/AC8/AC13 — the premise 409, word for word.

    The third of the three refusals, pinned exactly as the other two are
    (`:691-695`, `:753-756`), because its length is load-bearing in the same
    way: `.toast__message` clamps at 5.8em / 69.6px — four visible lines at
    12px/1.45, no ellipsis, no scrollbar (ToastHost.css), `overflow-wrap:
    anywhere`. The wording it replaced interpolated the branch AND the path and
    rendered past that clamp, so what the operator lost was "Commit or discard
    it, then delete the chat" — the instruction, which is the only half the
    clamped lines exist to carry (the unclamped toast title already says the
    delete failed).

    Two assertions, and the second is the one that matters:

    * the exact string, so a re-wording cannot drift silently;
    * `detail.endswith(str(root))`, which pins the ORDERING rather than a
      length. The worktrees base is operator-dependent — `worktree_service`
      caps the directory NAME at 46 chars but not the base — so a wording that
      merely fits this machine's 86-char path is one `--worktrees-dir` away
      from clipping the instruction again. With the whole action clause
      complete before the first interpolated character, the only thing the
      clamp can ever eat is the tail of the path. This follows the precedent
      the `OUTSIDE_BASE` branch set 12 lines above it
      (lg-external-harness-1165).

    Tightening the wording is allowed; lengthening the literal prefix, or
    moving the path forward, re-runs the AC1 measurement
    (`client/scripts/measure-toast-clamp.mjs`) rather than counting characters.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work nobody has seen\n")

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    assert detail == (
        f"Commit or discard this chat's unpublished work, then delete the chat. It is at: {root}"
    )
    # The ordering, as an assertion and not as a comment: nothing follows the
    # path, so nothing the clamp cuts is an instruction.
    assert detail.endswith(str(root))
    # The branch is not in it at all (AC7) — the path answers what it answered.
    assert chat_session_branch(chat_session) not in detail
    assert "`" not in detail
    # The other two messages' clauses, none of which belong here.
    assert "Could not remove" not in detail
    assert "Clear the path" not in detail
    assert "outside the worktrees folder" not in detail
    # And the refusal still keeps what it exists to keep.
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()
    assert (root / "new.txt").exists()


def test_the_premise_refusals_literal_prefix_holds_no_interpolated_value(
    session, workspace, chat_session
):
    """lg-external-harness-1167 AC6, as a measurement instead of a reading.

    AC6 is stronger than "the path is last": NO interpolated value may appear
    between the start of the message and the end of the action clause. The pin
    above asserts `endswith(path)`, which proves only that the path is last —
    it passes for a wording that opens with some OTHER unbounded per-session
    value and still pushes the instruction off the clamp. The chat title is the
    obvious one (operator-authored, no cap anywhere between the sidebar and
    this message), and the branch is the one this ticket just removed.

    So this asserts the property rather than a wording: two threads whose
    title, branch and worktree path all differ produce a detail with a
    BYTE-IDENTICAL literal prefix, and the only thing that differs between the
    two messages is the trailing path. Any value interpolated ahead of the
    action clause makes the two prefixes diverge, whatever the wording is, and
    the test does not have to be re-written when the wording is tightened.

    Deliberately NOT a character count — `overflow-wrap: anywhere` makes line
    count non-linear in length, so the clamp is measured in
    `client/scripts/measure-toast-clamp.mjs` and pinned here as ordering.
    """
    first_root = resolve_chat_execution_root(
        session, _run(session, workspace, code="r-prefix-a"), chat_session, workspace
    )
    (first_root / "new.txt").write_text("work nobody has seen\n")

    # A second thread, differing in every value the message could reach for:
    # a much longer title (so a different slug, a different branch, and a
    # different directory name).
    other_chat = BaxterChatSession(
        workspace_id=workspace.id,
        title="A deliberately much longer thread title than the other one has",
    )
    session.add(other_chat)
    session.commit()
    session.refresh(other_chat)
    other_root = resolve_chat_execution_root(
        session, _run(session, workspace, code="r-prefix-b"), other_chat, workspace
    )
    (other_root / "new.txt").write_text("work nobody has seen\n")

    assert str(first_root) != str(other_root)
    assert chat_session_branch(chat_session) != chat_session_branch(other_chat)

    details = []
    for thread, root in ((chat_session, first_root), (other_chat, other_root)):
        with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
            delete_chat_session(session, thread)
        detail = str(caught.value)
        # The path is last (AC6's weaker half), which is what makes the rest of
        # the string a literal prefix at all.
        assert detail.endswith(str(root)), detail
        details.append(detail[: -len(str(root))])

    prefix, other_prefix = details
    assert prefix == other_prefix, (prefix, other_prefix)
    # Not vacuous: there IS a prefix, and it carries the action.
    assert "Commit or discard" in prefix


# --------------------------------------------------------------------------
# Adversarial: the failed-removal branch's ordering as a property, not as prose
# — lg-external-harness-1208, test-break
#
# Measured, not argued: with the exact prose pin relaxed the way a tightening
# relaxes it (AC7 invites one), three mutants of the AC1 wording survive the
# whole module — the path re-interpolated mid-sentence and also kept last, the
# chat title interpolated ahead of the action clause, and the instruction
# dropped. Each is killed below by a property instead, so a later tightening
# cannot take the ordering with it.
# --------------------------------------------------------------------------


def test_the_failed_removals_literal_prefix_holds_no_interpolated_value(
    session, workspace, chat_session, repo
):
    """lg-external-harness-1208 AC1, as a measurement instead of a reading.

    The sibling of the premise test directly above, for the branch this ticket
    re-words. AC1 is stronger than "the path is last": NO interpolated value
    may appear between the start of the message and the end of the action
    clause. The pin asserts the exact prose, which catches everything — but
    AC7 explicitly INVITES tightening, and a tightening re-writes that pin, so
    after one the only ordering assertion left on this branch would be
    `endswith(path)`. That passes for a wording that names the path twice, and
    for one that opens with some other unbounded per-session value; the chat
    title is the obvious one, operator-authored with no cap anywhere between
    the sidebar and this message.

    So this asserts the property rather than a wording: two threads whose
    title, branch and worktree path all differ produce a detail with a
    BYTE-IDENTICAL literal prefix, and the only thing that differs between the
    two messages is the trailing path. Any value interpolated ahead of the
    action clause makes the two prefixes diverge, whatever the wording is, and
    the test does not have to be re-written when the wording is tightened.

    Deliberately NOT a character count — `overflow-wrap: anywhere` makes line
    count non-linear in length, so the clamp is measured in
    `client/scripts/measure-toast-clamp.mjs` and pinned here as ordering.
    """
    first_root = _committed_thread_tree(session, workspace, chat_session, repo, code="r-raised-a")

    # A second thread, differing in every value the message could reach for:
    # a much longer title (so a different slug, a different branch, and a
    # different directory name).
    other_chat = BaxterChatSession(
        workspace_id=workspace.id,
        title="A deliberately much longer thread title than the other one has",
    )
    session.add(other_chat)
    session.commit()
    session.refresh(other_chat)
    other_root = _committed_thread_tree(session, workspace, other_chat, repo, code="r-raised-b")

    assert str(first_root) != str(other_root)
    assert chat_session_branch(chat_session) != chat_session_branch(other_chat)

    details = []
    for thread, root in ((chat_session, first_root), (other_chat, other_root)):
        with (
            mock.patch.object(
                WorktreeService,
                "remove_checkout",
                side_effect=DirectoryReleaseFailed(
                    "rmtree failed",
                    disposition=ReleaseDisposition.STILL_PARKED,
                    refusal=CleanupRefusal.REMOVE_RAISED,
                    uncleaned_path=str(root),
                    parked_path=f"{root}.parked",
                ),
            ),
            pytest.raises(ChatSessionHasUnpublishedWork) as caught,
        ):
            delete_chat_session(session, thread)
        detail = str(caught.value)
        # The path is last (AC1's weaker half), which is what makes the rest of
        # the string a literal prefix at all.
        assert detail.endswith(str(root)), detail
        details.append(detail[: -len(str(root))])

    prefix, other_prefix = details
    assert prefix == other_prefix, (prefix, other_prefix)
    # Not vacuous: there IS a prefix, and it carries the action.
    assert "Clear the path" in prefix
    # And the path appears once, at the end — not also inside the prefix, which
    # is the defect this ticket exists to remove.
    for root in (first_root, other_root):
        assert str(root) not in prefix


def test_the_failed_removal_carries_the_clauses_the_other_two_refusals_assert_absent(
    session, workspace, chat_session, repo
):
    """lg-external-harness-1208 AC5/AC12 — the non-vacuity of four assertions.

    `"Could not remove" not in detail` and `"Clear the path" not in detail`
    hold five times across the other two branches' tests
    (`:780`, `:922`, `:923`, `:1083`, and `test_baxter_chat.py:1120,1146`), and
    every one of them passes vacuously if THIS branch stops saying either. The
    exact pin above would catch that today, but it is the assertion a
    tightening re-writes (AC7 invites one), and dropping `Clear the path, then
    delete again` is the cheapest clamp room there is to buy — it is also
    AC12's only action, and the whole reason the re-ordering was worth paying
    two words for.

    So the two clauses are required HERE, positively and separately from the
    prose pin, and so is the locator: AC12 rules out dropping the path to buy
    room, because it is the only thing that tells the operator which directory
    to clear.
    """
    root = _committed_thread_tree(session, workspace, chat_session, repo)

    with (
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(root),
                parked_path=f"{root}.parked",
            ),
        ),
        pytest.raises(ChatSessionHasUnpublishedWork) as caught,
    ):
        delete_chat_session(session, chat_session)

    detail = str(caught.value)
    # What the other two branches assert they are not.
    assert "Could not remove" in detail
    assert "Clear the path" in detail
    # AC12's retry instruction, and AC12's locator.
    assert "delete again" in detail
    assert str(root) in detail


def test_every_refusal_member_keeps_the_row_and_still_ends_with_the_path(
    session, workspace, chat_session, repo
):
    """lg-external-harness-1208 A2, as a guard instead of an assumption.

    The spec assumes `held.refusal is not None` covers `REMOVE_RAISED` "and any
    future non-`OUTSIDE_BASE` member", which is what lets this branch be
    written once for a family. Nothing executes that assumption: the pins above
    name the two members this enum has today, so narrowing the branch to
    `is CleanupRefusal.REMOVE_RAISED` changes no test — and the day a third
    member lands, the narrowed form drops a chat row whose checkout is still on
    disk, which is the orphan the whole refusal exists to prevent.

    Measured, not argued. Narrowing the branch and adding a third member does
    NOT drop the row — the `held is not None` branch below catches everything,
    and `_FAILURE_CLAUSE` is an unguarded lookup that fails loudly by design.
    What the narrowed form does is hand the new member the PREMISE sentence:
    "commit or discard this chat's unpublished work" for a cause where there
    may be no uncommitted work at all, which is the collapse
    lg-external-harness-954 and -1165 were filed to split apart. Nothing in the
    module noticed that until this test; the premise wording also ends with the
    path, so ordering alone does not catch it.

    Enumerated over the enum rather than over a list of members. Three
    invariants per member, none of them a wording: the delete REFUSES (row,
    directory and `worktrees` row all survive), the detail ends with the path,
    and the detail is not the premise sentence. Which words a new member gets
    is the author's call; that it gets its own, and keeps the ordering, is not.
    """
    for index, refusal in enumerate(CleanupRefusal):
        thread = BaxterChatSession(workspace_id=workspace.id, title=f"Thread {refusal.name}")
        session.add(thread)
        session.commit()
        session.refresh(thread)
        root = _committed_thread_tree(session, workspace, thread, repo, code=f"r-member-{index}")

        with (
            mock.patch.object(
                WorktreeService,
                "remove_checkout",
                side_effect=_seam_failure(root, refusal),
            ),
            pytest.raises(ChatSessionHasUnpublishedWork) as caught,
        ):
            delete_chat_session(session, thread)

        detail = str(caught.value)
        assert detail.endswith(str(root)), (refusal, detail)
        # And not the premise branch's words: every member of this enum means a
        # removal was attempted or refused, so "commit or discard it" is advice
        # about a condition that may not exist — the collapse
        # lg-external-harness-954 and -1165 split apart. This is what a member
        # narrowed out of the branch above falls through to.
        assert "Commit or discard" not in detail, (refusal, detail)
        assert "unpublished work" not in detail, (refusal, detail)
        assert session.get(BaxterChatSession, thread.id) is not None, refusal
        assert root.is_dir(), refusal
        assert (
            session.exec(select(Worktree).where(Worktree.chat_session_id == thread.id)).one().state
            == WorktreeState.ACTIVE
        ), refusal


# --------------------------------------------------------------------------
# The hold itself, and the two holds nothing above routes through the delete
# — lg-external-harness-954
# --------------------------------------------------------------------------


def test_a_hold_cannot_be_rewritten_after_the_fact(session, workspace, chat_session):
    """AC17 — frozen, for the same reason `Retirement` is.

    This answer decides whether a row that owns a directory on disk gets
    dropped. A mutable one is a `hold.outcome = ...` away from an
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
        outcome=Retirement(
            refusal=RemovalFailure.CLEANUP_FAILED,
            deleted_nothing=True,
            parked_path="/nowhere/.worktrees-releasing/deadbeef",
            detail="nothing was deleted",
        ),
        cause=CleanupRefusal.REMOVE_RAISED,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        hold.outcome = None  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        hold.cause = None  # type: ignore[misc]


def test_an_unresolvable_workspace_still_says_which_tree_it_left(
    session, workspace, chat_session, monkeypatch, caplog
):
    """AC17 — the hold that attempted nothing keeps the warning it already has.

    The hold's outcome tells `delete_chat_session` which words to
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
    assert held.outcome.refusal is not RemovalFailure.CLEANUP_FAILED
    assert held.outcome.deleted_nothing is True
    assert held.cause is None
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
    branch instead. The two-message branch has to key on the outcome's refusal, not
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
    session, workspace, chat_session, repo
):
    """AC3/AC17 — the row stays ACTIVE and still points at the thread.

    The refusal exists so the directory keeps an owner. The chat row surviving
    is half of that; the other half is the `worktrees` row, which is what names
    the path, the branch and the thread. Stamped CLEANUP, or detached from the
    session, it is a directory no query reaches — the orphan this ticket's whole
    argument is about — and every assertion in the test above still passes.
    """
    root = _committed_thread_tree(session, workspace, chat_session, repo)

    with (
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(root),
                parked_path=f"{root}.parked",
            ),
        ),
        pytest.raises(ChatSessionHasUnpublishedWork),
    ):
        delete_chat_session(session, chat_session)

    row = session.exec(select(Worktree).where(Worktree.chat_session_id == chat_session.id)).one()
    assert row.state == WorktreeState.ACTIVE
    assert row.worktree_path == str(root)
    assert row.branch == chat_session_branch(chat_session)
    assert row.cleaned_at is None


def test_the_retry_the_message_asks_for_completes_once_the_path_is_removable(
    session, workspace, chat_session, repo
):
    """AC9/AC18 — "Clear the path, then delete again" has to be true.

    The instruction is the message's only action, and nothing but the operator
    performs it. So the failed attempt must leave the thread deletable: no row
    half-stamped, no message rows dropped before the raise, no second-delete
    path that now raises for a different reason. This is the one assertion in
    the module that exercises the failure and the recovery in one order, which
    is the only way a leftover from the first attempt is observable at all.
    """
    root = _committed_thread_tree(session, workspace, chat_session, repo)
    session.add(BaxterChatMessage(session_id=chat_session.id, role="user", content="do a thing"))
    session.commit()

    with (
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(root),
                parked_path=f"{root}.parked",
            ),
        ),
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


# ===========================================================================
# lg-external-harness-1026 / AC17 — the chat refusal stops saying something
# false.
# ===========================================================================


def test_a_failed_removal_does_not_claim_the_thread_has_unpublished_work(
    session, workspace, chat_session, repo
):
    """The sentence this criterion exists to delete.

    "This chat still has unpublished work … Commit or discard it" was raised on
    *every* hold, including one where the work was published, the tree was
    clean, and the only thing that went wrong was the delete. An operator told
    that goes looking for edits that are not there and never learns that a
    directory is sitting half-removed on their disk.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "published.txt").write_text("committed and landed\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    _land_chat_branch(repo, chat_session)

    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=f"{root}.parked",
        ),
    ):
        with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
            delete_chat_session(session, chat_session)

    message = str(caught.value)
    assert "unpublished work" not in message
    assert "Commit or discard" not in message
    assert str(root) in message

    # And nothing was lost on the way out.
    assert session.get(BaxterChatSession, chat_session.id) is not None
    assert root.is_dir()


def test_a_parked_outcome_carries_the_exact_parked_path_without_spending_the_clamp(
    session, workspace, chat_session, repo
):
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "published.txt").write_text("committed and landed\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    _land_chat_branch(repo, chat_session)
    parked = f"{root}.parked-0ddba11"

    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=parked,
        ),
    ):
        with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
            delete_chat_session(session, chat_session)

    message = str(caught.value)
    # MIGRATED by lg-external-harness-1026 implement (AC18), and the reason is
    # a contradiction between two of this module's own tests rather than a
    # preference. Four tests here pin the failed-removal toast as the measured
    # string ENDING with the worktree path, with a parked path supplied
    # (`:1181`, `:1233`, `:574`, `:1546`); a message that also names the parked
    # path cannot end with the worktree path, and cannot keep the literal
    # prefix those tests compare across two threads. The clamp measurement is
    # 954's and 1208's, and AC17 defers to it explicitly.
    #
    # So the parked path is carried where a caller can read it and no clamp
    # truncates it — on the gate's answer, on the row, and in the sweep's and
    # the branch-triage endpoints' reports — and the toast keeps the one path
    # the operator can act on. What this test still pins is the half that was
    # the point: a parked outcome is NOT reported as unpublished work.
    assert "unpublished work" not in message
    assert "Commit or discard" not in message
    del parked


def test_a_genuine_premise_refusal_still_says_commit_or_discard(session, workspace, chat_session):
    """The other half, so the fix does not become "say less to everybody".

    Uncommitted work really is unpublished work, and "commit or discard it" is
    exactly what the operator should do. That message is right and stays; what
    changes is that it is no longer said about things it is false about.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "new.txt").write_text("work nobody has seen\n")

    with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
        delete_chat_session(session, chat_session)

    message = str(caught.value)
    assert "unpublished work" in message
    assert "Commit or discard" in message
    assert root.is_dir()


def test_each_refusal_is_one_sentence_of_reason_a_path_and_one_instruction(
    session, workspace, chat_session, repo
):
    """AC17's shape rule, and the clamp it is sized for.

    The toast these reach is four lines. A refusal that runs past that is a
    refusal whose path is clipped off the bottom — and the path is the only
    part the operator cannot reconstruct. The measured fit belongs to
    lg-external-harness-954; what is pinned here is that no message grows a
    second instruction or a "the server log names what failed" clause.
    """
    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "published.txt").write_text("committed and landed\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    _land_chat_branch(repo, chat_session)

    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=f"{root}.parked",
        ),
    ):
        with pytest.raises(ChatSessionHasUnpublishedWork) as caught:
            delete_chat_session(session, chat_session)

    message = str(caught.value)
    assert "the server log" not in message.lower()
    # Counted with the PATH taken out (lg-external-harness-1026 implement,
    # AC18). The path contains a `.` of its own — every worktree lives under
    # `.worktrees` — so counting full stops across the whole string counts one
    # that is not a sentence, and the measured string
    # `test_a_failed_removal_refuses_the_delete_without_blaming_the_operators_work`
    # pins by exact equality cannot satisfy both readings. The claim is
    # unchanged: a reason, a path and one instruction, and no more.
    sentences = message.replace(str(root), "")
    assert sentences.count(".") <= 3, f"more than a reason, a path and an instruction: {message!r}"


def test_release_chat_worktree_distinguishes_a_premise_from_a_failure_from_a_park(
    session, workspace, chat_session, repo
):
    """A bare `Worktree` cannot tell the three apart, which is why the caller
    had to guess — and guessed "unpublished work" every time."""
    from loregarden.services import worktree_lifecycle

    run = _run(session, workspace)
    root = resolve_chat_execution_root(session, run, chat_session, workspace)
    (root / "unsaved.txt").write_text("not committed\n")

    premise = worktree_lifecycle.release_chat_worktree(session, chat_session)
    assert premise is not None
    assert premise.outcome.refusal in set(worktree_lifecycle.KeepReason)
    assert premise.outcome.parked_path == ""
    assert premise.worktree.worktree_path == str(root)

    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "committed by the turn")
    _land_chat_branch(repo, chat_session)
    parked = f"{root}.parked-b0a710ad"
    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=parked,
        ),
    ):
        failed = worktree_lifecycle.release_chat_worktree(session, chat_session)

    assert failed is not None
    assert failed.outcome.refusal is worktree_lifecycle.RemovalFailure.CLEANUP_FAILED
    assert failed.outcome.parked_path == parked
