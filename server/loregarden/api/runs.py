from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from loregarden.db.session import get_session
from loregarden.models.domain import RunStatus
from loregarden.services.run_errors import normalize_timeout_stderr
from loregarden.services.run_service import RunService

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
