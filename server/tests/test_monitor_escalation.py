"""Failing contracts for cross-ticket monitor finding escalation.

Spec (lg-milestone-that-715): after record_findings, when ≥2 distinct tickets in
one workspace currently share a persisted monitor_finding title, upsert exactly
one report-only Bug under an idempotent Milestone. Lookup is legacy_external_id
via ticket_ids.resolve — never Ticket.external_id LIKE 'monitor-escalation:%'.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from loregarden.models.domain import (
    Approval,
    Artifact,
    MonitorArtifactKind,
    MonitorCondition,
    Ticket,
    WorkItemType,
)
from loregarden.models.domain.enums import AUTO_FIXABLE_CONDITIONS, MonitorMode
from loregarden.models.domain.workflow_monitor import MonitorFinding
from loregarden.services.orchestration_profile import MonitorConfig
from loregarden.services.ticket_ids import resolve as resolve_ticket_id
from loregarden.services.workflow_monitor import AUTO_FIXABLE, _finding_title, sweep
from sqlmodel import Session, col, select
from tests.factories import make_ticket, make_workspace, make_workspace_ticket

MILESTONE_LEGACY_ID = "monitor-escalations"
CONDITION = MonitorCondition.STAGE_THRASH


def _escalation():
    """Lazy import so CFG-1 regression tests still collect before the module exists."""
    from loregarden.services import monitor_escalation

    return monitor_escalation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plant_finding(
    session: Session,
    ticket: Ticket,
    *,
    condition: MonitorCondition,
    stage_key: str,
    last_seen: datetime,
    summary: str = "recurring",
    evidence: dict[str, str] | None = None,
) -> Artifact:
    """A persisted monitor_finding row — the escalation source of truth."""
    title = f"{condition.value}:{stage_key or '-'}"
    payload = {
        "condition": condition.value,
        "stage_key": stage_key,
        "summary": summary,
        "evidence": evidence or {"attempts": "9"},
        "last_seen": last_seen.isoformat(),
        "first_seen": last_seen.isoformat(),
        "occurrences": 1,
    }
    row = Artifact(
        ticket_id=ticket.id,
        kind=MonitorArtifactKind.FINDING.value,
        title=title,
        content_json=json.dumps(payload),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _escalation_bugs(session: Session, workspace_id: str) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id,
                Ticket.work_item_type == WorkItemType.BUG,
                col(Ticket.legacy_external_id).like("monitor-escalation:%"),
            )
        ).all()
    )


def _milestone(session: Session, workspace_id: str) -> Ticket | None:
    return session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace_id,
            Ticket.legacy_external_id == MILESTONE_LEGACY_ID,
        )
    ).first()


def _approvals_for(session: Session, ticket_ids: set[str]) -> list[Approval]:
    if not ticket_ids:
        return []
    return list(session.exec(select(Approval).where(col(Approval.ticket_id).in_(ticket_ids))).all())


# --- CFG-1: config untouched -------------------------------------------------


def test_monitor_defaults_and_autofixable_set_unchanged():
    """CFG-1. Escalation must not widen autofix or flip the default mode."""
    assert MonitorConfig().mode is MonitorMode.REPORT
    assert MonitorConfig().autofix == []
    assert AUTO_FIXABLE_CONDITIONS == frozenset(
        {MonitorCondition.STALE_CURSOR, MonitorCondition.EMPTIED_GROUP}
    )
    assert AUTO_FIXABLE == AUTO_FIXABLE_CONDITIONS


def test_escalation_threshold_is_two():
    assert _escalation().ESCALATION_THRESHOLD == 2


def test_escalation_legacy_id_matches_finding_title_encoding():
    """Public helper: monitor-escalation: + _finding_title encoding."""
    finding = MonitorFinding(
        condition=CONDITION,
        ticket_id="t",
        stage_key="implement",
        summary="x",
    )
    assert _escalation().escalation_legacy_id(CONDITION.value, "implement") == (
        f"monitor-escalation:{_finding_title(finding)}"
    )
    assert (
        _escalation().escalation_legacy_id(CONDITION.value, "")
        == "monitor-escalation:stage_thrash:-"
    )
    assert (
        _escalation().escalation_legacy_id(CONDITION.value, "-")
        == "monitor-escalation:stage_thrash:-"
    )


# --- REC-1 / RPT-1 / IDEM-2 lookup ------------------------------------------


def test_below_threshold_creates_nothing(db_session: Session):
    """REC-1 boundary: one ticket with a current finding is noise, not escalation."""
    ticket = make_workspace_ticket(db_session, "esc-solo")
    now = _utcnow()
    _plant_finding(db_session, ticket, condition=CONDITION, stage_key="implement", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(
        db_session, sweep_started_at=now - timedelta(seconds=1)
    )

    assert upserted == 0
    assert _milestone(db_session, ticket.workspace_id) is None
    assert _escalation_bugs(db_session, ticket.workspace_id) == []


def test_two_tickets_same_title_upsert_one_bug_under_milestone(db_session: Session):
    """REC-1 + RPT-1 + IDEM-2: one Bug per (condition, stage), resolve by legacy id."""
    a = make_workspace_ticket(db_session, "esc-a")
    b = make_workspace_ticket(db_session, "esc-b")
    assert a.workspace_id == b.workspace_id
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(
        db_session,
        a,
        condition=CONDITION,
        stage_key="implement",
        last_seen=now,
        summary="thrash on a",
    )
    _plant_finding(
        db_session,
        b,
        condition=CONDITION,
        stage_key="implement",
        last_seen=now,
        summary="thrash on b",
    )

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 1
    milestone = _milestone(db_session, a.workspace_id)
    assert milestone is not None
    assert milestone.work_item_type is WorkItemType.MILESTONE
    assert milestone.workflow_disabled is True
    assert milestone.legacy_external_id == MILESTONE_LEGACY_ID
    assert not milestone.external_id.startswith("monitor-escalation")
    assert (
        resolve_ticket_id(db_session, MILESTONE_LEGACY_ID, workspace_id=a.workspace_id) == milestone
    )

    bugs = _escalation_bugs(db_session, a.workspace_id)
    assert len(bugs) == 1
    bug = bugs[0]
    expected_legacy = _escalation().escalation_legacy_id(CONDITION.value, "implement")
    assert bug.legacy_external_id == expected_legacy
    assert bug.parent_ticket_id == milestone.id
    assert bug.work_item_type is WorkItemType.BUG
    assert bug.workflow_disabled is True
    assert bug.external_id.startswith(("lg-", "lor-"))
    assert not bug.external_id.startswith("monitor-escalation:")
    assert resolve_ticket_id(db_session, expected_legacy, workspace_id=a.workspace_id) == bug
    # Wrong-column lookup must not be how callers find it.
    assert (
        db_session.exec(
            select(Ticket).where(col(Ticket.external_id).like("monitor-escalation:%"))
        ).first()
        is None
    )
    assert a.external_id in bug.description or a.id in bug.description
    assert b.external_id in bug.description or b.id in bug.description
    assert _approvals_for(db_session, {bug.id, milestone.id}) == []


# --- TEST-1: blobert two-stage shape ----------------------------------------


def test_blobert_shape_one_bug_per_stage_key_under_one_milestone(db_session: Session):
    """TEST-1. Same condition, two stage_keys → two Bugs; second escalate does not multiply."""
    tickets = [make_workspace_ticket(db_session, f"esc-blob-{i}") for i in range(3)]
    workspace_id = tickets[0].workspace_id
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    for ticket in tickets:
        _plant_finding(
            db_session,
            ticket,
            condition=CONDITION,
            stage_key="implement",
            last_seen=now,
            summary=f"implement on {ticket.external_id}",
        )
        _plant_finding(
            db_session,
            ticket,
            condition=CONDITION,
            stage_key="verify",
            last_seen=now,
            summary=f"verify on {ticket.external_id}",
        )

    first = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)
    second = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert first == 2
    assert second == 2  # refreshed, not zeroed — still two groups upserted
    assert _milestone(db_session, workspace_id) is not None
    milestones = list(
        db_session.exec(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id,
                Ticket.legacy_external_id == MILESTONE_LEGACY_ID,
            )
        ).all()
    )
    assert len(milestones) == 1
    bugs = _escalation_bugs(db_session, workspace_id)
    assert len(bugs) == 2
    legacy_ids = {bug.legacy_external_id for bug in bugs}
    assert legacy_ids == {
        _escalation().escalation_legacy_id(CONDITION.value, "implement"),
        _escalation().escalation_legacy_id(CONDITION.value, "verify"),
    }
    assert {bug.parent_ticket_id for bug in bugs} == {milestones[0].id}
    assert all(bug.workflow_disabled for bug in bugs)
    assert milestones[0].workflow_disabled is True
    assert _approvals_for(db_session, {milestones[0].id, *(b.id for b in bugs)}) == []


# --- IDEM-1: refresh, never duplicate ---------------------------------------


def test_resweep_refreshes_description_without_second_bug(db_session: Session):
    """IDEM-1. Same current findings: one Bug, description refreshed, no Approval."""
    a = make_workspace_ticket(db_session, "esc-idem-a")
    b = make_workspace_ticket(db_session, "esc-idem-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(
        db_session,
        a,
        condition=CONDITION,
        stage_key="implement",
        last_seen=now,
        summary="first wave",
        evidence={"attempts": "9"},
    )
    _plant_finding(
        db_session,
        b,
        condition=CONDITION,
        stage_key="implement",
        last_seen=now,
        summary="first wave",
        evidence={"attempts": "9"},
    )

    _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)
    bug = _escalation_bugs(db_session, a.workspace_id)[0]
    bug_id = bug.id
    first_description = bug.description

    # Refresh the source findings' summaries as a later sweep would.
    for row in db_session.exec(
        select(Artifact).where(Artifact.kind == MonitorArtifactKind.FINDING.value)
    ).all():
        payload = json.loads(row.content_json)
        payload["summary"] = "second wave — still thrashing"
        payload["last_seen"] = _utcnow().isoformat()
        row.content_json = json.dumps(payload)
        db_session.add(row)
    db_session.commit()

    _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    bugs = _escalation_bugs(db_session, a.workspace_id)
    assert len(bugs) == 1
    assert bugs[0].id == bug_id
    assert bugs[0].description != first_description
    assert "second wave" in bugs[0].description
    assert (
        len(
            list(
                db_session.exec(
                    select(Ticket).where(
                        Ticket.workspace_id == a.workspace_id,
                        Ticket.legacy_external_id == MILESTONE_LEGACY_ID,
                    )
                ).all()
            )
        )
        == 1
    )
    assert _approvals_for(db_session, {bug_id}) == []


# --- CUR-1: currency filter -------------------------------------------------


def test_stale_last_seen_does_not_count_toward_threshold(db_session: Session):
    """CUR-1. Historical recurrence alone must not open or refresh an escalation."""
    a = make_workspace_ticket(db_session, "esc-stale-a")
    b = make_workspace_ticket(db_session, "esc-stale-b")
    sweep_started = _utcnow()
    stale = sweep_started - timedelta(hours=2)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=stale)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=stale)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _milestone(db_session, a.workspace_id) is None
    assert _escalation_bugs(db_session, a.workspace_id) == []


def test_mixed_currency_only_current_tickets_count(db_session: Session):
    """CUR-1. One current + one stale = below threshold → no escalation."""
    current = make_workspace_ticket(db_session, "esc-mixed-current")
    stale_ticket = make_workspace_ticket(db_session, "esc-mixed-stale")
    sweep_started = _utcnow()
    _plant_finding(
        db_session,
        current,
        condition=CONDITION,
        stage_key="implement",
        last_seen=sweep_started + timedelta(seconds=1),
    )
    _plant_finding(
        db_session,
        stale_ticket,
        condition=CONDITION,
        stage_key="implement",
        last_seen=sweep_started - timedelta(minutes=30),
    )

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 0
    )
    assert _escalation_bugs(db_session, current.workspace_id) == []


def test_stale_only_title_does_not_refresh_existing_escalation(db_session: Session):
    """CUR-1. A prior Bug must not be refreshed when the title is not currently reproducing."""
    a = make_workspace_ticket(db_session, "esc-hist-a")
    b = make_workspace_ticket(db_session, "esc-hist-b")
    now = _utcnow()
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)
    _escalation().escalate_recurring_findings(
        db_session, sweep_started_at=now - timedelta(seconds=5)
    )
    bug = _escalation_bugs(db_session, a.workspace_id)[0]
    frozen_description = bug.description
    bug_id = bug.id

    # Age every finding past the next sweep's start — all-time still ≥2.
    for row in db_session.exec(
        select(Artifact).where(Artifact.kind == MonitorArtifactKind.FINDING.value)
    ).all():
        payload = json.loads(row.content_json)
        payload["last_seen"] = (now - timedelta(days=1)).isoformat()
        row.content_json = json.dumps(payload)
        db_session.add(row)
    db_session.commit()

    later_sweep = now + timedelta(minutes=1)
    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=later_sweep)

    assert upserted == 0
    refreshed = db_session.get(Ticket, bug_id)
    assert refreshed is not None
    assert refreshed.description == frozen_description
    assert len(_escalation_bugs(db_session, a.workspace_id)) == 1


# --- IDEM-2: concurrent create race -----------------------------------------


def test_concurrent_create_valueerror_reresolves_and_refreshes(db_session: Session):
    """IDEM-2. ValueError('external_id already exists…') → re-resolve + refresh."""
    from loregarden.services.ticket_service import TicketService

    a = make_workspace_ticket(db_session, "esc-race-a")
    b = make_workspace_ticket(db_session, "esc-race-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)

    bug_legacy = _escalation().escalation_legacy_id(CONDITION.value, "implement")
    real_create = TicketService.create_ticket
    bug_create_attempts = {"n": 0}

    def racey_create(self, **kwargs):
        supplied = kwargs.get("external_id", "")
        if supplied == bug_legacy:
            bug_create_attempts["n"] += 1
            if bug_create_attempts["n"] == 1:
                # Peer insert commits first, then our create raises — escalate must
                # catch, re-resolve by legacy id, and refresh rather than fail.
                created = real_create(self, **kwargs)
                created.workflow_disabled = True
                self.session.add(created)
                self.session.commit()
                raise ValueError(f"external_id already exists: {supplied}")
        return real_create(self, **kwargs)

    with patch.object(TicketService, "create_ticket", racey_create):
        upserted = _escalation().escalate_recurring_findings(
            db_session, sweep_started_at=sweep_started
        )

    assert upserted == 1
    assert bug_create_attempts["n"] == 1
    bugs = _escalation_bugs(db_session, a.workspace_id)
    assert len(bugs) == 1
    assert bugs[0].legacy_external_id == bug_legacy
    assert bugs[0].description != ""
    assert "esc-race" in bugs[0].description or a.id in bugs[0].description
    assert bugs[0].workflow_disabled is True
    milestone = _milestone(db_session, a.workspace_id)
    assert milestone is not None
    assert _approvals_for(db_session, {bugs[0].id, milestone.id}) == []


# --- Sweep seam -------------------------------------------------------------


def test_sweep_calls_escalate_between_record_and_autofix():
    """Hook contract: scan → record_findings → escalate → apply_autofixes."""
    source = inspect.getsource(sweep)
    record_at = source.index("record_findings")
    escalate_at = source.index("escalate_recurring_findings")
    autofix_at = source.index("apply_autofixes")
    assert record_at < escalate_at < autofix_at
    assert "sweep_started_at" in source


# --- Adversarial (test-break): encoding, aggregation, currency, isolation ---


def test_empty_stage_key_end_to_end_uses_dash_sentinel(db_session: Session):
    """Empty stage_key must escalate under monitor-escalation:{condition}:- (not trailing colon)."""
    a = make_workspace_ticket(db_session, "esc-empty-a")
    b = make_workspace_ticket(db_session, "esc-empty-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 1
    bugs = _escalation_bugs(db_session, a.workspace_id)
    assert len(bugs) == 1
    assert bugs[0].legacy_external_id == "monitor-escalation:stage_thrash:-"
    assert bugs[0].legacy_external_id == _escalation().escalation_legacy_id(CONDITION.value, "")
    assert (
        resolve_ticket_id(
            db_session, "monitor-escalation:stage_thrash:-", workspace_id=a.workspace_id
        )
        == bugs[0]
    )


def test_empty_and_dash_stage_key_coalesce_into_one_group(db_session: Session):
    """stage_key '' and '-' share _finding_title sentinel — one Bug, not two."""
    a = make_workspace_ticket(db_session, "esc-coalesce-a")
    b = make_workspace_ticket(db_session, "esc-coalesce-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="-", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 1
    bugs = _escalation_bugs(db_session, a.workspace_id)
    assert len(bugs) == 1
    assert bugs[0].legacy_external_id == "monitor-escalation:stage_thrash:-"


def test_duplicate_findings_on_one_ticket_do_not_meet_threshold(db_session: Session):
    """Count distinct ticket_id — two rows on one ticket are still one ticket."""
    ticket = make_workspace_ticket(db_session, "esc-dup-solo")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, ticket, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, ticket, condition=CONDITION, stage_key="implement", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, ticket.workspace_id) == []
    assert _milestone(db_session, ticket.workspace_id) is None


def test_cross_workspace_same_title_does_not_cross_escalate(db_session: Session):
    """Aggregation is per workspace_id — one hit each side stays below threshold."""
    home = make_workspace_ticket(db_session, "esc-ws-home")
    other_ws = make_workspace(db_session, slug="esc-other-ws")
    other = make_ticket(
        db_session,
        workspace_id=other_ws.id,
        external_id="esc-ws-other",
        title="esc-ws-other",
    )
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, home, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, other, condition=CONDITION, stage_key="implement", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, home.workspace_id) == []
    assert _escalation_bugs(db_session, other.workspace_id) == []
    assert _milestone(db_session, home.workspace_id) is None
    assert _milestone(db_session, other.workspace_id) is None


def test_last_seen_equal_to_sweep_started_at_counts(db_session: Session):
    """Currency is inclusive: last_seen == sweep_started_at still qualifies."""
    a = make_workspace_ticket(db_session, "esc-eq-a")
    b = make_workspace_ticket(db_session, "esc-eq-b")
    sweep_started = _utcnow()
    _plant_finding(
        db_session, a, condition=CONDITION, stage_key="implement", last_seen=sweep_started
    )
    _plant_finding(
        db_session, b, condition=CONDITION, stage_key="implement", last_seen=sweep_started
    )

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 1
    )
    assert len(_escalation_bugs(db_session, a.workspace_id)) == 1


def test_malformed_or_missing_last_seen_is_ignored_not_raised(db_session: Session):
    """Corrupt currency fields must not crash the sweep path or count toward threshold."""
    good_a = make_workspace_ticket(db_session, "esc-bad-a")
    good_b = make_workspace_ticket(db_session, "esc-bad-b")
    junk = make_workspace_ticket(db_session, "esc-bad-junk")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, good_a, condition=CONDITION, stage_key="implement", last_seen=now)
    # One current peer would meet threshold if junk counted — it must not.
    for broken in (
        {"condition": CONDITION.value, "stage_key": "implement", "summary": "no clock"},
        {
            "condition": CONDITION.value,
            "stage_key": "implement",
            "summary": "garbage clock",
            "last_seen": "not-a-datetime",
        },
    ):
        db_session.add(
            Artifact(
                ticket_id=junk.id,
                kind=MonitorArtifactKind.FINDING.value,
                title=f"{CONDITION.value}:implement",
                content_json=json.dumps(broken),
            )
        )
    db_session.commit()

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, good_a.workspace_id) == []
    # Pair a real second current ticket — escalate still works after junk rows.
    _plant_finding(db_session, good_b, condition=CONDITION, stage_key="implement", last_seen=now)
    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 1
    )


def test_different_conditions_do_not_merge_under_same_stage(db_session: Session):
    """Group key is full title — thrash:implement ≠ stale_cursor:implement."""
    a = make_workspace_ticket(db_session, "esc-cond-a")
    b = make_workspace_ticket(db_session, "esc-cond-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(
        db_session,
        b,
        condition=MonitorCondition.STALE_CURSOR,
        stage_key="implement",
        last_seen=now,
    )

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, a.workspace_id) == []


def test_escalate_never_dispatches_orchestration_or_approvals(db_session: Session):
    """RPT-1. Escalation path must not start agents or open inbox items."""
    a = make_workspace_ticket(db_session, "esc-nodisp-a")
    b = make_workspace_ticket(db_session, "esc-nodisp-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)

    mod = _escalation()
    source = inspect.getsource(mod)
    for forbidden in ("start_orchestration", "request_approval", "start_stage"):
        assert forbidden not in source, f"monitor_escalation must not call {forbidden}"

    upserted = mod.escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 1
    bugs = _escalation_bugs(db_session, a.workspace_id)
    milestone = _milestone(db_session, a.workspace_id)
    assert bugs and milestone
    assert bugs[0].workflow_disabled is True
    assert milestone.workflow_disabled is True
    assert _approvals_for(db_session, {bugs[0].id, milestone.id}) == []


def test_concurrent_milestone_create_valueerror_reresolves(db_session: Session):
    """IDEM-2 race on the Milestone key, not only the Bug — re-resolve + continue."""
    from loregarden.services.ticket_service import TicketService

    a = make_workspace_ticket(db_session, "esc-ms-race-a")
    b = make_workspace_ticket(db_session, "esc-ms-race-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)

    real_create = TicketService.create_ticket
    milestone_attempts = {"n": 0}

    def racey_create(self, **kwargs):
        supplied = kwargs.get("external_id", "")
        if supplied == MILESTONE_LEGACY_ID:
            milestone_attempts["n"] += 1
            if milestone_attempts["n"] == 1:
                created = real_create(self, **kwargs)
                created.workflow_disabled = True
                self.session.add(created)
                self.session.commit()
                raise ValueError(f"external_id already exists: {supplied}")
        return real_create(self, **kwargs)

    with patch.object(TicketService, "create_ticket", racey_create):
        upserted = _escalation().escalate_recurring_findings(
            db_session, sweep_started_at=sweep_started
        )

    assert upserted == 1
    assert milestone_attempts["n"] == 1
    milestones = list(
        db_session.exec(
            select(Ticket).where(
                Ticket.workspace_id == a.workspace_id,
                Ticket.legacy_external_id == MILESTONE_LEGACY_ID,
            )
        ).all()
    )
    assert len(milestones) == 1
    assert milestones[0].workflow_disabled is True
    assert len(_escalation_bugs(db_session, a.workspace_id)) == 1


def test_wrong_column_external_id_never_holds_escalation_key(db_session: Session):
    """IDEM-2. Spelled external_id stays lg-/lor-; legacy_external_id holds the key."""
    a = make_workspace_ticket(db_session, "esc-col-a")
    b = make_workspace_ticket(db_session, "esc-col-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)

    _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    bug_legacy = _escalation().escalation_legacy_id(CONDITION.value, "implement")
    by_legacy = resolve_ticket_id(db_session, bug_legacy, workspace_id=a.workspace_id)
    by_milestone = resolve_ticket_id(db_session, MILESTONE_LEGACY_ID, workspace_id=a.workspace_id)
    assert by_legacy is not None
    assert by_milestone is not None
    assert by_legacy.external_id != bug_legacy
    assert by_milestone.external_id != MILESTONE_LEGACY_ID
    assert by_legacy.legacy_external_id == bug_legacy
    assert by_milestone.legacy_external_id == MILESTONE_LEGACY_ID
    # A caller filtering Ticket.external_id LIKE 'monitor-escalation:%' finds nothing.
    assert (
        db_session.exec(
            select(Ticket).where(
                Ticket.workspace_id == a.workspace_id,
                col(Ticket.external_id).like("monitor-escalation:%"),
            )
        ).first()
        is None
    )
    assert (
        db_session.exec(
            select(Ticket).where(
                Ticket.workspace_id == a.workspace_id,
                Ticket.external_id == MILESTONE_LEGACY_ID,
            )
        ).first()
        is None
    )


# --- Adversarial (test-break reroute): corrupt input, kind filter, race narrowness ---


def test_invalid_json_content_is_ignored_not_raised(db_session: Session):
    """Corrupt content_json must not crash escalate or pad the distinct-ticket count."""
    good = make_workspace_ticket(db_session, "esc-json-good")
    junk = make_workspace_ticket(db_session, "esc-json-junk")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, good, condition=CONDITION, stage_key="implement", last_seen=now)
    db_session.add(
        Artifact(
            ticket_id=junk.id,
            kind=MonitorArtifactKind.FINDING.value,
            title=f"{CONDITION.value}:implement",
            content_json="{not-json",
        )
    )
    db_session.commit()

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, good.workspace_id) == []


def test_non_finding_artifact_kind_does_not_count(db_session: Session):
    """Aggregation source is kind=monitor_finding only — colliding titles on other kinds are noise."""
    a = make_workspace_ticket(db_session, "esc-kind-a")
    b = make_workspace_ticket(db_session, "esc-kind-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    title = f"{CONDITION.value}:implement"
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    db_session.add(
        Artifact(
            ticket_id=b.id,
            kind="gate_evaluation",
            title=title,
            content_json=json.dumps(
                {
                    "condition": CONDITION.value,
                    "stage_key": "implement",
                    "summary": "not a finding",
                    "last_seen": now.isoformat(),
                }
            ),
        )
    )
    db_session.commit()

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 0
    )
    assert _escalation_bugs(db_session, a.workspace_id) == []


def test_unrelated_create_valueerror_is_not_swallowed_as_race(db_session: Session):
    """IDEM-2 is narrow: only 'external_id already exists…' re-resolves; other ValueErrors propagate."""
    from loregarden.services.ticket_service import TicketService

    a = make_workspace_ticket(db_session, "esc-ve-a")
    b = make_workspace_ticket(db_session, "esc-ve-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)

    bug_legacy = _escalation().escalation_legacy_id(CONDITION.value, "implement")
    real_create = TicketService.create_ticket

    def boom_create(self, **kwargs):
        if kwargs.get("external_id") == bug_legacy:
            raise ValueError("something else went wrong")
        return real_create(self, **kwargs)

    with patch.object(TicketService, "create_ticket", boom_create):
        try:
            _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)
            raised = False
        except ValueError as exc:
            raised = True
            assert "something else went wrong" in str(exc)

    assert raised is True
    assert _escalation_bugs(db_session, a.workspace_id) == []


def test_above_threshold_still_one_bug_per_title(db_session: Session):
    """REC-1 stress: five current tickets with the same title → still exactly one Bug."""
    tickets = [make_workspace_ticket(db_session, f"esc-many-{i}") for i in range(5)]
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    for ticket in tickets:
        _plant_finding(
            db_session, ticket, condition=CONDITION, stage_key="implement", last_seen=now
        )

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 1
    bugs = _escalation_bugs(db_session, tickets[0].workspace_id)
    assert len(bugs) == 1
    assert bugs[0].legacy_external_id == _escalation().escalation_legacy_id(
        CONDITION.value, "implement"
    )


def test_z_suffix_last_seen_parses_as_current(db_session: Session):
    """Currency parser must accept ISO-8601 Z (record_findings may emit offset or Z)."""
    a = make_workspace_ticket(db_session, "esc-z-a")
    b = make_workspace_ticket(db_session, "esc-z-b")
    sweep_started = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    z_stamp = "2026-09-21T12:00:01Z"
    for ticket in (a, b):
        db_session.add(
            Artifact(
                ticket_id=ticket.id,
                kind=MonitorArtifactKind.FINDING.value,
                title=f"{CONDITION.value}:implement",
                content_json=json.dumps(
                    {
                        "condition": CONDITION.value,
                        "stage_key": "implement",
                        "summary": "z clock",
                        "last_seen": z_stamp,
                        "first_seen": z_stamp,
                        "occurrences": 1,
                    }
                ),
            )
        )
    db_session.commit()

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 1
    )
    assert len(_escalation_bugs(db_session, a.workspace_id)) == 1


def test_workspace_scoped_condition_findings_never_escalate(db_session: Session):
    """WORKSPACE_SCOPED never persist via record_findings; if planted, still must not escalate."""
    a = make_workspace_ticket(db_session, "esc-wscope-a")
    b = make_workspace_ticket(db_session, "esc-wscope-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    scoped = MonitorCondition.FAILURE_CLUSTER
    _plant_finding(db_session, a, condition=scoped, stage_key="-", last_seen=now)
    _plant_finding(db_session, b, condition=scoped, stage_key="-", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, a.workspace_id) == []
    assert _milestone(db_session, a.workspace_id) is None


def test_sweep_captures_sweep_started_at_before_record_findings():
    """Hook timing: sweep_started_at must be bound before record_findings so currency is this sweep."""
    source = inspect.getsource(sweep)
    started_at = source.index("sweep_started_at")
    record_at = source.index("record_findings")
    escalate_at = source.index("escalate_recurring_findings")
    assert started_at < record_at < escalate_at


def test_aggregation_uses_artifact_title_not_content_condition(db_session: Session):
    """Group key is artifact.title — content_json.condition drift must not split or invent groups."""
    a = make_workspace_ticket(db_session, "esc-title-a")
    b = make_workspace_ticket(db_session, "esc-title-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    title = f"{CONDITION.value}:implement"
    for ticket, content_condition in (
        (a, CONDITION.value),
        (b, MonitorCondition.STALE_CURSOR.value),  # content lies; title is thrash:implement
    ):
        db_session.add(
            Artifact(
                ticket_id=ticket.id,
                kind=MonitorArtifactKind.FINDING.value,
                title=title,
                content_json=json.dumps(
                    {
                        "condition": content_condition,
                        "stage_key": "implement",
                        "summary": "title wins",
                        "last_seen": now.isoformat(),
                        "first_seen": now.isoformat(),
                        "occurrences": 1,
                    }
                ),
            )
        )
    db_session.commit()

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 1
    bugs = _escalation_bugs(db_session, a.workspace_id)
    assert len(bugs) == 1
    assert bugs[0].legacy_external_id == _escalation().escalation_legacy_id(
        CONDITION.value, "implement"
    )


# --- Adversarial (test-break re-entry): clocks, isolation, refresh, hook call ---


def test_offset_plus00_last_seen_parses_as_current(db_session: Session):
    """Currency parser must accept +00:00 — record_findings emits datetime.isoformat() that way."""
    a = make_workspace_ticket(db_session, "esc-offset-a")
    b = make_workspace_ticket(db_session, "esc-offset-b")
    sweep_started = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    stamp = "2026-09-21T12:00:01+00:00"
    for ticket in (a, b):
        db_session.add(
            Artifact(
                ticket_id=ticket.id,
                kind=MonitorArtifactKind.FINDING.value,
                title=f"{CONDITION.value}:implement",
                content_json=json.dumps(
                    {
                        "condition": CONDITION.value,
                        "stage_key": "implement",
                        "summary": "offset clock",
                        "last_seen": stamp,
                        "first_seen": stamp,
                        "occurrences": 1,
                    }
                ),
            )
        )
    db_session.commit()

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 1
    )
    assert len(_escalation_bugs(db_session, a.workspace_id)) == 1


def test_naive_last_seen_does_not_typeerror_against_aware_sweep(db_session: Session):
    """Aware sweep_started_at vs naive last_seen must not raise TypeError (CUR-1 parse path)."""
    a = make_workspace_ticket(db_session, "esc-naive-a")
    b = make_workspace_ticket(db_session, "esc-naive-b")
    sweep_started = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    for ticket in (a, b):
        db_session.add(
            Artifact(
                ticket_id=ticket.id,
                kind=MonitorArtifactKind.FINDING.value,
                title=f"{CONDITION.value}:implement",
                content_json=json.dumps(
                    {
                        "condition": CONDITION.value,
                        "stage_key": "implement",
                        "summary": "naive clock",
                        "last_seen": "2026-09-21T12:00:01",  # no tzinfo
                        "first_seen": "2026-09-21T12:00:01",
                        "occurrences": 1,
                    }
                ),
            )
        )
    db_session.commit()

    # Pin: no TypeError. Counting-as-UTC (1) or ignoring (0) are both legal; never multiply.
    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)
    assert upserted in (0, 1)
    assert len(_escalation_bugs(db_session, a.workspace_id)) == upserted


def test_last_seen_one_microsecond_before_sweep_is_stale(db_session: Session):
    """CUR-1 exclusive boundary: last_seen = sweep_started_at - 1µs must not count."""
    a = make_workspace_ticket(db_session, "esc-us-a")
    b = make_workspace_ticket(db_session, "esc-us-b")
    sweep_started = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    almost = sweep_started - timedelta(microseconds=1)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=almost)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=almost)

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 0
    )
    assert _escalation_bugs(db_session, a.workspace_id) == []


def test_two_workspaces_escalate_independently(db_session: Session):
    """REC-1 positive isolation: each workspace gets its own Milestone + Bug for the same title."""
    home_a = make_workspace_ticket(db_session, "esc-ind-home-a")
    home_b = make_workspace_ticket(db_session, "esc-ind-home-b")
    other_ws = make_workspace(db_session, slug="esc-ind-other")
    other_a = make_ticket(
        db_session,
        workspace_id=other_ws.id,
        external_id="esc-ind-other-a",
        title="esc-ind-other-a",
    )
    other_b = make_ticket(
        db_session,
        workspace_id=other_ws.id,
        external_id="esc-ind-other-b",
        title="esc-ind-other-b",
    )
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    for ticket in (home_a, home_b, other_a, other_b):
        _plant_finding(
            db_session, ticket, condition=CONDITION, stage_key="implement", last_seen=now
        )

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 2
    for workspace_id in (home_a.workspace_id, other_ws.id):
        milestone = _milestone(db_session, workspace_id)
        bugs = _escalation_bugs(db_session, workspace_id)
        assert milestone is not None
        assert milestone.workflow_disabled is True
        assert len(bugs) == 1
        assert bugs[0].parent_ticket_id == milestone.id
        assert bugs[0].legacy_external_id == _escalation().escalation_legacy_id(
            CONDITION.value, "implement"
        )
    # Two milestones with the same legacy key — one per workspace, not shared.
    milestones = list(
        db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == MILESTONE_LEGACY_ID)
        ).all()
    )
    assert len(milestones) == 2
    assert {m.workspace_id for m in milestones} == {home_a.workspace_id, other_ws.id}


def test_refresh_does_not_clear_workflow_disabled(db_session: Session):
    """IDEM-1/RPT-1: description refresh must not flip workflow_disabled True→False."""
    a = make_workspace_ticket(db_session, "esc-keepdis-a")
    b = make_workspace_ticket(db_session, "esc-keepdis-b")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, a, condition=CONDITION, stage_key="implement", last_seen=now)
    _plant_finding(db_session, b, condition=CONDITION, stage_key="implement", last_seen=now)

    _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)
    bug = _escalation_bugs(db_session, a.workspace_id)[0]
    milestone = _milestone(db_session, a.workspace_id)
    assert bug.workflow_disabled is True
    assert milestone is not None and milestone.workflow_disabled is True

    for row in db_session.exec(
        select(Artifact).where(Artifact.kind == MonitorArtifactKind.FINDING.value)
    ).all():
        payload = json.loads(row.content_json)
        payload["summary"] = "still thrashing on refresh"
        payload["last_seen"] = _utcnow().isoformat()
        row.content_json = json.dumps(payload)
        db_session.add(row)
    db_session.commit()

    _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    refreshed_bug = db_session.get(Ticket, bug.id)
    refreshed_ms = db_session.get(Ticket, milestone.id)
    assert refreshed_bug is not None and refreshed_bug.workflow_disabled is True
    assert refreshed_ms is not None and refreshed_ms.workflow_disabled is True
    assert "still thrashing on refresh" in refreshed_bug.description
    assert len(_escalation_bugs(db_session, a.workspace_id)) == 1


def test_empty_content_json_and_non_string_last_seen_ignored(db_session: Session):
    """Empty / typed-wrong content_json must not crash escalate or count toward threshold."""
    good = make_workspace_ticket(db_session, "esc-empty-good")
    junk = make_workspace_ticket(db_session, "esc-empty-junk")
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    _plant_finding(db_session, good, condition=CONDITION, stage_key="implement", last_seen=now)
    title = f"{CONDITION.value}:implement"
    for broken_content in ("", "null", json.dumps({"last_seen": 1_725_000_000})):
        db_session.add(
            Artifact(
                ticket_id=junk.id,
                kind=MonitorArtifactKind.FINDING.value,
                title=title,
                content_json=broken_content,
            )
        )
    db_session.commit()

    assert (
        _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started) == 0
    )
    assert _escalation_bugs(db_session, good.workspace_id) == []


def test_all_workspace_scoped_conditions_never_escalate(db_session: Session):
    """Every WORKSPACE_SCOPED condition is excluded — not only FAILURE_CLUSTER."""
    from loregarden.services.workflow_monitor import WORKSPACE_SCOPED

    assert WORKSPACE_SCOPED == frozenset(
        {
            MonitorCondition.FAILURE_CLUSTER,
            MonitorCondition.DRAFT_DRIFT,
            MonitorCondition.SKIP_CONDITION_ROT,
            MonitorCondition.TIMEOUT_FLOOR_STALE,
            MonitorCondition.HARNESS_FAILURE_CLUSTER,
        }
    )
    now = _utcnow()
    sweep_started = now - timedelta(seconds=5)
    tickets = [make_workspace_ticket(db_session, f"esc-wscope-all-{i}") for i in range(2)]
    for condition in WORKSPACE_SCOPED:
        for ticket in tickets:
            _plant_finding(db_session, ticket, condition=condition, stage_key="-", last_seen=now)

    upserted = _escalation().escalate_recurring_findings(db_session, sweep_started_at=sweep_started)

    assert upserted == 0
    assert _escalation_bugs(db_session, tickets[0].workspace_id) == []
    assert _milestone(db_session, tickets[0].workspace_id) is None


def test_sweep_calls_escalate_with_sweep_started_at_keyword():
    """Hook call shape: escalate_recurring_findings(..., sweep_started_at=<aware datetime>)."""
    source = inspect.getsource(sweep)
    # Bound before record, passed as keyword — not a bare positional after session.
    assert "sweep_started_at=" in source or "sweep_started_at =" in source
    assert "escalate_recurring_findings(session" in source.replace(" ", "") or (
        "escalate_recurring_findings(" in source and "sweep_started_at" in source
    )
    # Must not call escalate before capturing the clock.
    clock_bind = min(
        i
        for i in (
            source.find("sweep_started_at ="),
            source.find("sweep_started_at="),
        )
        if i >= 0
    )
    assert clock_bind < source.index("record_findings")
    assert source.index("record_findings") < source.index("escalate_recurring_findings")


def test_cfg1_autofix_and_refusal_bodies_untouched():
    """CFG-1: apply_autofixes / _raise_refusal_approval remain the pre-escalation implementations."""
    from loregarden.services import workflow_monitor

    autofix_src = inspect.getsource(workflow_monitor.apply_autofixes)
    refusal_src = inspect.getsource(workflow_monitor._raise_refusal_approval)
    # Escalation must not be folded into autofix or refusal — those stay independent.
    assert "escalate_recurring_findings" not in autofix_src
    assert "escalate_recurring_findings" not in refusal_src
    assert "monitor-escalation" not in autofix_src
    assert "monitor-escalation" not in refusal_src
    # Refusal still opens Approvals; escalation path must not borrow that.
    assert "Approval" in refusal_src or "approval" in refusal_src.lower()
