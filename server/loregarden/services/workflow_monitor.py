"""Look across runs for the failure modes nothing currently watches for.

Both bugs found while shipping PR #203 had been latent for an unknown period and
were found by reading code, not by any alarm. This is the alarm.

Three decisions worth knowing before changing anything here.

**The substrate is `agent_runs`, not `domain_events`.** `EventBus.list_recent` is
the event log's only query and takes no filters, `GET /api/events` has no client
consumer, and `EventBus.subscribe` is dead code. Its coverage is a strict subset
of what `agent_runs` already indexes.

**Thresholds are relative to the observed baseline**, from
`run_duration_stats.load_duration_stats`, which returns None rather than
inventing a default when there is no history. An absolute constant would be a
lie the day the pipeline improves — and this codebase's own numbers move fast:
`testing` fails 36 times in 50 runs today.

**`scan` decides nothing.** It is read-only and returns findings. Whether a
thrashing stage is a converging rework loop is `rework_feedback`'s ledger to
judge, and that module holds `MAX_REWORK_REROUTES`. A second retry cap here
would be a competing authority on the same question.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    MonitorArtifactKind,
    MonitorCondition,
    RunStatus,
    WorkflowTemplate,
)
from loregarden.models.domain.workflow_monitor import MonitorFinding, MonitorFindingView
from loregarden.services.run_duration_stats import (
    DurationStats,
    canonical_stage_key,
    load_duration_stats,
)
from loregarden.services.studio_drift import detect_all_drift
from loregarden.services.studio_routing import SKIP_CONDITIONS
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from sqlmodel import Session, col, select

#: A stage is thrashing at twice the observed re-run rate, but never below four
#: attempts. The floor matters: `attempts_per_stage` is ~1.4 across this
#: database, and 2.8 attempts is an ordinary rework round, not a defect.
THRASH_FLOOR = 4

#: Repeats on the standalone path, which had no counter at all before 560.
#: Lower than THRASH_FLOOR because nothing else is watching this path.
UNBUDGETED_REPEAT_FLOOR = 3

#: A stage is a failure cluster at twice the workspace failure rate, over a
#: sample big enough for the rate to mean anything.
FAILURE_CLUSTER_MULTIPLE = 2.0
FAILURE_CLUSTER_MIN_RUNS = 10

#: A run is stalled at four times what that stage has ever taken. Falls back to
#: a flat bound only when the stage has no history to be multiplied.
STALL_MULTIPLE = 4.0
STALL_FALLBACK = timedelta(hours=6)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; comparing one to an aware `now` raises."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _runs(session: Session, ticket_id: str | None) -> list[AgentRun]:
    """Stage dispatches only.

    Two exclusions, both load-bearing. Rows with `agent_id == TRIAGE_AGENT_ID`
    are Ticket Studio / Baxter chat turns built by hand in
    `agent_turn_runner._start_run`; they never went through `start_run`, are not
    stage dispatches, and `RunService.list_runs` filters them out for the same
    reason. Counting them is precisely the wrong exhibit that made an earlier
    reading of this data claim one ticket had 28 runaway `triage` attempts.

    Rows with no `ticket_id` are workspace-scoped chat runs with no workflow to
    report a finding against.
    """
    statement = select(AgentRun).where(
        col(AgentRun.agent_id) != TRIAGE_AGENT_ID,
        col(AgentRun.ticket_id).is_not(None),
    )
    if ticket_id:
        statement = statement.where(col(AgentRun.ticket_id) == ticket_id)
    return list(session.exec(statement).all())


def _detect_stage_thrash(runs: list[AgentRun], stats: DurationStats) -> list[MonitorFinding]:
    """A (orchestration run, stage) pair attempted far more than the baseline."""
    threshold = max(THRASH_FLOOR, round(stats.attempts_per_stage * 2))
    attempts: dict[tuple[str, str, str], int] = defaultdict(int)
    for run in runs:
        if not run.orchestration_run_id or not run.stage_key:
            continue
        attempts[(run.ticket_id, run.orchestration_run_id, canonical_stage_key(run.stage_key))] += 1

    return [
        MonitorFinding(
            condition=MonitorCondition.STAGE_THRASH,
            ticket_id=ticket_id,
            stage_key=stage_key,
            summary=(
                f"Stage '{stage_key}' ran {count} times in one orchestration run "
                f"(baseline {stats.attempts_per_stage:.2f} attempts per stage)."
            ),
            evidence={
                "attempts": str(count),
                "threshold": str(threshold),
                "baseline_attempts_per_stage": f"{stats.attempts_per_stage:.2f}",
                "orchestration_run_id": orch_id,
            },
        )
        for (ticket_id, orch_id, stage_key), count in sorted(attempts.items())
        if count >= threshold
    ]


def _detect_unbudgeted_repeats(runs: list[AgentRun]) -> list[MonitorFinding]:
    """Repeat attempts with no orchestration run behind them.

    These are the runs the retry budget could not see before 560, which is how
    one ticket accumulated 28 attempts at a single stage across five days.
    """
    attempts: dict[tuple[str, str], int] = defaultdict(int)
    for run in runs:
        if run.orchestration_run_id or not run.stage_key:
            continue
        attempts[(run.ticket_id, canonical_stage_key(run.stage_key))] += 1

    return [
        MonitorFinding(
            condition=MonitorCondition.UNBUDGETED_REPEAT,
            ticket_id=ticket_id,
            stage_key=stage_key,
            summary=(
                f"Stage '{stage_key}' was dispatched {count} times outside any orchestration run."
            ),
            evidence={"attempts": str(count), "threshold": str(UNBUDGETED_REPEAT_FLOOR)},
        )
        for (ticket_id, stage_key), count in sorted(attempts.items())
        if count >= UNBUDGETED_REPEAT_FLOOR
    ]


def _detect_failure_clusters(runs: list[AgentRun]) -> list[MonitorFinding]:
    """A stage failing far more often than the workspace as a whole.

    Workspace-relative, not absolute: a 30% failure rate is alarming in a healthy
    pipeline and unremarkable in one where everything fails.
    """
    finished = [run for run in runs if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED)]
    if not finished:
        return []
    overall = sum(1 for run in finished if run.status is RunStatus.FAILED) / len(finished)
    if overall <= 0:
        return []

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for run in finished:
        if not run.stage_key:
            continue
        bucket = totals[canonical_stage_key(run.stage_key)]
        bucket[0] += 1
        bucket[1] += 1 if run.status is RunStatus.FAILED else 0

    findings = []
    for stage_key, (total, failed) in sorted(totals.items()):
        if total < FAILURE_CLUSTER_MIN_RUNS:
            continue
        rate = failed / total
        if rate < overall * FAILURE_CLUSTER_MULTIPLE:
            continue
        findings.append(
            MonitorFinding(
                condition=MonitorCondition.FAILURE_CLUSTER,
                stage_key=stage_key,
                summary=(
                    f"Stage '{stage_key}' failed {failed} of {total} runs "
                    f"({rate:.0%}) against a workspace rate of {overall:.0%}."
                ),
                evidence={
                    "failed": str(failed),
                    "total": str(total),
                    "stage_rate": f"{rate:.2f}",
                    "workspace_rate": f"{overall:.2f}",
                },
            )
        )
    return findings


def _detect_stalled_runs(runs: list[AgentRun], stats: DurationStats) -> list[MonitorFinding]:
    """A run still RUNNING long past what its stage has ever taken."""
    now = _utcnow()
    findings = []
    for run in runs:
        if run.status is not RunStatus.RUNNING:
            continue
        started = _as_aware(run.started_at)
        if started is None:
            continue
        median = stats.stage_seconds(run.stage_key, run.agent_id)
        bound = timedelta(seconds=median * STALL_MULTIPLE) if median else STALL_FALLBACK
        elapsed = now - started
        if elapsed <= bound:
            continue
        findings.append(
            MonitorFinding(
                condition=MonitorCondition.STALLED_RUN,
                ticket_id=run.ticket_id,
                stage_key=run.stage_key or "",
                summary=(
                    f"Run has been RUNNING for {elapsed.total_seconds() / 3600:.1f}h, "
                    f"past the {bound.total_seconds() / 3600:.1f}h bound for this stage."
                ),
                evidence={
                    "run_id": run.id,
                    "elapsed_seconds": str(int(elapsed.total_seconds())),
                    "bound_seconds": str(int(bound.total_seconds())),
                    "basis": "stage median" if median else "fallback",
                },
            )
        )
    return findings


def _detect_draft_drift(session: Session) -> list[MonitorFinding]:
    """Studio drafts that would roll back the template they publish to."""
    return [
        MonitorFinding(
            condition=MonitorCondition.DRAFT_DRIFT,
            summary=(
                f"Studio draft '{drift.slug}' differs from "
                f"{drift.published_template_slug}; publishing would overwrite the "
                "live workflow."
            ),
            evidence={
                "slug": drift.slug,
                "stages_added": ", ".join(drift.stages_added) or "-",
                "stages_removed": ", ".join(drift.stages_removed) or "-",
                "stages_changed": ", ".join(sorted(drift.stages_changed)) or "-",
                "stranded_tickets": str(drift.stranded.count),
            },
        )
        for drift in detect_all_drift(session)
        if drift.drifted
    ]


def _detect_skip_condition_rot(session: Session) -> list[MonitorFinding]:
    """A `skip_when` naming a condition no resolver knows.

    `should_skip_stage` fails open on an unknown condition, which is right at
    runtime — a typo must not prune a stage. But it means a stage carrying
    `has_acceptance_critera` never prunes and nothing anywhere says so. This is
    the only thing that would notice.
    """
    findings = []
    for template in session.exec(select(WorkflowTemplate)).all():
        for stage in json.loads(template.stages_json or "[]"):
            condition = stage.get("skip_when") or ""
            if not condition or condition in SKIP_CONDITIONS:
                continue
            findings.append(
                MonitorFinding(
                    condition=MonitorCondition.SKIP_CONDITION_ROT,
                    stage_key=stage.get("key", ""),
                    summary=(
                        f"Template '{template.slug}' stage '{stage.get('key')}' declares "
                        f"skip_when '{condition}', which no resolver knows — the stage "
                        "will never be pruned."
                    ),
                    evidence={
                        "template": template.slug,
                        "skip_when": condition,
                        "known": ", ".join(SKIP_CONDITIONS),
                    },
                )
            )
    return findings


def scan(session: Session, *, ticket_id: str | None = None) -> list[MonitorFinding]:
    """Every condition, read-only. Mutates nothing, decides nothing.

    `ticket_id` narrows the run-derived detections to one ticket. The
    workspace-wide ones (failure clusters, draft drift, skip-condition rot) are
    properties of the templates and the pipeline rather than of a ticket, so
    they are skipped when scanning a single one rather than reported against a
    ticket that did not cause them.
    """
    runs = _runs(session, ticket_id)
    stats = load_duration_stats(session)

    findings = [
        *_detect_stage_thrash(runs, stats),
        *_detect_unbudgeted_repeats(runs),
        *_detect_stalled_runs(runs, stats),
    ]
    if ticket_id is None:
        findings.extend(_detect_failure_clusters(runs))
        findings.extend(_detect_draft_drift(session))
        findings.extend(_detect_skip_condition_rot(session))
    return findings


#: Conditions that are properties of the pipeline or the templates rather than
#: of any one ticket. They have no `ticket_id`, and `artifacts.ticket_id` is NOT
#: NULL behind an enforced foreign key — so rather than invent a ticket to hang
#: them on, they are recomputed on read. The cost is that they carry no
#: occurrence count; the alternative was a schema change for a report-only
#: feature, or a fabricated row that every later join would have to know about.
WORKSPACE_SCOPED = frozenset(
    {
        MonitorCondition.FAILURE_CLUSTER,
        MonitorCondition.DRAFT_DRIFT,
        MonitorCondition.SKIP_CONDITION_ROT,
    }
)


def _finding_title(finding: MonitorFinding) -> str:
    """The artifact title, which is also the upsert key.

    Encoded in the title because `artifacts` has no column for it. Stable across
    sweeps by construction: condition, ticket and stage are exactly the tuple
    the ticket asks findings to be deduplicated on.
    """
    return f"{finding.condition.value}:{finding.stage_key or '-'}"


def record_findings(session: Session, findings: list[MonitorFinding]) -> int:
    """Upsert ticket-scoped findings, returning how many rows were touched.

    Upserted, not appended. `record_gate_evaluation` appends, which is why
    `context` is the largest artifact kind in the database at 1765 rows; a sweep
    that runs on the reconcile timer would beat that inside a week.
    """
    touched = 0
    for finding in findings:
        if finding.condition in WORKSPACE_SCOPED or not finding.ticket_id:
            continue
        title = _finding_title(finding)
        existing = session.exec(
            select(Artifact).where(
                col(Artifact.ticket_id) == finding.ticket_id,
                col(Artifact.kind) == MonitorArtifactKind.FINDING.value,
                col(Artifact.title) == title,
            )
        ).first()
        now = _utcnow()
        payload = {
            "condition": finding.condition.value,
            "stage_key": finding.stage_key,
            "summary": finding.summary,
            "evidence": finding.evidence,
            "last_seen": now.isoformat(),
        }
        if existing is None:
            payload["occurrences"] = 1
            payload["first_seen"] = now.isoformat()
            session.add(
                Artifact(
                    ticket_id=finding.ticket_id,
                    kind=MonitorArtifactKind.FINDING.value,
                    title=title,
                    content_json=json.dumps(payload),
                )
            )
        else:
            prior = json.loads(existing.content_json or "{}")
            payload["occurrences"] = int(prior.get("occurrences", 0)) + 1
            payload["first_seen"] = prior.get("first_seen", now.isoformat())
            existing.content_json = json.dumps(payload)
            session.add(existing)
        touched += 1
    session.commit()
    return touched


def sweep(session: Session) -> int:
    """Scan and persist. Registered as a reconciliation step, not its own loop.

    `reconcile_once` already runs on a worker thread and already wraps each step
    so a bad pass cannot end the loop. A second `while True` would be a second
    way to wedge the process.
    """
    return record_findings(session, scan(session))


def list_findings(session: Session, *, ticket_id: str | None = None) -> list[MonitorFindingView]:
    """Persisted ticket findings, plus the workspace-scoped ones recomputed now.

    See `WORKSPACE_SCOPED` for why the two halves are sourced differently.
    """
    statement = select(Artifact).where(col(Artifact.kind) == MonitorArtifactKind.FINDING.value)
    if ticket_id:
        statement = statement.where(col(Artifact.ticket_id) == ticket_id)

    views = []
    for row in session.exec(statement).all():
        payload = json.loads(row.content_json or "{}")
        views.append(
            MonitorFindingView(
                condition=MonitorCondition(payload["condition"]),
                ticket_id=row.ticket_id,
                stage_key=payload.get("stage_key", ""),
                summary=payload.get("summary", ""),
                evidence=payload.get("evidence", {}),
                occurrences=int(payload.get("occurrences", 1)),
                first_seen=_parse_iso(payload.get("first_seen")),
                last_seen=_parse_iso(payload.get("last_seen")),
            )
        )

    if ticket_id is None:
        views.extend(
            MonitorFindingView(
                condition=finding.condition,
                stage_key=finding.stage_key,
                summary=finding.summary,
                evidence=finding.evidence,
            )
            for finding in scan(session)
            if finding.condition in WORKSPACE_SCOPED
        )
    return views


def _parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
