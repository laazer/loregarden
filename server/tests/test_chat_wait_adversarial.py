"""Adversarial cases the happy-path chat-wait suite cannot see.

`test_chat_wait.py` covers the spec's named paths. This module attacks the
seams those tests leave open: a recycled pid that is still ALIVE, a child that
exits before the origin turn settles, a cap that counts the wrong statuses, a
unique index that is only a service check, a log longer than 8000 characters,
and locators that look complete but name the wrong surface.
"""

from __future__ import annotations

import inspect
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.agents.executors.approval_scope import HOME_CHAT_STAGE_KEY
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_registry import EXTENDED_TOOLS
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    ChatSurface,
    ProcessState,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.models.domain.chat_wait_enums import (
    ChatWaitRefuseReason,
    ChatWaitStatus,
    ChatWaitUnknownPolicy,
)
from loregarden.models.domain.chat_wait_tables import ChatWait
from loregarden.models.domain.enums import utcnow
from loregarden.services.chat_wait import continuation_blocks, outstanding_for, register
from loregarden.services.chat_wait_watch import poll_chat_waits, resume_chat_waits
from loregarden.services.process_identity import IdentityReading, identify, session_of
from loregarden.services.run_concurrency import IN_FLIGHT_STATUSES, TRIAGE_STAGE_KEY
from loregarden.services.triage_run_service import cancel_triage_turn
from loregarden.services.triage_service import TRIAGE_AGENT_ID, triage_snapshot
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket
from tests.test_chat_wait import (
    BRANCH,
    CAP_COPY,
    COMMAND,
    _child,
    _home_session,
    _log,
    _running_home,
    _running_triage,
    _runs_for_ticket,
    _system_triage,
)

#: Paths below are repo-relative; pytest runs from server/, CI included.
_REPO_ROOT = Path(__file__).resolve().parents[2]

LOG_TAIL_CHARS = 8000


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "chat-wait-adv")


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session, ticket: Ticket) -> Workspace:
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    return workspace


def _settle(db_session: Session, run: AgentRun) -> None:
    run.status = RunStatus.SUCCEEDED
    db_session.add(run)
    db_session.commit()


def _register_live(
    db_session: Session,
    ticket: Ticket,
    tmp_path: Path,
    child,
    *,
    body: str = "ok\n",
    rc: int | None = 0,
):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path, body=body, rc=rc)
    result = register(
        db_session,
        pid=child.pid,
        command=COMMAND,
        log_path=str(log_path),
        ticket_id=ticket.id,
    )
    assert result["ok"] is True
    return origin, log_path


def _settled_wait(
    db_session: Session,
    ticket: Ticket,
    origin: AgentRun,
    status: ChatWaitStatus,
    *,
    command: str = COMMAND,
) -> ChatWait:
    wait = ChatWait(
        surface=ChatSurface.TICKET_TRIAGE,
        thread_key=ticket.id,
        origin_run_id=origin.id,
        pid=1,
        pid_identity="stamp",
        command=command,
        log_path="/tmp/old.log",
        registered_at=utcnow(),
        deadline=utcnow() + timedelta(seconds=7200),
        status=status,
    )
    db_session.add(wait)
    db_session.commit()
    return wait


# ---- R2 register mutations ------------------------------------------------


