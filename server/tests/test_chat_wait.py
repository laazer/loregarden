"""Chat wait/resume: one table, one tool, one poller. Spec R2–R7.

A chat turn that backgrounds work registers a pid; process exit starts a new
AgentRun on the same thread; Stop/timeout/cap/boot-UNKNOWN write a Loregarden
system message. Never drop. The named regression is
``test_triage_register_then_child_exit_starts_second_agent_run``.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.agents.executors.approval_scope import HOME_CHAT_STAGE_KEY
from loregarden.agents.executors.permission_bridge import BRANCH_TRIAGE_STAGE_KEY
from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    ORCHESTRATED_DENIED_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    McpTool,
)
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    BaxterChatMessage,
    BaxterChatSession,
    BranchTriageMessage,
    ChatSurface,
    ProcessState,
    RunStatus,
    Ticket,
    TriageMessage,
    Workspace,
)
from loregarden.models.domain.chat_wait_enums import (
    ChatWaitRefuseReason,
    ChatWaitStatus,
    ChatWaitUnknownPolicy,
)
from loregarden.models.domain.chat_wait_tables import ChatWait
from loregarden.models.domain.enums import utcnow
from loregarden.services.baxter_chat_run_service import (
    cancel_baxter_chat_turn,
    start_baxter_chat_turn,
)
from loregarden.services.baxter_chat_service import (
    BaxterChatConflictError,
    baxter_chat_run_status,
)
from loregarden.services.branch_triage_chat_service import branch_triage_run_status
from loregarden.services.branch_triage_run_service import (
    BranchTriageConflictError,
    cancel_branch_triage_turn,
    start_branch_triage_run,
)
from loregarden.services.chat_wait import (
    MAX_CHAT_WAIT_CONTINUATIONS,
    WAIT_CEILING_SECONDS,
    continuation_blocks,
    outstanding_for,
    register,
    take_continuation,
    thread_key,
)
from loregarden.services.chat_wait_watch import poll_chat_waits, resume_chat_waits
from loregarden.services.process_identity import identify, session_of
from loregarden.services.run_concurrency import TRIAGE_STAGE_KEY, new_run_code
from loregarden.services.triage_run_service import (
    TriageConflictError,
    cancel_triage_turn,
    start_triage_run,
)
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    build_triage_prompt,
    list_triage_messages,
    triage_run_status,
    triage_snapshot,
)
from sqlmodel import Session, col, select
from tests.factories import make_workspace_ticket

STOP_COPY = (
    "Stopped waiting for {command} at your request. "
    "The process was not killed — ask again, or watch {log_path}."
)
TIMEOUT_COPY = (
    "Gave up waiting for {command} after 2 hours. "
    "The process was not killed — ask again, or watch {log_path}."
)
CAP_COPY = (
    "This thread has already continued 3 times from background work. "
    "{command} finished but another turn was not started — ask again, or watch {log_path}."
)
BOOT_UNKNOWN_COPY = (
    "Lost track of {command} after a server restart. "
    "It may still be running — ask again, or watch {log_path}."
)

#: Paths below are repo-relative; pytest runs from server/, CI included.
_REPO_ROOT = Path(__file__).resolve().parents[2]

COMMAND = "git push origin HEAD"
BRANCH = "chat-wait-branch"


# ---- fixtures -----------------------------------------------------------


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "chat-wait-ticket")


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session, ticket: Ticket) -> Workspace:
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    return workspace


@contextmanager
def _child(*, new_session: bool):
    proc = subprocess.Popen(["sleep", "60"], start_new_session=new_session)
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def _log(tmp_path: Path, *, body: str = "ok\n", rc: int | None = 0) -> Path:
    path = tmp_path / "job.log"
    path.write_text(body, encoding="utf-8")
    if rc is not None:
        path.with_name(path.name + ".rc").write_text(str(rc), encoding="utf-8")
    return path


def _succeed_run(db_session: Session, run: AgentRun) -> None:
    """Origin turn finished: run SUCCEEDED and its pending assistant settled."""
    run.status = RunStatus.SUCCEEDED
    db_session.add(run)
    home = db_session.exec(
        select(BaxterChatMessage).where(
            BaxterChatMessage.run_id == run.id,
            BaxterChatMessage.status == "pending",
        )
    ).first()
    if home is not None:
        home.status = "complete"
        db_session.add(home)
    branch = db_session.exec(
        select(BranchTriageMessage).where(
            BranchTriageMessage.run_id == run.id,
            BranchTriageMessage.status == "pending",
        )
    ).first()
    if branch is not None:
        branch.status = "complete"
        db_session.add(branch)
    db_session.commit()
    db_session.refresh(run)


def _stamp_agent(run: AgentRun, pid: int, db_session: Session) -> None:
    reading = identify(pid)
    run.agent_pid = pid
    run.agent_pid_identity = reading.stamp
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)


def _running_triage(db_session: Session, ticket: Ticket) -> AgentRun:
    run = AgentRun(
        run_code="run_wait_triage",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=TRIAGE_AGENT_ID,
        stage_key=TRIAGE_STAGE_KEY,
        status=RunStatus.RUNNING,
        auto_approve=True,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    _stamp_agent(run, os.getpid(), db_session)
    return run


def _home_session(db_session: Session, workspace: Workspace) -> BaxterChatSession:
    chat = BaxterChatSession(workspace_id=workspace.id, title="wait home")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return chat


def _bind_pending_home(
    db_session: Session, chat: BaxterChatSession, run: AgentRun
) -> BaxterChatMessage:
    message = BaxterChatMessage(
        session_id=chat.id,
        role="assistant",
        content="",
        status="pending",
        run_id=run.id,
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def _bind_pending_branch(
    db_session: Session, workspace: Workspace, run: AgentRun, *, branch: str
) -> BranchTriageMessage:
    message = BranchTriageMessage(
        workspace_id=workspace.id,
        branch=branch,
        role="assistant",
        content="",
        status="pending",
        run_id=run.id,
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def _running_home(db_session: Session, workspace: Workspace, chat: BaxterChatSession) -> AgentRun:
    run = AgentRun(
        run_code=new_run_code(),
        ticket_id=None,
        workspace_id=workspace.id,
        agent_id=TRIAGE_AGENT_ID,
        stage_key=HOME_CHAT_STAGE_KEY,
        status=RunStatus.RUNNING,
        auto_approve=True,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    _stamp_agent(run, os.getpid(), db_session)
    _bind_pending_home(db_session, chat, run)
    return run


def _running_branch(db_session: Session, workspace: Workspace, *, branch: str = BRANCH) -> AgentRun:
    run = AgentRun(
        run_code=new_run_code(),
        ticket_id=None,
        workspace_id=workspace.id,
        agent_id=TRIAGE_AGENT_ID,
        stage_key=BRANCH_TRIAGE_STAGE_KEY,
        status=RunStatus.RUNNING,
        auto_approve=True,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    _stamp_agent(run, os.getpid(), db_session)
    _bind_pending_branch(db_session, workspace, run, branch=branch)
    return run


def _payload(raw: str) -> dict:
    return json.loads(raw)


def _mcp(session: Session, arguments: dict, *, orchestrated: bool = False) -> dict:
    return _payload(
        execute_tool(
            session,
            McpTool.REGISTER_CHAT_WAIT,
            normalize_tool_arguments(McpTool.REGISTER_CHAT_WAIT, arguments),
            orchestrated=orchestrated,
        )
    )


def _system_triage(session: Session, ticket_id: str) -> list[TriageMessage]:
    return list(
        session.exec(
            select(TriageMessage).where(
                TriageMessage.ticket_id == ticket_id,
                TriageMessage.role == "system",
            )
        ).all()
    )


def _runs_for_ticket(session: Session, ticket_id: str) -> list[AgentRun]:
    return list(
        session.exec(
            select(AgentRun)
            .where(AgentRun.ticket_id == ticket_id)
            .order_by(col(AgentRun.created_at).asc())
        ).all()
    )


# ---- R2.3 constants -----------------------------------------------------


def test_wait_bounds_are_armed_literals_not_a_rework_import():
    """R2.3. A pipeline-loop retune must not silently retune chat."""
    from loregarden.services import chat_wait as chat_wait_mod

    assert WAIT_CEILING_SECONDS == 7200
    assert MAX_CHAT_WAIT_CONTINUATIONS == 3
    text = Path(chat_wait_mod.__file__).read_text(encoding="utf-8")
    assert "MAX_REWORK_REROUTES" not in text
    assert "WAIT_CEILING_SECONDS = 7200" in text
    assert "MAX_CHAT_WAIT_CONTINUATIONS = 3" in text


def test_baxter_chat_message_has_additive_run_id():
    """R2.7. Matches BranchTriageMessage.run_id; origin turns mint AgentRun."""
    assert "run_id" in BaxterChatMessage.model_fields


# ---- R2.2 thread_key ----------------------------------------------------


def test_thread_key_is_session_ticket_or_slug_unit_separator_branch(ticket, workspace):
    chat_id = "sess-wait"
    assert thread_key(ChatSurface.HOME, chat_session_id=chat_id) == chat_id
    assert thread_key(ChatSurface.TICKET_TRIAGE, ticket_id=ticket.id) == ticket.id
    assert (
        thread_key(
            ChatSurface.BRANCH_TRIAGE,
            workspace_slug=workspace.slug,
            branch=BRANCH,
        )
        == f"{workspace.slug}\x1f{BRANCH}"
    )


def test_thread_key_rejects_studio_and_planner():
    with pytest.raises(ValueError):
        thread_key(ChatSurface.TICKET_STUDIO, ticket_id="nope")
    with pytest.raises(ValueError):
        thread_key(ChatSurface.INITIATIVE_PLANNER, ticket_id="nope")


# ---- R2 register contract -----------------------------------------------


def test_detached_alive_pid_registers_an_outstanding_row(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )
        assert result["ok"] is True
        assert result["wait_id"]
        assert result["deadline"]
        row = db_session.get(ChatWait, result["wait_id"])
        assert row is not None
        assert row.status is ChatWaitStatus.OUTSTANDING
        assert row.surface is ChatSurface.TICKET_TRIAGE
        assert row.thread_key == ticket.id
        assert row.origin_run_id == origin.id
        assert row.pid == child.pid
        assert row.pid_identity
        assert row.command == COMMAND
        assert row.log_path == str(log_path)
        assert row.deadline == row.registered_at + timedelta(seconds=7200)


def test_a_child_spawned_like_the_permission_bridge_is_same_session(db_session, ticket, tmp_path):
    """R2 risk: permission_bridge Popen has no start_new_session."""
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=False) as child:
        assert session_of(child.pid) == os.getsid(os.getpid())
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )
        assert result["ok"] is False
        assert result["error_kind"] == ChatWaitRefuseReason.SAME_SESSION.value
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_a_child_spawned_like_agent_spawn_is_accepted_when_not_the_agent(
    db_session, ticket, tmp_path
):
    """agent_spawn._spawn_detached uses start_new_session=True."""
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert session_of(child.pid) != os.getsid(os.getpid())
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )
        assert result["ok"] is True


def test_registering_the_originating_agent_pid_is_same_session(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        origin.agent_pid = child.pid
        db_session.add(origin)
        db_session.commit()
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )
        assert result["ok"] is False
        assert result["error_kind"] == ChatWaitRefuseReason.SAME_SESSION.value


def test_missing_agent_pid_cannot_prove_detach(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    origin.agent_pid = None
    db_session.add(origin)
    db_session.commit()
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )
        assert result["ok"] is False
        assert result["error_kind"] == ChatWaitRefuseReason.SAME_SESSION.value


def test_session_of_none_on_the_child_is_same_session(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        with patch("loregarden.services.chat_wait.session_of", return_value=None):
            result = register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )
        assert result["ok"] is False
        assert result["error_kind"] == ChatWaitRefuseReason.SAME_SESSION.value


def test_gone_pid_is_not_alive(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    result = register(
        db_session,
        pid=2147483000,
        command=COMMAND,
        log_path=str(_log(tmp_path)),
        ticket_id=ticket.id,
    )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.NOT_ALIVE.value
    assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_empty_stamp_from_identify_is_not_a_row(db_session, ticket, tmp_path):
    """R2: do not copy record_process_identity's swallow."""
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        fake = type(identify(os.getpid()))(ProcessState.ALIVE, "")
        with patch("loregarden.services.chat_wait.identify", return_value=fake):
            result = register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )
        assert result["ok"] is False
        assert result["error_kind"] == ChatWaitRefuseReason.NOT_ALIVE.value


