"""Re-entry is registered and bounded: `land_verification_ran` (lg-milestone-that-1419).

R4 of the specification. AC4, AC10 and AC11.

Two hand repairs have already failed to prevent the next instance of "a ticket
is `done`, its tests landed, its implementation is missing". The third is not a
repair: it is a registered invariant, so the state is reported the next time a
writer lets it through instead of waiting for somebody to run the full suite by
hand.

**The detector does not run pytest, and that is settled, measured and not to be
re-litigated.** `config.reconcile_interval_seconds` is 30.0,
`reconciliation.PERIODIC_STEPS` runs every detector on that tick, the suite is
901s, collection alone is 21s, and `test_invariants.py` runs every registered
detector against the production-shaped scenario — so a test-running detector is
pytest inside pytest. The detector reads a **record**, and R3's land-verification
receipt is the record it reads.

**Bounding it is the other half.** Measured on the live database read-only
2026-10-23: 1,844 tickets, 474 `done`, **80** `done` with a `landed_sha`, **0**
land-verification receipts of any kind. A detector reading only "done + landed +
no passing receipt" fires on all 80 inside one reconcile tick — 80 upserted
`invariant_unrepaired` findings plus one escalation Bug listing all 80 by
external id, every one a landing from before the writer existed. `run_invariant`
re-reports the pre-`since` backlog on EVERY pass for a report-only invariant, so
it is not self-limiting. That is the 138-finding Monitor wall this project
already has a name for.

So the detector's fourth condition inverts `invariants._entered_after`'s
"unknown entry time counts as re-entry" default on purpose: a landing it cannot
date is by construction one that predates the receipt writer.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    InvariantReentry,
    MonitorArtifactKind,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services import git_subprocess, invariant_registry
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.invariant_registry import INVARIANTS, LAND_VERIFICATION_RAN
from loregarden.services.invariants import run_invariant
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.testing.factories import make_artifact, make_ticket, make_workspace
from loregarden.testing.prod_shape import build_prod_shape
from sqlmodel import Session, col, select

#: The receipt's three verdict words; only the first clears the invariant.
PASSED, FAILED, DISABLED = "passed", "failed", "disabled"


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    """Seeded via `client`: escalating a finding files a bug, which needs a template."""
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def _since() -> date:
    return LAND_VERIFICATION_RAN.since


def _landed(
    session: Session,
    workspace: Workspace,
    external_id: str,
    *,
    days_from_since: int | None = 1,
    state: TicketState = TicketState.DONE,
    landed_sha: str = "c0ffee" + "0" * 34,
    landed_branch: str = "integration/lg-ms-1",
) -> Ticket:
    """A ticket recorded as landed. `days_from_since=None` leaves it undated."""
    resolved = None
    if days_from_since is not None:
        midnight = datetime(_since().year, _since().month, _since().day, tzinfo=timezone.utc)
        resolved = midnight + timedelta(days=days_from_since)
    ticket = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=external_id,
        title=f"Ticket {external_id}",
        state=state,
        resolved_at=resolved,
    )
    ticket.resolved_at = resolved
    ticket.landed_sha = landed_sha
    ticket.landed_branch = landed_branch
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _receipt(
    session: Session, ticket: Ticket, status: str, *, outcome: str | None = None
) -> Artifact:
    return make_artifact(
        session,
        ticket_id=ticket.id,
        kind=ArtifactKind.LAND_VERIFICATION,
        title=f"Land verification — {status} — {ticket.landed_branch}",
        content={
            "status": status,
            "stage_key": "done",
            "outcome": outcome or status,
            "branch": ticket.landed_branch,
            "target": ticket.landed_branch,
            "candidate_sha": ticket.landed_sha,
            "command": "uv run --directory server pytest -q -n auto",
            "exit_code": 0 if status == PASSED else 1,
            "output": "42 passed",
        },
    )


def _detect(session: Session) -> list:
    return list(LAND_VERIFICATION_RAN.detect(session))


def _subjects(session: Session) -> set[str]:
    return {v.subject_id for v in _detect(session)}


# ---------------------------------------------------------------------------
# AC4. The registration is the contract `fix_coverage` and the PR check resolve
# against, so its decided fields are pinned rather than left to drift.
# ---------------------------------------------------------------------------


def test_the_invariant_is_registered_as_this_ticket_declares_it():
    assert LAND_VERIFICATION_RAN in INVARIANTS
    assert LAND_VERIFICATION_RAN.name == "land_verification_ran"
    assert LAND_VERIFICATION_RAN.ticket == "lg-milestone-that-1419"
    assert LAND_VERIFICATION_RAN.statement == (
        "every recorded landing has a passing land-verification receipt"
    )
    assert LAND_VERIFICATION_RAN.reentry is InvariantReentry.DEFECT, (
        "a writer is supposed to make this unreachable; EXPECTED would say the "
        "control plane means to land things nobody tested"
    )
    assert LAND_VERIFICATION_RAN.repair is None, (
        "only a person can run a suite and judge it; an automatic repair would be "
        "a guard marking its own homework"
    )
    assert LAND_VERIFICATION_RAN.report_only
    # AC4 names `date(2026, 10, 23)`, the day this change lands. The bound has
    # to be the day AFTER: six loregarden landings from earlier that same day
    # (00:29 to 07:28) carry no receipt and fall at or after a 2026-10-23
    # midnight bound, which is six re-entry findings and an escalation Bug on
    # the first pass where AC10 requires zero. Reproduced against a copy of
    # `data/loregarden.db`; `test_the_bound_is_the_day_after_the_writer_landed_not_the_day_of_it`
    # holds those six rows.
    assert LAND_VERIFICATION_RAN.since == date(2026, 10, 24)
    assert LAND_VERIFICATION_RAN.prevented_by == (
        "server/tests/test_land_verification_receipt.py"
        "::test_every_landing_that_records_a_landed_sha_writes_a_receipt"
    )


def test_it_is_scheduled_on_the_reconcile_pass_exactly_once():
    scheduled = [step.name for step in PERIODIC_STEPS]

    assert scheduled.count(LAND_VERIFICATION_RAN.name) == 1, scheduled


def test_the_detector_body_lives_beside_the_report_only_precedent():
    """AC4. `_landed_with_uncommitted_work` is the same shape — report-only
    DEFECT over done tickets — and it is in `invariant_registry.py`. A new
    service module for fifteen lines of database reads is a module to find."""
    assert LAND_VERIFICATION_RAN.detect.__module__ == ("loregarden.services.invariant_registry"), (
        LAND_VERIFICATION_RAN.detect.__module__
    )
    assert hasattr(invariant_registry, "_landed_with_uncommitted_work"), (
        "the precedent this sits beside has moved; check the placement argument still holds"
    )


def test_the_since_bound_is_one_constant_the_detector_and_the_registration_share(
    session, workspace, monkeypatch
):
    """AC4. Two copies of the date is two answers to "what is backlog?", and the
    one that drifts is the detector's — which is the one that decides whether 80
    old landings are reported.

    Proved by moving the constant and watching the detector follow it, not by
    comparing two literals: a detector with its own inline `date(...)` would pass
    an equality check and ignore this.
    """
    assert invariant_registry.LAND_VERIFICATION_SINCE == LAND_VERIFICATION_RAN.since
    ticket = _landed(session, workspace, "lg-since-1", days_from_since=1)
    assert _subjects(session) == {ticket.id}, "fixture: the landing must be in range first"

    monkeypatch.setattr(
        invariant_registry, "LAND_VERIFICATION_SINCE", _since() + timedelta(days=30)
    )

    assert _subjects(session) == set(), (
        "the detector did not read the shared constant; its bound is a copy"
    )


def test_the_detector_says_in_its_docstring_that_it_inverts_the_frameworks_default():
    """AC10. Erring towards silence is the opposite of `_entered_after`'s
    documented default, so a reader meets it as a bug unless the docstring says
    it was deliberate."""
    doc = (inspect.getdoc(LAND_VERIFICATION_RAN.detect) or "").lower()

    assert doc, "the detector has no docstring"
    assert "unknown" in doc or "undated" in doc, doc
    assert "since" in doc, doc


# ---------------------------------------------------------------------------
# The predicate, condition by condition.
# ---------------------------------------------------------------------------


def test_a_landing_with_no_passing_receipt_is_a_violation(session, workspace):
    ticket = _landed(session, workspace, "lg-lv-1")

    assert _subjects(session) == {ticket.id}


def test_a_landing_with_a_passing_receipt_is_not(session, workspace):
    ticket = _landed(session, workspace, "lg-lv-2")
    _receipt(session, ticket, PASSED)

    assert _subjects(session) == set()


def test_a_landing_whose_only_receipt_says_nobody_verified_it_is_a_violation(session, workspace):
    """The whole point. `disabled` is the honest report of a landing nobody
    checked, and it is exactly the state all three recurrences were in."""
    ticket = _landed(session, workspace, "lg-lv-3")
    _receipt(session, ticket, DISABLED, outcome="already_contained")

    assert _subjects(session) == {ticket.id}


def test_a_failed_receipt_without_a_landing_recorded_is_not_a_violation(session, workspace):
    """A refused land records no `landed_sha`, so there is no landing to judge —
    the refusal is already visible as a block on the ticket."""
    ticket = _landed(session, workspace, "lg-lv-4", landed_sha="", landed_branch="")
    _receipt(session, ticket, FAILED)

    assert _subjects(session) == set()


def test_a_re_land_that_wrote_a_disabled_receipt_beside_a_passing_one_is_not_a_violation(
    session, workspace
):
    """Condition 3 is "no PASSING receipt", not "no receipt". A verified landing
    re-landed later writes an `already_contained` row, and reading "the latest
    receipt" instead of "any passing receipt" would turn that into a violation."""
    ticket = _landed(session, workspace, "lg-lv-5")
    _receipt(session, ticket, PASSED)
    _receipt(session, ticket, DISABLED, outcome="already_contained")

    assert _subjects(session) == set()


