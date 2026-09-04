"""The monitor looks across runs for failure modes nothing else watches for.

Both bugs found while shipping PR #203 had been latent for an unknown period and
were found by reading code, not by any alarm.

Every threshold here is relative to the observed baseline rather than an absolute
constant, so the tests build the baseline they then measure against — a test that
hardcodes "4 attempts is thrash" would pass while the production threshold moved.
"""

import json
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    MonitorArtifactKind,
    MonitorCondition,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from loregarden.services.workflow_monitor import (
    WORKSPACE_SCOPED,
    list_findings,
    record_findings,
    scan,
    sweep,
)
from sqlmodel import Session, select
from tests.factories import make_ticket


def _ticket(db_session: Session, external_id: str) -> Ticket:
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    return make_ticket(
        db_session,
        workspace_id=ws.id,
        external_id=external_id,
        title=external_id,
        state=TicketState.IN_PROGRESS,
    )


def _orchestration(db_session: Session, ticket: Ticket, code: str) -> str:
    """A real orchestration row: FKs are enforced on this engine (PR #165), so a
    made-up orchestration_run_id is rejected rather than quietly stored."""
    existing = db_session.exec(
        select(OrchestrationRun).where(OrchestrationRun.run_code == code)
    ).first()
    if existing:
        return existing.id
    run = OrchestrationRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run.id


def _run(
    db_session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    orch_id: str | None = "orch-1",
    status: RunStatus = RunStatus.SUCCEEDED,
    agent_id: str = "backend_implementer",
    started_at: datetime | None = None,
) -> AgentRun:
    _run.counter = getattr(_run, "counter", 0) + 1
    run = AgentRun(
        run_code=f"run_{_run.counter}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=status,
        orchestration_run_id=_orchestration(db_session, ticket, orch_id) if orch_id else None,
        started_at=started_at,
    )
    db_session.add(run)
    db_session.commit()
    return run


def _conditions(findings) -> set[MonitorCondition]:
    return {f.condition for f in findings}


# --- AC1: scan mutates nothing ---------------------------------------------


def test_scan_mutates_nothing(db_session: Session):
    """AC1. Snapshotted across the scan, because a 'read-only' function that
    writes is the one defect this module cannot be allowed to have — it runs on
    the reconcile timer, against every ticket, unattended."""
    ticket = _ticket(db_session, "monitor-readonly")
    template = WorkflowTemplate(slug="monitor-tpl", name="Monitor", stages_json="[]")
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="implement",
        stages_json="{}",
    )
    db_session.add(instance)
    db_session.commit()
    for _ in range(8):
        _run(db_session, ticket, stage_key="implement")

    before_state = ticket.state
    before_stages = instance.stages_json
    before_artifacts = len(db_session.exec(select(Artifact)).all())

    scan(db_session)

    db_session.refresh(ticket)
    db_session.refresh(instance)
    assert ticket.state == before_state
    assert instance.stages_json == before_stages
    assert len(db_session.exec(select(Artifact)).all()) == before_artifacts


# --- AC3: the detections ----------------------------------------------------


def test_stage_thrash_fires_on_repeated_attempts_in_one_orchestration(db_session: Session):
    ticket = _ticket(db_session, "monitor-thrash")
    for _ in range(9):
        _run(db_session, ticket, stage_key="implement", orch_id="orch-thrash")

    findings = [f for f in scan(db_session) if f.condition is MonitorCondition.STAGE_THRASH]
    assert len(findings) == 1
    assert findings[0].stage_key == "implement"
    assert findings[0].evidence["attempts"] == "9"


def test_a_normal_rework_round_is_not_thrash(db_session: Session):
    """The control. attempts_per_stage sits near 1.4 in this database, so two
    attempts is an ordinary reroute — a detector that fires on it reports every
    ticket and is therefore worth nothing."""
    ticket = _ticket(db_session, "monitor-normal")
    for _ in range(2):
        _run(db_session, ticket, stage_key="implement", orch_id="orch-normal")

    assert MonitorCondition.STAGE_THRASH not in _conditions(scan(db_session))


