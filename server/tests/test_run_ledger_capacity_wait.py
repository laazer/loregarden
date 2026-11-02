"""How much of a ticket's wall-clock was queueing rather than working.

Acceptance criterion (lg-machine-resource-1923) R9.

`run_ledger` read `agent_runs` and nothing else, so no surface on a ticket could
show a capacity wait — which is half of the 1912 finding (8 minutes of agent
work, 29 minutes in the queue) and the half nobody could see. The figure comes
from the HOST `docker_leases` rows that already carry the ticket's id, its
`requested_at` and its `granted_at`. No new table, no new artifact kind, and the
same `GET /tickets/{id}/ledger` payload.

Three decisions are pinned here, and each of them is a way the obvious
implementation lies:

**Absent is not zero.** `capacity_wait` is `null` with the ledger switched off,
and `null` for a ticket older than the ledger. `0.0` would say "this ticket
never waited", which is a measurement nobody made.

**Attribution is per visit, not per lease.** One live ticket holds 30 gate
leases (lg-external-harness-1026, 2867s of wait). Per lease that is 30 lines on
a Timeline that shows one row per stage visit. A lease belongs to the earliest
visit whose first attempt's `created_at` is at or after that lease's
`granted_at`; a lease matching no visit counts in the total and in no visit.

**A five-second floor.** One of 1912's five gate leases was granted in 0.1s. An
unconditional per-visit row would add lines that say nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    DockerFootprint,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    RunStatus,
)
from loregarden.services.run_ledger import capacity_waits, ledger_payload
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace

T0 = datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc)
TICKET_ID = "t-capacity-wait"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ticket")
def ticket_fixture(session):
    workspace = make_workspace(session, slug="ledger-capacity-wait")
    return make_ticket(
        session,
        workspace_id=workspace.id,
        ticket_id=TICKET_ID,
        external_id="lg-reference-sources-1912",
    )


@pytest.fixture(name="enabled", autouse=True)
def enabled_fixture(monkeypatch):
    monkeypatch.setattr("loregarden.config.settings.docker_capacity_enabled", True)


def _run(session, ticket, stage_key: str, *, created_at: datetime, seconds: int = 60) -> AgentRun:
    run = AgentRun(
        run_code=f"run_{stage_key}_{created_at.minute}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=RunStatus.SUCCEEDED,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at + timedelta(seconds=seconds),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _lease(
    session,
    ticket,
    *,
    requested_at: datetime,
    waited_seconds: float,
    holder_kind: DockerHolderKind = DockerHolderKind.ORCHESTRATION,
    parent_lease_id: str | None = None,
    end_reason: DockerLeaseEndReason = DockerLeaseEndReason.RELEASED,
    label: str = "run orch_a · proj · lg-reference-sources-1912",
) -> DockerLease:
    """A finished HOST lease row, as the ledger leaves one behind.

    Written directly rather than through `reserve`: the point under test is the
    arithmetic over `requested_at`/`granted_at`, and a real reservation cannot
    be made to have waited 384 seconds without actually waiting them.
    """
    lease = DockerLease(
        status=DockerLeaseStatus.RELEASED,
        holder_kind=holder_kind,
        holder_label=label,
        parent_lease_id=parent_lease_id,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        pool=CapacityPool.HOST,
        takes_slot=False,
        footprint=DockerFootprint.LIGHT,
        cpus=0.5,
        memory_mb=512,
        position=0,
        ttl_seconds=900,
        requested_at=requested_at,
        granted_at=requested_at + timedelta(seconds=waited_seconds),
        end_reason=end_reason,
    )
    session.add(lease)
    session.commit()
    session.refresh(lease)
    return lease


def _payload(session, ticket) -> dict:
    runs = list(session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).all())
    return ledger_payload(runs, capacity_wait=capacity_waits(session, ticket.id, runs))


def test_absent_is_not_zero_when_capacity_is_disabled(session, ticket, monkeypatch) -> None:
    """R9. `null`, not `0.0`.

    A zero here is a claim that the ticket queued for no time, which is what an
    operator would read it as. With the ledger off nothing was measured, and the
    Timeline's job is then to omit the clause rather than to state a wait of
    nothing.
    """
    _run(session, ticket, "plan", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=384), waited_seconds=384)
    monkeypatch.setattr("loregarden.config.settings.docker_capacity_enabled", False)

    payload = _payload(session, ticket)

    assert payload["capacity_wait"] is None
    assert payload["visits"][0]["capacity_wait_seconds"] is None
    assert payload["visits"][0]["capacity_wait_after_park"] is False


def test_a_ticket_with_no_lease_rows_reports_null_not_zero(session, ticket) -> None:
    """R9. A ticket older than the ledger has no rows, which is not a wait of zero."""
    _run(session, ticket, "plan", created_at=T0)

    payload = _payload(session, ticket)

    assert payload["capacity_wait"] is None
    assert payload["visits"][0]["capacity_wait_seconds"] is None


def test_a_wait_is_attributed_to_the_visit_it_preceded(session, ticket) -> None:
    """R9. 1912's two waits, on the two visits they held up.

    6m24s before `plan` and 22m40s before `plan-synthesis`, which is the finding
    this ticket was written from. The totals are the sum; each visit carries
    only the lease granted before its first attempt started.
    """
    plan_at = T0
    synthesis_at = T0 + timedelta(minutes=20)
    _run(session, ticket, "plan", created_at=plan_at)
    _run(session, ticket, "plan-synthesis", created_at=synthesis_at)
    _lease(session, ticket, requested_at=plan_at - timedelta(seconds=384), waited_seconds=384)
    _lease(
        session, ticket, requested_at=synthesis_at - timedelta(seconds=1360), waited_seconds=1360
    )

    payload = _payload(session, ticket)

    assert payload["capacity_wait"] == {
        "total_seconds": 1744.0,
        "lease_count": 2,
        "reacquired_seconds": 0.0,
    }
    assert [visit["capacity_wait_seconds"] for visit in payload["visits"]] == [384.0, 1360.0]
    assert [visit["stage_key"] for visit in payload["visits"]] == ["plan", "plan-synthesis"]


def test_a_wait_under_five_seconds_is_not_attributed_to_a_visit(session, ticket) -> None:
    """R9. The measured floor.

    1912's `spec_to_test-design` gate was granted in 0.1s. A row saying "waited
    0s for machine capacity" under a stage name is a line that costs a reader
    attention and tells them nothing. It still counts in the total, because the
    total is the ticket's real queue time.
    """
    _run(session, ticket, "spec", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=1), waited_seconds=0.1)

    payload = _payload(session, ticket)

    assert payload["visits"][0]["capacity_wait_seconds"] is None, (
        "a sub-5s wait is below the floor; absent, not a zero row"
    )
    assert payload["capacity_wait"]["total_seconds"] == 0.1
    assert payload["capacity_wait"]["lease_count"] == 1


def test_a_lease_granted_after_every_visit_counts_in_the_total_and_in_no_visit(
    session, ticket
) -> None:
    """R9. The transition after the last stage visit has no visit to belong to.

    The spec stage flagged the neighbouring edge as the one a test should pin
    rather than inherit: a lease granted *during* a visit rather than before it
    — a gate firing mid-visit on a retry — is attributed to the NEXT visit, and
    where there is no next visit it is counted only in the total. Dropping it
    instead would make the ticket's total disagree with the sum of its rows.
    """
    _run(session, ticket, "plan", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=100), waited_seconds=60)
    _lease(session, ticket, requested_at=T0 + timedelta(minutes=5), waited_seconds=30)

    payload = _payload(session, ticket)

    assert payload["capacity_wait"]["total_seconds"] == 90.0
    assert payload["capacity_wait"]["lease_count"] == 2
    assert payload["visits"][0]["capacity_wait_seconds"] == 60.0


def test_a_re_acquire_after_a_park_is_flagged(session, ticket) -> None:
    """R9/R5. The second trip to the line, told apart from the first.

    A parent whose predecessor ended `PARK_RELEASED` is a re-acquire: a person
    owed this ticket something, the capacity was given back, and the run paid
    the line again on resolve. Without the distinct end reason the surface
    cannot tell this from a first acquire, and R12's "re-acquired after 6m24s"
    would have nothing to read.
    """
    first_at = T0
    resumed_at = T0 + timedelta(hours=9)
    _run(session, ticket, "spec", created_at=first_at)
    _run(session, ticket, "test-design", created_at=resumed_at)
    _lease(
        session,
        ticket,
        requested_at=first_at - timedelta(seconds=10),
        waited_seconds=10,
        end_reason=DockerLeaseEndReason.PARK_RELEASED,
    )
    _lease(session, ticket, requested_at=resumed_at - timedelta(seconds=384), waited_seconds=384)

    payload = _payload(session, ticket)

    first, second = payload["visits"]
    assert first["capacity_wait_after_park"] is False, "nothing preceded the first acquire"
    assert first["capacity_wait_seconds"] == 10.0
    assert second["capacity_wait_after_park"] is True
    assert second["capacity_wait_seconds"] == 384.0
    assert payload["capacity_wait"]["reacquired_seconds"] == 384.0, (
        "the re-acquire is reported on its own, because it is the cost of the park"
    )


def test_a_gate_childs_wait_is_not_double_counted_under_its_parent(session, ticket) -> None:
    """R9. A child never waits, so it contributes nothing — and must not be
    counted as if it did.

    Every gate leaves a child row with `granted_at == requested_at`. Summing
    every HOST row for the ticket without noticing would inflate `lease_count`
    by one per transition and make the figure read as many small waits rather
    than one.
    """
    _run(session, ticket, "plan", created_at=T0)
    parent = _lease(session, ticket, requested_at=T0 - timedelta(seconds=384), waited_seconds=384)
    _lease(
        session,
        ticket,
        requested_at=T0,
        waited_seconds=0,
        holder_kind=DockerHolderKind.AD_HOC,
        parent_lease_id=parent.id,
        label="gates triage_to_plan · proj · lg-reference-sources-1912",
    )

    payload = _payload(session, ticket)

    assert payload["capacity_wait"]["total_seconds"] == 384.0
    assert payload["capacity_wait"]["lease_count"] == 1, (
        "a lease that never queued is not a trip to the line"
    )
    assert payload["visits"][0]["capacity_wait_seconds"] == 384.0


def test_the_endpoint_serves_the_capacity_wait_in_the_same_payload(client, db_session) -> None:
    """R9. The same `GET /tickets/{id}/ledger`, so the Timeline reads the wait
    in the response it already makes. A second request would be a second
    failure mode, and the Timeline's error state is written for one."""
    from loregarden.models.domain import Ticket

    ticket = db_session.exec(select(Ticket)).first()
    _run(db_session, ticket, "plan", created_at=T0)
    _lease(db_session, ticket, requested_at=T0 - timedelta(seconds=384), waited_seconds=384)

    response = client.get(f"/api/tickets/{ticket.id}/ledger")

    assert response.status_code == 200
    body = response.json()
    assert body["capacity_wait"]["total_seconds"] == 384.0
    assert all("capacity_wait_seconds" in visit for visit in body["visits"])


