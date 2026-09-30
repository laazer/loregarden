"""HARNESS_FAILURE_CLUSTER — workspace share of failed stage runs that are harness.

Failures that are mostly harness interruptions (reload, lease, orphan, restart,
auth/usage) look like a healthy pipeline under FAILURE_CLUSTER: that condition
keys on per-stage rate vs workspace rate, so a uniform harness storm produces no
finding. This detector measures transient share over a recent window of FAILED
stage agent_runs, partitioned by workspace_id.

Report-only. Owns its own windowed query with stdout/stderr — do not widen
``workflow_monitor._runs`` (raiseload omits those columns by design).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import AgentRun, MonitorCondition, RunStatus, Workspace
from loregarden.models.domain.workflow_monitor import MonitorFinding
from loregarden.services.stage_report import harness_cause_label, is_transient_failure
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from sqlmodel import Session, col, select

#: Calibrated HC-5 (primary DB, 90-day weekly backtest, 2026-09-21):
#: WINDOW=14 / MIN=10 / SHARE=0.5 → loregarden 8/14 fire weeks, blobert 4/14;
#: fires blobert's worst weeks (incl. current 10/10 transient); quiet on
#: rejection-heavy lore weeks (share ~0.23). MIN matches FAILURE_CLUSTER_MIN_RUNS.
#: Share threshold barely separates histories (most weeks are extreme) but
#: guards mixed windows; 0.5 is the midpoint of the flat band 0.4–0.7.
WINDOW_DAYS = 14
MIN_FAILURES = 10
SHARE_THRESHOLD = 0.5


def should_fire(
    failed_count: int,
    transient_count: int,
    *,
    min_failures: int = MIN_FAILURES,
    share_threshold: float = SHARE_THRESHOLD,
) -> bool:
    """Pure fire predicate: counts only — no IO, no cause labels, no classifier."""
    if failed_count < min_failures or failed_count <= 0:
        return False
    if transient_count < 0 or transient_count > failed_count:
        return False
    return (transient_count / failed_count) >= share_threshold


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _failed_stage_runs_in_window(session: Session, *, window_start: datetime) -> list[AgentRun]:
    """FAILED stage runs after ``window_start``, excluding triage and null tickets.

    Own query — not ``workflow_monitor._runs``. Needs stdout/stderr for the
    transient classifier and cause breakdown.
    """
    return list(
        session.exec(
            select(AgentRun).where(
                col(AgentRun.status) == RunStatus.FAILED,
                col(AgentRun.started_at) >= window_start,
                col(AgentRun.ticket_id).is_not(None),
                col(AgentRun.agent_id) != TRIAGE_AGENT_ID,
                col(AgentRun.stage_key).is_not(None),
                col(AgentRun.stage_key) != "",
            )
        ).all()
    )


def detect_harness_failure_clusters(session: Session) -> list[MonitorFinding]:
    """One finding per workspace whose recent failed stage runs are mostly harness."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)
    runs = _failed_stage_runs_in_window(session, window_start=window_start)
    if not runs:
        return []

    by_workspace: dict[str, list[AgentRun]] = defaultdict(list)
    for run in runs:
        if not run.workspace_id:
            continue
        started = _as_aware(run.started_at)
        if started is None or started < window_start:
            continue
        by_workspace[run.workspace_id].append(run)

    if not by_workspace:
        return []

    workspaces = {
        ws.id: ws
        for ws in session.exec(
            select(Workspace).where(col(Workspace.id).in_(list(by_workspace)))
        ).all()
    }

    findings: list[MonitorFinding] = []
    for workspace_id, ws_runs in sorted(by_workspace.items()):
        workspace = workspaces.get(workspace_id)
        if workspace is None:
            continue
        failed_count = len(ws_runs)
        transient_count = sum(
            1 for run in ws_runs if is_transient_failure(run.stdout or "", run.stderr or "")
        )
        if not should_fire(failed_count, transient_count):
            continue

        cause_counts: Counter[str] = Counter(
            harness_cause_label(run.stdout or "", run.stderr or "") for run in ws_runs
        )
        breakdown = ", ".join(
            f"{bucket}={cause_counts[bucket]}"
            for bucket in (
                "reload",
                "restart",
                "lease",
                "orphan",
                "stranded",
                "timeout",
                "auth",
                "usage",
                "other",
            )
            if cause_counts[bucket]
        )
        share = transient_count / failed_count
        slug = workspace.slug
        findings.append(
            MonitorFinding(
                condition=MonitorCondition.HARNESS_FAILURE_CLUSTER,
                ticket_id="",
                stage_key=slug,
                summary=(
                    f"Workspace '{slug}' had {transient_count} of {failed_count} failed "
                    f"stage runs ({share:.0%}) classified as harness interruptions over "
                    f"{WINDOW_DAYS} days (threshold {SHARE_THRESHOLD:.0%}, "
                    f"min {MIN_FAILURES}). Cause breakdown: {breakdown}."
                ),
                evidence={
                    "workspace_id": workspace_id,
                    "workspace_slug": slug,
                    "failed": str(failed_count),
                    "transient": str(transient_count),
                    "share": f"{share:.2f}",
                    "threshold": f"{SHARE_THRESHOLD:.2f}",
                    "window_days": str(WINDOW_DAYS),
                    "min_failures": str(MIN_FAILURES),
                    "cause_breakdown": breakdown,
                },
            )
        )
    return findings