def test_whitespace_command_and_log_path_are_named_missing(db_session, ticket, tmp_path):
    """CHECKPOINT: blank after strip is empty; interpolating spaces into Stop copy is a lie."""
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert (
            register(
                db_session,
                pid=child.pid,
                command="   ",
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
                log_path="   ",
                ticket_id=ticket.id,
            )["error_kind"]
            == ChatWaitRefuseReason.MISSING_LOG_PATH.value
        )
    assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_identify_unknown_at_register_is_not_alive_and_writes_no_row(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        with patch(
            "loregarden.services.chat_wait.identify",
            return_value=IdentityReading(ProcessState.UNKNOWN),
        ):
            result = register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.NOT_ALIVE.value
    assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_session_of_none_on_the_originating_agent_is_same_session(db_session, ticket, tmp_path):
    """R2.6. Cannot prove detach if the agent's sid cannot be read."""
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:

        def only_child(pid: int):
            if pid == origin.agent_pid:
                return None
            return session_of(pid)

        with patch("loregarden.services.chat_wait.session_of", side_effect=only_child):
            result = register(
                db_session,
                pid=child.pid,
                command=COMMAND,
                log_path=str(log_path),
                ticket_id=ticket.id,
            )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.SAME_SESSION.value


def test_registering_the_server_pid_is_same_session(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    with _child(new_session=True) as agent:
        origin.agent_pid = agent.pid
        origin.agent_pid_identity = identify(agent.pid).stamp
        db_session.add(origin)
        db_session.commit()
        result = register(
            db_session,
            pid=os.getpid(),
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            ticket_id=ticket.id,
        )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.SAME_SESSION.value


def test_incomplete_branch_locator_is_invalid(db_session, workspace, tmp_path):
    chat = _home_session(db_session, workspace)
    _running_home(db_session, workspace, chat)
    with _child(new_session=True) as child:
        missing_branch = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            workspace_slug=workspace.slug,
        )
        missing_slug = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            branch=BRANCH,
        )
    assert missing_branch["error_kind"] == ChatWaitRefuseReason.INVALID_LOCATOR.value
    assert missing_slug["error_kind"] == ChatWaitRefuseReason.INVALID_LOCATOR.value


def test_two_locator_families_together_are_invalid(db_session, ticket, workspace, tmp_path):
    _running_triage(db_session, ticket)
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            ticket_id=ticket.id,
            workspace_slug=workspace.slug,
            branch=BRANCH,
        )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.INVALID_LOCATOR.value


def test_home_locator_against_a_ticket_triage_run_is_no_in_flight_turn(
    db_session, ticket, workspace, tmp_path
):
    chat = _home_session(db_session, workspace)
    _running_triage(db_session, ticket)
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            chat_session_id=chat.id,
        )
    assert result["ok"] is False
    assert result["error_kind"] == ChatWaitRefuseReason.NO_IN_FLIGHT_TURN.value


def test_failed_run_is_not_an_in_flight_turn(db_session, ticket, tmp_path):
    run = AgentRun(
        run_code="run_failed_wait",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=TRIAGE_AGENT_ID,
        stage_key=TRIAGE_STAGE_KEY,
        status=RunStatus.FAILED,
    )
    db_session.add(run)
    db_session.commit()
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            ticket_id=ticket.id,
        )
    assert result["error_kind"] == ChatWaitRefuseReason.NO_IN_FLIGHT_TURN.value


def test_log_file_need_not_exist_at_register(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    missing = tmp_path / "not-written-yet.log"
    with _child(new_session=True) as child:
        result = register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(missing),
            ticket_id=ticket.id,
        )
        assert result["ok"] is True


def test_a_continued_thread_can_register_the_next_wait(db_session, ticket, tmp_path):
    """R2.1. Unique is outstanding-only; a continuation may register the next wait."""
    origin = _running_triage(db_session, ticket)
    _settled_wait(db_session, ticket, origin, ChatWaitStatus.CONTINUED)
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
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is not None


def test_two_tickets_can_wait_independently(db_session, ticket, tmp_path):
    other = make_workspace_ticket(db_session, "chat-wait-adv-b")
    _running_triage(db_session, ticket)
    other_run = AgentRun(
        run_code="run_wait_triage_b",
        ticket_id=other.id,
        workspace_id=other.workspace_id,
        agent_id=TRIAGE_AGENT_ID,
        stage_key=TRIAGE_STAGE_KEY,
        status=RunStatus.RUNNING,
        auto_approve=True,
    )
    db_session.add(other_run)
    db_session.commit()
    db_session.refresh(other_run)
    other_run.agent_pid = os.getpid()
    other_run.agent_pid_identity = identify(os.getpid()).stamp
    db_session.add(other_run)
    db_session.commit()
    with _child(new_session=True) as first, _child(new_session=True) as second:
        assert register(
            db_session,
            pid=first.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            ticket_id=ticket.id,
        )["ok"]
        assert register(
            db_session,
            pid=second.pid,
            command="pytest -q",
            log_path=str(tmp_path / "other.log"),
            ticket_id=other.id,
        )["ok"]
        (tmp_path / "other.log").write_text("ok\n", encoding="utf-8")
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is not None
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, other.id) is not None