# ---- lg-machine-resource-1923 R9, adversarially: the arithmetic's corners ----
#
# The cases above pin the rule. These pin the boundaries of the rule, where a
# plausible implementation disagrees with it while passing every case above:
# the floor's exact value, "at or after" at the instant itself, a lease that was
# never granted, a clock that went backwards, rows that belong to something
# else, and whether the figure is the same twice.


def test_a_wait_of_exactly_five_seconds_is_attributed_to_its_visit(session, ticket) -> None:
    """R9. The floor is "under 5 seconds", so 5.0 is above it.

    `< 5` and `<= 5` both pass `test_a_wait_under_five_seconds_is_not_attributed_to_a_visit`
    at 0.1s. They disagree here, and the number in the criterion is the one an
    operator can argue with — so it is the one the test has to hold to.
    """
    _run(session, ticket, "spec", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=10), waited_seconds=5.0)

    payload = _payload(session, ticket)

    assert payload["visits"][0]["capacity_wait_seconds"] == 5.0
    assert payload["capacity_wait"]["total_seconds"] == 5.0


def test_a_lease_granted_at_the_instant_a_visit_began_belongs_to_that_visit(
    session, ticket
) -> None:
    """R9. "At or after that lease's `granted_at`" — at the instant itself.

    The gate for a transition is granted and then the next stage's run is
    created, and on a machine that grants instantly those two stamps can be the
    same. A strict `>` drops the lease to the following visit, or off the end of
    the ledger entirely when it is the last one, and the per-visit figures then
    disagree with the stage they belong to by one row.
    """
    plan_at = T0
    spec_at = T0 + timedelta(minutes=10)
    _run(session, ticket, "plan", created_at=plan_at)
    _run(session, ticket, "spec", created_at=spec_at)
    _lease(session, ticket, requested_at=plan_at - timedelta(seconds=60), waited_seconds=60)

    payload = _payload(session, ticket)

    assert payload["visits"][0]["capacity_wait_seconds"] == 60.0, (
        "granted_at == the visit's first attempt created_at is 'at or after'"
    )
    assert payload["visits"][1]["capacity_wait_seconds"] is None