def test_a_ticket_that_never_landed_is_the_outputs_tabs_empty_state(session, workspace):
    """AC10 and AC12. The receipt's absence on a ticket that has not landed is
    'No outputs yet', not a failure — and a detector that read it as one would
    report every done ticket that was closed without landing."""
    ticket = _landed(session, workspace, "lg-lv-6", landed_sha="", landed_branch="")

    assert ticket.landed_sha == ""
    assert _subjects(session) == set()


@pytest.mark.parametrize(
    "state", [TicketState.IN_PROGRESS, TicketState.BLOCKED, TicketState.BACKLOG]
)
def test_a_landing_on_a_ticket_that_is_not_done_yet_is_not_a_violation(session, workspace, state):
    """`landed_sha` is stamped before `done` is derived, so the row exists for a
    moment on an unfinished ticket. Reporting it would file a finding on every
    landing the instant it happened."""
    _landed(session, workspace, f"lg-lv-7-{state.value}", state=state)

    assert _subjects(session) == set()


def test_another_tickets_receipt_does_not_clear_this_one(session, workspace):
    """A receipt is per ticket. A detector joining on kind alone would read the
    first passing row in the table and clear every landing in the database."""
    verified = _landed(session, workspace, "lg-lv-8a")
    _receipt(session, verified, PASSED)
    unverified = _landed(session, workspace, "lg-lv-8b")

    assert _subjects(session) == {unverified.id}


