"""HARNESS_FAILURE_CLUSTER — workspace harness share, not per-stage rates.

Pins REQ-HC-1 … REQ-HC-6 and UI-1 from lg-milestone-that-716. Constants
(WINDOW_DAYS / MIN_FAILURES / SHARE_THRESHOLD) are imported from the detector
module so fixtures stay relative to whatever HC-5 calibration chooses — never
hardcoded to the ticket prose "42/42".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AUTO_FIXABLE_CONDITIONS,
    AgentRun,
    Artifact,
    MonitorArtifactKind,
    MonitorCondition,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
)
from loregarden.services.interruption_messages import (
    INTERRUPTED_RUN_MESSAGE,
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    RESTART_INTERRUPTION_MARKER,
    STRANDED_STAGE_MESSAGE,
    restart_interruption_message,
)
from loregarden.services.orchestration_profile import MonitorConfig
from loregarden.services.run_errors import NO_RECORDED_REASON, agent_timeout_message
from loregarden.services.stage_report import is_transient_failure
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from loregarden.services.workflow_monitor import (
    AUTO_FIXABLE,
    FAILURE_CLUSTER_MIN_RUNS,
    WORKSPACE_SCOPED,
    list_findings,
    record_findings,
    scan,
    sweep,
)
from pydantic import ValidationError
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace, make_workspace_ticket


def _detector():
    """Import the new module — AttributeError/ImportError until implement lands it."""
    from loregarden.services import monitor_harness_cluster as module

    return module


def _harness_cause_label(stdout: str, stderr: str) -> str:
    from loregarden.services.stage_report import harness_cause_label

    return harness_cause_label(stdout, stderr)


def _constants():
    mod = _detector()
    return mod.WINDOW_DAYS, mod.MIN_FAILURES, mod.SHARE_THRESHOLD


def _should_fire(failed: int, transient: int, *, min_failures: int, share_threshold: float) -> bool:
    return _detector().should_fire(
        failed,
        transient,
        min_failures=min_failures,
        share_threshold=share_threshold,
    )


_CLOSED_CAUSE_BUCKETS = frozenset(
    {
        "reload",
        "restart",
        "lease",
        "orphan",
        "stranded",
        "timeout",
        "auth",
        "usage",
        "other",
    }
)

_RELOAD_STDERR = INTERRUPTED_RUN_MESSAGE
_LEASE_STDERR = (
    "Agent run lease expired: nothing has renewed this run, so the thread that was "
    "supervising it is gone. Failed by the reconciliation sweep rather than by a restart."
)
_AUTH_STDERR = (
    "Error: Cursor couldn't find your saved login in the macOS keychain.\n"
    "Log out and sign back in to refresh your saved login: run `agent logout`, "
    "then start agent again."
)
_USAGE_STDERR = "You've hit your session limit · resets 3pm"
_REJECTION_STDERR = "Traceback (most recent call last): AssertionError: criteria unmet"
_TIMEOUT_STDERR = agent_timeout_message(600)


def _orchestration(db_session: Session, ticket: Ticket, code: str) -> str:
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


def _failed_stage_run(
    db_session: Session,
    ticket: Ticket,
    *,
    stderr: str,
    stdout: str = "",
    stage_key: str = "implement",
    agent_id: str = "backend_implementer",
    started_at: datetime | None = None,
    orch_code: str = "orch-harness",
) -> AgentRun:
    _failed_stage_run.counter = getattr(_failed_stage_run, "counter", 0) + 1
    now = datetime.now(timezone.utc)
    run = AgentRun(
        run_code=f"harness_{_failed_stage_run.counter}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=RunStatus.FAILED,
        orchestration_run_id=_orchestration(db_session, ticket, orch_code),
        started_at=started_at or now,
        finished_at=now,
        stdout=stdout,
        stderr=stderr,
    )
    db_session.add(run)
    db_session.commit()
    return run


def _harness_findings(findings) -> list:
    return [f for f in findings if f.condition is MonitorCondition.HARNESS_FAILURE_CLUSTER]


def _finding_rows(db_session: Session) -> list[Artifact]:
    return list(
        db_session.exec(
            select(Artifact).where(Artifact.kind == MonitorArtifactKind.FINDING.value)
        ).all()
    )


def _pair_labels(n: int) -> list[tuple[str, str]]:
    """≥13 distinct (agent_id, stage_key) pairs for HC-6 shape."""
    agents = [
        "backend_implementer",
        "frontend_implementer",
        "planner",
        "spec",
        "test_designer",
        "test_breaker",
        "verifier",
        "architecture_reviewer",
        "static_qa",
        "security_reviewer",
        "visual_qa",
        "ui-design-decision",
        "ticket_scoper",
        "learning",
    ]
    stages = [
        "plan",
        "spec",
        "test-design",
        "test-break",
        "implement",
        "verify",
        "review",
        "ui-design",
        "triage",
        "plan-synthesis",
    ]
    pairs: list[tuple[str, str]] = []
    for index in range(n):
        pairs.append((agents[index % len(agents)], stages[index % len(stages)]))
    # Force uniqueness when n ≤ len(agents)*something — expand stage suffix if needed.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for agent_id, stage_key in pairs:
        key = (agent_id, stage_key)
        suffix = 0
        while key in seen:
            suffix += 1
            key = (agent_id, f"{stage_key}-x{suffix}")
        seen.add(key)
        unique.append(key)
    return unique


def _transient_majority_window(
    db_session: Session,
    ticket: Ticket,
    *,
    pair_count: int = 13,
    rejection_extra: int = 0,
    timeout_extra: int = 0,
) -> None:
    """Build a window with failed_count ≥ MIN_FAILURES and transient share ≥ SHARE.

    Majority stderr is accepted by is_transient_failure; timeouts/rejections may
    appear for cause-shape realism without dropping the share below threshold.
    """
    _window_days, min_failures, share_threshold = _constants()
    del _window_days
    extras = rejection_extra + timeout_extra
    transient_count = max(pair_count, min_failures)
    while (
        transient_count < min_failures
        or (transient_count / (transient_count + extras)) < share_threshold
    ):
        transient_count += 1

    pairs = _pair_labels(transient_count)
    for index, (agent_id, stage_key) in enumerate(pairs):
        cycle = index % 4
        if cycle == 0:
            stderr = _RELOAD_STDERR
        elif cycle == 1:
            stderr = _LEASE_STDERR
        elif cycle == 2:
            stderr = restart_interruption_message("Baxter", "did not finish this turn.")
        else:
            stderr = _AUTH_STDERR
        assert is_transient_failure("", stderr) is True
        _failed_stage_run(
            db_session,
            ticket,
            stderr=stderr,
            agent_id=agent_id,
            stage_key=stage_key,
            orch_code=f"orch-{ticket.external_id}",
        )
    for _ in range(rejection_extra):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_REJECTION_STDERR,
            agent_id="backend_implementer",
            stage_key="implement",
            orch_code=f"orch-{ticket.external_id}-rej",
        )
    for _ in range(timeout_extra):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_TIMEOUT_STDERR,
            agent_id="backend_implementer",
            stage_key="implement",
            orch_code=f"orch-{ticket.external_id}-to",
        )


# --- HC-1: enum + WORKSPACE_SCOPED visibility --------------------------------


def test_harness_failure_cluster_enum_and_workspace_scoped_membership():
    """AC-HC-1.1 / AC-HC-1.2."""
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER.value == "harness_failure_cluster"
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER in WORKSPACE_SCOPED


def test_workspace_wide_scan_returns_harness_cluster_with_empty_ticket_and_slug_stage(
    db_session: Session,
):
    """AC-HC-1.3 — ticket_id '', stage_key = workspace slug, evidence ids."""
    ticket = make_workspace_ticket(db_session, "hc1-fire", slug="loregarden")
    _transient_majority_window(db_session, ticket)

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.ticket_id == ""
    assert finding.stage_key == "loregarden"
    assert finding.evidence.get("workspace_slug") == "loregarden"
    assert finding.evidence.get("workspace_id") == ticket.workspace_id

    listed = _harness_findings(list_findings(db_session))
    assert len(listed) == 1
    assert listed[0].ticket_id == ""
    assert listed[0].stage_key == "loregarden"


def test_record_findings_and_sweep_do_not_persist_harness_cluster(db_session: Session):
    """AC-HC-1.4 — WORKSPACE_SCOPED skip; recomputed on read."""
    ticket = make_workspace_ticket(db_session, "hc1-persist", slug="loregarden")
    _transient_majority_window(db_session, ticket)

    findings = _harness_findings(scan(db_session))
    assert findings
    assert record_findings(db_session, findings) == 0
    sweep(db_session)
    persisted = {json.loads(row.content_json)["condition"] for row in _finding_rows(db_session)}
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER.value not in persisted
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER in {
        f.condition for f in list_findings(db_session)
    }


def test_ticket_scoped_scan_and_list_never_return_harness_cluster(db_session: Session):
    """AC-HC-1.5."""
    ticket = make_workspace_ticket(db_session, "hc1-scoped", slug="loregarden")
    _transient_majority_window(db_session, ticket)

    assert _harness_findings(scan(db_session, ticket_id=ticket.id)) == []
    assert _harness_findings(list_findings(db_session, ticket_id=ticket.id)) == []


# --- HC-2: report-only -------------------------------------------------------


def test_harness_cluster_is_not_auto_fixable():
    """AC-HC-2.1 / AC-HC-2.2 / AC-HC-2.3."""
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER not in AUTO_FIXABLE_CONDITIONS
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER not in AUTO_FIXABLE
    with pytest.raises((ValidationError, ValueError), match="(?i)auto-?fix"):
        MonitorConfig(autofix=[MonitorCondition.HARNESS_FAILURE_CLUSTER])


# --- HC-3: fire rule ---------------------------------------------------------


def test_should_fire_matches_min_and_share_formula():
    """AC-HC-3.1 — pure helper; no IO."""
    assert _should_fire(30, 22, min_failures=10, share_threshold=0.5) is True
    assert _should_fire(30, 10, min_failures=10, share_threshold=0.5) is False
    assert _should_fire(8, 8, min_failures=10, share_threshold=0.5) is False
    assert _should_fire(0, 0, min_failures=10, share_threshold=0.5) is False


def test_two_workspaces_evaluate_independently(db_session: Session):
    """AC-HC-3.2."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    hot = make_workspace_ticket(db_session, "hc3-hot", slug="loregarden")
    cold_ws = make_workspace(db_session, slug="blobert-cold")
    cold = make_ticket(
        db_session,
        workspace_id=cold_ws.id,
        external_id="hc3-cold",
    )
    _transient_majority_window(db_session, hot)
    # Cold workspace: enough failures, all rejection — must not fire.
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            cold,
            stderr=_REJECTION_STDERR,
            agent_id=f"agent-{index % 3}",
            stage_key=f"stage-{index % 4}",
            orch_code="orch-cold",
        )

    by_slug = {f.stage_key: f for f in _harness_findings(scan(db_session))}
    assert "loregarden" in by_slug
    assert "blobert-cold" not in by_slug


