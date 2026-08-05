"""Asides — questions put to a ticket while one of its runs is still working.

Two channels already carried traffic in this direction and neither fits.
``run_steering`` is imperative: it writes into the agent's stdin, changes the
work, and exists only for a RUNNING claude-adapter run. Ticket chat is the
opposite problem — ``start_triage_run`` refuses outright while another run is
active, which is exactly when an operator wants to ask something.

So an aside is answered by a *different* agent than the one it is about: a
read-only observer turn that reads the ticket and the run's log and answers from
the record. That costs nothing to the run and works whatever the adapter is
doing, at the price of being a reconstruction rather than testimony. The card
says so in words, because an operator who mistakes one for the other has been
misled by us, not by the model.

Escalation is the escape hatch for when the reconstruction will not do: the same
question goes into the live run's stdin through ``run_steering``. That is a real
perturbation of the work, so it is a separate, explicit act — never the default.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from loregarden.models.domain import AgentRun, BtwStatus, Ticket, TriageMessage, Workspace
from loregarden.models.domain.chat_primitives import BtwPart
from loregarden.models.domain.tables import BtwExchange
from loregarden.services.artifact_service import load_run_log
from loregarden.services.chat_primitives import parts_to_jsonable
from loregarden.services.cli_agent_runner import run_cli_agent_turn, stub_response
from loregarden.services.run_concurrency import IN_FLIGHT_STATUSES
from loregarden.services.run_steering import queue_message, steer_refusal
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    TRIAGE_CLI_PROFILE,
    apply_triage_runtime_overrides,
)
from sqlmodel import Session, col, select

BTW_CLI_PROFILE = replace(
    TRIAGE_CLI_PROFILE,
    stub_env="LOREGARDEN_BTW_STUB_RESPONSE",
    timeout_env="LOREGARDEN_BTW_TIMEOUT",
    tmp_prefix="loregarden-btw-",
    # An aside is a sentence-sized question. A long answer here is a sign the
    # observer has started narrating the run rather than answering.
    reply_cap=2000,
)

MAX_QUESTION_CHARS = 2000
#: Log tail handed to the observer. Long runs produce megabytes; the last stretch
#: is what a question asked *now* is almost always about, and every extra line is
#: paid for on a channel meant to be cheap.
MAX_LOG_LINES = 40
MAX_LOG_LINE_CHARS = 300

ESCALATION_PREFIX = (
    "BTW — the operator has a question about what you are doing. Answer it in a "
    "sentence or two and then carry on with the task exactly as you were. Do not "
    "change your plan, and do not treat this as a new instruction.\n\nQuestion: "
)


def find_observed_run(session: Session, ticket_id: str) -> AgentRun | None:
    """The run an aside asked right now is about.

    A live stage run wins over a live triage turn: when both are going, the work
    is what the operator is watching. With nothing in flight it falls back to the
    most recent run, because "what did you just do" is as much an aside as "what
    are you doing" and refusing one of them would make the composer's behaviour
    depend on a race.
    """
    in_flight = session.exec(
        select(AgentRun)
        .where(
            AgentRun.ticket_id == ticket_id,
            col(AgentRun.status).in_(IN_FLIGHT_STATUSES),
        )
        .order_by(AgentRun.created_at.desc())
    ).all()
    for run in in_flight:
        if run.agent_id != TRIAGE_AGENT_ID:
            return run
    if in_flight:
        return in_flight[0]
    return session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id, AgentRun.agent_id != TRIAGE_AGENT_ID)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    ).first()


def ask(session: Session, ticket: Ticket, question: str) -> BtwExchange:
    """Record an aside. Executes nothing — schedule the answer turn next."""
    text = question.strip()
    if not text:
        raise ValueError("Question cannot be empty")

    run = find_observed_run(session, ticket.id)
    exchange = BtwExchange(
        ticket_id=ticket.id,
        observed_run_id=run.id if run else None,
        question=text[:MAX_QUESTION_CHARS],
        status=BtwStatus.PENDING,
    )
    session.add(exchange)
    session.commit()
    session.refresh(exchange)
    return exchange


def _log_tail(session: Session, run_id: str) -> str:
    body = load_run_log(session, run_id) or {}
    lines = body.get("lines")
    live = body.get("live")
    rendered: list[str] = []
    if isinstance(lines, list):
        for line in lines[-MAX_LOG_LINES:]:
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "")[:MAX_LOG_LINE_CHARS]
            rendered.append(f"{line.get('time', '')} {line.get('tag', '')} {text}".strip())
    if isinstance(live, str) and live.strip():
        rendered.append(f"[live] {live.strip()[:MAX_LOG_LINE_CHARS]}")
    return "\n".join(rendered)


def build_btw_prompt(session: Session, ticket: Ticket, run: AgentRun | None, question: str) -> str:
    """The observer's brief: answer from the record, and say so when it cannot."""
    ac = json.loads(ticket.acceptance_criteria_json or "[]")
    sections = [
        "# Aside about a run in progress",
        f"You are {TRIAGE_AGENT_NAME}, observing a run on the operator's behalf.",
        "",
        "The operator has a question about a run that is happening right now. You are NOT "
        "that run — you are reading its log from the outside. Answer only from the record "
        "below and from the repository as it stands.",
        "",
        "Rules for this turn:",
        "- Answer in one or two sentences. This is an aside, not a report.",
        "- Never state the running agent's intent as fact. The log shows what it did, not "
        "why. If the question asks why, say what the record supports and mark the rest as "
        "inference.",
        "- If the log does not contain the answer, say so plainly and suggest putting the "
        "question to the running agent directly. A guess dressed as an observation is worse "
        "than no answer.",
        "- Do not modify anything. This channel is read-only by design.",
        "",
        f"Ticket: {ticket.external_id} — {ticket.title}",
        f"State: {ticket.state.value}",
        f"Workflow stage: {ticket.workflow_stage_key} ({ticket.workflow_stage_status.value})",
        "",
        "## Acceptance criteria",
        *([f"- {item}" for item in ac] if ac else ["- None"]),
    ]

    if run is None:
        sections.extend(
            [
                "",
                "## The run",
                "No run has executed for this ticket yet — answer from the ticket and the "
                "repository alone, and say that there is nothing running.",
            ]
        )
    else:
        finished = run.status not in set(IN_FLIGHT_STATUSES)
        sections.extend(
            [
                "",
                "## The run",
                f"- {run.run_code} · stage `{run.stage_key}` · agent `{run.agent_id}` "
                f"· {run.status.value}",
                (
                    "- This run has already finished; the operator is asking about work that "
                    "is over."
                    if finished
                    else "- This run is still going right now."
                ),
            ]
        )
        if run.stderr:
            sections.append(f"- stderr: {run.stderr[:400]}")
        tail = _log_tail(session, run.id)
        sections.extend(
            [
                "",
                "## Log tail",
                tail or "(this run has produced no log output yet)",
            ]
        )

    sections.extend(["", "## The operator's question", question, "", "Answer it concisely."])
    return "\n".join(sections)