def test_a_receipt_of_another_kind_does_not_clear_a_landing(session, workspace):
    """The kind is the contract, not the title: an agent may coin any kind and
    any title through `loregarden_attach_artifact`."""
    ticket = _landed(session, workspace, "lg-lv-9")
    make_artifact(
        session,
        ticket_id=ticket.id,
        kind=ArtifactKind.LOG,
        title=f"Land verification — {PASSED} — {ticket.landed_branch}",
        content={"status": PASSED, "stage_key": "done"},
    )

    assert _subjects(session) == {ticket.id}


# ---------------------------------------------------------------------------
# AC10. The bound, and why the first pass must report nothing.
# ---------------------------------------------------------------------------


def test_a_landing_from_before_the_guard_landed_is_backlog_and_not_reported(session, workspace):
    """The 80 live landings. They predate the writer, nobody can act on them, and
    `run_invariant` would re-report every one of them on every 30-second pass."""
    _landed(session, workspace, "lg-old-1", days_from_since=-1)

    check = run_invariant(session, LAND_VERIFICATION_RAN)

    assert (check.found, check.reentered, check.unrepaired) == (0, 0, 0), check
    assert _findings(session) == []


def test_a_landing_the_detector_cannot_date_is_left_alone(session, workspace):
    """AC10's deliberate inversion of `_entered_after`'s default. A landing with
    no `resolved_at` is by construction one that predates the receipt writer —
    every landing after it carries a date, because `done` stamps one."""
    _landed(session, workspace, "lg-undated-1", days_from_since=None)

    assert _detect(session) == []


