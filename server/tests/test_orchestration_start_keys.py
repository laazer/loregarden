"""A start's idempotency key names its run from the claim until the run is over.

`lg-workflow-integrity-2797`. The key used to be stamped once the start
returned, and the builtin driver returns when the pipeline is finished: for
hours the run carried no key, a retry was refused as "already running", and a
restart in that window lost the key for good. `test_orchestration_idempotency`
holds the contract a caller sees on a start that returns at once; this is the
rest of the run's life.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from unittest import mock

import loregarden
import pytest
from loregarden.models.domain import (
    AgentSlot,
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    Ticket,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.operations.run_callbacks import _start_orchestration
from loregarden.services.operations.spec import Caller, Transport
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.parallel_queue import claim_free_slot, release_slot
from loregarden.services.queue_admission import QueueAdmissionService, Reservation
from loregarden.services.queue_dispatch import LaneDispatch
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.run_concurrency import ORCHESTRATION_LEASE
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    settle_expired_orchestration_leases,
)
from loregarden.services.start_key_invariants import (
    PARKED_STARTS_KEEP_THEIR_KEY,
    UNSTARTED_RUNS_HOLD_NO_START_KEY,
)
from loregarden.services.work_admission import OrchestrationStart, start_orchestration_admitted
from sqlmodel import Session, col, select
from tests.factories import make_workspace_ticket

PACKAGE = Path(loregarden.__file__).parent
EXECUTE = "loregarden.services.builtin_orchestrator.BuiltinOrchestrator.execute"
BUILTIN = OrchestrationDriver.BUILTIN_AUTOPILOT


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "key-1")


def _start(
    session: Session, ticket: Ticket, key: str, driver: OrchestrationDriver = BUILTIN
) -> tuple[Reservation, OrchestrationRun | None]:
    return start_orchestration_admitted(
        session,
        OrchestrationCallbackService(session),
        OrchestrationStart(ticket_id=ticket.id, driver=driver, idempotency_key=key),
    )


def _runs(session: Session, ticket: Ticket) -> list[OrchestrationRun]:
    return list(
        session.exec(
            select(OrchestrationRun)
            .where(col(OrchestrationRun.ticket_id) == ticket.id)
            .order_by(col(OrchestrationRun.created_at))
        ).all()
    )


def _entries(session: Session, ticket: Ticket) -> list[QueuedRun]:
    return list(session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).all())


def _busy_slots(session: Session) -> int:
    session.expire_all()
    return len(session.exec(select(AgentSlot).where(col(AgentSlot.is_available).is_(False))).all())


def _adopt(orchestrator, ticket: Ticket, profile) -> OrchestrationRun:
    return orchestrator.callbacks.start_orchestration_run(
        ticket, driver=BUILTIN, profile_slug=profile.slug
    )


# ---- while the pipeline runs ----------------------------------------------


def test_a_retry_during_the_pipeline_returns_the_running_run(db_session: Session, ticket: Ticket):
    seen: dict = {}

    def _execute(self, ticket, profile, **_):
        run = _adopt(self, ticket, profile)
        before = _busy_slots(db_session)
        reservation, retried = _start(db_session, ticket, "during-1")
        seen.update(
            run_id=run.id,
            retried_id=retried.id,
            slot=reservation.slot_number,
            took=_busy_slots(db_session) - before,
        )
        return run

    with mock.patch(EXECUTE, _execute):
        _start(db_session, ticket, "during-1")

    assert seen["retried_id"] == seen["run_id"]
    assert (seen["slot"], seen["took"]) == (None, 0)
    assert len(_runs(db_session, ticket)) == 1


def test_a_second_key_during_the_pipeline_is_still_refused(db_session: Session, ticket: Ticket):
    def _execute(self, ticket, profile, **_):
        run = _adopt(self, ticket, profile)
        with pytest.raises(ValueError, match="already running"):
            _start(db_session, ticket, "another-key")
        return run

    with mock.patch(EXECUTE, _execute):
        _start(db_session, ticket, "first-key")


# ---- the start raised, or the server went away ----------------------------


def test_a_pipeline_that_raised_after_adoption_keeps_its_key(db_session: Session, ticket: Ticket):
    """Agents may have run. The retry gets that run, not a second set of agents."""

    def _execute(self, ticket, profile, **_):
        _adopt(self, ticket, profile)
        raise RuntimeError("pipeline blew up")

    with mock.patch(EXECUTE, _execute), pytest.raises(RuntimeError):
        _start(db_session, ticket, "raised-1")
    fail_interrupted_orchestration_runs(db_session)

    _, retried = _start(db_session, ticket, "raised-1")
    assert [r.id for r in _runs(db_session, ticket)] == [retried.id]
    assert retried.status is OrchestrationRunStatus.FAILED
    assert retried.started_at is not None


def test_a_restart_mid_pipeline_keeps_the_key(db_session: Session, ticket: Ticket):
    """A killed server unwinds nothing: the row is all that is left."""

    def _killed(self, ticket, profile, **_):
        _adopt(self, ticket, profile)
        raise SystemExit  # not an Exception, so no handler on the way out runs

    with mock.patch(EXECUTE, _killed), pytest.raises(SystemExit):
        _start(db_session, ticket, "restart-1")
    fail_interrupted_orchestration_runs(db_session)

    _, retried = _start(db_session, ticket, "restart-1")
    assert [r.id for r in _runs(db_session, ticket)] == [retried.id]


def test_a_start_that_raised_before_adoption_releases_its_key(db_session: Session, ticket: Ticket):
    with mock.patch(EXECUTE, side_effect=RuntimeError("no worktree")):
        with pytest.raises(RuntimeError):
            _start(db_session, ticket, "early-1")
    assert _busy_slots(db_session) == 0

    # The retry goes through the external driver, which needs no pipeline.
    _, retried = _start(db_session, ticket, "early-1", OrchestrationDriver.EXTERNAL_MCP)

    abandoned, started = _runs(db_session, ticket)
    assert (abandoned.status, abandoned.idempotency_key) == (OrchestrationRunStatus.FAILED, "")
    assert (started.id, started.idempotency_key) == (retried.id, "early-1")


def _abandon(session, svc, claim, ticket):
    svc.abandon_claim(claim, message="refused")


def _complete_failed(session, svc, claim, ticket):
    svc.complete_orchestration(claim, ticket, status=OrchestrationRunStatus.FAILED)


def _restart(session, svc, claim, ticket):
    """Boot fails RUNNING runs only; a claim the restart orphaned ends by its lease."""
    claim.created_at = utcnow() - ORCHESTRATION_LEASE - timedelta(minutes=1)
    session.add(claim)
    session.commit()
    settle_expired_orchestration_leases(session)


@pytest.mark.parametrize("end", [_abandon, _complete_failed, _restart])
def test_every_way_a_claim_fails_unstarted_releases_its_key(
    db_session: Session, ticket: Ticket, end
):
    svc = OrchestrationCallbackService(db_session)
    claim = svc.claim_orchestration_run(ticket, driver=BUILTIN, idempotency_key="claim-1")
    assert claim.idempotency_key == "claim-1"

    end(db_session, svc, claim, ticket)

    db_session.refresh(claim)
    assert (claim.status, claim.idempotency_key) == (OrchestrationRunStatus.FAILED, "")


def test_a_claim_a_person_cancelled_keeps_its_key(db_session: Session, ticket: Ticket):
    """A cancel is an answer. A retry must not start what a person stopped."""
    svc = OrchestrationCallbackService(db_session)
    claim = svc.claim_orchestration_run(ticket, driver=BUILTIN, idempotency_key="cancel-1")
    svc.complete_orchestration(claim, ticket, status=OrchestrationRunStatus.CANCELLED)

    _, retried = _start(db_session, ticket, "cancel-1")
    assert retried.id == claim.id


def test_only_one_place_ends_an_orchestration_run():
    """The release lives in `_finish_orchestration_run`; a second writer skips it.

    A literal terminal status assigned anywhere else is that second writer.
    """
    writers = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign):
                continue
            value = ast.unparse(node.value)
            if any(
                isinstance(target, ast.Attribute) and target.attr == "status"
                for target in node.targets
            ) and value.startswith("OrchestrationRunStatus."):
                writers.add((path.relative_to(PACKAGE).as_posix(), value))
    assert writers == {
        ("services/orchestration_callbacks.py", "OrchestrationRunStatus.RUNNING"),
    }


# ---- two attempts at once --------------------------------------------------


def test_a_retry_that_races_its_first_attempt_replays_it(
    db_session: Session, ticket: Ticket, isolated_db
):
    """The first attempt claims between the retry's lookup and its own claim."""
    reserve = QueueAdmissionService.reserve_orchestration

    def _reserve_after_the_other_attempt(self, ticket, **options):
        with mock.patch.object(QueueAdmissionService, "reserve_orchestration", reserve):
            with Session(isolated_db) as other:
                _start(
                    other, other.get(Ticket, ticket.id), "race-1", OrchestrationDriver.EXTERNAL_MCP
                )
        return reserve(self, ticket, **options)

    with mock.patch.object(
        QueueAdmissionService, "reserve_orchestration", _reserve_after_the_other_attempt
    ):
        _, run = _start(db_session, ticket, "race-1", OrchestrationDriver.EXTERNAL_MCP)

    assert [r.id for r in _runs(db_session, ticket)] == [run.id]
    assert _busy_slots(db_session) == 1, "the losing attempt kept the lane it reserved"