def test_rejection_heavy_window_never_fires(db_session: Session):
    """AC-HC-3.3 — failed_count ≥ MIN_FAILURES, transient share below threshold."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "hc3-reject", slug="loregarden")
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_REJECTION_STDERR,
            agent_id=f"rej-agent-{index % 5}",
            stage_key=f"rej-stage-{index % 5}",
            orch_code="orch-rej",
        )

    assert _harness_findings(scan(db_session)) == []


def test_spread_across_many_agent_stage_pairs_still_fires(db_session: Session):
    """AC-HC-3.4 — no per-(agent, stage) rate gate."""
    ticket = make_workspace_ticket(db_session, "hc3-spread", slug="loregarden")
    _transient_majority_window(db_session, ticket, pair_count=13)

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1


def test_harness_detector_does_not_depend_on_workflow_monitor_runs(db_session: Session):
    """AC-HC-3.5 — own query; _runs raiseload must not be the substrate."""
    ticket = make_workspace_ticket(db_session, "hc3-own-query", slug="loregarden")
    _transient_majority_window(db_session, ticket)

    with patch("loregarden.services.workflow_monitor._runs", return_value=[]):
        findings = _harness_findings(scan(db_session))

    assert len(findings) == 1


def test_timeouts_do_not_raise_the_fire_share(db_session: Session):
    """HC-3 — timeouts are not transient; alone they must not fire."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "hc3-timeouts", slug="loregarden")
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_TIMEOUT_STDERR,
            agent_id=f"to-agent-{index % 4}",
            stage_key=f"to-stage-{index % 4}",
            orch_code="orch-to",
        )
    assert is_transient_failure("", _TIMEOUT_STDERR) is False
    assert _harness_findings(scan(db_session)) == []