def test_unbudgeted_repeats_are_reported_separately(db_session: Session):
    """Runs with no orchestration run had no retry budget at all before 560."""
    ticket = _ticket(db_session, "monitor-unbudgeted")
    for _ in range(4):
        _run(db_session, ticket, stage_key="triage-stage", orch_id=None)

    findings = [f for f in scan(db_session) if f.condition is MonitorCondition.UNBUDGETED_REPEAT]
    assert len(findings) == 1
    assert findings[0].evidence["attempts"] == "4"


def test_chat_turns_are_not_counted_as_stage_dispatches(db_session: Session):
    """The wrong exhibit, made impossible.

    An earlier reading of this data reported one ticket with 28 runaway `triage`
    attempts. All 28 were Ticket Studio chat turns built by hand in
    agent_turn_runner._start_run — never stage dispatches, and filtered out of
    RunService.list_runs for the same reason. Counting them here would resurrect
    exactly that claim.
    """
    ticket = _ticket(db_session, "monitor-chat")
    for _ in range(28):
        _run(db_session, ticket, stage_key="triage", orch_id=None, agent_id=TRIAGE_AGENT_ID)

    assert MonitorCondition.UNBUDGETED_REPEAT not in _conditions(scan(db_session))


def test_failure_clusters_are_relative_to_the_workspace_rate(db_session: Session):
    ticket = _ticket(db_session, "monitor-cluster")
    for _ in range(12):
        _run(db_session, ticket, stage_key="testing", status=RunStatus.FAILED)
    for _ in range(40):
        _run(db_session, ticket, stage_key="implement", status=RunStatus.SUCCEEDED)

    findings = [f for f in scan(db_session) if f.condition is MonitorCondition.FAILURE_CLUSTER]
    assert [f.stage_key for f in findings] == ["testing"]


def test_a_uniformly_failing_pipeline_reports_no_cluster(db_session: Session):
    """The control for the test above, and the reason the rate is workspace-
    relative: when everything fails at the same rate, no stage is the outlier."""
    ticket = _ticket(db_session, "monitor-uniform")
    for stage in ("testing", "implement"):
        for index in range(20):
            _run(
                db_session,
                ticket,
                stage_key=stage,
                status=RunStatus.FAILED if index % 2 else RunStatus.SUCCEEDED,
            )

    assert MonitorCondition.FAILURE_CLUSTER not in _conditions(scan(db_session))


def test_a_long_running_run_is_reported_as_stalled(db_session: Session):
    ticket = _ticket(db_session, "monitor-stalled")
    _run(
        db_session,
        ticket,
        stage_key="implement",
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(hours=24),
    )

    findings = [f for f in scan(db_session) if f.condition is MonitorCondition.STALLED_RUN]
    assert len(findings) == 1
    assert findings[0].evidence["basis"] in {"stage median", "fallback"}


def test_a_run_that_just_started_is_not_stalled(db_session: Session):
    ticket = _ticket(db_session, "monitor-fresh")
    _run(
        db_session,
        ticket,
        stage_key="implement",
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )

    assert MonitorCondition.STALLED_RUN not in _conditions(scan(db_session))


def test_an_unknown_skip_when_is_reported_as_rot(db_session: Session):
    """`should_skip_stage` fails open on an unknown condition, which is right at
    runtime — a typo must not prune a stage. The cost is that the stage silently
    never prunes, and nothing anywhere said so."""
    db_session.add(
        WorkflowTemplate(
            slug="rot-template",
            name="Rot",
            stages_json=json.dumps(
                [
                    {"key": "ui", "name": "UI", "order": 1, "skip_when": "has_acceptance_critera"},
                    {"key": "ok", "name": "Ok", "order": 2, "skip_when": "has_description"},
                    {"key": "done", "name": "Done", "order": 3, "terminal": True},
                ]
            ),
        )
    )
    db_session.commit()

    findings = [f for f in scan(db_session) if f.condition is MonitorCondition.SKIP_CONDITION_ROT]
    assert [f.stage_key for f in findings] == ["ui"]
    assert findings[0].evidence["skip_when"] == "has_acceptance_critera"