def answer_text(session: Session, exchange: BtwExchange, ticket: Ticket) -> str:
    """Run the observer turn and return its reply. Raises if the CLI fails."""
    stub = stub_response(BTW_CLI_PROFILE)
    if stub is not None:
        return stub

    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Ticket workspace not found")

    run = session.get(AgentRun, exchange.observed_run_id) if exchange.observed_run_id else None
    prompt = build_btw_prompt(session, ticket, run, exchange.question)
    effective = apply_triage_runtime_overrides(workspace, ticket)
    return run_cli_agent_turn(
        BTW_CLI_PROFILE,
        workspace=effective,
        prompt=prompt,
        workspace_slug=effective.slug or workspace.slug or "",
        read_only=True,
    )


def _btw_part(session: Session, exchange: BtwExchange) -> BtwPart:
    run = session.get(AgentRun, exchange.observed_run_id) if exchange.observed_run_id else None
    return BtwPart(
        exchange_id=exchange.id,
        ticket_id=exchange.ticket_id,
        question=exchange.question,
        answer=exchange.answer,
        observed_run_id=run.id if run else None,
        observed_agent_id=run.agent_id if run else None,
        observed_stage_key=run.stage_key if run else None,
        escalated=exchange.escalated_at is not None,
    )


def record_answer(session: Session, exchange: BtwExchange, answer: str) -> TriageMessage:
    """Settle the exchange and mirror it into the ticket's transcript.

    One assistant message carrying one card, rather than the question-then-answer
    pair ``triage_question_log`` writes: an aside is a single exchange, and
    splitting it would put the operator's words in the thread twice — once as a
    message, once inside the card that answers them.
    """
    exchange.answer = (answer or "").strip()[: BTW_CLI_PROFILE.reply_cap]
    exchange.status = BtwStatus.ANSWERED
    exchange.answered_at = datetime.now(timezone.utc)
    session.add(exchange)

    message = TriageMessage(
        ticket_id=exchange.ticket_id,
        role="assistant",
        content=exchange.answer,
        parts_json=json.dumps(parts_to_jsonable([_btw_part(session, exchange)])),
        # Deliberately not the observed run: this message was not produced by it,
        # and attributing it there would make the run's own transcript wrong.
        run_id=None,
    )
    session.add(message)
    session.commit()
    session.refresh(exchange)
    session.refresh(message)
    return message