def test_triage_agent_and_null_ticket_runs_are_excluded(db_session: Session):
    """HC-3 exclusions — TRIAGE_AGENT_ID and null ticket_id do not inflate share."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "hc3-exclude", slug="loregarden")
    # Genuine rejection floor that would otherwise sit just below MIN if triage
    # rows were wrongly counted as transient failures.
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_REJECTION_STDERR,
            orch_code="orch-ex-rej",
            agent_id=f"real-{index}",
            stage_key="implement",
        )
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_RELOAD_STDERR,
            agent_id=TRIAGE_AGENT_ID,
            stage_key="triage",
            orch_code="orch-ex-triage",
        )
    now = datetime.now(timezone.utc)
    orphan = AgentRun(
        run_code="harness_null_ticket",
        ticket_id=None,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.FAILED,
        started_at=now,
        finished_at=now,
        stderr=_RELOAD_STDERR,
    )
    db_session.add(orphan)
    db_session.commit()

    assert _harness_findings(scan(db_session)) == []


def test_runs_outside_the_window_are_ignored(db_session: Session):
    """HC-3 — only WINDOW_DAYS contributes."""
    window_days, min_failures, _share = _constants()
    del _share
    ticket = make_workspace_ticket(db_session, "hc3-window", slug="loregarden")
    stale = datetime.now(timezone.utc) - timedelta(days=window_days + 2)
    for index in range(min_failures + 5):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_RELOAD_STDERR,
            started_at=stale,
            agent_id=f"old-{index}",
            stage_key=f"old-{index % 3}",
            orch_code="orch-stale",
        )

    assert _harness_findings(scan(db_session)) == []


# --- HC-4: cause labels + summary --------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("", _RELOAD_STDERR, "reload"),
        ("", restart_interruption_message("Baxter", "Send the message again."), "restart"),
        ("", _LEASE_STDERR, "lease"),
        ("", ORPHAN_OF_TERMINAL_ORCH_MESSAGE, "orphan"),
        ("", STRANDED_STAGE_MESSAGE, "stranded"),
        ("", _TIMEOUT_STDERR, "timeout"),
        ("", _AUTH_STDERR, "auth"),
        ("", _USAGE_STDERR, "usage"),
        ("", NO_RECORDED_REASON, "other"),
        ("", _REJECTION_STDERR, "other"),
        ("", "", "other"),
    ],
)
def test_harness_cause_label_closed_buckets(stdout: str, stderr: str, expected: str):
    """AC-HC-4.1 / AC-HC-4.2 / AC-HC-4.3 / AC-HC-4.5."""
    label = _harness_cause_label(stdout, stderr)
    assert label in _CLOSED_CAUSE_BUCKETS
    assert label == expected
    if expected == "timeout":
        assert is_transient_failure(stdout, stderr) is False


def test_restart_marker_beats_generic_other():
    """Priority pin — RESTART_INTERRUPTION_MARKER is a first-class bucket."""
    assert RESTART_INTERRUPTION_MARKER in restart_interruption_message("X", "y").lower()
    assert _harness_cause_label("", restart_interruption_message("X", "y")) == "restart"


def test_fired_summary_includes_cause_breakdown_across_buckets(db_session: Session):
    """AC-HC-4.4 — ≥2 non-other buckets appear in summary prose."""
    ticket = make_workspace_ticket(db_session, "hc4-summary", slug="loregarden")
    _transient_majority_window(db_session, ticket, timeout_extra=3)

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1
    summary = findings[0].summary.lower()
    # At least two distinct cause names from the closed set (not only "other").
    named = [
        bucket for bucket in ("reload", "restart", "lease", "auth", "timeout") if bucket in summary
    ]
    assert len(named) >= 2, summary
    for key in ("failed", "transient", "share", "threshold", "window_days"):
        assert key in findings[0].evidence, findings[0].evidence


def test_fire_share_ignores_timeout_labels_in_the_window(db_session: Session):
    """HC-4 — labels may name timeouts; fire share uses is_transient_failure only.

    Enough failures for the min floor, but transient share kept strictly below
    SHARE_THRESHOLD by padding with timeout stderr (non-transient).
    """
    _window_days, min_failures, share_threshold = _constants()
    del _window_days
    ticket = make_workspace_ticket(db_session, "hc4-fire-independent", slug="loregarden")
    transient_count = 1
    timeout_count = min_failures
    while (transient_count / (transient_count + timeout_count)) >= share_threshold:
        timeout_count += 1
    for index in range(transient_count):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_RELOAD_STDERR,
            agent_id=f"t-{index}",
            stage_key=f"s-{index}",
            orch_code="orch-hc4-t",
        )
    for index in range(timeout_count):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_TIMEOUT_STDERR,
            agent_id=f"to-{index}",
            stage_key=f"tos-{index % 3}",
            orch_code="orch-hc4-to",
        )
    failed = transient_count + timeout_count
    assert (
        _should_fire(
            failed,
            transient_count,
            min_failures=min_failures,
            share_threshold=share_threshold,
        )
        is False
    )
    assert _harness_findings(scan(db_session)) == []


# --- HC-5: constant shape (calibration artifact is implement-only) -----------


def test_calibrated_constants_have_sane_shape():
    """HC-5 shape constraints — values themselves land after the backtest artifact."""
    window_days, min_failures, share_threshold = _constants()
    assert isinstance(window_days, int) and window_days >= 1
    assert isinstance(min_failures, int) and min_failures >= FAILURE_CLUSTER_MIN_RUNS
    assert isinstance(share_threshold, float) and 0.0 < share_threshold <= 1.0


# --- HC-6: blobert-like shape vs rejection-heavy -----------------------------


def test_blobert_like_shape_fires(db_session: Session):
    """HC-6 — ≥13 agent/stage pairs; majority stderr accepted by is_transient_failure."""
    ws = make_workspace(db_session, slug="blobert")
    ticket = make_ticket(db_session, workspace_id=ws.id, external_id="hc6-blobert-shape")
    _transient_majority_window(db_session, ticket, pair_count=13, timeout_extra=4)

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1
    assert findings[0].stage_key == "blobert"


def test_rejection_heavy_above_min_does_not_fire(db_session: Session):
    """HC-6 control — failed_count ≥ MIN_FAILURES, mostly non-transient."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "hc6-rejection", slug="loregarden")
    for index in range(min_failures + 5):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_REJECTION_STDERR,
            agent_id=f"rej-{index % 7}",
            stage_key=f"st-{index % 7}",
            orch_code="orch-hc6-rej",
        )

    assert _harness_findings(scan(db_session)) == []


def test_transient_classifier_corpus_timeout_pin_unchanged():
    """HC-6 — corpus pin: timeouts stay non-transient; do not expand the classifier."""
    assert is_transient_failure("", _TIMEOUT_STDERR) is False
    assert is_transient_failure("", _RELOAD_STDERR) is True


# --- UI-1: no condition-specific client contract -----------------------------


def test_ui_consumes_generic_condition_slug_only():
    """UI-1 — backend exposes the slug; client conditionLabel is already generic.

    No client/ edits in this ticket. The slug must remain the stable wire value
    existing WorkflowMonitorView / conditionLabel already render.
    """
    assert MonitorCondition.HARNESS_FAILURE_CLUSTER.value == "harness_failure_cluster"
