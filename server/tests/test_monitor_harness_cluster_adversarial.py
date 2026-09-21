"""Adversarial / edge-case coverage for HARNESS_FAILURE_CLUSTER (lg-milestone-that-716).

Test-break stage. Complements `test_monitor_harness_cluster.py` (happy-path AC map)
with boundary, mutation, null/empty, and assumption-check cases the design suite
does not pin. Expected red until implement lands the detector + enum.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import AgentRun, MonitorCondition, RunStatus, Ticket
from loregarden.services.interruption_messages import (
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    STRANDED_STAGE_MESSAGE,
)
from loregarden.services.stage_report import is_transient_failure
from loregarden.services.workflow_monitor import _runs, list_findings, scan
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace, make_workspace_ticket
from tests.test_monitor_harness_cluster import (
    _AUTH_STDERR,
    _REJECTION_STDERR,
    _RELOAD_STDERR,
    _TIMEOUT_STDERR,
    _USAGE_STDERR,
    _constants,
    _failed_stage_run,
    _harness_cause_label,
    _harness_findings,
    _should_fire,
    _transient_majority_window,
)

# --- Boundary: inclusive >= on both floors -----------------------------------


def test_should_fire_at_exact_min_and_exact_share_threshold():
    """Mutation: `>=` vs `>` — equality must fire; one-failure-under must not.

    Design suite checks 22/30 and 10/30 at 0.5, but never the exact threshold
    point that separates inclusive from exclusive arithmetic.
    """
    min_failures = 10
    share = 0.5
    assert _should_fire(10, 5, min_failures=min_failures, share_threshold=share) is True
    assert _should_fire(9, 9, min_failures=min_failures, share_threshold=share) is False
    assert _should_fire(10, 4, min_failures=min_failures, share_threshold=share) is False


def test_should_fire_rejects_impossible_and_empty_inputs():
    """Null/empty / corrupted counts: never True when failed is zero or negative."""
    assert _should_fire(0, 0, min_failures=10, share_threshold=0.5) is False
    assert _should_fire(0, 5, min_failures=10, share_threshold=0.5) is False
    assert _should_fire(-1, 0, min_failures=10, share_threshold=0.5) is False
    assert _should_fire(10, -1, min_failures=10, share_threshold=0.5) is False


# --- Non-FAILED status pollution ---------------------------------------------


def _add_run(
    db_session: Session,
    ticket: Ticket,
    *,
    status: RunStatus,
    stderr: str,
    run_code: str,
    started_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    run = AgentRun(
        run_code=run_code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=status,
        started_at=started_at or now,
        finished_at=now if status != RunStatus.RUNNING else None,
        stdout="",
        stderr=stderr,
    )
    db_session.add(run)
    db_session.commit()


def test_non_failed_runs_with_transient_stderr_do_not_inflate_share(db_session: Session):
    """Assumption check: classifier is only meaningful for FAILED (stage_report docstring).

    SUCCEEDED / RUNNING / CANCELLED rows carrying reload stderr must not push a
    rejection-heavy workspace over the fire bar.
    """
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "adv-status", slug="loregarden")
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_REJECTION_STDERR,
            agent_id=f"rej-{index}",
            stage_key="implement",
            orch_code="orch-adv-status-rej",
        )
    for index, status in enumerate(
        (RunStatus.SUCCEEDED, RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.QUEUED)
    ):
        for j in range(min_failures):
            _add_run(
                db_session,
                ticket,
                status=status,
                stderr=_RELOAD_STDERR,
                run_code=f"adv_status_{status.value}_{j}_{index}",
            )

    assert _harness_findings(scan(db_session)) == []


# --- Workspace aggregation / multi-ticket ------------------------------------


def test_multiple_tickets_in_one_workspace_aggregate_into_single_finding(db_session: Session):
    """Combinatorial: share is workspace-partitioned, not per-ticket.

    Split a fire-worthy transient majority across two tickets in the same
    workspace — one finding, stage_key = slug. A per-ticket mis-partition would
    miss the bar on each ticket and stay quiet.
    """
    _window_days, min_failures, share_threshold = _constants()
    del _window_days
    ws = make_workspace(db_session, slug="adv-aggregate")
    first = make_ticket(db_session, workspace_id=ws.id, external_id="adv-agg-a")
    second = make_ticket(db_session, workspace_id=ws.id, external_id="adv-agg-b")

    half = max(1, (min_failures + 1) // 2)
    # Enough total failures, and enough transient across both tickets.
    transient_needed = max(min_failures, int(min_failures * share_threshold) + 1)
    for index in range(transient_needed):
        ticket = first if index < half else second
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_RELOAD_STDERR if index % 2 == 0 else _USAGE_STDERR,
            agent_id=f"agg-{index}",
            stage_key=f"st-{index % 5}",
            orch_code=f"orch-agg-{ticket.external_id}",
        )

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1
    assert findings[0].ticket_id == ""
    assert findings[0].stage_key == "adv-aggregate"


def test_two_hot_workspaces_emit_two_distinct_findings(db_session: Session):
    """Stress: both workspaces meet the bar — two rows, distinct stage_key slugs."""
    hot_a = make_workspace_ticket(db_session, "adv-hot-a", slug="loregarden")
    ws_b = make_workspace(db_session, slug="blobert-hot")
    hot_b = make_ticket(db_session, workspace_id=ws_b.id, external_id="adv-hot-b")
    _transient_majority_window(db_session, hot_a, pair_count=13)
    _transient_majority_window(db_session, hot_b, pair_count=13)

    by_slug = {f.stage_key: f for f in _harness_findings(scan(db_session))}
    assert set(by_slug) >= {"loregarden", "blobert-hot"}
    assert by_slug["loregarden"].ticket_id == ""
    assert by_slug["blobert-hot"].ticket_id == ""


# --- Window boundary ---------------------------------------------------------


def test_run_exactly_at_window_boundary_is_included(db_session: Session):
    """Boundary: started_at == now - WINDOW_DAYS must still count (inclusive window).

    Design only covers WINDOW_DAYS+2 (clearly stale). Off-by-one on the cut
    would mute a real storm that started on the first day of the window.
    """
    window_days, min_failures, _share = _constants()
    del _share
    ticket = make_workspace_ticket(db_session, "adv-boundary", slug="loregarden")
    # Slightly inside the cut so clock skew during the test cannot push us out.
    edge = datetime.now(timezone.utc) - timedelta(days=window_days) + timedelta(seconds=30)
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_RELOAD_STDERR,
            started_at=edge,
            agent_id=f"edge-{index}",
            stage_key=f"e-{index % 3}",
            orch_code="orch-edge",
        )

    assert len(_harness_findings(scan(db_session))) == 1


def test_started_at_outside_window_ignored_even_if_finished_inside(db_session: Session):
    """Assumption: window keys on started_at (design + HC-3), not finished_at.

    A long-running failure that started before the window but finished "now"
    must not inflate the share if the detector mistakenly uses finished_at.
    """
    window_days, min_failures, _share = _constants()
    del _share
    ticket = make_workspace_ticket(db_session, "adv-started", slug="loregarden")
    stale_start = datetime.now(timezone.utc) - timedelta(days=window_days + 5)
    now = datetime.now(timezone.utc)
    for index in range(min_failures + 3):
        run = AgentRun(
            run_code=f"adv_finished_now_{index}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id=f"late-{index}",
            stage_key="implement",
            status=RunStatus.FAILED,
            started_at=stale_start,
            finished_at=now,
            stdout="",
            stderr=_RELOAD_STDERR,
        )
        db_session.add(run)
    db_session.commit()

    assert _harness_findings(scan(db_session)) == []


# --- stdout-only / empty stderr / None-like ----------------------------------


def test_transient_signature_in_stdout_only_counts_toward_share(db_session: Session):
    """is_transient_failure reads stdout+stderr; fire must not stderr-only scan."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "adv-stdout", slug="loregarden")
    assert is_transient_failure(_RELOAD_STDERR, "") is True
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr="",
            stdout=_RELOAD_STDERR,
            agent_id=f"so-{index}",
            stage_key=f"sos-{index % 4}",
            orch_code="orch-stdout",
        )

    assert len(_harness_findings(scan(db_session))) == 1