def test_a_landing_on_the_first_day_in_range_is_reported(session, workspace):
    """The bound is inclusive: a landing at midnight on `since` — the day AFTER
    the receipt writer landed — is the first one the writer existed for all day.
    The day of the writer's own landing is backlog; see
    `test_the_bound_is_the_day_after_the_writer_landed_not_the_day_of_it`."""
    ticket = _landed(session, workspace, "lg-edge-1", days_from_since=0)

    assert _subjects(session) == {ticket.id}


def _done_and_landed(session: Session) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(
                col(Ticket.state) == TicketState.DONE,
                col(Ticket.landed_sha) != "",
            )
        ).all()
    )


@pytest.fixture(name="prod_shape_session")
def prod_shape_session_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
    with Session(isolated_db) as session:
        yield session


def test_the_first_pass_over_productions_shape_reports_nothing(prod_shape_session: Session):
    """AC10, and both halves are the criterion.

    The zero is what stops 80 findings and one escalation Bug reaching the
    operator on the first reconcile tick after boot. But a zero may be the
    detector, so the same test asserts the scenario has done-and-landed tickets
    to be scanned at all: it had NONE before this ticket (`grep landed_sha
    loregarden/testing/prod_shape.py` matched nothing), which would have made
    this pass vacuously.
    """
    landed = _done_and_landed(prod_shape_session)
    assert landed, (
        "the scenario has no done-and-landed tickets, so the zero below is the "
        "detector and not the bound. Add them at the calibrated proportion "
        "(80 of 474 done), dated before `since`"
    )
    assert len(landed) >= 40, (
        f"{len(landed)} done-and-landed tickets; live is 80 of 474 done, and a "
        "handful would not exercise the volume the bound exists for"
    )
    assert all(t.resolved_at is not None for t in landed), (
        "an undated landing is not what live looks like; the bound must be "
        "exercised by dates, not by nulls"
    )

    check = run_invariant(prod_shape_session, LAND_VERIFICATION_RAN)

    assert (check.found, check.reentered, check.unrepaired) == (0, 0, 0), (
        f"{check} — the backlog is being reported; a report-only invariant "
        "re-reports it on every 30-second pass"
    )
    assert _findings(prod_shape_session) == []
    assert _escalations(prod_shape_session) == []


def test_one_landing_after_the_guard_is_found_in_the_same_scenario(prod_shape_session: Session):
    """The control for the test above: the same 1,844-row scenario, one landing
    dated after `since`, and the detector must find exactly it. Without this the
    zero above is indistinguishable from a detector that selects nothing."""
    workspace = prod_shape_session.exec(
        select(Workspace).where(Workspace.slug == "loregarden")
    ).one()
    fresh = _landed(prod_shape_session, workspace, "lg-fresh-1", days_from_since=2)

    check = run_invariant(prod_shape_session, LAND_VERIFICATION_RAN)

    assert [v.subject_id for v in _detect(prod_shape_session)] == [fresh.id]
    assert (check.found, check.reentered) == (1, 1), check
    assert [f.ticket_id for f in _findings(prod_shape_session)] == [fresh.id]


def test_the_detector_runs_no_suite_and_shells_out_to_nothing(prod_shape_session, monkeypatch):
    """The settled constraint, as a mechanical check rather than a comment.

    30-second reconcile tick, 901s suite, and `test_invariants.py` runs every
    detector inside pytest. Both seams are blocked, so a detector that grew a
    `git` call or a `subprocess.run` fails here instead of hanging the sweep.
    """

    def reached_outside(*_args, **_kwargs):
        raise AssertionError("the detector reached outside the database")

    monkeypatch.setattr(subprocess.Popen, "__init__", reached_outside)
    monkeypatch.setattr(git_subprocess, "run_git", reached_outside)

    assert _detect(prod_shape_session) == []


# ---------------------------------------------------------------------------
# AC11. Each finding names its own ticket and its own next action.
# ---------------------------------------------------------------------------


def _findings(session: Session) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                col(Artifact.kind) == MonitorArtifactKind.FINDING.value,
                col(Artifact.title).contains(LAND_VERIFICATION_RAN.name),
            )
        ).all()
    )


def _escalations(session: Session) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(col(Ticket.title).contains(LAND_VERIFICATION_RAN.name))
        ).all()
    )