def test_scanning_one_ticket_skips_workspace_wide_conditions(db_session: Session):
    """A failure cluster is a property of the pipeline, not of the ticket that
    happened to be scanned — reporting it against one would misattribute it."""
    ticket = _ticket(db_session, "monitor-scoped")
    for _ in range(12):
        _run(db_session, ticket, stage_key="testing", status=RunStatus.FAILED)

    assert not _conditions(scan(db_session, ticket_id=ticket.id)) & WORKSPACE_SCOPED


# --- AC4: findings persist, upserted ----------------------------------------


def _finding_rows(db_session: Session) -> list[Artifact]:
    return list(
        db_session.exec(
            select(Artifact).where(Artifact.kind == MonitorArtifactKind.FINDING.value)
        ).all()
    )


def test_a_repeated_finding_upserts_rather_than_appending(db_session: Session):
    """AC4. record_gate_evaluation appends, which is why `context` is the largest
    artifact kind in the database; on the reconcile timer this would beat that
    inside a week."""
    ticket = _ticket(db_session, "monitor-upsert")
    for _ in range(9):
        _run(db_session, ticket, stage_key="implement", orch_id="orch-upsert")

    sweep(db_session)
    sweep(db_session)
    sweep(db_session)

    rows = _finding_rows(db_session)
    assert len(rows) == 1
    payload = json.loads(rows[0].content_json)
    assert payload["occurrences"] == 3
    assert payload["first_seen"] <= payload["last_seen"]


def test_workspace_scoped_findings_are_not_persisted(db_session: Session):
    """They have no ticket_id, and artifacts.ticket_id is NOT NULL behind an
    enforced foreign key. Recomputed on read instead of hung on a invented row."""
    ticket = _ticket(db_session, "monitor-ws")
    for _ in range(12):
        _run(db_session, ticket, stage_key="testing", status=RunStatus.FAILED)
    for _ in range(40):
        _run(db_session, ticket, stage_key="implement", status=RunStatus.SUCCEEDED)

    sweep(db_session)
    persisted = {json.loads(row.content_json)["condition"] for row in _finding_rows(db_session)}
    assert MonitorCondition.FAILURE_CLUSTER.value not in persisted

    # …but a reader still sees it.
    assert MonitorCondition.FAILURE_CLUSTER in _conditions(list_findings(db_session))


def test_record_findings_ignores_findings_with_no_ticket(db_session: Session):
    assert record_findings(db_session, []) == 0
    assert _finding_rows(db_session) == []


def test_list_findings_narrows_to_one_ticket(db_session: Session):
    first = _ticket(db_session, "monitor-list-a")
    second = _ticket(db_session, "monitor-list-b")
    for _ in range(9):
        _run(db_session, first, stage_key="implement", orch_id="orch-a")
    for _ in range(9):
        _run(db_session, second, stage_key="implement", orch_id="orch-b")
    sweep(db_session)

    scoped = list_findings(db_session, ticket_id=first.id)
    assert {f.ticket_id for f in scoped} == {first.id}


# --- AC5: cadence -----------------------------------------------------------


def test_the_sweep_is_registered_as_a_reconciliation_step():
    """AC5. reconcile_once already runs on a worker thread and already wraps each
    step so a bad pass cannot end the loop. A second `while True` would be a
    second way to wedge the process."""
    from loregarden.services.reconciliation import PERIODIC_STEPS

    assert "scan_workflow_monitor" in {step.name for step in PERIODIC_STEPS}


def test_a_failing_monitor_does_not_take_the_repair_sweeps_down(db_session: Session):
    """The monitor is report-only; the other steps are repair. If observing can
    break repairing, the observation is not worth having."""
    from unittest.mock import patch

    from loregarden.services import reconciliation, workflow_monitor

    # Patched inside workflow_monitor, NOT on reconciliation.monitor_sweep:
    # PERIODIC_STEPS holds a direct reference captured at import, so rebinding
    # the name in the importing module leaves the tuple pointing at the original
    # and the "control" would pass while proving nothing. `sweep` looks `scan`
    # up in its own module at call time, so this one actually takes.
    with patch.object(workflow_monitor, "scan", side_effect=RuntimeError("boom")):
        failed = reconciliation.reconcile_once(db_session)

    assert failed == ["scan_workflow_monitor"]
