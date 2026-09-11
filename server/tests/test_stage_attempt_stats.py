"""Attempt telemetry: how many tries a stage needs, and whether the caps bind.

Both halves of this module have already been wrong once, in the two ways
measurement code fails: an ordering mistake that would have reported every stage
as passing first try, and a guessed artifact kind that undercounted one cap's
pressure by an order of magnitude while still returning a plausible number. The
second is the dangerous one — nobody re-derives a figure that looks reasonable —
so these tests pin the row selection against the modules that write the rows.
"""

import json
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    ReworkArtifactKind,
    StageBudgetArtifactKind,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.rework_feedback import rework_feedback_artifact_title
from loregarden.services.stage_attempt_stats import (
    RetryCounter,
    cap_pressure,
    load_stage_attempt_stats,
)
from loregarden.services.stage_retry_budget import (
    gate_failure_artifact_title,
    stage_dispatch_artifact_title,
)
from loregarden.services.stage_transient_retry import transient_retry_artifact_title
from sqlmodel import Session

BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _ticket(db_session: Session, external_id: str) -> Ticket:
    ws = Workspace(
        slug=f"attempt-stats-{external_id}",
        name="Attempt stats",
        repo_path="/nonexistent/attempt-stats",
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title="Attempt stats",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _report(db_session: Session, ticket: Ticket, stage: str, status: str, minute: int) -> None:
    """One stage report, at a controlled time so ordinals are deterministic."""
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=f"Stage report — {stage}",
            content_json=json.dumps({"stage_key": stage, "status": status}),
            created_at=BASE + timedelta(minutes=minute),
        )
    )
    db_session.commit()


def test_the_first_pass_ordinal_is_counted_in_chronological_order(db_session: Session):
    """The load-bearing detail: a newest-first query reports every stage as
    passing on attempt 1, which is a plausible and completely wrong answer."""
    ticket = _ticket(db_session, "chronological")
    _report(db_session, ticket, "implement", "needs_rework", minute=1)
    _report(db_session, ticket, "implement", "fail", minute=2)
    _report(db_session, ticket, "implement", "pass", minute=3)

    stats = load_stage_attempt_stats(db_session, workspace_id=ticket.workspace_id)
    profile = stats.profile("implement")

    assert profile is not None
    assert profile.pairs == 1
    assert profile.passed == 1
    assert profile.first_pass_at_attempt == {3: 1}
    assert profile.median_attempts_to_pass == 3
    assert profile.never_passed == 0


def test_a_later_pass_does_not_move_the_first_one(db_session: Session):
    """A stage re-entered after passing (a rework round on a sibling) must not
    overwrite where it first converged."""
    ticket = _ticket(db_session, "first-pass-sticks")
    _report(db_session, ticket, "review", "fail", minute=1)
    _report(db_session, ticket, "review", "pass", minute=2)
    _report(db_session, ticket, "review", "pass", minute=3)

    profile = load_stage_attempt_stats(db_session, workspace_id=ticket.workspace_id).profile(
        "review"
    )

    assert profile is not None
    assert profile.first_pass_at_attempt == {2: 1}


def test_a_stage_that_never_passed_is_counted_separately(db_session: Session):
    """Not a failure rate: a stage still in flight lands here too, which is why
    it is reported rather than folded into an average."""
    ticket = _ticket(db_session, "never-passed")
    _report(db_session, ticket, "verify", "needs_rework", minute=1)
    _report(db_session, ticket, "verify", "needs_rework", minute=2)

    profile = load_stage_attempt_stats(db_session, workspace_id=ticket.workspace_id).profile(
        "verify"
    )

    assert profile is not None
    assert profile.pairs == 1
    assert profile.passed == 0
    assert profile.never_passed == 1
    assert profile.median_attempts_to_pass is None
    assert profile.p95_attempts_to_pass is None, (
        "a stage with no pass has no percentile; 0 would read as 'instant'"
    )


def test_a_blocked_verdict_is_not_a_pass(db_session: Session):
    """`blocked` stops a stage without settling it."""
    ticket = _ticket(db_session, "blocked-verdict")
    _report(db_session, ticket, "spec", "blocked", minute=1)

    profile = load_stage_attempt_stats(db_session, workspace_id=ticket.workspace_id).profile("spec")

    assert profile is not None
    assert profile.passed == 0


def test_an_unparseable_report_is_skipped_not_fatal(db_session: Session):
    """Payload shapes change; a 90-day window must survive an old row."""
    ticket = _ticket(db_session, "unparseable")
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title="Stage report — plan",
            content_json="[not an object]",
            created_at=BASE,
        )
    )
    db_session.commit()
    _report(db_session, ticket, "plan", "pass", minute=1)

    stats = load_stage_attempt_stats(db_session, workspace_id=ticket.workspace_id)

    assert stats.reports_read == 1
    assert stats.profile("plan").first_pass_at_attempt == {1: 1}