def test_a_visits_two_waits_are_summed_and_the_floor_applies_to_the_sum(session, ticket) -> None:
    """R9. "The sum of its attributed leases' waits", and the floor is on the sum.

    A retried visit can be preceded by two grants. Two 3-second waits are 6
    seconds of queueing before one stage, which is above the floor — an
    implementation that applies the 5s test per lease shows nothing and reports
    a stage that waited 6 seconds as a stage that waited none.
    """
    _run(session, ticket, "implement", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=120), waited_seconds=3.0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=60), waited_seconds=3.0)

    payload = _payload(session, ticket)

    assert payload["visits"][0]["capacity_wait_seconds"] == 6.0
    assert payload["capacity_wait"] == {
        "total_seconds": 6.0,
        "lease_count": 2,
        "reacquired_seconds": 0.0,
    }


def test_a_lease_that_was_never_granted_is_not_counted_as_a_wait_of_zero(session, ticket) -> None:
    """R9. `granted_at` is NULL for a claim that never got capacity.

    Both reachable rows are here: a gate that gave up inside its budget, which
    `acquire_gate_capacity` releases as ABANDONED without ever being granted,
    and a claim still in the line right now. Neither has a `granted_at`, so the
    obvious subtraction raises `TypeError` inside a ledger read — which is a
    500 on the Timeline, not a missing figure — and the obvious guard of
    treating it as zero reports "this ticket did not wait" about the one row
    that is waiting as the payload is built.
    """
    _run(session, ticket, "plan", created_at=T0)
    granted = _lease(session, ticket, requested_at=T0 - timedelta(seconds=60), waited_seconds=60)
    abandoned = _lease(session, ticket, requested_at=T0 - timedelta(hours=1), waited_seconds=0)
    abandoned.granted_at = None
    abandoned.end_reason = DockerLeaseEndReason.ABANDONED
    waiting = _lease(session, ticket, requested_at=T0 + timedelta(minutes=1), waited_seconds=0)
    waiting.granted_at = None
    waiting.status = DockerLeaseStatus.WAITING
    waiting.end_reason = None
    session.add_all([abandoned, waiting])
    session.commit()

    payload = _payload(session, ticket)

    assert payload["capacity_wait"] == {
        "total_seconds": 60.0,
        "lease_count": 1,
        "reacquired_seconds": 0.0,
    }, "an ungranted claim is not a trip through the line that cost nothing"
    assert payload["visits"][0]["capacity_wait_seconds"] == 60.0
    assert granted.granted_at is not None