def test_the_violation_names_the_ticket_the_branch_and_what_was_not_found(session, workspace):
    """AC11. `_summary` is identical across every violation of one invariant by
    construction, so the distinguishing fact has to be in `detail` — the
    escalation Bug's body is one `- <external_id> (<uuid>): <summary>` line per
    ticket, and `findUsabilityProblems` flags walls of repeated text."""
    ticket = _landed(session, workspace, "lg-detail-1", landed_branch="integration/lg-ms-7")

    violation = _detect(session)[0]

    assert violation.subject_id == ticket.id
    assert violation.ticket_id == ticket.id, "a finding with no ticket is a log line nobody reads"
    assert ticket.external_id in violation.detail, violation.detail
    assert ticket.landed_branch in violation.detail, violation.detail
    assert ticket.landed_sha[:12] in violation.detail, violation.detail
    assert ticket.landed_sha not in violation.detail, (
        "the short sha is what a person pastes; the full forty are noise in a one-line body"
    )
    assert "land_verification" in violation.detail, (
        f"what was looked for and not found: {violation.detail!r}"
    )
    assert LAND_VERIFICATION_RAN.statement not in violation.detail, (
        "the detail restates the invariant statement, which every finding already carries"
    )


def test_the_entry_time_is_the_landing_the_detector_is_judging(session, workspace):
    """AC11. `Ticket` carries no landing timestamp, so `resolved_at` is the best
    available date — the same choice `_landed_with_uncommitted_work` makes, and
    the `Violation` contract asks a detector whose date is not the row's own to
    say so."""
    ticket = _landed(session, workspace, "lg-entered-1", days_from_since=3)

    violation = _detect(session)[0]

    assert violation.entered_at is not None
    assert violation.entered_at.replace(tzinfo=timezone.utc) == ticket.resolved_at.replace(
        tzinfo=timezone.utc
    )


def test_two_violations_do_not_read_as_one_repeated_line(session, workspace):
    """AC11's reason, measured rather than asserted about one row: the 138-finding
    Monitor wall was 94 findings of the same sentence with no ticket named."""
    first = _landed(session, workspace, "lg-wall-1", landed_branch="integration/lg-ms-1")
    second = _landed(session, workspace, "lg-wall-2", landed_branch="integration/lg-ms-2")

    details = {v.detail for v in _detect(session)}

    assert len(details) == 2, details
    assert first.external_id in " ".join(details)
    assert second.external_id in " ".join(details)


def test_the_action_is_one_a_person_can_execute_from_the_repository_root():
    """AC11. Report-only means the finding reappears on every 30-second pass
    until a person acts, so the action has to be the command and the decision,
    not 'investigate'.

    The command's form is the same constraint as the armed one: `shlex.split`
    with no shell, from the repository root.
    """
    action = LAND_VERIFICATION_RAN.unrepaired_action

    assert "uv run --directory server pytest" in action, action
    assert "cd server" not in action and "bash -c" not in action, (
        f"that form cannot run where the finding tells a person to run it: {action!r}"
    )
    assert "reopen" in action.lower(), f"what to do when it is red: {action!r}"
    assert "record" in action.lower(), f"what to do when it is green: {action!r}"
    assert "investigate" not in action.lower(), "'investigate' is not an action"


def test_the_finding_a_person_reads_names_the_ticket_and_the_falsified_guard(session, workspace):
    """Where the operator actually meets the finding.

    Every violation this detector returns is post-`since` by construction — the
    bound drops the rest — so the condition is always `invariant_reentered` and
    `_summary` carries the `prevented_by` claim rather than
    `unrepaired_action`. The distinguishing fact therefore has to be in the
    evidence, which is what AC11 asks `Violation.detail` for.
    """
    ticket = _landed(session, workspace, "lg-finding-1")

    run_invariant(session, LAND_VERIFICATION_RAN)

    findings = _findings(session)
    assert [f.ticket_id for f in findings] == [ticket.id]
    payload = json.loads(findings[0].content_json or "{}")
    assert payload["stage_key"] == LAND_VERIFICATION_RAN.name
    assert LAND_VERIFICATION_RAN.prevented_by in payload["summary"], payload["summary"]
    assert payload["evidence"]["invariant"] == LAND_VERIFICATION_RAN.name
    assert ticket.external_id in payload["evidence"]["detail"], payload["evidence"]