def test_the_stage_is_recovered_from_the_title_when_the_payload_omits_it(db_session: Session):
    """Payloads predating `stage_key` in the report content are still countable —
    the title has carried the stage since the first version."""
    ticket = _ticket(db_session, "legacy-payload")
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title="Stage report — test-break",
            content_json=json.dumps({"status": "pass"}),
            created_at=BASE,
        )
    )
    db_session.commit()

    profile = load_stage_attempt_stats(db_session, workspace_id=ticket.workspace_id).profile(
        "test-break"
    )

    assert profile is not None and profile.passed == 1


# --------------------------------------------------------------------------- #
# Cap pressure reads each cap's own counter                                    #
# --------------------------------------------------------------------------- #


def _marker(db_session: Session, ticket: Ticket, *, kind: str, title: str, minute: int) -> None:
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=kind,
            title=title,
            created_at=BASE + timedelta(minutes=minute),
        )
    )
    db_session.commit()


def test_the_reroute_cap_counts_the_ledgers_own_kind(db_session: Session):
    """The bug this pins: the ledger's kind was guessed as `context`, which was
    right until migration 0103 gave it one of its own. The endpoint then counted
    legacy rows, missed every current one, and reported a tenth of the real
    pressure — a wrong number that looked entirely reasonable.
    """
    ticket = _ticket(db_session, "rework-kind")
    title = rework_feedback_artifact_title("implement")
    for minute in range(4):
        _marker(db_session, ticket, kind=ReworkArtifactKind.FEEDBACK, title=title, minute=minute)
    # A legacy row under the old shared kind must not be what makes this pass.
    _marker(db_session, ticket, kind=ArtifactKind.CONTEXT, title=title, minute=9)

    pressure = cap_pressure(
        db_session,
        name="rework",
        cap=3,
        counter=RetryCounter.REWORK,
        workspace_id=ticket.workspace_id,
    )

    assert pressure.worst_observed == 4
    assert pressure.pairs_at_or_over_cap == 1
    assert pressure.margin == -1


def test_each_counter_reads_only_its_own_rows(db_session: Session):
    """Four caps, four counters. Comparing them all to one metric is what the
    first version of this did, and it reported a gate-fix margin against a
    review stage's report rounds."""
    ticket = _ticket(db_session, "counter-isolation")
    _marker(
        db_session,
        ticket,
        kind=StageBudgetArtifactKind.DISPATCH,
        title=stage_dispatch_artifact_title("implement"),
        minute=1,
    )
    for minute in (2, 3):
        _marker(
            db_session,
            ticket,
            kind=ArtifactKind.ERROR,
            title=gate_failure_artifact_title("implement"),
            minute=minute,
        )
    for minute in (4, 5, 6):
        _marker(
            db_session,
            ticket,
            kind=StageBudgetArtifactKind.TRANSIENT_RETRY,
            title=transient_retry_artifact_title("implement"),
            minute=minute,
        )

    def worst(counter: RetryCounter) -> int:
        return cap_pressure(
            db_session,
            name=counter.value,
            cap=99,
            counter=counter,
            workspace_id=ticket.workspace_id,
        ).worst_observed

    assert worst(RetryCounter.DISPATCH) == 1
    assert worst(RetryCounter.GATE_FIX) == 2
    assert worst(RetryCounter.TRANSIENT) == 3
    assert worst(RetryCounter.REWORK) == 0


def test_a_cap_nothing_approaches_reports_positive_margin(db_session: Session):
    """The reading that says "raising this is free, lowering it is untested"."""
    ticket = _ticket(db_session, "slack-cap")
    _marker(
        db_session,
        ticket,
        kind=StageBudgetArtifactKind.DISPATCH,
        title=stage_dispatch_artifact_title("plan"),
        minute=1,
    )

    pressure = cap_pressure(
        db_session,
        name="dispatch",
        cap=12,
        counter=RetryCounter.DISPATCH,
        workspace_id=ticket.workspace_id,
    )

    assert pressure.worst_observed == 1
    assert pressure.margin == 11
    assert pressure.pairs_at_or_over_cap == 0


def test_an_empty_workspace_reports_no_pressure_rather_than_failing(db_session: Session):
    ticket = _ticket(db_session, "empty")
    pressure = cap_pressure(
        db_session,
        name="dispatch",
        cap=12,
        counter=RetryCounter.DISPATCH,
        workspace_id=ticket.workspace_id,
    )
    assert pressure.worst_observed == 0
    assert pressure.margin == 12
