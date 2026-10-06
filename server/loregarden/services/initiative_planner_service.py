"""The initiative planner: a conversation, and a "draft the schedule" turn, per initiative.

Same lifecycle as the Ticket Studio scoper (`ticket_studio_run_service`): a
turn is a pending assistant row, executed on a background thread, settled to
complete or failed — and a stop settles it first, so a late reply is discarded
rather than reopening a turn the operator ended.

The planner proposes; it does not write targets. Its one write tool files a
`ScheduleProposal` the operator accepts on the page (`initiative_plan_service`).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime

from loregarden.agents.chat_role_prompt import chat_role_blocks
from loregarden.agents.mcp_context import resolve_mcp_url
from loregarden.agents.registry import get_agent
from loregarden.db.session import engine
from loregarden.dot_line import Dot
from loregarden.mcp.tool_ids import INITIATIVE_PLANNER_MCP_TOOLS, mcp_tool_values
from loregarden.models.domain import (
    ChatSurface,
    InitiativePlannerMessage,
    InitiativePlanView,
    NodeStatus,
    PlannerRole,
    PlannerTurnMode,
    PlannerTurnStatus,
    Ticket,
    Workspace,
)
from loregarden.services.chat_primitives import (
    EMPTY_PARTS_JSON,
    load_parts_json,
    parts_json_for_reply,
)
from loregarden.services.chat_thinking import (
    ChatTurnThinkingSink,
    finish_chat_turn_thinking,
    with_thinking_part,
)
from loregarden.services.cli_agent_runner import CliAgentProfile, run_cli_agent_turn, stub_response
from loregarden.services.initiative_plan_service import load_initiative, plan_view
from loregarden.services.initiative_service import milestones_under
from loregarden.services.interruption_messages import restart_interruption_message
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

INITIATIVE_PLANNER_AGENT_ID = "initiative_planner"

INITIATIVE_PLANNER_CLI_PROFILE = CliAgentProfile(
    agent_id=INITIATIVE_PLANNER_AGENT_ID,
    assistant_label="Initiative planner",
    cli_label="Initiative planner",
    stub_env="LOREGARDEN_INITIATIVE_PLANNER_STUB_RESPONSE",
    timeout_env="LOREGARDEN_INITIATIVE_PLANNER_TIMEOUT",
    tmp_prefix="loregarden-initiative-planner-",
    reply_cap=12000,
)

MAX_HISTORY_MESSAGES = 16
MAX_MESSAGE_CHARS = 3000
MAX_PLAN_CHARS = 30_000

DRAFT_REQUEST = "Draft a schedule for this initiative."

INTERRUPTED_TURN_MESSAGE = restart_interruption_message(
    "The planner", "did not finish this turn. Run it again."
)
CANCELLED_TURN_MESSAGE = "Stopped before the planner finished this turn."


class PlannerConflictError(RuntimeError):
    """A turn is already running for this initiative."""


class PlannerMessageView(BaseModel):
    id: str
    role: PlannerRole
    content: str
    status: PlannerTurnStatus
    turn_mode: PlannerTurnMode
    parts: list[dict]
    created_at: str


class PlannerChatSnapshot(BaseModel):
    initiative_id: str
    messages: list[PlannerMessageView]
    #: The pending assistant row, when a turn is running; null when idle.
    active_turn_id: str | None


def list_messages(session: Session, initiative_id: str) -> list[InitiativePlannerMessage]:
    return list(
        session.exec(
            select(InitiativePlannerMessage)
            .where(InitiativePlannerMessage.initiative_id == initiative_id)
            .order_by(col(InitiativePlannerMessage.created_at))
        ).all()
    )


def latest_pending_turn(session: Session, initiative_id: str) -> InitiativePlannerMessage | None:
    return session.exec(
        select(InitiativePlannerMessage)
        .where(
            InitiativePlannerMessage.initiative_id == initiative_id,
            InitiativePlannerMessage.status == PlannerTurnStatus.PENDING,
        )
        .order_by(col(InitiativePlannerMessage.created_at).desc())
    ).first()


def planner_snapshot(session: Session, initiative_id: str) -> PlannerChatSnapshot:
    load_initiative(session, initiative_id)
    pending = latest_pending_turn(session, initiative_id)
    return PlannerChatSnapshot(
        initiative_id=initiative_id,
        messages=[
            PlannerMessageView(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                status=msg.status,
                turn_mode=msg.turn_mode,
                parts=load_parts_json(msg.parts_json),
                created_at=msg.created_at.isoformat(),
            )
            for msg in list_messages(session, initiative_id)
        ],
        active_turn_id=pending.id if pending is not None else None,
    )


def planner_workspace(session: Session, initiative: Ticket) -> Workspace:
    """The CLI runs in a repository; an initiative has none, so borrow a milestone's.

    The planner reads tickets through MCP, not files, so which of the
    initiative's repositories it runs in does not change what it can see — but
    the CLI refuses a directory that does not exist, and a milestone can live in
    a workspace not checked out on this machine. The first one that is wins.
    """
    milestones = milestones_under(session, initiative.id)
    if not milestones:
        raise ValueError(
            f"Initiative {initiative.external_id} has no milestones or members yet — attach one, or add a ticket, before planning."
        )
    missing: list[str] = []
    for milestone in milestones:
        workspace = (
            session.get(Workspace, milestone.workspace_id) if milestone.workspace_id else None
        )
        if workspace is None or workspace.slug in missing:
            continue
        if resolve_workspace_root(workspace).is_dir():
            return workspace
        missing.append(workspace.slug)
    raise ValueError(
        "None of this initiative's workspaces is checked out on this machine "
        f"({', '.join(missing)}); the planner needs one to run in."
    )


def start_turn(
    session: Session, initiative_id: str, content: str, *, mode: PlannerTurnMode
) -> InitiativePlannerMessage:
    """Write the user row and a pending assistant row. Returns the assistant row."""
    initiative = load_initiative(session, initiative_id)
    # Fail before queuing anything the worker could only fail on later.
    planner_workspace(session, initiative)
    if latest_pending_turn(session, initiative.id) is not None:
        raise PlannerConflictError("The planner is still working on the previous message.")
    text = content.strip() or (DRAFT_REQUEST if mode == PlannerTurnMode.DRAFT else "")
    if not text:
        raise ValueError("Message is empty")
    session.add(
        InitiativePlannerMessage(
            initiative_id=initiative.id, role=PlannerRole.USER, content=text, turn_mode=mode
        )
    )
    assistant = InitiativePlannerMessage(
        initiative_id=initiative.id,
        role=PlannerRole.ASSISTANT,
        status=PlannerTurnStatus.PENDING,
        turn_mode=mode,
    )
    session.add(assistant)
    session.commit()
    session.refresh(assistant)
    return assistant


def _task_block(initiative: Ticket, mode: PlannerTurnMode, latest_user_message: str) -> list[str]:
    if mode == PlannerTurnMode.DRAFT:
        return [
            "## Task",
            "Draft a complete schedule for this initiative: put its open milestones in a "
            "sensible order within each workspace and give every open milestone (and the "
            "initiative itself) a target date grounded in the forecasts above. Submit it with "
            f"`loregarden_propose_initiative_schedule` (initiative_id `{initiative.id}`, "
            'source "draft"), then summarise it for the operator in a few sentences.',
            "",
            "Operator's note:",
            latest_user_message[:MAX_MESSAGE_CHARS],
        ]
    return ["## Operator's message", latest_user_message[:MAX_MESSAGE_CHARS]]


def _day(value: datetime | date | None) -> str:
    return value.strftime("%Y-%m-%d") if value is not None else "?"


def plan_digest(view: InitiativePlanView) -> str:
    """The plan as lines an agent can scan — the full JSON runs to tens of KB.

    Every ticket line carries its uuid, because that is what the tools take.
    """
    by_id = {n.id: n for n in view.nodes}
    autopilot = view.autopilot
    lines = [
        str(
            Dot(f"status {view.status.value}")
            / f"target {_day(view.target_date)}"
            / f"forecast {_day(view.forecast_date)}"
            / f"mode {view.mode.value}"
        ),
        str(
            Dot(f"autopilot {'ON' if autopilot.enabled else 'off'}")
            / f"max_parallel {autopilot.max_parallel}"
            / f"in flight {autopilot.in_flight}"
            / (f"PAUSED: {autopilot.paused_reason}" if autopilot.paused_reason else "")
            / ("" if autopilot.available else "(sandbox: autopilot loop does not run)")
        ),
        "next it would start: "
        + (", ".join(by_id[i].external_id for i in autopilot.next_up) or "nothing"),
        "pace: "
        + "; ".join(
            f"{p.workspace_slug} {p.per_day * 7:.1f}/wk ({p.basis.value})"
            if p.per_day
            else f"{p.workspace_slug} none measured"
            for p in view.paces
        ),
        "",
        "Milestones (phase order): id | external | target | forecast | status | open/total | counts",
    ]
    for m in view.milestones:
        counts = " ".join(f"{k.value}={c}" for k, c in m.counts.items())
        lines.append(
            f"- {m.id} | {m.external_id} | {_day(m.target_date)} | {_day(m.forecast_date)} | "
            f"{m.status.value} | {m.remaining}/{m.total} | {counts}"
        )
    lines += [
        "",
        "Critical path: "
        + " -> ".join(
            by_id[i].external_id + (" [outside]" if by_id[i].external else "")
            for i in view.critical_path
        ),
        "",
        "Open tickets: id | external | status | lane | step | finish | waiting on",
    ]
    for node in sorted(view.nodes, key=lambda n: (n.status.value, n.step, n.external_id)):
        if node.status == NodeStatus.DONE:
            continue
        waiting = ",".join(by_id[w].external_id for w in node.waiting_on if w in by_id)
        lines.append(
            f"- {node.id} | {node.external_id}{' [outside]' if node.external else ''}"
            f"{' *critical*' if node.critical else ''} | {node.status.value} | {node.lane} | "
            f"{node.step} | {_day(node.finish)} | {waiting} | {node.title[:70]}"
        )
    return "\n".join(lines)


def build_planner_prompt(
    session: Session,
    initiative: Ticket,
    history: list[InitiativePlannerMessage],
    latest_user_message: str,
    *,
    mode: PlannerTurnMode,
) -> str:
    agent = get_agent(INITIATIVE_PLANNER_AGENT_ID) or {}
    digest = plan_digest(plan_view(session, initiative.id))
    if len(digest) > MAX_PLAN_CHARS:
        digest = digest[:MAX_PLAN_CHARS] + "\n…[truncated — call loregarden_get_initiative_plan]"
    transcript = [
        f"{msg.role.value}: {msg.content[:MAX_MESSAGE_CHARS]}"
        for msg in history[-MAX_HISTORY_MESSAGES:]
        if msg.status == PlannerTurnStatus.COMPLETE and msg.content
    ]
    return "\n".join(
        [
            *chat_role_blocks(agent, surface=ChatSurface.INITIATIVE_PLANNER),
            "",
            "## Loregarden MCP",
            f"MCP endpoint: `{resolve_mcp_url()}`",
            "Tools: " + ", ".join(mcp_tool_values(INITIATIVE_PLANNER_MCP_TOOLS)),
            "",
            "## Initiative",
            f"id: `{initiative.id}`",
            f"external id: {initiative.external_id}",
            f"title: {initiative.title}",
            initiative.description[:MAX_MESSAGE_CHARS] or "(no description)",
            "",
            "## Current plan (computed now; re-read with the tool after acting)",
            digest,
            "",
            "## Conversation so far",
            *(transcript or ["(none)"]),
            "",
            *_task_block(initiative, mode, latest_user_message),
        ]
    )


def invoke_planner_model(
    session: Session, assistant: InitiativePlannerMessage, latest_user_message: str
) -> str:
    stub = stub_response(INITIATIVE_PLANNER_CLI_PROFILE)
    if stub is not None:
        return stub
    initiative = load_initiative(session, assistant.initiative_id)
    workspace = planner_workspace(session, initiative)
    history = [m for m in list_messages(session, initiative.id) if m.id != assistant.id]
    prompt = build_planner_prompt(
        session, initiative, history, latest_user_message, mode=assistant.turn_mode
    )
    thinking = ChatTurnThinkingSink(assistant.id)
    try:
        return run_cli_agent_turn(
            INITIATIVE_PLANNER_CLI_PROFILE,
            workspace=workspace,
            prompt=prompt,
            thinking_sink=thinking,
            run_id=assistant.id,
            workspace_slug=workspace.slug or "",
            granted_tools=mcp_tool_values(INITIATIVE_PLANNER_MCP_TOOLS),
            surface=ChatSurface.INITIATIVE_PLANNER,
        )
    finally:
        thinking.close()


def _settle(
    session: Session,
    assistant: InitiativePlannerMessage,
    *,
    content: str,
    status: PlannerTurnStatus,
    parts_json: str = EMPTY_PARTS_JSON,
) -> None:
    thinking = finish_chat_turn_thinking(session, assistant.id)
    assistant.content = content
    assistant.status = status
    assistant.parts_json = with_thinking_part(parts_json, thinking)
    session.add(assistant)
    session.commit()


def _latest_user_content(session: Session, initiative_id: str) -> str:
    latest = session.exec(
        select(InitiativePlannerMessage)
        .where(
            InitiativePlannerMessage.initiative_id == initiative_id,
            InitiativePlannerMessage.role == PlannerRole.USER,
        )
        .order_by(col(InitiativePlannerMessage.created_at).desc())
    ).first()
    return latest.content if latest is not None else ""


def execute_planner_turn_background(assistant_id: str) -> None:
    try:
        with Session(engine) as session:
            assistant = session.get(InitiativePlannerMessage, assistant_id)
            if assistant is None:
                logger.error("Background planner turn not found: %s", assistant_id)
                return
            try:
                reply = invoke_planner_model(
                    session, assistant, _latest_user_content(session, assistant.initiative_id)
                )
            except Exception as exc:
                logger.exception("Initiative planner turn failed: %s", assistant_id)
                _settle(
                    session,
                    assistant,
                    content=f"Initiative planner unavailable: {exc}",
                    status=PlannerTurnStatus.FAILED,
                )
                return
            # A stop settles the row while the model is still running; settling
            # again would resurrect a turn the operator ended.
            session.refresh(assistant)
            if assistant.status != PlannerTurnStatus.PENDING:
                logger.info(
                    "Discarding reply for planner turn %s: already settled as %s",
                    assistant_id,
                    assistant.status.value,
                )
                return
            initiative = load_initiative(session, assistant.initiative_id)
            _settle(
                session,
                assistant,
                content=reply,
                status=PlannerTurnStatus.COMPLETE,
                parts_json=parts_json_for_reply(
                    session, reply, workspace_id=planner_workspace(session, initiative).id
                ),
            )
    except Exception:
        # Never leave the row pending: a pending row locks the composer for good.
        logger.exception("Background planner turn crashed: %s", assistant_id)
        try:
            with Session(engine) as session:
                assistant = session.get(InitiativePlannerMessage, assistant_id)
                if assistant is not None and assistant.status == PlannerTurnStatus.PENDING:
                    _settle(
                        session,
                        assistant,
                        content="Initiative planner unavailable: internal error",
                        status=PlannerTurnStatus.FAILED,
                    )
        except Exception:
            # The startup reaper settles it on the next boot; until then the
            # panel's Stop button settles it too.
            logger.exception("Failed to settle planner turn %s after crash", assistant_id)


def schedule_planner_turn(assistant_id: str) -> None:
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_planner_turn_background(assistant_id)
        return
    threading.Thread(
        target=execute_planner_turn_background,
        args=(assistant_id,),
        name=f"loregarden-planner-turn-{assistant_id[:8]}",
        daemon=True,
    ).start()


def fail_interrupted_planner_turns(session: Session) -> list[InitiativePlannerMessage]:
    """Settle turns orphaned by a restart so no planner stays stuck working."""
    orphaned = list(
        session.exec(
            select(InitiativePlannerMessage).where(
                InitiativePlannerMessage.status == PlannerTurnStatus.PENDING
            )
        ).all()
    )
    for assistant in orphaned:
        assistant.content = INTERRUPTED_TURN_MESSAGE
        assistant.status = PlannerTurnStatus.FAILED
        session.add(assistant)
    if orphaned:
        session.commit()
    return orphaned


def cancel_planner_turn(session: Session, initiative_id: str) -> InitiativePlannerMessage | None:
    pending = latest_pending_turn(session, initiative_id)
    if pending is None:
        return None
    _settle(session, pending, content=CANCELLED_TURN_MESSAGE, status=PlannerTurnStatus.FAILED)
    return pending