def test_second_register_on_the_same_thread_is_already_waiting(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as first, _child(new_session=True) as second:
        assert (
            register(
                db_session,
                pid=first.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        result = register(
            db_session,
            pid=second.pid,
            command="pytest -q",
            log_path=str(log_path),
            ticket_id=ticket.id,
        )
        assert result["ok"] is False
        assert result["error_kind"] == ChatWaitRefuseReason.ALREADY_WAITING.value
        rows = db_session.exec(
            select(ChatWait).where(
                ChatWait.thread_key == ticket.id,
                ChatWait.status == ChatWaitStatus.OUTSTANDING,
            )
        ).all()
        assert len(rows) == 1


def test_queued_or_awaiting_permission_is_not_an_in_flight_turn(db_session, ticket, tmp_path):
    for status in (RunStatus.QUEUED, RunStatus.AWAITING_PERMISSION, RunStatus.SUCCEEDED):
        run = AgentRun(
            run_code=f"run_{status.value}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id=TRIAGE_AGENT_ID,
            stage_key=TRIAGE_STAGE_KEY,
            status=status,
        )
        db_session.add(run)
        db_session.commit()
        _stamp_agent(run, os.getpid(), db_session)
        with _child(new_session=True) as child:
            result = register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(_log(tmp_path)),
                ticket_id=ticket.id,
            )
        assert result["ok"] is False, status
        assert result["error_kind"] == ChatWaitRefuseReason.NO_IN_FLIGHT_TURN.value
        db_session.delete(run)
        db_session.commit()


def test_missing_log_path_and_command_and_locator_are_named_refusals(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path="",
                ticket_id=ticket.id,
            )["error_kind"]
            == ChatWaitRefuseReason.MISSING_LOG_PATH.value
        )
        assert (
            register(
                db_session,
                pid=child.pid,
                command="",
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["error_kind"]
            == ChatWaitRefuseReason.MISSING_COMMAND.value
        )
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
            )["error_kind"]
            == ChatWaitRefuseReason.INVALID_LOCATOR.value
        )
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
                chat_session_id="also",
            )["error_kind"]
            == ChatWaitRefuseReason.INVALID_LOCATOR.value
        )