def test_a_clock_that_went_backwards_never_reports_a_negative_wait(session, ticket) -> None:
    """R9. `granted_at` before `requested_at`.

    Both stamps are written by the process that holds the lease, and this
    machine's leases are written by several processes; a corrected clock, or a
    row restored from a snapshot, is enough. A negative wait would subtract
    from the ticket's total and could make a ticket that queued for 20 minutes
    read as having queued for 12 — a figure that is wrong in the direction
    nobody checks, on the surface this ticket adds to make queueing visible.
    """
    _run(session, ticket, "plan", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=60), waited_seconds=60)
    skewed = _lease(session, ticket, requested_at=T0 - timedelta(seconds=30), waited_seconds=0)
    skewed.granted_at = skewed.requested_at - timedelta(seconds=90)
    session.add(skewed)
    session.commit()

    payload = _payload(session, ticket)

    assert payload["capacity_wait"]["total_seconds"] >= 60.0, (
        "a backwards clock was subtracted from the ticket's queue time"
    )
    assert payload["visits"][0]["capacity_wait_seconds"] >= 60.0


def test_another_tickets_leases_and_the_docker_pool_are_not_this_tickets_wait(
    session, ticket
) -> None:
    """R9. "The HOST `docker_leases` rows already carrying this ticket's id".

    Two rows that are not this ticket's queue time and look exactly like it:
    the lease of the ticket that was ahead of it in the line, and this ticket's
    own DOCKER-pool claim, which is charged to the host pool but is a second
    ledger whose wait is a different event. Counting either turns the Timeline
    figure into something no operator can reconcile against the capacity board.
    """
    other = make_ticket(
        session, workspace_id=ticket.workspace_id, external_id="lg-machine-resource-1925"
    )
    _run(session, ticket, "plan", created_at=T0)
    _lease(session, ticket, requested_at=T0 - timedelta(seconds=60), waited_seconds=60)
    _lease(session, other, requested_at=T0 - timedelta(seconds=900), waited_seconds=900)
    docker_row = _lease(
        session, ticket, requested_at=T0 - timedelta(seconds=300), waited_seconds=300
    )
    docker_row.pool = CapacityPool.DOCKER
    session.add(docker_row)
    session.commit()

    payload = _payload(session, ticket)

    assert payload["capacity_wait"] == {
        "total_seconds": 60.0,
        "lease_count": 1,
        "reacquired_seconds": 0.0,
    }