# ---- parked: the pool is full and no run exists yet ------------------------


def _fill_pool(session: Session) -> list[AgentSlot]:
    QueueLaneService(session).slots.initialize_slots()
    taken = []
    while (slot := claim_free_slot(session)) is not None:
        taken.append(slot)
    return taken


def test_a_parked_start_keeps_its_key_and_a_retry_finds_its_place(
    db_session: Session, ticket: Ticket
):
    _fill_pool(db_session)
    parked, run = _start(db_session, ticket, "parked-1")
    again, _ = _start(db_session, ticket, "parked-1")

    assert run is None
    assert (parked.admitted, again.admitted) == (False, False)
    assert (again.slot_number, again.position) == (parked.slot_number, parked.position)
    assert [e.idempotency_key for e in _entries(db_session, ticket)] == ["parked-1"]


def test_a_retry_is_not_admitted_beside_the_entry_it_already_queued(
    db_session: Session, ticket: Ticket
):
    """Another lane freed first. Admitting there ran the ticket twice: once now,
    and once when the waiting entry reached the front of its own lane."""
    taken = _fill_pool(db_session)
    parked, _ = _start(db_session, ticket, "parked-2")
    other = next(slot for slot in taken if slot.slot_number != parked.slot_number)
    release_slot(db_session, other)
    db_session.commit()

    again, run = _start(db_session, ticket, "parked-2")

    assert (again.admitted, run) == (False, None)
    assert again.slot_number == parked.slot_number
    assert _runs(db_session, ticket) == []