def record_failure(session: Session, exchange: BtwExchange, error: str) -> None:
    exchange.status = BtwStatus.FAILED
    exchange.error = (error or "")[:4000]
    exchange.answered_at = datetime.now(timezone.utc)
    session.add(exchange)
    session.commit()


def escalate(session: Session, exchange: BtwExchange) -> None:
    """Put the same question to the working agent itself.

    This writes into the run's context and will influence it — the observer's
    answer costs the run nothing, and this one does not. Raises ValueError with
    ``run_steering``'s own reason when the run cannot take it, so the UI and the
    API say the same thing.
    """
    refusal = escalation_refusal(session, exchange)
    if refusal:
        raise ValueError(refusal)
    run = session.get(AgentRun, exchange.observed_run_id)
    assert run is not None  # escalation_refusal rejects a missing run

    queue_message(session, run, f"{ESCALATION_PREFIX}{exchange.question}")
    exchange.escalated_at = datetime.now(timezone.utc)
    session.add(exchange)
    session.commit()
    session.refresh(exchange)


def escalation_refusal(session: Session, exchange: BtwExchange) -> str:
    """Why this aside cannot be put to the running agent, or "" when it can."""
    run = session.get(AgentRun, exchange.observed_run_id) if exchange.observed_run_id else None
    if run is None:
        return "There is no run to ask — this aside was answered from the record alone."
    return steer_refusal(run)


def exchange_view(session: Session, exchange: BtwExchange) -> dict:
    run = session.get(AgentRun, exchange.observed_run_id) if exchange.observed_run_id else None
    return {
        "id": exchange.id,
        "ticket_id": exchange.ticket_id,
        "question": exchange.question,
        "answer": exchange.answer,
        "status": exchange.status.value,
        "error": exchange.error,
        "escalated": exchange.escalated_at is not None,
        "escalation_refusal": escalation_refusal(session, exchange),
        "observed_run_id": run.id if run else None,
        "observed_agent_id": run.agent_id if run else None,
        "observed_stage_key": run.stage_key if run else None,
        "observed_run_active": bool(run and run.status in set(IN_FLIGHT_STATUSES)),
        "created_at": exchange.created_at.isoformat(),
        "answered_at": exchange.answered_at.isoformat() if exchange.answered_at else None,
    }


def list_exchanges(session: Session, ticket_id: str, *, limit: int = 50) -> list[BtwExchange]:
    return list(
        session.exec(
            select(BtwExchange)
            .where(BtwExchange.ticket_id == ticket_id)
            .order_by(BtwExchange.created_at.desc())
            .limit(limit)
        ).all()
    )


def pending_exchanges(session: Session, ticket_id: str) -> list[BtwExchange]:
    """Asides still waiting on an answer, oldest first.

    These have no chat message yet — the mirror is written when the answer lands —
    so a surface that shows only the transcript shows nothing while one is in
    flight.
    """
    return list(
        session.exec(
            select(BtwExchange)
            .where(
                BtwExchange.ticket_id == ticket_id,
                BtwExchange.status == BtwStatus.PENDING,
            )
            .order_by(BtwExchange.created_at)
        ).all()
    )
