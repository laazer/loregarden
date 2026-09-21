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