def test_every_violation_is_a_reentry_because_the_bound_drops_the_backlog(session, workspace):
    """The consequence of AC10's bound, stated so a reader of `InvariantCheck`
    is not surprised by a permanent `unrepaired=0`: a landing old enough to be
    backlog is not returned at all."""
    _landed(session, workspace, "lg-reent-split-1", days_from_since=1)
    _landed(session, workspace, "lg-reent-split-2", days_from_since=-5)

    check = run_invariant(session, LAND_VERIFICATION_RAN)

    assert (check.found, check.reentered, check.unrepaired) == (1, 1, 0), check


def test_a_landing_after_the_guard_is_a_reentry_and_not_quiet_backlog(session, workspace):
    """DEFECT, so one violation after `since` falsifies the guard and escalates
    at the first ticket rather than waiting for a second."""
    ticket = _landed(session, workspace, "lg-reentry-1", days_from_since=5)

    check = run_invariant(session, LAND_VERIFICATION_RAN)

    assert (check.found, check.reentered, check.repaired) == (1, 1, 0)
    bugs = _escalations(session)
    assert len(bugs) == 1, [b.title for b in bugs]
    assert ticket.external_id in bugs[0].description


def test_nothing_is_repaired_behind_a_persons_back(session, workspace):
    """Report-only: a repair here would be the control plane deciding a suite
    passed. The row stays, and so does the finding, until a person runs one."""
    ticket = _landed(session, workspace, "lg-norepair-1")

    run_invariant(session, LAND_VERIFICATION_RAN)
    session.refresh(ticket)

    assert ticket.state is TicketState.DONE
    assert ticket.landed_sha != ""
    assert _subjects(session) == {ticket.id}, "the second pass must still see it"


# ---------------------------------------------------------------------------
# test-break. The receipt is operator-visible JSON, so the detector reads a
# field anybody can write. A detector that raises on one row stops reporting
# every other one, on a 30-second timer, with nothing in the UI to say so.
# ---------------------------------------------------------------------------

#: Receipt bodies that are not "a passing verdict", written raw rather than
#: through `make_artifact` because `make_artifact` serialises a dict and these
#: are the shapes a dict cannot express. Every one of them must read as "no
#: passing receipt" — the landing stays a violation — and none of them may take
#: the detector down.
#:
#: The casing and whitespace entries are deliberate: `status` is one of three
#: exact words (AC9) and the client's `PASSING` set matches them exactly, so a
#: `.strip().lower()` in the detector would be defensive normalization of a
#: value this ticket's own writer controls — and would clear a landing whose
#: receipt the client itself colours `warn`.
UNREADABLE_RECEIPTS = (
    ("no body at all", None),
    ("an empty body", ""),
    ("a truncated object", '{"status": "passed"'),
    ("a JSON array", '[{"status": "passed"}]'),
    ("a bare JSON string", '"passed"'),
    ("a JSON number", "7"),
    ("a null status", '{"status": null, "stage_key": "done"}'),
    ("a status of the wrong type", '{"status": ["passed"], "stage_key": "done"}'),
    ("the verdict in the wrong case", '{"status": "PASSED", "stage_key": "done"}'),
    ("the verdict padded with whitespace", '{"status": " passed ", "stage_key": "done"}'),
    ("the verdict under the wrong key", '{"outcome": "passed", "stage_key": "done"}'),
)


@pytest.mark.parametrize(
    ("label", "body"), UNREADABLE_RECEIPTS, ids=[c[0] for c in UNREADABLE_RECEIPTS]
)
def test_a_receipt_whose_body_cannot_be_read_does_not_clear_a_landing(
    session, workspace, label, body
):
    """Neither a pass nor a crash: the landing is still unverified.

    `content_json` is a text column, and every `loregarden_attach_artifact`
    caller and every hand-repair can put anything in it. The detector's third
    condition is "no LAND_VERIFICATION artifact whose `content_json.status` is
    `passed`", which is a `json.loads` and a subscript over operator-supplied
    text — so the two ways to get this wrong are to let a malformed row raise
    inside the reconcile tick, and to let a nearly-right one count as a pass.

    A raise is the worse of the two. `run_invariant` is one step of
    `PERIODIC_STEPS`; a detector that throws reports nothing for ANY ticket from
    then on, and the surface says "no violations" — a guard failing that reads
    exactly like a guard working, which is the shape this whole ticket exists
    because of.
    """
    ticket = _landed(session, workspace, f"lg-lv-unreadable-{abs(hash(label)) % 10_000}")
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.LAND_VERIFICATION,
            title=f"Land verification — {PASSED} — {ticket.landed_branch}",
            content_json=body,
        )
    )
    session.commit()

    violations = _detect(session)

    assert {v.subject_id for v in violations} == {ticket.id}, (
        f"{label}: the landing is unverified and must still be reported — a body "
        "that cannot be read is not a passing verdict"
    )