def test_home_and_branch_locators_bind_their_surfaces(db_session, workspace, tmp_path):
    chat = _home_session(db_session, workspace)
    _running_home(db_session, workspace, chat)
    _running_branch(db_session, workspace)
    log_path = _log(tmp_path)
    with _child(new_session=True) as home_child, _child(new_session=True) as branch_child:
        home = register(
            db_session,
            pid=home_child.pid,
            command=COMMAND,
            log_path=str(log_path),
            chat_session_id=chat.id,
        )
        branch = register(
            db_session,
            pid=branch_child.pid,
            command=COMMAND,
            log_path=str(log_path),
            workspace_slug=workspace.slug,
            branch=BRANCH,
        )
        assert home["ok"] is True
        assert branch["ok"] is True
        assert outstanding_for(db_session, ChatSurface.HOME, chat.id).surface is ChatSurface.HOME
        key = thread_key(ChatSurface.BRANCH_TRIAGE, workspace_slug=workspace.slug, branch=BRANCH)
        assert (
            outstanding_for(db_session, ChatSurface.BRANCH_TRIAGE, key).surface
            is ChatSurface.BRANCH_TRIAGE
        )


def test_home_register_on_a_session_without_this_threads_running_turn_is_refused(
    db_session, workspace, tmp_path
):
    session_a = _home_session(db_session, workspace)
    session_b = _home_session(db_session, workspace)
    _running_home(db_session, workspace, session_b)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            chat_session_id=session_a.id,
        )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.NO_IN_FLIGHT_TURN.value
    assert outstanding_for(db_session, ChatSurface.HOME, session_a.id) is None


