"""Preview a cleanup, then carry out exactly the part a person confirmed.

Two steps, so nothing is moved on the strength of a picture that may be stale:

1. `preview` assesses every selected path (`safety.assess`) and keeps the plan
   for ten minutes under an id, with each allowed path's fingerprint.
2. `execute` takes that id and the paths the person confirmed, which must be
   allowed items of that plan. Each is assessed **again**; one that now
   refuses, or whose fingerprint changed (something wrote into it since),
   is refused rather than moved. A plan is used once.

Everything goes to the Trash. A worktree's directory goes to the Trash and
`git worktree prune` then drops git's record of it, so its branch can be
checked out elsewhere. Every outcome, refusals and failures included, is a
`DiskCleanupEvent` row.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from loregarden.models.domain import (
    DiskCleanupEvent,
    DiskCleanupMethod,
    DiskCleanupOutcome,
    Worktree,
    WorktreeState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.disk_usage.roots import scan_plan
from loregarden.services.disk_usage.safety import Verdict, assess, build_context
from loregarden.services.disk_usage.store import find_node, latest_result, remove_from_result
from loregarden.services.git_subprocess import run_git
from pydantic import BaseModel, Field
from send2trash import send2trash
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

PLAN_TTL = timedelta(minutes=10)
MAX_PATHS = 200


class PlanItem(BaseModel):
    verdict: Verdict
    #: From the last scan; 0 when the scan never saw it.
    size_bytes: int = 0


class CleanupPlan(BaseModel):
    plan_id: str
    expires_at: datetime
    items: list[PlanItem]
    allowed_bytes: int
    allowed_count: int
    refused_count: int
    #: Selected paths dropped because another selected path contains them.
    covered: list[str] = Field(default_factory=list)


class ItemResult(BaseModel):
    path: str
    outcome: DiskCleanupOutcome
    size_bytes: int = 0
    detail: str = ""


class CleanupResult(BaseModel):
    results: list[ItemResult]
    trashed_bytes: int
    trashed_count: int
    refused_count: int
    failed_count: int


class PlanNotFound(LookupError):
    pass


_PLANS: dict[str, CleanupPlan] = {}
_PLANS_LOCK = threading.Lock()


def preview(session: Session, paths: list[str]) -> CleanupPlan:
    if not paths:
        raise ValueError("Select at least one path.")
    if len(paths) > MAX_PATHS:
        raise ValueError(f"Select at most {MAX_PATHS} paths at once.")
    ctx = build_context(session, scan_plan(session))
    result = latest_result()
    items: list[PlanItem] = []
    covered: list[str] = []
    # Outermost first. A path inside an *allowed* marked folder goes with it; a
    # path inside a *refused* one is judged on its own, since its parent going
    # is no longer the plan.
    for path in sorted(dict.fromkeys(p.rstrip("/") or "/" for p in paths), key=len):
        target = Path(path)
        if any(
            item.verdict.allowed and target.is_relative_to(Path(item.verdict.path))
            for item in items
        ):
            covered.append(path)
            continue
        node = find_node(result, path) if result else None
        items.append(PlanItem(verdict=assess(ctx, path), size_bytes=node.size_bytes if node else 0))
    allowed = [item for item in items if item.verdict.allowed]
    plan = CleanupPlan(
        plan_id=str(uuid4()),
        expires_at=utcnow() + PLAN_TTL,
        items=items,
        allowed_bytes=sum(item.size_bytes for item in allowed),
        allowed_count=len(allowed),
        refused_count=len(items) - len(allowed),
        covered=covered,
    )
    with _PLANS_LOCK:
        now = utcnow()
        for stale in [pid for pid, p in _PLANS.items() if p.expires_at <= now]:
            del _PLANS[stale]
        _PLANS[plan.plan_id] = plan
    return plan


def _take_plan(plan_id: str) -> CleanupPlan:
    with _PLANS_LOCK:
        plan = _PLANS.pop(plan_id, None)
    if plan is None or plan.expires_at <= utcnow():
        raise PlanNotFound("This cleanup preview has expired or was already used. Review it again.")
    return plan


def _forget_worktree(session: Session, path: Path, repo: str) -> str:
    """Prune git's record of a trashed worktree, and retire any row tracking it."""
    detail = ""
    proc = run_git(
        ["worktree", "prune"], cwd=repo, check=False, capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        detail = f"Moved, but `git worktree prune` failed in {repo}: {(proc.stderr or '').strip()}"
        logger.warning(detail)
    for row in session.exec(select(Worktree)).all():
        if (
            row.worktree_path
            and Path(row.worktree_path).resolve() == path
            and row.state is not WorktreeState.CLEANUP
        ):
            row.state = WorktreeState.CLEANUP
            row.cleaned_at = utcnow()
            session.add(row)
    return detail


def _record(
    session: Session, item: PlanItem, outcome: DiskCleanupOutcome, detail: str
) -> ItemResult:
    verdict = item.verdict
    session.add(
        DiskCleanupEvent(
            path=verdict.path,
            size_bytes=item.size_bytes,
            kind=verdict.kind,
            method=verdict.method or DiskCleanupMethod.TRASH,
            outcome=outcome,
            detail=detail,
        )
    )
    return ItemResult(path=verdict.path, outcome=outcome, size_bytes=item.size_bytes, detail=detail)


def _carry_out(session: Session, item: PlanItem, fresh: Verdict) -> ItemResult:
    planned = item.verdict
    if not fresh.allowed:
        return _record(session, item, DiskCleanupOutcome.REFUSED, " ".join(fresh.reasons))
    if fresh.fingerprint != planned.fingerprint or fresh.method != planned.method:
        return _record(
            session,
            item,
            DiskCleanupOutcome.REFUSED,
            "It changed after the preview. Review it again.",
        )
    try:
        send2trash(planned.path)
    except OSError as exc:
        logger.exception("Moving %s to the Trash failed", planned.path)
        return _record(
            session, item, DiskCleanupOutcome.FAILED, f"Could not move it to the Trash: {exc}"
        )
    detail = ""
    if planned.method is DiskCleanupMethod.TRASH_WORKTREE:
        detail = _forget_worktree(session, Path(planned.path), planned.worktree_repo)
    return _record(session, item, DiskCleanupOutcome.TRASHED, detail)


def execute(session: Session, plan_id: str, paths: list[str]) -> CleanupResult:
    plan = _take_plan(plan_id)
    by_path = {item.verdict.path: item for item in plan.items if item.verdict.allowed}
    unknown = [p for p in paths if p not in by_path]
    if unknown:
        raise ValueError(f"Not allowed by this preview: {', '.join(unknown[:5])}")
    if not paths:
        raise ValueError("Confirm at least one path.")
    ctx = build_context(session, scan_plan(session))
    results = [
        _carry_out(session, by_path[path], assess(ctx, path)) for path in dict.fromkeys(paths)
    ]
    session.commit()
    trashed = [r for r in results if r.outcome is DiskCleanupOutcome.TRASHED]
    if trashed:
        remove_from_result([r.path for r in trashed])
    return CleanupResult(
        results=results,
        trashed_bytes=sum(r.size_bytes for r in trashed),
        trashed_count=len(trashed),
        refused_count=sum(1 for r in results if r.outcome is DiskCleanupOutcome.REFUSED),
        failed_count=sum(1 for r in results if r.outcome is DiskCleanupOutcome.FAILED),
    )


def recent_events(session: Session, limit: int = 20) -> list[DiskCleanupEvent]:
    return list(
        session.exec(
            select(DiskCleanupEvent).order_by(DiskCleanupEvent.created_at.desc()).limit(limit)
        ).all()
    )