def test_one_unreadable_receipt_does_not_hide_every_other_landing(session, workspace):
    """The blast radius, which the parametrized case above cannot show.

    A single malformed row raising inside the detector takes the whole pass with
    it, so the two landings that ARE unverified stop being reported and the
    Monitor surface goes quiet. The quiet is the failure.
    """
    poisoned = _landed(session, workspace, "lg-lv-poison")
    session.add(
        Artifact(
            ticket_id=poisoned.id,
            kind=ArtifactKind.LAND_VERIFICATION,
            title="Land verification — passed — integration/lg-ms-1",
            content_json="{not json at all",
        )
    )
    session.commit()
    unverified = _landed(session, workspace, "lg-lv-after-poison")
    verified = _landed(session, workspace, "lg-lv-also-verified")
    _receipt(session, verified, PASSED)

    check = run_invariant(session, LAND_VERIFICATION_RAN)

    assert _subjects(session) == {poisoned.id, unverified.id}, (
        "one unreadable row silenced the rest of the pass"
    )
    assert check.found == 2, check


# ---------------------------------------------------------------------------
# AC10's bound, measured against the landings the writer's own day carries.
# ---------------------------------------------------------------------------


#: The six loregarden landings on 2026-10-23, the day the receipt writer landed,
#: by the hour they resolved — read from a copy of the live database. Every one
#: of them predates the writer by hours and carries no receipt, so a bound of
#: the writer's own day reports all six on the first pass where AC10 requires
#: zero.
#: The day this change — the receipt writer — lands.
WRITER_LANDED = date(2026, 10, 23)

LANDINGS_ON_THE_WRITERS_OWN_DAY = (
    ("lg-milestone-that-1314", 0, 29),
    ("lg-milestone-that-1315", 1, 47),
    ("lg-milestone-that-1319", 3, 2),
    ("lg-milestone-that-1324", 5, 11),
    ("lg-milestone-that-1417", 6, 40),
    ("lg-milestone-that-1418", 7, 28),
)


def test_the_bound_is_the_day_after_the_writer_landed_not_the_day_of_it(session, workspace):
    """AC10, reproduced from the live rows rather than reasoned about.

    `invariants._since` reads `Invariant.since` as that day's midnight, so a
    bound of the writer's landing day puts every landing from earlier the SAME
    day at or after it — hours before any receipt could be written. The first
    whole day on which every landing can carry one is the day after, and these
    six rows are why: measured against a copy of `data/loregarden.db`, a
    2026-10-23 bound reported all six, each a DEFECT re-entry re-reported on
    every 30-second pass plus one auto-filed escalation Bug.

    The dates here are ABSOLUTE and not `since`-relative on purpose. A
    `since`-relative fixture moves with the constant, so it says the bound is
    inclusive and cannot say which day the bound is — it passes against the very
    off-by-one-day it exists to catch.
    """
    for external_id, hour, minute in LANDINGS_ON_THE_WRITERS_OWN_DAY:
        landing = _landed(session, workspace, external_id, days_from_since=None)
        landing.resolved_at = datetime(
            WRITER_LANDED.year,
            WRITER_LANDED.month,
            WRITER_LANDED.day,
            hour,
            minute,
            tzinfo=timezone.utc,
        )
        session.add(landing)
    session.commit()

    check = run_invariant(session, LAND_VERIFICATION_RAN)

    assert _detect(session) == [], (
        f"the bound is {_since().isoformat()}, the writer's own landing day, so "
        "six landings from the hours before the writer existed are reported as "
        "re-entry; it has to be the day after"
    )
    assert (check.found, check.reentered, check.unrepaired) == (0, 0, 0), check
    assert _findings(session) == [] and _escalations(session) == []


# ---------------------------------------------------------------------------
# AC4 and AC7. The arming is workspace-scoped, so the detector is too.
# ---------------------------------------------------------------------------