def test_branch_register_on_a_branch_without_this_branchs_running_turn_is_refused(
    db_session, workspace, tmp_path
):
    _running_branch(db_session, workspace, branch="other-branch")
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            workspace_slug=workspace.slug,
            branch=BRANCH,
        )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.NO_IN_FLIGHT_TURN.value


def test_start_triage_raises_in_flight_conflict_while_a_wait_is_outstanding(
    db_session, ticket, tmp_path
):
    origin = _running_triage(db_session, ticket)
    origin.status = RunStatus.SUCCEEDED
    db_session.add(origin)
    db_session.commit()
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        origin.status = RunStatus.RUNNING
        db_session.add(origin)
        db_session.commit()
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        with pytest.raises(TriageConflictError):
            start_triage_run(db_session, ticket, "next question")


def test_start_home_and_branch_raise_in_flight_conflict_while_waiting(
    db_session, workspace, tmp_path
):
    chat = _home_session(db_session, workspace)
    home_run = _running_home(db_session, workspace, chat)
    branch_run = _running_branch(db_session, workspace)
    log_path = _log(tmp_path)
    with _child(new_session=True) as home_child, _child(new_session=True) as branch_child:
        assert (
            register(
                db_session,
                pid=home_child.pid,
                command=COMMAND,
                log_path=str(log_path),
                chat_session_id=chat.id,
            )["ok"]
            is True
        )
        assert (
            register(
                db_session,
                pid=branch_child.pid,
                command=COMMAND,
                log_path=str(log_path),
                workspace_slug=workspace.slug,
                branch=BRANCH,
            )["ok"]
            is True
        )
        _succeed_run(db_session, home_run)
        _succeed_run(db_session, branch_run)
        with pytest.raises(BaxterChatConflictError):
            start_baxter_chat_turn(db_session, chat, "next")
        with pytest.raises(BranchTriageConflictError):
            start_branch_triage_run(db_session, workspace, BRANCH, "next")


def test_start_star_empty_content_is_still_refused_for_operator_turns(
    db_session, ticket, workspace
):
    """R3.5. Continuation must not weaken the operator empty-content guard."""
    chat = _home_session(db_session, workspace)
    with pytest.raises(ValueError, match="empty|required"):
        start_triage_run(db_session, ticket, "   ")
    with pytest.raises(ValueError, match="empty|required"):
        start_baxter_chat_turn(db_session, chat, "   ")
    with pytest.raises(ValueError, match="empty|required"):
        start_branch_triage_run(db_session, workspace, BRANCH, "   ")


# ---- R2.4 / R2.5 MCP tool -----------------------------------------------


def test_register_chat_wait_is_auto_approved_orchestrated_denied_not_stage_default():
    assert McpTool.REGISTER_CHAT_WAIT in AUTO_APPROVED_MCP_TOOLS
    assert McpTool.REGISTER_CHAT_WAIT in ORCHESTRATED_DENIED_MCP_TOOLS
    assert McpTool.REGISTER_CHAT_WAIT not in STAGE_DEFAULT_MCP_TOOLS


