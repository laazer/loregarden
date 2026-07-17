from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.services.artifact_service import load_run_log
from loregarden.services.run_errors import normalize_timeout_stderr
from loregarden.services.run_service import RunService
from sqlmodel import Session

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