def test_a_held_entry_still_answers_for_its_key(db_session: Session, ticket: Ticket):
    """Autopilot paused it; releasing it runs it, so a retry must not start beside it."""
    taken = _fill_pool(db_session)
    parked, _ = _start(db_session, ticket, "held-1")
    (entry,) = _entries(db_session, ticket)
    entry.status = QueuePosition.PAUSED
    db_session.add(entry)
    for slot in taken:
        release_slot(db_session, slot)
    db_session.commit()

    again, run = _start(db_session, ticket, "held-1")

    assert (again.admitted, again.slot_number, run) == (False, parked.slot_number, None)


def test_the_run_a_lane_starts_carries_the_parked_key(db_session: Session, ticket: Ticket):
    taken = _fill_pool(db_session)
    parked, _ = _start(db_session, ticket, "parked-3")
    lane = next(slot for slot in taken if slot.slot_number == parked.slot_number)
    release_slot(db_session, lane)
    db_session.commit()

    with mock.patch("loregarden.services.queue_dispatch.schedule_orchestration"):
        QueueLaneService(db_session).start_lane_head(parked.slot_number)
    (run,) = _runs(db_session, ticket)
    assert run.idempotency_key == "parked-3"
    _, during = _start(db_session, ticket, "parked-3")
    assert during.id == run.id

    svc = OrchestrationCallbackService(db_session)
    adopted = svc.start_orchestration_run(ticket, driver=BUILTIN, profile_slug="p")
    svc.complete_orchestration(adopted, ticket, status=OrchestrationRunStatus.SUCCEEDED)
    _, after = _start(db_session, ticket, "parked-3")
    assert after.id == run.id
    assert len(_runs(db_session, ticket)) == 1


