"""A Home Baxter thread's feature loop: chat proposes criteria, the workflow builds them.

Two agents share the work. Baxter talks with the operator and turns what they
ask for into acceptance criteria, which it can only *propose*
(``loregarden_propose_feature_criteria``). The operator accepts or drops each
one in the chat's Feature card. The ticket's own workflow (the orchestrator
and its stage agents) is the second agent: this module carries accepted
criteria onto the backing ticket one round at a time and starts the workflow
for that round.

A round is the criteria queued while the previous round ran:

1. Nothing runs on the ticket and criteria are queued. They are appended to the
   ticket (a done ticket is reopened in the same write, since a done ticket's
   criteria are frozen), marked ``in_round``, and the ticket's workflow starts.
2. While that runs, the operator keeps chatting; new criteria queue behind it.
3. The workflow finishes with the ticket done. The round's criteria are
   ``met``, and step 1 picks up whatever queued meanwhile.

A round that stops short (the ticket is blocked, or the workflow ended without
finishing it) is not restarted. It is reported as stalled, because restarting
a failing round on a timer is a loop nobody asked for.

``advance_feature_loop`` does steps 1 and 3. It runs when the operator accepts
criteria and on the reconcile timer (``reconciliation.PERIODIC_STEPS``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    FeatureCriterion,
    FeatureCriterionStatus,
    FeatureLoopStatus,
    Ticket,
    TicketActivity,
    TicketState,
    UpdateTicketRequest,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.work_item_types import WorkItemType
from loregarden.services.acceptance_criteria import (
    MAX_ACCEPTANCE_CRITERIA,
    guard_criteria_growth,
    load_criteria,
    merge_criteria,
    normalize_criteria,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.queue_admission import QueueAdmissionService
from loregarden.services.requeue import requeue_stage
from loregarden.services.run_service import schedule_orchestration
from loregarden.services.ticket_activity import activity_for
from loregarden.services.ticket_ids import resolve
from loregarden.services.ticket_service import TicketService
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Who the loop's ticket writes are attributed to.
LOOP_ACTOR = "baxter-feature-loop"
#: How many of the ticket's agent runs the Changes card lists.
MAX_LOOP_RUNS = 6

_OPEN = (
    FeatureCriterionStatus.PROPOSED,
    FeatureCriterionStatus.QUEUED,
    FeatureCriterionStatus.IN_ROUND,
)


class FeatureLoopError(ValueError):
    """A loop request that cannot be done as asked; the message says why."""


@dataclass(frozen=True)
class LoopAssessment:
    status: FeatureLoopStatus
    #: Why a stalled or blocked loop is not moving, in the operator's words; "" otherwise.
    reason: str = ""


def _criteria(
    session: Session, session_id: str, statuses: Sequence[FeatureCriterionStatus] = ()
) -> list[FeatureCriterion]:
    query = select(FeatureCriterion).where(FeatureCriterion.session_id == session_id)
    if statuses:
        query = query.where(col(FeatureCriterion.status).in_(list(statuses)))
    return list(session.exec(query.order_by(col(FeatureCriterion.created_at).asc())).all())


def loop_ticket(session: Session, chat_session: BaxterChatSession) -> Ticket | None:
    if not chat_session.feature_ticket_id:
        return None
    return session.get(Ticket, chat_session.feature_ticket_id)


def _require_ticket(session: Session, chat_session: BaxterChatSession) -> Ticket:
    ticket = loop_ticket(session, chat_session)
    if ticket is None:
        raise FeatureLoopError("This chat has no feature loop. Start one from the Feature card.")
    return ticket


# --- starting -----------------------------------------------------------------


def start_feature_loop(
    session: Session,
    chat_session: BaxterChatSession,
    *,
    title: str = "",
    workspace_slug: str = "",
    parent_ticket_id: str = "",
    existing_ticket: str = "",
) -> Ticket:
    """Bind the thread to a ticket: an existing one, or a new one created here.

    A new ticket is a feature under ``parent_ticket_id`` (a milestone or
    initiative), or a stand-alone milestone without one, since only those may
    have no parent. It starts with no criteria and nothing runs until the
    operator accepts the first ones.
    """
    if chat_session.feature_ticket_id:
        raise FeatureLoopError("This chat already runs a feature loop.")
    if existing_ticket.strip():
        ref = existing_ticket.strip()
        ticket = session.get(Ticket, ref) or resolve(session, ref)
        if ticket is None:
            raise FeatureLoopError(f"No ticket {ref}.")
        if ticket.work_item_type == WorkItemType.INITIATIVE:
            raise FeatureLoopError("An initiative has no workflow to run; pick a ticket under it.")
    else:
        if not title.strip():
            raise FeatureLoopError("A new feature needs a title.")
        slug = workspace_slug.strip() or _host_slug(session, chat_session)
        try:
            ticket = TicketService(session).create_ticket(
                workspace_slug=slug,
                title=title.strip(),
                work_item_type=(
                    WorkItemType.FEATURE if parent_ticket_id.strip() else WorkItemType.MILESTONE
                ),
                parent_ticket_id=parent_ticket_id.strip() or None,
                description=f"Built from a Baxter chat: {chat_session.title}",
                created_by=LOOP_ACTOR,
            )
        except (LookupError, ValueError) as exc:
            raise FeatureLoopError(f"Could not create the feature: {exc}") from exc
    chat_session.feature_ticket_id = ticket.id
    session.add(chat_session)
    session.commit()
    return ticket


def _host_slug(session: Session, chat_session: BaxterChatSession) -> str:
    workspace = session.get(Workspace, chat_session.workspace_id)
    if workspace is None:
        raise FeatureLoopError("The workspace this chat runs in no longer exists.")
    return workspace.slug


# --- the queue ----------------------------------------------------------------


def _known_texts(session: Session, chat_session: BaxterChatSession, ticket: Ticket) -> set[str]:
    on_ticket = set(load_criteria(ticket.acceptance_criteria_json))
    open_rows = {
        row.text
        for row in _criteria(session, chat_session.id)
        if row.status != FeatureCriterionStatus.REJECTED
    }
    return on_ticket | open_rows


def propose_criteria(
    session: Session, chat_session: BaxterChatSession, texts: Sequence[str]
) -> list[FeatureCriterion]:
    """Baxter's drafts. They reach the ticket only once the operator accepts them.

    A text already on the ticket, or already proposed, queued or met here, is
    skipped rather than drafted twice.
    """
    ticket = _require_ticket(session, chat_session)
    known = _known_texts(session, chat_session, ticket)
    fresh = [text for text in dict.fromkeys(normalize_criteria(texts)) if text not in known]
    rows = [
        FeatureCriterion(session_id=chat_session.id, ticket_id=ticket.id, text=text)
        for text in fresh
    ]
    session.add_all(rows)
    session.commit()
    return rows


def _check_capacity(ticket: Ticket, queued: int, adding: int) -> None:
    """Refuse before accepting what the ticket could never take.

    Checked here rather than when the round starts, where a refusal would
    strand criteria the operator had already said yes to.
    """
    on_ticket = len(load_criteria(ticket.acceptance_criteria_json))
    total = on_ticket + queued + adding
    if total > MAX_ACCEPTANCE_CRITERIA:
        raise FeatureLoopError(
            f"The ticket holds at most {MAX_ACCEPTANCE_CRITERIA} criteria: it has {on_ticket}, "
            f"{queued} are queued, and this would add {adding}. Finish this feature, then "
            "start a new loop for the rest."
        )


def add_criterion(session: Session, chat_session: BaxterChatSession, text: str) -> FeatureCriterion:
    """A criterion the operator typed: accepted as written, so it queues directly."""
    ticket = _require_ticket(session, chat_session)
    [clean] = normalize_criteria([text]) or [""]
    if not clean:
        raise FeatureLoopError("A criterion needs some text.")
    if clean in _known_texts(session, chat_session, ticket):
        raise FeatureLoopError("That criterion is already in this loop.")
    queued = len(_criteria(session, chat_session.id, (FeatureCriterionStatus.QUEUED,)))
    _check_capacity(ticket, queued, 1)
    row = FeatureCriterion(
        session_id=chat_session.id,
        ticket_id=ticket.id,
        text=clean,
        status=FeatureCriterionStatus.QUEUED,
        decided_at=utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def decide_criteria(
    session: Session, chat_session: BaxterChatSession, ids: Sequence[str], *, accept: bool
) -> list[FeatureCriterion]:
    """Accept (queue) or drop proposed criteria. Only proposals can be decided."""
    ticket = _require_ticket(session, chat_session)
    proposed = {
        row.id: row
        for row in _criteria(session, chat_session.id, (FeatureCriterionStatus.PROPOSED,))
    }
    missing = [i for i in ids if i not in proposed]
    if missing:
        raise FeatureLoopError(
            f"Not a proposal waiting on you: {', '.join(missing)}. Reload the Feature card."
        )
    chosen = [proposed[i] for i in dict.fromkeys(ids)]
    if accept:
        queued = len(_criteria(session, chat_session.id, (FeatureCriterionStatus.QUEUED,)))
        _check_capacity(ticket, queued, len(chosen))
    status = FeatureCriterionStatus.QUEUED if accept else FeatureCriterionStatus.REJECTED
    now = utcnow()
    for row in chosen:
        row.status = status
        row.decided_at = now
        session.add(row)
    session.commit()
    return chosen


# --- the loop -----------------------------------------------------------------


def assess(session: Session, ticket: Ticket, rows: Sequence[FeatureCriterion]) -> LoopAssessment:
    """What the loop is doing, read-only. ``advance_feature_loop`` acts on the same reading."""
    if activity_for(session, ticket.id) != TicketActivity.IDLE:
        return LoopAssessment(FeatureLoopStatus.RUNNING)
    if ticket.state == TicketState.BLOCKED:
        return LoopAssessment(
            FeatureLoopStatus.BLOCKED,
            ticket.blocking_issues.strip()
            or "The ticket is blocked. Clear it on the ticket, then the loop resumes.",
        )
    in_round = [r for r in rows if r.status == FeatureCriterionStatus.IN_ROUND]
    if in_round and ticket.state != TicketState.DONE:
        return LoopAssessment(
            FeatureLoopStatus.BLOCKED,
            "This round's workflow stopped before the ticket was done. Open the ticket to "
            "see the last stage, then requeue it; the loop picks up from there.",
        )
    return LoopAssessment(FeatureLoopStatus.IDLE)


def _first_stage_key(session: Session, ticket: Ticket) -> str:
    _template, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        raise FeatureLoopError(f"{ticket.external_id or ticket.id} has no workflow to run.")
    return stages[0].key


def _start_workflow(session: Session, ticket: Ticket) -> None:
    """Start the ticket's whole pipeline through the shared slot pool, as the board does.

    A full pool parks the request in the queue, and a lane starts it later;
    `activity_for` reads that as queued, so the loop waits for it.
    """
    reservation = QueueAdmissionService(session).reserve_orchestration(ticket)
    if not reservation.admitted:
        return
    callbacks = OrchestrationCallbackService(session)
    claim = callbacks.claim_orchestration_run(
        ticket,
        driver=None,
        auto_approve=False,
        approve_design_plans=True,
        auto_repair=True,
        stop_at_stage_key="",
        timeout_override_seconds=None,
    )
    reservation.bind(orchestration_run_id=claim.id)
    try:
        schedule_orchestration(
            ticket.id,
            max_stages=None,
            driver=None,
            stop_at_stage_key=None,
            auto_approve=False,
            approve_design_plans=True,
            auto_repair=True,
            timeout_seconds=None,
        )
    except ValueError as exc:
        callbacks.abandon_claim(claim, message=str(exc))
        reservation.release()
        raise FeatureLoopError(f"The round's workflow did not start: {exc}") from exc


def _start_round(
    session: Session, ticket: Ticket, rows: list[FeatureCriterion], queued: list[FeatureCriterion]
) -> None:
    round_no = max((r.round for r in rows), default=0) + 1
    before = load_criteria(ticket.acceptance_criteria_json)
    merged = merge_criteria(before, [r.text for r in queued], "append")
    guard_criteria_growth(before, merged)
    reopen = ticket.state == TicketState.DONE
    orch = OrchestrationService(session)
    # One write: a done ticket's criteria are frozen unless the same write reopens it.
    orch.update_ticket_manual(
        ticket,
        UpdateTicketRequest(
            acceptance_criteria=merged, state=TicketState.BACKLOG if reopen else None
        ),
    )
    if reopen:
        requeue_stage(
            session,
            orch,
            ticket,
            stage_key=_first_stage_key(session, ticket),
            reason=f"Feature loop round {round_no}: {len(queued)} new criteria from chat",
            actor=LOOP_ACTOR,
        )
    for row in queued:
        row.status = FeatureCriterionStatus.IN_ROUND
        row.round = round_no
        session.add(row)
    session.commit()
    _start_workflow(session, ticket)


def advance_feature_loop(session: Session, chat_session: BaxterChatSession) -> LoopAssessment:
    """Settle a finished round, then start the next if criteria are queued.

    Idempotent: with nothing finished and nothing queued it writes nothing.
    """
    ticket = _require_ticket(session, chat_session)
    rows = _criteria(session, chat_session.id)
    if activity_for(session, ticket.id) == TicketActivity.IDLE and ticket.state == TicketState.DONE:
        settled = [r for r in rows if r.status == FeatureCriterionStatus.IN_ROUND]
        for row in settled:
            row.status = FeatureCriterionStatus.MET
            session.add(row)
        if settled:
            session.commit()
    reading = assess(session, ticket, rows)
    queued = [r for r in rows if r.status == FeatureCriterionStatus.QUEUED]
    if reading.status != FeatureLoopStatus.IDLE or not queued:
        return reading
    _start_round(session, ticket, rows, queued)
    session.refresh(ticket)
    return assess(session, ticket, _criteria(session, chat_session.id))


def advance_feature_loops(session: Session) -> int:
    """The reconcile pass: advance every loop with a round to settle or criteria waiting.

    One loop's failure is logged and does not stop the others; its card shows
    the stalled round, so the failure is visible where the operator looks.
    """
    sessions = session.exec(
        select(BaxterChatSession)
        .join(FeatureCriterion, col(FeatureCriterion.session_id) == BaxterChatSession.id)
        .where(
            col(FeatureCriterion.status).in_(
                [FeatureCriterionStatus.QUEUED, FeatureCriterionStatus.IN_ROUND]
            )
        )
        .distinct()
    ).all()
    advanced = 0
    for chat_session in sessions:
        try:
            advance_feature_loop(session, chat_session)
            advanced += 1
        except ValueError:
            session.rollback()
            logger.exception("Feature loop for chat %s could not advance", chat_session.id)
    return advanced


# --- what the chat sees -------------------------------------------------------


def delete_feature_loop(session: Session, session_id: str) -> None:
    """Drop a thread's criteria ahead of the thread. The ticket stays. The caller commits."""
    for row in _criteria(session, session_id):
        session.delete(row)


