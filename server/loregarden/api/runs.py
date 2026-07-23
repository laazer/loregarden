from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import HandoffCheckinRequest, RunMessageCreate, RunStatus
from loregarden.services.artifact_service import load_run_log
from loregarden.services.run_errors import normalize_timeout_stderr
from loregarden.services.run_service import (
    HANDOFF_EXITED_MESSAGE,
    TERMINAL_HANDOFF_COMMAND_PREFIX,
    RunService,
    settle_dead_handoff_run,
)
from loregarden.services.run_steering import list_messages, queue_message, steer_refusal
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
            "ticket_id": r.ticket_id,
            "workspace_id": r.workspace_id,
            "agent_id": r.agent_id,
            "skill_name": r.skill_name,
            "stage_key": r.stage_key,
            "status": r.status.value,
            "command": r.command,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "stdout": r.stdout[:2000] if r.stdout else "",
            "stderr": normalize_timeout_stderr(r.stderr[:2000] if r.stderr else ""),
        }
        for r in runs
    ]


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
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
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
        "ticket_id": run.ticket_id,
        "workspace_id": run.workspace_id,
        "agent_id": run.agent_id,
        "skill_name": run.skill_name,
        "stage_key": run.stage_key,
        "status": run.status.value,
        "command": run.command,
        "stdout": run.stdout,
        "stderr": normalize_timeout_stderr(run.stderr or ""),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