def test_register_chat_wait_is_not_documented_in_the_embedded_mcp_module():
    doc = (_REPO_ROOT / "agent_context/agents/common_assets/loregarden_mcp_v1.md").read_text(
        encoding="utf-8"
    )
    assert "loregarden_register_chat_wait" not in doc


def test_orchestrated_execute_is_denied_and_writes_no_row(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        with pytest.raises(ValueError, match="orchestrated"):
            execute_tool(
                db_session,
                McpTool.REGISTER_CHAT_WAIT,
                normalize_tool_arguments(
                    McpTool.REGISTER_CHAT_WAIT,
                    {
                        "pid": child.pid,
                        "command": COMMAND,
                        "log_path": str(log_path),
                        "ticket_id": ticket.id,
                    },
                ),
                orchestrated=True,
            )
    assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_chat_orchestrated_false_auto_approves_and_does_not_spawn(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    tool_src = (_REPO_ROOT / "server/loregarden/mcp/chat_wait_tool.py").read_text(encoding="utf-8")
    assert "Popen" not in tool_src
    assert "subprocess" not in tool_src
    assert "setsid" in tool_src
    with _child(new_session=True) as child:
        body = _mcp(
            db_session,
            {
                "pid": child.pid,
                "command": COMMAND,
                "log_path": str(log_path),
                "ticket_id": ticket.id,
            },
        )
        assert body["ok"] is True


def test_mcp_tool_description_documents_the_setsid_wrap():
    from loregarden.mcp.chat_wait_tool import TOOL_DEFINITIONS

    description = TOOL_DEFINITIONS[0]["description"]
    assert "setsid" in description
    assert "inbox" in description.lower()


# ---- R3 continuation_blocks ---------------------------------------------


def test_continuation_blocks_state_exit_code_command_and_no_operator_message():
    zero = "\n".join(continuation_blocks(0, "passed\n", COMMAND, "/tmp/job.log"))
    assert "0" in zero
    assert COMMAND in zero
    assert "/tmp/job.log" in zero
    assert "passed" in zero
    assert "operator did not send a message" in zero.lower()
    missing = "\n".join(continuation_blocks(None, "", COMMAND, "/tmp/job.log"))
    assert "unknown — no .rc file" in missing


# ---- R3 named regression ------------------------------------------------


def test_triage_register_then_child_exit_starts_second_agent_run(db_session, ticket, tmp_path):
    """AC6 / R3.4. Exit 0 on the same ticket; prompt carries that code."""
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path, body="push ok\n", rc=0)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)

    runs = _runs_for_ticket(db_session, ticket.id)
    assert len(runs) == 2
    continuation = runs[1]
    assert continuation.stage_key == TRIAGE_STAGE_KEY
    assert continuation.id != origin.id
    assert continuation.auto_approve is True
    wait = db_session.exec(select(ChatWait).where(ChatWait.origin_run_id == origin.id)).one()
    assert wait.status is ChatWaitStatus.CONTINUED
    assert wait.continuation_run_id == continuation.id
    payload = take_continuation(db_session, continuation.id)
    assert payload is not None
    assert payload.exit_code == 0
    assert "push ok" in payload.log_tail
    extra = continuation_blocks(
        payload.exit_code, payload.log_tail, payload.command, payload.log_path
    )
    prompt = build_triage_prompt(
        ticket,
        list_triage_messages(db_session, ticket.id),
        "",
        session=db_session,
        extra_sections=extra,
    )
    assert "Exit code: 0" in prompt
    assert "push ok" in prompt
    assert "operator did not send a message" in prompt.lower()
    users = db_session.exec(
        select(TriageMessage).where(
            TriageMessage.ticket_id == ticket.id, TriageMessage.role == "user"
        )
    ).all()
    assert users == []


@pytest.mark.parametrize("rc", [1, None])
def test_nonzero_and_missing_rc_also_continue(db_session, ticket, tmp_path, rc):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path, rc=rc)
    if rc is None:
        rc_file = Path(str(log_path) + ".rc")
        if rc_file.exists():
            rc_file.unlink()
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    runs = _runs_for_ticket(db_session, ticket.id)
    assert len(runs) == 2
    assert db_session.exec(select(ChatWait)).one().status is ChatWaitStatus.CONTINUED