def test_a_child_is_excluded_because_it_is_a_child_not_because_it_waited_nothing(
    session, ticket
) -> None:
    """R9. The exclusion has to be structural.

    `test_a_gate_childs_wait_is_not_double_counted_under_its_parent` is also
    passed by an implementation that filters on `granted_at > requested_at` and
    never looks at `parent_lease_id` — the child it uses happens to have waited
    zero. This one hands a child a non-zero wait it could never earn, so only
    an implementation that excludes children *as children* survives.

    It matters because the two filters disagree on real rows the other way too:
    a top-level parent granted the instant it asked is a trip to the line, and
    a child is not one however its stamps read.
    """
    _run(session, ticket, "plan", created_at=T0)
    parent = _lease(session, ticket, requested_at=T0 - timedelta(seconds=384), waited_seconds=384)
    impossible = _lease(
        session,
        ticket,
        requested_at=T0 - timedelta(seconds=200),
        waited_seconds=200,
        holder_kind=DockerHolderKind.AD_HOC,
        parent_lease_id=parent.id,
        label="gates triage_to_plan · proj · lg-reference-sources-1912",
    )

    payload = _payload(session, ticket)

    assert impossible.parent_lease_id == parent.id
    assert payload["capacity_wait"]["lease_count"] == 1
    assert payload["capacity_wait"]["total_seconds"] == 384.0, (
        "a child's stamps were added to the ticket's queue time"
    )
    assert payload["visits"][0]["capacity_wait_seconds"] == 384.0


