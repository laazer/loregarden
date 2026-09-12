"""Publishing follows the reply; it does not hold it up.

`push` runs lefthook's pre-push suite — 37 minutes, measured, for a change
reaching a widely-imported backend module. While an assistant row is `pending`
the composer is disabled, so publishing inside the turn would freeze the chat for
exactly that long. The reply settles first and the publish result arrives as a
message of its own.

These pin the ordering, which is the whole point of the design and is invisible
in any test that only checks the end state.
"""

from __future__ import annotations

import threading
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    BaxterChatMessage,
    BaxterChatSession,
    RunStatus,
    Workspace,
)
from loregarden.services import baxter_chat_run_service as runsvc
from loregarden.services.chat_publish import TurnPublishOutcome
from sqlmodel import Session, select
from tests.worktree_helpers import make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="chat")
def chat_fixture(session, tmp_path):
    workspace = Workspace(slug="loregarden", name="loregarden", repo_path=str(make_repo(tmp_path)))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    chat_session = BaxterChatSession(workspace_id=workspace.id, title="Rename the editor")
    run = AgentRun(
        run_code="r1",
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key="home-chat",
        status=RunStatus.SUCCEEDED,
    )
    session.add(chat_session)
    session.add(run)
    session.commit()
    session.refresh(chat_session)
    session.refresh(run)
    return chat_session, run


def _system_messages(session, chat_session_id: str) -> list[BaxterChatMessage]:
    return list(
        session.exec(
            select(BaxterChatMessage).where(
                BaxterChatMessage.session_id == chat_session_id,
                BaxterChatMessage.role == "system",
            )
        ).all()
    )


def test_the_result_lands_in_the_thread_as_its_own_message(session, chat, isolated_db, tmp_path):
    chat_session, run = chat
    outcome = TurnPublishOutcome(
        repo_root=tmp_path,
        branch="chat/rename-the-editor-abcd1234",
        dirty=True,
        automation=None,
    )
    with (
        mock.patch.object(runsvc, "engine", isolated_db),
        mock.patch.object(runsvc, "publish_chat_turn", return_value=outcome),
    ):
        runsvc.execute_chat_publish_background(chat_session.id, run.id)

    messages = _system_messages(session, chat_session.id)
    assert len(messages) == 1
    assert "chat/rename-the-editor-abcd1234" in messages[0].content
    # A message of its own, so no leading rule across an empty bubble.
    assert not messages[0].content.startswith("\n\n---")


def test_a_turn_that_changed_nothing_posts_no_message(session, chat, isolated_db, tmp_path):
    """Silence is the correct output for a turn that only answered a question."""
    chat_session, run = chat
    clean = TurnPublishOutcome(repo_root=tmp_path, branch="chat/x", dirty=False, automation=None)
    with (
        mock.patch.object(runsvc, "engine", isolated_db),
        mock.patch.object(runsvc, "publish_chat_turn", return_value=clean),
    ):
        runsvc.execute_chat_publish_background(chat_session.id, run.id)

    assert _system_messages(session, chat_session.id) == []


def test_a_crash_while_publishing_still_reaches_the_thread(session, chat, isolated_db):
    """The one thing that must not happen quietly.

    The reply has already settled, so a failure here wedges nothing — it just
    loses the only record of where the operator's work went.
    """
    chat_session, run = chat
    with (
        mock.patch.object(runsvc, "engine", isolated_db),
        mock.patch.object(runsvc, "publish_chat_turn", side_effect=RuntimeError("remote hung up")),
    ):
        runsvc.execute_chat_publish_background(chat_session.id, run.id)

    messages = _system_messages(session, chat_session.id)
    assert len(messages) == 1
    assert "remote hung up" in messages[0].content
    assert "still in the thread's checkout" in messages[0].content


def test_two_turns_in_one_thread_do_not_run_git_concurrently(chat, isolated_db):
    """One worktree, so one publish at a time.

    `git add -A` / `commit` / `push` in the same directory from two threads is a
    lock fight at best. The second waits rather than skipping: it may be carrying
    edits the in-flight publish started too early to see.
    """
    chat_session, run = chat
    overlapped = False
    inside = threading.Event()
    release = threading.Event()

    def slow_publish(*_args, **_kwargs):
        nonlocal overlapped
        if inside.is_set():
            overlapped = True
        inside.set()
        release.wait(timeout=5)
        inside.clear()
        return None

    with (
        mock.patch.object(runsvc, "engine", isolated_db),
        mock.patch.object(runsvc, "publish_chat_turn", side_effect=slow_publish),
    ):
        first = threading.Thread(
            target=runsvc.execute_chat_publish_background, args=(chat_session.id, run.id)
        )
        first.start()
        assert inside.wait(timeout=5), "first publish never started"
        second = threading.Thread(
            target=runsvc.execute_chat_publish_background, args=(chat_session.id, run.id)
        )
        second.start()
        # The second must be blocked on the lock, not running alongside.
        second.join(timeout=0.5)
        assert second.is_alive()
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert not overlapped


def test_the_reply_settles_before_publishing_is_even_scheduled(session, chat, isolated_db):
    """The ordering, asserted directly rather than inferred from the end state.

    A test that only checks "the reply is complete and a system message exists"
    passes just as well with publishing back inside the turn. What matters is
    that the assistant row is out of `pending` — which is what re-enables the
    composer — at the moment the publish job is handed off.
    """
    from loregarden.services.baxter_chat_service import ChatTurnOutcome

    chat_session, run = chat
    user = BaxterChatMessage(session_id=chat_session.id, role="user", content="do the thing")
    assistant = BaxterChatMessage(
        session_id=chat_session.id, role="assistant", content="", status="pending"
    )
    session.add(user)
    session.add(assistant)
    session.commit()
    session.refresh(assistant)
    assistant_id = assistant.id

    status_when_scheduled: list[str] = []

    def record(_chat_session_id: str, _run_id: str) -> None:
        with Session(isolated_db) as probe:
            row = probe.get(BaxterChatMessage, assistant_id)
            status_when_scheduled.append(row.status if row else "missing")

    with (
        mock.patch.object(runsvc, "engine", isolated_db),
        mock.patch.object(
            runsvc,
            "invoke_baxter_chat_model",
            return_value=ChatTurnOutcome(reply="done", run_id=run.id, acting=True),
        ),
        mock.patch.object(runsvc, "schedule_chat_publish", side_effect=record),
    ):
        runsvc.execute_baxter_chat_turn_background(assistant_id)

    assert status_when_scheduled == ["complete"], (
        "publishing was scheduled while the assistant row was still pending, "
        "which is what disables the composer"
    )


def test_an_advisory_turn_schedules_no_publish(session, chat, isolated_db):
    """Nothing was written, so there is no tree to commit and no job to run."""
    from loregarden.services.baxter_chat_service import ChatTurnOutcome

    chat_session, run = chat
    session.add(BaxterChatMessage(session_id=chat_session.id, role="user", content="what is x?"))
    assistant = BaxterChatMessage(
        session_id=chat_session.id, role="assistant", content="", status="pending"
    )
    session.add(assistant)
    session.commit()
    session.refresh(assistant)

    with (
        mock.patch.object(runsvc, "engine", isolated_db),
        mock.patch.object(
            runsvc,
            "invoke_baxter_chat_model",
            return_value=ChatTurnOutcome(reply="x is x", run_id=run.id, acting=False),
        ),
        mock.patch.object(runsvc, "schedule_chat_publish") as scheduled,
    ):
        runsvc.execute_baxter_chat_turn_background(assistant.id)

    scheduled.assert_not_called()