def test_home_continuation_binds_home_chat_stage_and_pending_assistant(
    db_session, workspace, tmp_path
):
    chat = _home_session(db_session, workspace)
    origin = _running_home(db_session, workspace, chat)
    log_path = _log(tmp_path, rc=0)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                chat_session_id=chat.id,
            )["ok"]
            is True
        )
        _succeed_run(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    home_runs = db_session.exec(
        select(AgentRun)
        .where(AgentRun.stage_key == HOME_CHAT_STAGE_KEY)
        .order_by(col(AgentRun.created_at).asc())
    ).all()
    assert len(home_runs) == 2
    pending = db_session.exec(
        select(BaxterChatMessage).where(
            BaxterChatMessage.session_id == chat.id,
            BaxterChatMessage.role == "assistant",
            BaxterChatMessage.status == "pending",
        )
    ).first()
    assert pending is not None
    assert pending.run_id == home_runs[1].id
    users = db_session.exec(
        select(BaxterChatMessage).where(
            BaxterChatMessage.session_id == chat.id, BaxterChatMessage.role == "user"
        )
    ).all()
    assert users == []


def test_branch_continuation_binds_branch_triage_stage(db_session, workspace, tmp_path):
    origin = _running_branch(db_session, workspace)
    log_path = _log(tmp_path, rc=0)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                workspace_slug=workspace.slug,
                branch=BRANCH,
            )["ok"]
            is True
        )
        _succeed_run(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    branch_runs = db_session.exec(
        select(AgentRun)
        .where(AgentRun.stage_key == BRANCH_TRIAGE_STAGE_KEY)
        .order_by(col(AgentRun.created_at).asc())
    ).all()
    assert len(branch_runs) == 2
    pending = db_session.exec(
        select(BranchTriageMessage).where(
            BranchTriageMessage.workspace_id == workspace.id,
            BranchTriageMessage.branch == BRANCH,
            BranchTriageMessage.role == "assistant",
            BranchTriageMessage.status == "pending",
        )
    ).first()
    assert pending is not None
    assert pending.run_id == branch_runs[1].id


def test_unknown_on_the_timer_is_never_gone(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()

        def times_out(args, *rest, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 5))

        with patch("loregarden.services.process_identity.subprocess.run", times_out):
            poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        assert (
            outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id).status
            is ChatWaitStatus.OUTSTANDING
        )
        assert len(_runs_for_ticket(db_session, ticket.id)) == 1
        assert _system_triage(db_session, ticket.id) == []


def test_alive_before_deadline_keeps_watching(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is not None
        assert len(_runs_for_ticket(db_session, ticket.id)) == 1


def test_poll_chat_waits_is_on_periodic_steps_and_resume_is_not():
    from loregarden.services.reconciliation import PERIODIC_STEPS

    names = {step.name for step in PERIODIC_STEPS}
    assert "poll_chat_waits" in names
    assert "resume_chat_waits" not in names


# ---- R4 control-plane messages ------------------------------------------


def _register_then_succeed_origin(db_session, ticket, tmp_path, child):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    assert (
        register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )["ok"]
        is True
    )
    origin.status = RunStatus.SUCCEEDED
    db_session.add(origin)
    db_session.commit()
    return origin, log_path


def test_stop_writes_system_copy_does_not_kill_and_does_not_continue(db_session, ticket, tmp_path):
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        assert cancel_triage_turn(db_session, ticket.id) is None
        wait = db_session.exec(select(ChatWait)).one()
        assert wait.status is ChatWaitStatus.CANCELLED
        messages = _system_triage(db_session, ticket.id)
        assert len(messages) == 1
        assert messages[0].content == STOP_COPY.format(command=COMMAND, log_path=wait.log_path)
        assert messages[0].role == "system"
        assert identify(child.pid).state is ProcessState.ALIVE
        assert len(_runs_for_ticket(db_session, ticket.id)) == 1
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_cancel_triage_after_miss_writes_stop_without_a_direct_cancel_wait_call(
    db_session, ticket, tmp_path
):
    """R4.2. cancel_triage_turn is the operator Stop path."""
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        result = cancel_triage_turn(db_session, ticket.id)
        assert result is None
        wait = db_session.exec(select(ChatWait)).one()
        assert wait.status is ChatWaitStatus.CANCELLED
        assert _system_triage(db_session, ticket.id)[0].content == STOP_COPY.format(
            command=COMMAND, log_path=wait.log_path
        )