def test_two_parks_on_one_ticket_report_both_re_acquires(session, ticket) -> None:
    """R9/R5. `reacquired_seconds` is a sum, not a flag.

    A ticket can be parked more than once — an authored question, then an
    authority grant — and each resolve pays the line again. One park is also
    passed by an implementation that reports the *last* re-acquire, so the
    second park is what makes the field a total. The first acquire is never a
    re-acquire, however many follow it.
    """
    stages = [
        ("spec", T0),
        ("test-design", T0 + timedelta(hours=9)),
        ("implement", T0 + timedelta(hours=18)),
    ]
    for stage_key, at in stages:
        _run(session, ticket, stage_key, created_at=at)
    _lease(
        session,
        ticket,
        requested_at=stages[0][1] - timedelta(seconds=10),
        waited_seconds=10,
        end_reason=DockerLeaseEndReason.PARK_RELEASED,
    )
    _lease(
        session,
        ticket,
        requested_at=stages[1][1] - timedelta(seconds=120),
        waited_seconds=120,
        end_reason=DockerLeaseEndReason.PARK_RELEASED,
    )
    _lease(session, ticket, requested_at=stages[2][1] - timedelta(seconds=300), waited_seconds=300)

    payload = _payload(session, ticket)

    assert [visit["capacity_wait_after_park"] for visit in payload["visits"]] == [
        False,
        True,
        True,
    ]
    assert payload["capacity_wait"]["reacquired_seconds"] == 420.0, (
        "both re-acquires, summed: 120 + 300"
    )
    assert payload["capacity_wait"]["total_seconds"] == 430.0


def test_thirty_leases_read_the_same_twice_whatever_order_the_rows_are_in(session, ticket) -> None:
    """R9, at the measured volume, twice.

    `lg-external-harness-1026` holds 30 gate leases and 2867s of wait, measured
    live. Two properties at that size, neither of which a three-row test can
    see: the figures are identical on a second read of the same rows (no
    dependence on row order, dict iteration or a mutated accumulator), and the
    per-visit figures plus the unattributed remainder add up to the total —
    so the number on the summary line can never disagree with the rows beneath
    it, which is the one way this surface could mislead an operator while
    looking consistent.
    """
    waits = [1 + (index * 37) % 120 for index in range(30)]
    for index, stage_key in enumerate(["plan", "spec", "implement", "verify", "review"]):
        _run(session, ticket, stage_key, created_at=T0 + timedelta(minutes=30 * index))
    # Inserted back to front, so a reader that trusts primary-key order is wrong.
    for index, wait in reversed(list(enumerate(waits))):
        _lease(
            session,
            ticket,
            requested_at=T0 + timedelta(minutes=5 * index) - timedelta(seconds=wait + 1),
            waited_seconds=wait,
        )

    first = _payload(session, ticket)
    second = _payload(session, ticket)

    assert first["capacity_wait"] == second["capacity_wait"]
    assert [visit["capacity_wait_seconds"] for visit in first["visits"]] == [
        visit["capacity_wait_seconds"] for visit in second["visits"]
    ]
    assert first["capacity_wait"]["lease_count"] == 30
    assert first["capacity_wait"]["total_seconds"] == pytest.approx(sum(waits))
    attributed = sum(visit["capacity_wait_seconds"] or 0.0 for visit in first["visits"])
    assert attributed <= first["capacity_wait"]["total_seconds"] + 0.05, (
        "the per-visit rows claim more queue time than the ticket spent"
    )