def _unarmed_workspace(session: Session, slug: str = "blobert") -> Workspace:
    """A workspace whose profile leaves `land_verify_commands` empty.

    `blobert` is the measured case: it has an orchestration profile, that profile
    sets no `land_verify_commands`, and it had 8 done-and-landed tickets on the
    live database the day this landed. `default.yaml` keeps the empty list too,
    per AC7, so a workspace with no profile at all reads the same.
    """
    workspace = make_workspace(session, slug=slug)
    assert not resolve_git_automation(workspace).land_verify_commands, (
        f"fixture: {slug} resolves land_verify_commands, so it cannot stand in "
        "for a workspace in the documented off state"
    )
    return workspace


def test_the_armed_workspace_is_armed_in_the_profile_the_detector_reads(workspace):
    """The control for every test below: loregarden's own landings ARE judged
    one by one, because AC7 arms its profile. Without this the workspace
    predicate could be silencing everything."""
    assert resolve_git_automation(workspace).land_verify_commands == [
        "uv run --directory server pytest -q -n auto"
    ]


def test_a_landing_in_a_workspace_that_verifies_nothing_is_not_a_per_landing_violation(
    session,
):
    """AC4's grounds, and AC7's "no other workspace changes behaviour".

    `land_verifier` returns `GateOutcome.DISABLED` when `land_verify_commands`
    is empty and `record_land_verification` writes that verdict as `disabled`,
    never `passed` — so a per-landing report here is a violation no writer can
    ever clear, on a workspace sitting in its documented off state. `DEFECT`
    re-entry means "a writer is supposed to make this unreachable"; in this
    workspace none is.
    """
    unarmed = _unarmed_workspace(session)
    landings = [
        _landed(session, unarmed, "blob-off-1"),
        _landed(session, unarmed, "blob-off-2"),
        _landed(session, unarmed, "blob-off-3"),
    ]

    violations = _detect(session)

    assert [v.subject_id for v in violations] == [unarmed.id], (
        "three landings in an off workspace are three permanent findings and one "
        "escalation Bug; they are one fact about the workspace"
    )
    assert violations[0].ticket_id in {t.id for t in landings}, (
        "a finding needs a ticket to hang on: `artifacts.ticket_id` is NOT NULL "
        "and `invariants._report` only logs a ticketless violation"
    )


def test_the_off_workspace_is_reported_once_so_disarming_cannot_silence_the_detector(session):
    """The blindness the plan stage warned the substitute detector had.

    "A workspace whose `land_verify_commands` is empty" was rejected as the only
    detector because it goes blind the moment R5 arms it. The inverse is the risk
    here: scoping the per-landing report to armed workspaces would let anyone
    empty the list and make the invariant say nothing. So the off state is still
    a violation — one, workspace-scoped, naming the workspace and its count.
    """
    unarmed = _unarmed_workspace(session)
    for index in range(4):
        _landed(session, unarmed, f"blob-silent-{index}")

    check = run_invariant(session, LAND_VERIFICATION_RAN)
    violation = _detect(session)[0]

    assert (check.found, check.reentered) == (1, 1), check
    assert len(_findings(session)) == 1, "one workspace, one finding"
    assert len(_escalations(session)) == 1, "one workspace, one escalation Bug"
    assert unarmed.slug in violation.detail, violation.detail
    assert "land_verify_commands" in violation.detail, violation.detail
    assert "4 landing" in violation.detail, (
        f"the count is the size of what is unverified: {violation.detail!r}"
    )


def test_an_armed_workspaces_landings_are_judged_beside_the_off_workspaces_one_finding(
    session, workspace
):
    """Both at once, because the live database has both: loregarden armed, and
    blobert, lore-eden, loremaker and real929 on the empty default."""
    unarmed = _unarmed_workspace(session)
    for index in range(3):
        _landed(session, unarmed, f"blob-mixed-{index}")
    reported = _landed(session, workspace, "lg-mixed-1")
    verified = _landed(session, workspace, "lg-mixed-2")
    _receipt(session, verified, PASSED)

    assert _subjects(session) == {unarmed.id, reported.id}
    assert verified.id not in _subjects(session)


def test_a_disabled_receipt_in_an_armed_workspace_is_still_an_unverified_landing(
    session, workspace
):
    """The exact row `land_ticket` writes when verification is off, dated after
    `since`, in a workspace that IS armed. The landing is unverified and a person
    has to judge it — this is the one state the workspace predicate must not
    swallow along with the off workspace's.
    """
    ticket = _landed(session, workspace, "lg-armed-disabled-1", days_from_since=2)
    _receipt(session, ticket, DISABLED, outcome="disabled")

    assert _subjects(session) == {ticket.id}