# ---- R2 unique index / MCP schema ----------------------------------------


def test_unique_partial_index_rejects_two_outstanding_rows(db_session, ticket):
    origin = _running_triage(db_session, ticket)
    _settle(db_session, origin)

    def row() -> ChatWait:
        return ChatWait(
            surface=ChatSurface.TICKET_TRIAGE,
            thread_key=ticket.id,
            origin_run_id=origin.id,
            pid=1,
            pid_identity="stamp",
            command=COMMAND,
            log_path="/tmp/a.log",
            registered_at=utcnow(),
            deadline=utcnow() + timedelta(seconds=7200),
            status=ChatWaitStatus.OUTSTANDING,
        )

    db_session.add(row())
    db_session.commit()
    db_session.add(row())
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_two_continued_rows_on_one_thread_are_allowed(db_session, ticket):
    origin = _running_triage(db_session, ticket)
    _settle(db_session, origin)
    _settled_wait(db_session, ticket, origin, ChatWaitStatus.CONTINUED)
    _settled_wait(db_session, ticket, origin, ChatWaitStatus.CONTINUED)
    rows = db_session.exec(
        select(ChatWait).where(
            ChatWait.thread_key == ticket.id,
            ChatWait.status == ChatWaitStatus.CONTINUED,
        )
    ).all()
    assert len(rows) == 2


def test_register_handler_is_on_extended_tools_not_an_execute_tool_branch():
    assert McpTool.REGISTER_CHAT_WAIT.value in EXTENDED_TOOLS
    tools_src = (_REPO_ROOT / "server/loregarden/mcp/tools.py").read_text(encoding="utf-8")
    assert "REGISTER_CHAT_WAIT" not in tools_src.split("def execute_tool", 1)[-1]


def test_mcp_schema_forbids_extra_keys_and_requires_positive_pid():
    from loregarden.mcp.chat_wait_tool import TOOL_DEFINITIONS

    spec = TOOL_DEFINITIONS[0]["inputSchema"]
    assert spec.get("additionalProperties") is False
    pid = spec["properties"]["pid"]
    assert pid.get("exclusiveMinimum") == 0 or pid.get("minimum") == 1


def test_mcp_pid_zero_is_refused_with_no_row(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    with pytest.raises(ValueError):
        execute_tool(
            db_session,
            McpTool.REGISTER_CHAT_WAIT,
            normalize_tool_arguments(
                McpTool.REGISTER_CHAT_WAIT,
                {
                    "pid": 0,
                    "command": COMMAND,
                    "log_path": str(_log(tmp_path)),
                    "ticket_id": ticket.id,
                },
            ),
        )
    assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


def test_mcp_unknown_argument_is_refused(db_session, ticket, tmp_path):
    _running_triage(db_session, ticket)
    with _child(new_session=True) as child:
        with pytest.raises(ValueError):
            execute_tool(
                db_session,
                McpTool.REGISTER_CHAT_WAIT,
                {
                    "pid": child.pid,
                    "command": COMMAND,
                    "log_path": str(_log(tmp_path)),
                    "ticket_id": ticket.id,
                    "spawn": True,
                },
            )
    assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is None


# ---- R3 watcher races ----------------------------------------------------


def test_alive_pid_with_a_mismatched_stamp_is_gone_and_continues(db_session, ticket, tmp_path):
    """liveness, not identify().state: a recycled pid that is still ALIVE is GONE."""
    with _child(new_session=True) as child:
        origin, log_path = _register_live(db_session, ticket, tmp_path, child, rc=0)
        _settle(db_session, origin)
        wait = outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id)
        wait.pid_identity = "not-this-process"
        db_session.add(wait)
        db_session.commit()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        assert identify(child.pid).state is ProcessState.ALIVE
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    assert db_session.exec(select(ChatWait)).one().status is ChatWaitStatus.CONTINUED