def with_feature_loop_tools(
    session: Session, chat_session: BaxterChatSession | None, agent: dict
) -> dict:
    """Grant the proposal tool to a thread that runs a loop, and only to one."""
    if chat_session is None or loop_ticket(session, chat_session) is None:
        return agent
    tools = list(agent.get("mcp_tools") or [])
    if McpTool.PROPOSE_FEATURE_CRITERIA.value not in tools:
        tools.append(McpTool.PROPOSE_FEATURE_CRITERIA.value)
    return {**agent, "mcp_tools": tools}


def feature_prompt_lines(session: Session, chat_session: BaxterChatSession | None) -> list[str]:
    """The loop's state and Baxter's part in it, for the turn's prompt."""
    if chat_session is None:
        return []
    ticket = loop_ticket(session, chat_session)
    if ticket is None:
        return []
    lines = [
        "",
        "## Feature loop",
        f"This chat builds ticket {ticket.external_id} (id {ticket.id}): {ticket.title}, "
        f"state {ticket.state.value}, stage {ticket.workflow_stage_key or '—'}.",
        "Your part: when the operator asks for behaviour this feature should have, turn it "
        "into small, testable acceptance criteria and call "
        f"{McpTool.PROPOSE_FEATURE_CRITERIA.value} with chat_session_id `{chat_session.id}`. "
        "The operator accepts or drops each proposal; accepted ones run on the ticket's "
        "workflow in the next round. Never edit the ticket's criteria or start its "
        "workflow yourself.",
    ]
    rows = [r for r in _criteria(session, chat_session.id) if r.status in _OPEN]
    for row in rows:
        lines.append(f"- [{row.status.value}] {row.text}")
    if not rows:
        lines.append("No criteria are open in this loop yet.")
    return lines