def test_home_and_branch_stop_write_system_copy(db_session, workspace, tmp_path):
    chat = _home_session(db_session, workspace)
    home_run = _running_home(db_session, workspace, chat)
    branch_run = _running_branch(db_session, workspace)
    log_path = _log(tmp_path)
    with _child(new_session=True) as home_child, _child(new_session=True) as branch_child:
        assert (
            register(
                db_session,
                pid=home_child.pid,
                command=COMMAND,
                log_path=str(log_path),
                chat_session_id=chat.id,
            )["ok"]
            is True
        )
        assert (
            register(
                db_session,
                pid=branch_child.pid,
                command=COMMAND,
                log_path=str(log_path),
                workspace_slug=workspace.slug,
                branch=BRANCH,
            )["ok"]
            is True
        )
        _succeed_run(db_session, home_run)
        _succeed_run(db_session, branch_run)
        assert cancel_baxter_chat_turn(db_session, chat) is None
        assert cancel_branch_triage_turn(db_session, workspace, BRANCH) is None
        home_msg = db_session.exec(
            select(BaxterChatMessage).where(
                BaxterChatMessage.session_id == chat.id,
                BaxterChatMessage.role == "system",
            )
        ).one()
        branch_msg = db_session.exec(
            select(BranchTriageMessage).where(
                BranchTriageMessage.workspace_id == workspace.id,
                BranchTriageMessage.branch == BRANCH,
                BranchTriageMessage.role == "system",
            )
        ).one()
        assert home_msg.content == STOP_COPY.format(command=COMMAND, log_path=str(log_path))
        assert home_msg.status == "complete"
        assert branch_msg.content == STOP_COPY.format(command=COMMAND, log_path=str(log_path))


def test_timeout_alive_past_deadline_writes_timeout_copy_not_boot_copy(
    db_session, ticket, tmp_path
):
    with _child(new_session=True) as child:
        origin, log_path = _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        wait = outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id)
        wait.registered_at = utcnow() - timedelta(seconds=7201)
        wait.deadline = utcnow() - timedelta(seconds=1)
        db_session.add(wait)
        db_session.commit()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        wait = db_session.exec(select(ChatWait)).one()
        assert wait.status is ChatWaitStatus.TIMED_OUT
        assert _system_triage(db_session, ticket.id)[0].content == TIMEOUT_COPY.format(
            command=COMMAND, log_path=str(log_path)
        )
        assert identify(child.pid).state is ProcessState.ALIVE
        assert len(_runs_for_ticket(db_session, ticket.id)) == 1
        assert origin.id == _runs_for_ticket(db_session, ticket.id)[0].id


def test_timer_unknown_at_deadline_uses_timeout_copy(db_session, ticket, tmp_path):
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        wait = outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id)
        wait.deadline = utcnow() - timedelta(seconds=1)
        db_session.add(wait)
        db_session.commit()

        def times_out(args, *rest, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 5))

        with patch("loregarden.services.process_identity.subprocess.run", times_out):
            poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        wait = db_session.exec(select(ChatWait)).one()
        assert wait.status is ChatWaitStatus.TIMED_OUT
        text = _system_triage(db_session, ticket.id)[0].content
        assert text == TIMEOUT_COPY.format(command=COMMAND, log_path=wait.log_path)
        assert "server restart" not in text


def test_fourth_gone_writes_cap_copy_and_starts_no_turn(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    for index in range(3):
        db_session.add(
            ChatWait(
                surface=ChatSurface.TICKET_TRIAGE,
                thread_key=ticket.id,
                origin_run_id=origin.id,
                pid=1,
                pid_identity="stamp",
                command=COMMAND,
                log_path="/tmp/old.log",
                registered_at=utcnow(),
                deadline=utcnow() + timedelta(seconds=7200),
                status=ChatWaitStatus.CONTINUED,
                continued_index=index + 1,
            )
        )
    db_session.commit()
    log_path = _log(tmp_path, rc=0)
    with _child(new_session=True) as child:
        origin.status = RunStatus.RUNNING
        db_session.add(origin)
        db_session.commit()
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    capped = db_session.exec(select(ChatWait).where(ChatWait.status == ChatWaitStatus.CAPPED)).one()
    assert _system_triage(db_session, ticket.id)[0].content == CAP_COPY.format(
        command=COMMAND, log_path=capped.log_path
    )
    assert len(_runs_for_ticket(db_session, ticket.id)) == 1


def test_boot_unknown_writes_boot_copy_and_drops_the_wait(db_session, ticket, tmp_path):
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)

        def times_out(args, *rest, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 5))

        with patch("loregarden.services.process_identity.subprocess.run", times_out):
            resume_chat_waits(db_session)
        wait = db_session.exec(select(ChatWait)).one()
        assert wait.status is ChatWaitStatus.INTERRUPTED
        assert _system_triage(db_session, ticket.id)[0].content == BOOT_UNKNOWN_COPY.format(
            command=COMMAND, log_path=wait.log_path
        )
        assert len(_runs_for_ticket(db_session, ticket.id)) == 1


def test_boot_empty_stamp_is_boot_unknown(db_session, ticket, tmp_path):
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        wait = outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id)
        wait.pid_identity = ""
        db_session.add(wait)
        db_session.commit()
        resume_chat_waits(db_session)
        wait = db_session.exec(select(ChatWait)).one()
        assert wait.status is ChatWaitStatus.INTERRUPTED
        assert "server restart" in _system_triage(db_session, ticket.id)[0].content


def test_boot_alive_keeps_watching(db_session, ticket, tmp_path):
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        resume_chat_waits(db_session)
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is not None
        assert _system_triage(db_session, ticket.id) == []