def test_gone_while_origin_still_running_does_not_mint_a_second_in_flight_run(
    db_session, ticket, tmp_path
):
    """CHECKPOINT: child can exit before origin `_finish()`; two RUNNING triage
    runs would break `find_active_run`. Keep outstanding; continue once settled."""
    with _child(new_session=True) as child:
        origin, _log_path = _register_live(db_session, ticket, tmp_path, child, rc=0)
        assert origin.status is RunStatus.RUNNING
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        assert len(_runs_for_ticket(db_session, ticket.id)) == 1
        assert outstanding_for(db_session, ChatSurface.TICKET_TRIAGE, ticket.id) is not None
        _settle(db_session, origin)
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    assert _runs_for_ticket(db_session, ticket.id)[1].status in {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
    }


def test_log_tail_handed_to_continuation_is_the_last_8000_chars(db_session, ticket, tmp_path):
    head = "HEADMARKER"
    tail = "TAILMARKER"
    body = head + ("x" * (LOG_TAIL_CHARS + 200)) + tail
    with _child(new_session=True) as child:
        origin, _log_path = _register_live(db_session, ticket, tmp_path, child, body=body, rc=0)
        _settle(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    wait = db_session.exec(select(ChatWait)).one()
    assert wait.log_tail is not None
    handed = wait.log_tail
    assert len(handed) <= LOG_TAIL_CHARS
    assert handed.endswith(tail)
    assert head not in handed


def test_empty_or_missing_log_still_continues(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    missing = tmp_path / "never-written.log"
    with _child(new_session=True) as child:
        assert register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(missing),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    assert db_session.exec(select(ChatWait)).one().status is ChatWaitStatus.CONTINUED


def test_corrupt_rc_file_continues_with_unknown_exit(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path, rc=None)
    Path(str(log_path) + ".rc").write_text("not-an-int\n", encoding="utf-8")
    with _child(new_session=True) as child:
        assert register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    wait = db_session.exec(select(ChatWait)).one()
    assert wait.exit_code is None
    joined = "\n".join(continuation_blocks(None, "", COMMAND, str(log_path)))
    assert "unknown — no .rc file" in joined


def test_continuation_inherits_auto_approve_false(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    origin.auto_approve = False
    db_session.add(origin)
    db_session.commit()
    with _child(new_session=True) as child:
        assert register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path, rc=0)),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    assert _runs_for_ticket(db_session, ticket.id)[1].auto_approve is False


def test_one_poll_continues_only_the_gone_wait(db_session, ticket, workspace, tmp_path):
    chat = _home_session(db_session, workspace)
    home_run = _running_home(db_session, workspace, chat)
    with _child(new_session=True) as live, _child(new_session=True) as dead:
        origin, _log_path = _register_live(db_session, ticket, tmp_path, dead, rc=0)
        assert register(
            db_session,
            pid=live.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path)),
            chat_session_id=chat.id,
        )["ok"]
        _settle(db_session, origin)
        _settle(db_session, home_run)
        dead.kill()
        dead.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
        assert outstanding_for(db_session, ChatSurface.HOME, chat.id) is not None
        assert identify(live.pid).state is ProcessState.ALIVE
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    home_runs = db_session.exec(
        select(AgentRun).where(AgentRun.stage_key == HOME_CHAT_STAGE_KEY)
    ).all()
    assert len(home_runs) == 1