def test_usage_limit_window_alone_can_fire(db_session: Session):
    """Mutation: usage is transient via detect_usage_limit, not only reload/lease."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "adv-usage", slug="loregarden")
    assert is_transient_failure("", _USAGE_STDERR) is True
    for index in range(min_failures):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_USAGE_STDERR,
            agent_id=f"u-{index}",
            stage_key=f"us-{index % 4}",
            orch_code="orch-usage",
        )

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1
    assert "usage" in findings[0].summary.lower()


def test_orphan_and_stranded_count_as_transient_for_fire(db_session: Session):
    """Control-plane death signatures are transient; labels are orphan/stranded."""
    _window_days, min_failures, _share = _constants()
    del _window_days, _share
    ticket = make_workspace_ticket(db_session, "adv-orphan", slug="loregarden")
    assert is_transient_failure("", ORPHAN_OF_TERMINAL_ORCH_MESSAGE) is True
    assert is_transient_failure("", STRANDED_STAGE_MESSAGE) is True
    for index in range(min_failures):
        stderr = ORPHAN_OF_TERMINAL_ORCH_MESSAGE if index % 2 == 0 else STRANDED_STAGE_MESSAGE
        _failed_stage_run(
            db_session,
            ticket,
            stderr=stderr,
            agent_id=f"os-{index}",
            stage_key=f"oss-{index % 3}",
            orch_code="orch-orphan",
        )

    findings = _harness_findings(scan(db_session))
    assert len(findings) == 1
    summary = findings[0].summary.lower()
    assert "orphan" in summary or "stranded" in summary


# --- Fire boolean must not call harness_cause_label (HC-4) -------------------


def test_should_fire_helper_does_not_reference_harness_cause_label():
    """HC-4: fire boolean is pure counts — must not import/call cause labels.

    Summary construction may still call harness_cause_label after a fire; this
    pins the predicate itself so implement cannot couple them.
    """
    from loregarden.services import monitor_harness_cluster as module

    source = inspect.getsource(module.should_fire)
    assert "harness_cause_label" not in source
    assert "is_transient_failure" not in source  # counts only; classification is upstream


# --- Cause-label priority / closed set mutations -----------------------------


def test_harness_cause_label_prefers_reload_over_rejection_noise():
    """Combinatorial: reload signature + rejection traceback → reload, not other."""
    mixed = f"{_RELOAD_STDERR}\n{_REJECTION_STDERR}"
    assert _harness_cause_label("", mixed) == "reload"
    assert _harness_cause_label(mixed, "") == "reload"


def test_harness_cause_label_never_returns_outside_closed_set():
    """Fuzz-ish corpus: every fixture stderr maps into the closed bucket frozenset."""
    samples = [
        _RELOAD_STDERR,
        _AUTH_STDERR,
        _USAGE_STDERR,
        _TIMEOUT_STDERR,
        _REJECTION_STDERR,
        ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
        STRANDED_STAGE_MESSAGE,
        "",
        "   ",
        "completely unrelated agent chatter",
    ]
    closed = {
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
    for stderr in samples:
        assert _harness_cause_label("", stderr) in closed


# --- Substrate / wiring mutations --------------------------------------------


def test_workflow_monitor_runs_still_omits_stdout_stderr_columns():
    """HC-3 anti-pattern: wiring must not widen `_runs` raiseload to pull output.

    The harness detector owns its own query; `_runs` must keep omitting
    stdout/stderr so the reconcile sweep stays cheap.
    """
    source = inspect.getsource(_runs)
    assert "AgentRun.stdout" not in source
    assert "AgentRun.stderr" not in source
    assert "raiseload" in source


def test_is_transient_failure_source_not_expanded_for_timeouts():
    """HC-3 / HC-6: implement must not teach timeouts as transient to hit 42/42."""
    source = inspect.getsource(is_transient_failure)
    assert "timeout" not in source.lower()
    assert is_transient_failure("", _TIMEOUT_STDERR) is False


# --- Determinism -------------------------------------------------------------


def test_repeated_scan_is_deterministic_for_same_window(db_session: Session):
    """Two consecutive scans on an unchanged DB must agree on harness findings."""
    ticket = make_workspace_ticket(db_session, "adv-det", slug="loregarden")
    _transient_majority_window(db_session, ticket)

    first = [
        (f.stage_key, f.ticket_id, f.summary, tuple(sorted(f.evidence.items())))
        for f in _harness_findings(scan(db_session))
    ]
    second = [
        (f.stage_key, f.ticket_id, f.summary, tuple(sorted(f.evidence.items())))
        for f in _harness_findings(scan(db_session))
    ]
    assert first == second
    assert first  # control: we actually fired


def test_list_findings_matches_scan_for_harness_cluster(db_session: Session):
    """list_findings recomputes WORKSPACE_SCOPED — must not diverge from scan()."""
    ticket = make_workspace_ticket(db_session, "adv-list", slug="loregarden")
    _transient_majority_window(db_session, ticket)

    scanned = _harness_findings(scan(db_session))
    listed = _harness_findings(list_findings(db_session))
    assert len(scanned) == 1
    assert len(listed) == 1
    assert scanned[0].stage_key == listed[0].stage_key
    assert scanned[0].ticket_id == listed[0].ticket_id == ""


# --- Share just-shy of threshold with mixed timeouts (tight) -----------------


def test_share_one_transient_below_threshold_does_not_fire(db_session: Session):
    """Boundary: floor met, share = (threshold - ε) via timeout padding."""
    _window_days, min_failures, share_threshold = _constants()
    del _window_days
    ticket = make_workspace_ticket(db_session, "adv-shy", slug="loregarden")

    # Choose transient_count / failed so share is strictly below threshold while
    # failed >= min_failures.
    transient_count = max(1, int(min_failures * share_threshold) - 1)
    if transient_count < 1:
        transient_count = 1
    failed_target = max(min_failures, transient_count + 1)
    while (transient_count / failed_target) >= share_threshold:
        failed_target += 1
    timeout_count = failed_target - transient_count

    for index in range(transient_count):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_RELOAD_STDERR,
            agent_id=f"shy-t-{index}",
            stage_key=f"shy-ts-{index}",
            orch_code="orch-shy-t",
        )
    for index in range(timeout_count):
        _failed_stage_run(
            db_session,
            ticket,
            stderr=_TIMEOUT_STDERR,
            agent_id=f"shy-to-{index}",
            stage_key=f"shy-tos-{index % 3}",
            orch_code="orch-shy-to",
        )

    assert (
        _should_fire(
            failed_target,
            transient_count,
            min_failures=min_failures,
            share_threshold=share_threshold,
        )
        is False
    )
    assert _harness_findings(scan(db_session)) == []


def test_enum_member_absent_from_autofixable_even_after_cast():
    """Type/structure mutation: string value must not sneak into AUTO_FIXABLE sets."""
    from loregarden.models.domain import AUTO_FIXABLE_CONDITIONS
    from loregarden.services.workflow_monitor import AUTO_FIXABLE

    slug = MonitorCondition.HARNESS_FAILURE_CLUSTER.value
    assert slug == "harness_failure_cluster"
    assert all(c.value != slug for c in AUTO_FIXABLE_CONDITIONS)
    assert all(c.value != slug for c in AUTO_FIXABLE)