def test_boot_gone_continues_subject_to_cap(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path, rc=0)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        child.kill()
        child.wait()
        resume_chat_waits(db_session)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    assert db_session.exec(select(ChatWait)).one().status is ChatWaitStatus.CONTINUED


def test_resume_chat_waits_runs_after_fail_interrupted_on_boot():
    from loregarden.main import _recover_previous_boot

    src = inspect.getsource(_recover_previous_boot)
    assert "resume_chat_waits" in src
    last_fail = src.rindex("fail_interrupted_")
    assert src.index("resume_chat_waits") > last_fail


def test_wait_end_messages_are_not_assistant_toasts_or_restart_copy(db_session, ticket, tmp_path):
    from loregarden.services.interruption_messages import restart_interruption_message
    from loregarden.services.triage_service import TRIAGE_AGENT_NAME

    restart = restart_interruption_message(
        TRIAGE_AGENT_NAME, "did not finish this turn. Send the message again."
    )
    with _child(new_session=True) as child:
        _register_then_succeed_origin(db_session, ticket, tmp_path, child)
        cancel_triage_turn(db_session, ticket.id)
    text = _system_triage(db_session, ticket.id)[0].content
    assert text != restart
    assistants = db_session.exec(
        select(TriageMessage).where(
            TriageMessage.ticket_id == ticket.id, TriageMessage.role == "assistant"
        )
    ).all()
    assert assistants == []


# ---- R5 snapshot --------------------------------------------------------


def test_snapshot_is_waiting_with_payload_and_null_active_ids(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )["ok"]
            is True
        )
        origin.status = RunStatus.SUCCEEDED
        db_session.add(origin)
        db_session.commit()
        status, active = triage_run_status(db_session, ticket.id)
        assert status == "waiting"
        assert active is None
        snap = triage_snapshot(db_session, ticket)
        assert snap["run_status"] == "waiting"
        assert snap["active_run_id"] is None or snap.get("active_turn_id") is None
        wait = snap["wait"]
        assert wait["command"] == COMMAND
        assert wait["log_path"] == str(log_path)
        assert wait["registered_at"]
        assert wait["deadline"]


def test_home_and_branch_snapshots_wait_the_same_way(db_session, workspace, tmp_path):
    chat = _home_session(db_session, workspace)
    home_run = _running_home(db_session, workspace, chat)
    branch_run = _running_branch(db_session, workspace)
    log_path = _log(tmp_path)
    with _child(new_session=True) as home_child, _child(new_session=True) as branch_child:
        register(
            db_session,
            pid=home_child.pid,
            command=COMMAND,
            log_path=str(log_path),
            chat_session_id=chat.id,
        )
        register(
            db_session,
            pid=branch_child.pid,
            command=COMMAND,
            log_path=str(log_path),
            workspace_slug=workspace.slug,
            branch=BRANCH,
        )
        _succeed_run(db_session, home_run)
        _succeed_run(db_session, branch_run)
        home_status, home_turn = baxter_chat_run_status(db_session, chat)
        branch_status, branch_turn = branch_triage_run_status(db_session, workspace.id, BRANCH)
        assert home_status == "waiting"
        assert home_turn is None
        assert branch_status == "waiting"
        assert branch_turn is None


def test_switching_home_archive_sessions_does_not_cancel_the_wait_left_behind(
    db_session, workspace, tmp_path
):
    first = _home_session(db_session, workspace)
    second = _home_session(db_session, workspace)
    origin = _running_home(db_session, workspace, first)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                chat_session_id=first.id,
            )["ok"]
            is True
        )
        _succeed_run(db_session, origin)
        assert cancel_baxter_chat_turn(db_session, second) is None
        assert outstanding_for(db_session, ChatSurface.HOME, first.id) is not None
        assert outstanding_for(db_session, ChatSurface.HOME, second.id) is None


# ---- R7 leave-alone -----------------------------------------------------


def test_agent_run_has_no_waiting_status():
    assert not hasattr(RunStatus, "WAITING")
    assert "waiting" not in {member.value for member in RunStatus}


def test_wait_modules_never_call_the_leave_alone_list():
    roots = [
        (_REPO_ROOT / "server/loregarden/services/chat_wait.py"),
        (_REPO_ROOT / "server/loregarden/services/chat_wait_watch.py"),
        (_REPO_ROOT / "server/loregarden/mcp/chat_wait_tool.py"),
    ]
    forbidden = (
        "complete_run",
        "run_resupervise",
        "run_reattach",
        "wrap_for_files",
        "SIGTERM",
        "SIGHUP",
        "host_capacity",
        "reserve_docker",
        "MAX_REWORK_REROUTES",
        "restart_interruption_message",
        "orchestrated=True",
        "Popen",
        "threading.Thread",
    )
    for path in roots:
        body = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in body, f"{token} in {path}"
