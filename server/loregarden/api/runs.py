from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.core.timestamps import iso_utc
from loregarden.db.session import get_session
from loregarden.models.domain import HandoffCheckinRequest, RunMessageCreate, RunStatus
from loregarden.services.artifact_service import load_run_log
from loregarden.services.run_cancellation import cancel_refusal, request_cancel
from loregarden.services.run_errors import normalize_timeout_stderr
from loregarden.services.run_service import (
    HANDOFF_EXITED_MESSAGE,
    TERMINAL_HANDOFF_COMMAND_PREFIX,
    RunService,
    settle_dead_handoff_run,
)
from loregarden.services.run_steering import list_messages, queue_message, steer_refusal
from loregarden.services.run_token_usage import TokenTotals, ticket_usage
from sqlmodel import Session

_IN_FLIGHT_STATUSES = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def list_runs(
    ticket_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    session: Session = Depends(get_session),
) -> list[dict]:
    svc = RunService(session)
    runs = svc.list_runs(ticket_id=ticket_id, limit=limit)
    return [
        {
            "id": r.id,
            "run_code": r.run_code,
            "ticket_id": r.ticket_id or "",
            "workspace_id": r.workspace_id,
            "agent_id": r.agent_id,
            "skill_name": r.skill_name,
            "stage_key": r.stage_key,
            "status": r.status.value,
            "command": r.command,
            # created_at is the only stamp a run that never started still has —
            # the errors list must be able to say when a dispatch failed too.
            "created_at": iso_utc(r.created_at),
            "started_at": iso_utc(r.started_at),
            "finished_at": iso_utc(r.finished_at),
            "stdout": r.stdout[:2000] if r.stdout else "",
            "stderr": normalize_timeout_stderr(r.stderr[:2000] if r.stderr else ""),
        }
        for r in runs
    ]


def _totals_payload(totals: TokenTotals) -> dict:
    """Totals as JSON, with nulls kept as nulls.

    ``null`` is the answer, not a gap to fill: a client that renders it as 0
    would put unmeasured runs into a cost figure as free work, which is the one
    thing these columns exist to prevent.
    """
    return {
        "key": totals.key,
        "runs": totals.runs,
        "measured_runs": totals.measured_runs,
        "unmeasured_runs": totals.unmeasured_runs,
        "input_tokens": totals.input_tokens,
        "output_tokens": totals.output_tokens,
        "cache_read_tokens": totals.cache_read_tokens,
        "cache_write_tokens": totals.cache_write_tokens,
        "total_tokens": totals.total_tokens,
    }


@router.get("/usage")
def get_ticket_usage(
    ticket_id: str = Query(),
    session: Session = Depends(get_session),
) -> dict:
    """What this ticket's runs cost, whole and per stage.

    Declared above ``/{run_id}`` so ``usage`` is not swallowed as a run id.
    """
    total, by_stage = ticket_usage(session, ticket_id)
    return {
        "ticket_id": ticket_id,
        "total": _totals_payload(total),
        "by_stage": [_totals_payload(stage) for stage in by_stage],
    }


@router.get("/{run_id}/log")
def get_run_log(run_id: str, session: Session = Depends(get_session)) -> dict:
    """Rendered log lines for one run, for the run-log modal.

    Serves the capped `{lines, live}` artifact rather than `AgentRun.stdout`:
    stdout holds the raw stream-json transcript, which is unbounded (megabytes
    per run) and not human-readable. Runs that predate the log streamer have no
    artifact — they return empty `lines` rather than 404, so the caller can still
    show the run's identity.
    """
    svc = RunService(session)
    run = svc.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    body = load_run_log(session, run_id) or {}
    lines = body.get("lines")
    live = body.get("live")
    return {
        "id": run.id,
        "run_code": run.run_code,
        "agent_id": run.agent_id,
        "skill_name": run.skill_name,
        "stage_key": run.stage_key,
        "status": run.status.value,
        "command": run.command,
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
        "lines": lines if isinstance(lines, list) else [],
        "live": live if isinstance(live, str) else None,
        "stderr": normalize_timeout_stderr(run.stderr or ""),
    }


def _message_payload(message) -> dict:
    return {
        "id": message.id,
        "run_id": message.run_id,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
    }


@router.get("/{run_id}/messages")
def get_run_messages(run_id: str, session: Session = Depends(get_session)) -> dict:
    """Steering messages for a run, and whether another can be sent.

    `refusal` is non-empty when the run cannot take one, so the UI can disable
    the composer and say why instead of accepting input that goes nowhere.
    """
    run = RunService(session).get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "messages": [_message_payload(m) for m in list_messages(session, run_id)],
        "refusal": steer_refusal(run),
    }


@router.post("/{run_id}/messages")
def post_run_message(
    run_id: str, body: RunMessageCreate, session: Session = Depends(get_session)
) -> dict:
    """Send a message to a run that is already going."""
    run = RunService(session).get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    try:
        message = queue_message(session, run, body.content)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _message_payload(message)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    """Ask an in-flight run to stop cooperatively."""
    run = RunService(session).get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    try:
        run = request_cancel(session, run)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "id": run.id,
        "status": run.status.value,
        "cancel_requested_at": (
            run.cancel_requested_at.isoformat() if run.cancel_requested_at else None
        ),
        "refusal": cancel_refusal(run),
    }


@router.post("/{run_id}/handoff-checkin")
def handoff_checkin(
    run_id: str, body: HandoffCheckinRequest, session: Session = Depends(get_session)
) -> dict:
    """Record that a pasted terminal-handoff command actually started.

    The command is chained with `&&`, so a 409 here stops the CLI from doing
    stage work against a run that was already reaped as stale.
    """
    run = RunService(session).get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if not run.command.startswith(TERMINAL_HANDOFF_COMMAND_PREFIX):
        raise HTTPException(409, "Run is not a terminal handoff")
    if run.status not in _IN_FLIGHT_STATUSES:
        raise HTTPException(
            409,
            "This handoff is no longer active — generate a fresh handoff command and use that.",
        )
    run.handoff_accepted_at = datetime.now(timezone.utc)
    run.handoff_pid = body.pid
    session.add(run)
    session.commit()
    return {"ok": True}


@router.post("/{run_id}/handoff-exited")
def handoff_exited(run_id: str, session: Session = Depends(get_session)) -> dict:
    """Settle a terminal-handoff run whose CLI session has ended.

    Nothing supervises a handoff, so without this ping a session that ended
    without completing its stage would leave the run RUNNING forever. A no-op
    when the run was already settled (e.g. the stage completed normally).
    """
    run = RunService(session).get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if (
        run.command.startswith(TERMINAL_HANDOFF_COMMAND_PREFIX)
        and run.status in _IN_FLIGHT_STATUSES
    ):
        settle_dead_handoff_run(session, run, message=HANDOFF_EXITED_MESSAGE)
        session.refresh(run)
    return {"ok": True, "status": run.status.value}


@router.get("/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    svc = RunService(session)
    run = svc.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "id": run.id,
        "run_code": run.run_code,
        "ticket_id": run.ticket_id or "",
        "workspace_id": run.workspace_id,
        "agent_id": run.agent_id,
        "skill_name": run.skill_name,
        "stage_key": run.stage_key,
        "status": run.status.value,
        "command": run.command,
        "stdout": run.stdout,
        "stderr": normalize_timeout_stderr(run.stderr or ""),
        "created_at": iso_utc(run.created_at),
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
    }