def test_a_key_given_to_work_already_waiting_is_kept(db_session: Session, ticket: Ticket):
    """Parked without a key, then asked for with one: one entry, findable by it."""
    _fill_pool(db_session)
    _start(db_session, ticket, "")
    _start(db_session, ticket, "late-key")
    assert [e.idempotency_key for e in _entries(db_session, ticket)] == ["late-key"]


def _dispatch(session: Session, ticket: Ticket, key: str) -> OrchestrationRun | None:
    with mock.patch("loregarden.services.queue_dispatch.schedule_orchestration"):
        return LaneDispatch(session).dispatch_orchestration(
            ticket, auto_approve=False, stop_at_stage_key=None, idempotency_key=key
        )


def test_a_lane_re_dispatching_a_repair_leaves_the_key_on_the_first_run(
    db_session: Session, ticket: Ticket
):
    """A repair hold starts the same entry again; the key is already spent."""
    first = _dispatch(db_session, ticket, "repair-1")
    svc = OrchestrationCallbackService(db_session)
    svc.start_orchestration_run(ticket, driver=BUILTIN, profile_slug="p")
    svc.complete_orchestration(first, ticket, status=OrchestrationRunStatus.BLOCKED)

    second = _dispatch(db_session, ticket, "repair-1")

    assert second is not None and second.id != first.id
    assert [r.idempotency_key for r in _runs(db_session, ticket)] == ["repair-1", ""]


def test_an_entry_satisfied_by_a_run_already_live_names_it(db_session: Session, ticket: Ticket):
    svc = OrchestrationCallbackService(db_session)
    live = svc.start_orchestration_run(ticket, driver=BUILTIN, profile_slug="p")

    assert _dispatch(db_session, ticket, "absorbed-1").id == live.id

    db_session.refresh(live)
    assert live.idempotency_key == "absorbed-1"


# ---- an outside harness ----------------------------------------------------


def test_an_external_harness_start_honours_its_key(db_session: Session, ticket: Ticket):
    start = OrchestrationStart(
        ticket_id=ticket.id,
        external_harness=ExternalHarness.CLAUDE_CODE,
        idempotency_key="harness-1",
    )
    caller = Caller(transport=Transport.MCP)
    first = _start_orchestration(db_session, start, caller)
    second = _start_orchestration(db_session, start, caller)

    assert first.id == second.id
    assert [r.idempotency_key for r in _runs(db_session, ticket)] == ["harness-1"]


# ---- what the reconcile pass catches --------------------------------------


def test_a_key_held_by_a_claim_that_never_started_is_released(db_session: Session, ticket: Ticket):
    inv = UNSTARTED_RUNS_HOLD_NO_START_KEY
    svc = OrchestrationCallbackService(db_session)
    claim = svc.claim_orchestration_run(ticket, driver=BUILTIN, idempotency_key="phantom-1")
    assert list(inv.detect(db_session)) == [], "a live claim holds its key legitimately"
    claim.status = OrchestrationRunStatus.FAILED
    db_session.add(claim)
    db_session.commit()

    (violation,) = inv.detect(db_session)
    assert violation.subject_id == claim.id
    assert inv.repair(db_session, [violation]) == 1
    assert list(inv.detect(db_session)) == []


def test_a_parked_start_whose_run_lost_its_key_gets_it_back(db_session: Session, ticket: Ticket):
    inv = PARKED_STARTS_KEEP_THEIR_KEY
    svc = OrchestrationCallbackService(db_session)
    run = svc.start_orchestration_run(ticket, driver=BUILTIN, profile_slug="p")
    db_session.add(
        QueuedRun(
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            status=QueuePosition.ACTIVE,
            orchestration_run_id=run.id,
            idempotency_key="lost-1",
        )
    )
    db_session.commit()

    (violation,) = inv.detect(db_session)
    assert inv.repair(db_session, [violation]) == 1
    db_session.refresh(run)
    assert run.idempotency_key == "lost-1"
    assert list(inv.detect(db_session)) == []