def _runs_view(session: Session, ticket_id: str) -> list[dict]:
    runs = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(col(AgentRun.started_at).desc())
        .limit(MAX_LOOP_RUNS)
    ).all()
    return [
        {
            "id": run.id,
            "run_code": run.run_code,
            "agent_id": run.agent_id,
            "stage_key": run.stage_key,
            "status": run.status.value,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        for run in runs
    ]


def feature_loop_view(session: Session, chat_session: BaxterChatSession) -> dict | None:
    """The Feature card's data, or None for a thread with no loop."""
    ticket = loop_ticket(session, chat_session)
    if ticket is None:
        return None
    rows = _criteria(session, chat_session.id)
    reading = assess(session, ticket, rows)
    workspace = session.get(Workspace, ticket.workspace_id) if ticket.workspace_id else None
    return {
        "ticket": {
            "id": ticket.id,
            "external_id": ticket.external_id,
            "title": ticket.title,
            "state": ticket.state.value,
            "stage_key": ticket.workflow_stage_key,
            "workspace_slug": workspace.slug if workspace else None,
            "criteria_count": len(load_criteria(ticket.acceptance_criteria_json)),
        },
        "status": reading.status.value,
        "reason": reading.reason,
        "round": max((r.round for r in rows), default=0),
        "max_criteria": MAX_ACCEPTANCE_CRITERIA,
        "criteria": [
            {"id": r.id, "text": r.text, "status": r.status.value, "round": r.round}
            for r in rows
            if r.status != FeatureCriterionStatus.REJECTED
        ],
        "runs": _runs_view(session, ticket.id),
    }