# ---- R4 cap / boot / copy ------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        ChatWaitStatus.CANCELLED,
        ChatWaitStatus.TIMED_OUT,
        ChatWaitStatus.INTERRUPTED,
        ChatWaitStatus.CAPPED,
    ],
)
def test_cap_counts_only_continued_rows(db_session, ticket, tmp_path, status):
    origin = _running_triage(db_session, ticket)
    for _ in range(3):
        _settled_wait(db_session, ticket, origin, status)
    with _child(new_session=True) as child:
        origin.status = RunStatus.RUNNING
        db_session.add(origin)
        db_session.commit()
        assert register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path, rc=0)),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    assert (
        db_session.exec(select(ChatWait).where(ChatWait.status == ChatWaitStatus.CONTINUED)).first()
        is not None
    )
    assert _system_triage(db_session, ticket.id) == []


def test_third_gone_still_continues(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    _settled_wait(db_session, ticket, origin, ChatWaitStatus.CONTINUED)
    _settled_wait(db_session, ticket, origin, ChatWaitStatus.CONTINUED)
    with _child(new_session=True) as child:
        origin.status = RunStatus.RUNNING
        db_session.add(origin)
        db_session.commit()
        assert register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path, rc=0)),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        child.kill()
        child.wait()
        poll_chat_waits(db_session, on_unknown=ChatWaitUnknownPolicy.SKIP)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 2
    continued = db_session.exec(
        select(ChatWait).where(ChatWait.status == ChatWaitStatus.CONTINUED)
    ).all()
    assert len(continued) == 3


def test_boot_gone_with_three_continued_writes_cap_not_a_fourth_run(db_session, ticket, tmp_path):
    origin = _running_triage(db_session, ticket)
    for _ in range(3):
        _settled_wait(db_session, ticket, origin, ChatWaitStatus.CONTINUED)
    with _child(new_session=True) as child:
        origin.status = RunStatus.RUNNING
        db_session.add(origin)
        db_session.commit()
        assert register(
            db_session,
            pid=child.pid,
            command=COMMAND,
            log_path=str(_log(tmp_path, rc=0)),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        child.kill()
        child.wait()
        resume_chat_waits(db_session)
    assert len(_runs_for_ticket(db_session, ticket.id)) == 1
    capped = db_session.exec(select(ChatWait).where(ChatWait.status == ChatWaitStatus.CAPPED)).one()
    assert _system_triage(db_session, ticket.id)[0].content == CAP_COPY.format(
        command=COMMAND, log_path=capped.log_path
    )


def test_stop_copy_interpolates_the_exact_command_including_special_chars(
    db_session, ticket, tmp_path
):
    weird = "git push origin HEAD && echo 'done — $HOME'"
    origin = _running_triage(db_session, ticket)
    log_path = _log(tmp_path)
    with _child(new_session=True) as child:
        assert register(
            db_session,
            pid=child.pid,
            command=weird,
            log_path=str(log_path),
            ticket_id=ticket.id,
        )["ok"]
        _settle(db_session, origin)
        cancel_triage_turn(db_session, ticket.id)
        assert identify(child.pid).state is ProcessState.ALIVE
    text = _system_triage(db_session, ticket.id)[0].content
    assert weird in text
    assert str(log_path) in text
    assert "was not killed" in text


def test_resume_chat_waits_defaults_on_unknown_to_interrupt():
    params = inspect.signature(resume_chat_waits).parameters
    default = params["on_unknown"].default
    assert default is ChatWaitUnknownPolicy.INTERRUPT or default == "interrupt"


# ---- R5 / R7 -------------------------------------------------------------


def test_in_flight_statuses_do_not_include_waiting():
    assert not hasattr(RunStatus, "WAITING")
    assert "waiting" not in {member.value for member in IN_FLIGHT_STATUSES}


def test_snapshot_omits_wait_once_the_wait_is_settled(db_session, ticket, tmp_path):
    with _child(new_session=True) as child:
        origin, _log_path = _register_live(db_session, ticket, tmp_path, child)
        _settle(db_session, origin)
        cancel_triage_turn(db_session, ticket.id)
    snap = triage_snapshot(db_session, ticket)
    assert snap["run_status"] != "waiting"
    assert snap.get("wait") in (None, {})
