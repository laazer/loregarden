"""A lane is held by a promise, so the promise has to expire.

`OrchestrationRun` carried no owner, pid, heartbeat or deadline, so
`_occupant_is_live` answered "is this still running?" with `status in
LIVE_ORCHESTRATION_STATUSES` — a field only the run's own owner ever moves. An
external harness lives in someone else's terminal by design; when that session
walked away the status stayed RUNNING and the lane was held permanently. Not
until restart: the startup reaper exempts external runs unconditionally, and the
operator surface its comment delegates cleanup to has never existed.

Observed 2026-08-14 as `orch_5a2daa`: zero agent runs beneath it, slot 1 bound
three seconds after it started and never released, all three slots reading
`is_available = 0` with the pool's real capacity at zero.

The lease is renewed by the work itself — any control-plane write naming the
run — so a slow-but-live session keeps its lane with nobody vouching for it,
and only a session that has stopped talking expires.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    BlockKind,
    ExternalHarness,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.block_classification import classify_block_message
from loregarden.services.interruption_messages import (
    ORCHESTRATION_LEASE_EXPIRED_PREFIX,
    expired_lease_message,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.reconciliation import reconcile_once
from loregarden.services.run_concurrency import (
    ORCHESTRATION_LEASE,
    orchestration_lease_expired,
    subtree_orchestration_ids,
)
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    settle_expired_orchestration_leases,
)
from sqlmodel import Session, select

EXPIRED = ORCHESTRATION_LEASE + timedelta(minutes=5)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(
    session: Session, workspace, code: str = "T-1", *, parent: Ticket | None = None
) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id,
        title=code,
        parent_ticket_id=None if parent is None else parent.id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _orch(
    session: Session,
    ticket: Ticket,
    *,
    external: ExternalHarness | None = None,
    age: timedelta = timedelta(minutes=1),
    last_seen: timedelta | None = None,
    run_code: str = "orch_1",
) -> OrchestrationRun:
    started = datetime.now(timezone.utc) - age
    run = OrchestrationRun(
        run_code=run_code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
        started_at=started,
        created_at=started,
        external_harness=external,
        last_seen_at=None if last_seen is None else datetime.now(timezone.utc) - last_seen,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---- the lease itself --------------------------------------------------


def test_a_renewed_run_holds_its_lease(session, workspace):
    run = _orch(session, _ticket(session, workspace), age=EXPIRED, last_seen=timedelta(minutes=1))
    assert orchestration_lease_expired(session, run) is False


def test_a_quiet_run_expires(session, workspace):
    run = _orch(session, _ticket(session, workspace), age=EXPIRED, last_seen=EXPIRED)
    assert orchestration_lease_expired(session, run) is True


def test_a_run_never_renewed_falls_back_to_when_it_started(session, workspace):
    """Rows written before the lease existed must be reclaimable without a backfill."""
    assert orchestration_lease_expired(
        session, _orch(session, _ticket(session, workspace), age=EXPIRED)
    )
    assert not orchestration_lease_expired(
        session, _orch(session, _ticket(session, workspace, "T-2"))
    )


def test_a_control_plane_write_renews_the_lease(session, workspace):
    """The work vouches for the run; nobody has to remember to."""
    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED)
    assert orchestration_lease_expired(session, run) is True

    OrchestrationCallbackService(session).touch_lease(run)
    session.commit()

    assert orchestration_lease_expired(session, run) is False


# ---- the lane comes back -----------------------------------------------


def test_an_expired_lease_releases_the_lane(session, workspace):
    """The permanent case: status says RUNNING and always will."""
    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()

    freed = ParallelQueueService(session).reconcile_slots()

    assert freed == [1]
    session.refresh(slot)
    assert slot.is_available is True


def test_a_live_external_session_keeps_its_lane(session, workspace):
    """The property the exemption exists to protect.

    An external caller still making control-plane writes must not lose its lane
    to a sweep — that is the whole reason external runs are exempt at all.
    """
    run = _orch(
        session,
        _ticket(session, workspace),
        external=ExternalHarness.CODEX,
        age=EXPIRED,
        last_seen=timedelta(minutes=2),
    )
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()

    assert ParallelQueueService(session).reconcile_slots() == []
    session.refresh(slot)
    assert slot.is_available is False


# ---- the startup reaper's exemption is now bounded ---------------------


def test_the_reaper_still_spares_a_live_external_run_across_a_restart(session, workspace):
    """Restarting this server must not end someone's terminal session."""
    run = _orch(
        session,
        _ticket(session, workspace),
        external=ExternalHarness.CODEX,
        last_seen=timedelta(minutes=1),
    )

    assert fail_interrupted_orchestration_runs(session) == []
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.RUNNING


def test_the_reaper_settles_an_abandoned_external_run(session, workspace):
    """Was exempt unconditionally, with cleanup delegated to a surface that
    does not exist — so it was never settled at all."""
    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)

    assert [r.id for r in fail_interrupted_orchestration_runs(session)] == [run.id]
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED


def test_the_reaper_still_settles_a_run_this_server_owned(session, workspace):
    run = _orch(session, _ticket(session, workspace))

    assert [r.id for r in fail_interrupted_orchestration_runs(session)] == [run.id]


# ---- stop is no longer a silent no-op ----------------------------------


def test_stopping_an_external_run_settles_it_rather_than_reporting_success(client, db_session):
    """`cancel_requested_at` is polled by BuiltinOrchestrator, which does not
    drive an external run. Stop set a flag nobody reads, returned 200, and left
    the lane held."""
    ws = db_session.exec(select(Workspace)).first()
    ticket = Ticket(external_id="T-stop", workspace_id=ws.id, title="stop me")
    db_session.add(ticket)
    db_session.commit()
    run = OrchestrationRun(
        run_code="orch_ext",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        status=OrchestrationRunStatus.RUNNING,
        external_harness=ExternalHarness.CODEX,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    assert client.post(f"/api/tickets/{ticket.id}/stop").status_code == 200

    db_session.refresh(run)
    assert run.status is OrchestrationRunStatus.CANCELLED
    assert run.finished_at is not None


# ---- a long stage is not a dead run ------------------------------------


def test_a_long_running_stage_keeps_its_lane(session, workspace):
    """The lease renews at stage boundaries, so a long stage has none to give.

    Two agent runs on the day this was written took 51 and 38 minutes against a
    30-minute lease. Judging on the lease alone frees the lane out from under an
    agent that is working, and the pool then admits past its own limit. An agent
    run in flight is work in flight and outranks the lease.
    """
    from loregarden.models.domain import AgentRun, RunStatus

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED)
    session.add(
        AgentRun(
            run_code="run_long",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
            orchestration_run_id=run.id,
            started_at=datetime.now(timezone.utc) - EXPIRED,
        )
    )
    session.commit()

    assert orchestration_lease_expired(session, run) is False

    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()
    assert ParallelQueueService(session).reconcile_slots() == []
    session.refresh(slot)
    assert slot.is_available is False


# ---- the run becomes terminal, not just the slot free ------------------


def test_an_expired_lease_settles_the_run_not_only_the_slot(session, workspace):
    """The lane coming back is not enough.

    `claim_orchestration_run` adopts any active run for a ticket, so a run left
    RUNNING blocks that ticket from ever being orchestrated again until a
    restart; `ticket_activity` also counts it as running. The lease frees the
    slot on a status read but only the startup reaper settled the run.
    """
    from loregarden.services.run_service import settle_expired_orchestration_leases

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, external=ExternalHarness.CODEX, age=EXPIRED)

    assert [r.id for r in settle_expired_orchestration_leases(session)] == [run.id]
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED
    assert run.finished_at is not None


def test_the_sweep_spares_a_renewed_run(session, workspace):
    from loregarden.services.run_service import settle_expired_orchestration_leases

    _orch(session, _ticket(session, workspace), last_seen=timedelta(minutes=1))
    assert settle_expired_orchestration_leases(session) == []


def test_the_sweep_spares_a_run_with_a_live_agent(session, workspace):
    """The long-stage case again, through the sweep rather than the slot check."""
    from loregarden.models.domain import AgentRun, RunStatus
    from loregarden.services.run_service import settle_expired_orchestration_leases

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED)
    session.add(
        AgentRun(
            run_code="run_long",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
            orchestration_run_id=run.id,
        )
    )
    session.commit()

    assert settle_expired_orchestration_leases(session) == []
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.RUNNING


def test_the_reconciliation_pass_settles_expired_leases(session, workspace):
    """A periodic tick exists now, and it is the cadence.

    This used to assert the *board read* settled the lease, because at the time
    the read was the only thing that ran often enough to count as a clock. That
    made repair conditional on someone having the dashboard open — ticket 437 —
    so the sweep moved to the reconciliation timer and the read went back to
    being a read.
    """
    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()

    reconcile_once(session)

    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED
    session.refresh(slot)
    assert slot.is_available is True


def test_a_board_read_does_not_draw_a_lane_whose_lease_expired(session, workspace):
    """The read must be right between sweeps, without doing the repair itself."""

    from loregarden.services.queue_status import build_queue_status

    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)
    session.add(AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id))
    session.commit()

    snapshot = build_queue_status(session)

    assert snapshot["active_runs"] == []


# ---- the handoff between stages is not a quiet run ---------------------


def test_a_just_finished_child_run_holds_the_lease(session, workspace):
    """The gap the in-flight veto cannot cover.

    `orch_749069` had every stage green — three script_review reviewers passing
    — and was swept 345ms after the last of them exited, in the seconds between
    that stage ending and its exit gate dispatching. The veto above only sees
    RUNNING/QUEUED children, so it lapses the instant a stage's last agent
    finishes; the run then has nothing holding it up until the next dispatch.
    A child that finished seconds ago is activity.
    """
    from loregarden.models.domain import AgentRun, RunStatus

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED)
    session.add(
        AgentRun(
            run_code="run_done",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="architecture_reviewer",
            status=RunStatus.SUCCEEDED,
            orchestration_run_id=run.id,
            started_at=datetime.now(timezone.utc) - EXPIRED,
            finished_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    session.commit()

    assert orchestration_lease_expired(session, run) is False


def test_a_run_whose_last_child_finished_long_ago_still_expires(session, workspace):
    """The stamp must widen the window, not remove it."""
    from loregarden.models.domain import AgentRun, RunStatus

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED)
    session.add(
        AgentRun(
            run_code="run_stale",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="architecture_reviewer",
            status=RunStatus.SUCCEEDED,
            orchestration_run_id=run.id,
            started_at=datetime.now(timezone.utc) - EXPIRED,
            finished_at=datetime.now(timezone.utc) - EXPIRED,
        )
    )
    session.commit()

    assert orchestration_lease_expired(session, run) is True


# ---- a parent is not dead while its children are working ---------------
#
# lg-milestone-that-780. A parent orchestration runs no stages of its own: its
# work is its children's runs, dispatched from inside
# `_orchestrate_incomplete_children` → `BuiltinOrchestrator.execute(child)`,
# under *nested* orchestrations of its own making. The driver thread is blocked
# in that call and renews nothing, and the in-flight veto only ever looked at
# agent runs whose `orchestration_run_id` was the parent's — so a healthy parent
# waiting on a healthy child was indistinguishable from a dead one.
#
# Observed 2026-09-21 20:43:26 on lg-milestone-that-714 (orch_2b5b48, run 4):
# reaped while 718's verifier (run_033763, last_seen_at 20:44:04) was renewing
# under the nested orchestration orch_3cc557. The parent's thread kept running
# and went on to dispatch 719 under an orchestration the database called failed.
#
# Everything the sweep can see is in `orchestration_runs`, `agent_runs` and
# `tickets`, so these build rows. No thread blocks in `execute()`: the parent
# and every nested `execute()` share one Session
# (`builtin_orchestrator.py:966`), which makes a threaded repro unsafe as well
# as flaky.


def _agent_run(
    session: Session,
    ticket: Ticket,
    orch: OrchestrationRun | None,
    *,
    run_code: str,
    status: RunStatus,
    agent_id: str = "verifier",
    started: timedelta = timedelta(minutes=2),
    finished: timedelta | None = None,
) -> AgentRun:
    now = datetime.now(timezone.utc)
    run = AgentRun(
        run_code=run_code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        status=status,
        orchestration_run_id=None if orch is None else orch.id,
        started_at=now - started,
        finished_at=None if finished is None else now - finished,
        last_seen_at=None if finished is not None else now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _nested_pair(session, workspace):
    """The observed shape: a stale parent, a live run under a nested child."""
    parent = _ticket(session, workspace, "T-714")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_2b5b48")
    child = _ticket(session, workspace, "T-718", parent=parent)
    child_orch = _orch(
        session,
        child,
        age=timedelta(minutes=10),
        last_seen=timedelta(minutes=1),
        run_code="orch_3cc557",
    )
    _agent_run(session, child, child_orch, run_code="run_033763", status=RunStatus.RUNNING)
    return parent_orch, child_orch


# AC1 / R2-AC1, R2-AC2.
def test_a_parent_waiting_on_a_nested_child_is_not_reaped(session, workspace):
    parent_orch, _child_orch = _nested_pair(session, workspace)

    assert orchestration_lease_expired(session, parent_orch) is False
    assert settle_expired_orchestration_leases(session) == []
    session.refresh(parent_orch)
    assert parent_orch.status is OrchestrationRunStatus.RUNNING


# AC2 / R2-AC3 — the lane is the second consumer of the same predicate
# (`parallel_queue.py:585`), and the half the incident report named.
def test_the_lane_is_not_released_under_a_parent_waiting_on_a_nested_child(session, workspace):
    parent_orch, _child_orch = _nested_pair(session, workspace)
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=parent_orch.id)
    session.add(slot)
    session.commit()

    assert ParallelQueueService(session).reconcile_slots() == []
    session.refresh(slot)
    assert slot.is_available is False


# AC3 / R2-AC4 — the walk is the whole subtree, not direct children.
def test_a_live_grandchild_run_holds_both_ancestors(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    grandchild = _ticket(session, workspace, "T-grandchild", parent=child)
    grandchild_orch = _orch(
        session,
        grandchild,
        age=timedelta(minutes=5),
        last_seen=timedelta(minutes=1),
        run_code="orch_g",
    )
    _agent_run(session, grandchild, grandchild_orch, run_code="run_deep", status=RunStatus.RUNNING)

    assert orchestration_lease_expired(session, parent_orch) is False
    assert orchestration_lease_expired(session, child_orch) is False
    assert settle_expired_orchestration_leases(session) == []


# AC3 — the AgentRun query is scoped by descendant ORCHESTRATION ids, never by
# descendant TICKET ids. A Baxter triage turn on a child ticket is the
# operator talking about the work, not the work; scoping by ticket would let it
# vouch for a dead parent (see TRIAGE_STAGE_KEY in run_concurrency.py).
def test_a_triage_turn_on_a_child_ticket_does_not_vouch_for_a_dead_parent(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    _agent_run(
        session,
        child,
        None,
        run_code="run_chat",
        status=RunStatus.RUNNING,
        agent_id="baxter",
    )

    assert orchestration_lease_expired(session, parent_orch) is True


# AC4 / R3-AC1 — the between-children window: `_orchestrate_incomplete_children`
# runs every runnable child inside one call and the parent's only `_renew_lease`
# is at the top of the outer loop, so between child A finishing and child B's
# first run starting there is neither an in-flight descendant run nor a parent
# renewal. That is the orch_749069 window, one level down.
def test_a_just_finished_descendant_run_holds_the_parents_lease(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    _agent_run(
        session,
        child,
        child_orch,
        run_code="run_done",
        status=RunStatus.SUCCEEDED,
        started=EXPIRED,
        finished=timedelta(seconds=10),
    )

    assert orchestration_lease_expired(session, parent_orch) is False


# AC4 / R3-AC2 — and the window stays a window.
def test_a_long_finished_descendant_run_does_not_hold_the_parents_lease(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    _agent_run(
        session,
        child,
        child_orch,
        run_code="run_old",
        status=RunStatus.SUCCEEDED,
        started=EXPIRED,
        finished=EXPIRED,
    )

    assert orchestration_lease_expired(session, parent_orch) is True


# AC5 — the guard that stays. This change widens the window; it must not remove
# it (lg-milestone-that-779: a suite that removes one of two guards reliably
# forgets to pin the one that stays).
def test_a_genuinely_dead_parent_is_still_reaped(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_dead")
    for index in (1, 2):
        child = _ticket(session, workspace, f"T-child-{index}", parent=parent)
        child_orch = _orch(
            session, child, age=EXPIRED, last_seen=EXPIRED, run_code=f"orch_child{index}"
        )
        _agent_run(
            session,
            child,
            child_orch,
            run_code=f"run_old{index}",
            status=RunStatus.SUCCEEDED,
            started=EXPIRED,
            finished=EXPIRED,
        )

    assert orchestration_lease_expired(session, parent_orch) is True

    settled = settle_expired_orchestration_leases(session)

    assert parent_orch.id in {run.id for run in settled}
    session.refresh(parent_orch)
    assert parent_orch.status is OrchestrationRunStatus.FAILED


# AC6 / R1-AC1 — pre-existing leaf behaviour is unchanged.
def test_a_leaf_ticket_has_no_subtree_orchestrations(session, workspace):
    run = _orch(session, _ticket(session, workspace), age=EXPIRED, last_seen=EXPIRED)

    assert subtree_orchestration_ids(session, run) == []


# R1-AC2, R1-AC3, R1-AC4 — the helper itself: every depth, no status filter,
# and never the subject run's own id.
def test_subtree_orchestration_ids_walks_every_level_and_filters_no_status(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, run_code="orch_c")
    child_orch.status = OrchestrationRunStatus.SUCCEEDED
    session.add(child_orch)
    grandchild = _ticket(session, workspace, "T-grandchild", parent=child)
    grandchild_orch = _orch(session, grandchild, run_code="orch_g")
    session.commit()

    ids = subtree_orchestration_ids(session, parent_orch)

    assert set(ids) == {child_orch.id, grandchild_orch.id}
    assert parent_orch.id not in ids


# ---- the sweep message names what it examined --------------------------
#
# AC7–AC13. The message is the only operator-visible change in this ticket. It
# reaches a person as `tickets.blocking_issues` / `orchestration_runs.error_message`
# in the TicketAttention row and the Timeline tab, and has to answer one
# question from their side: was this run's driver actually dead, or was it
# waiting on work one level down? Measured read-only from the live database
# 2026-10-20: a parent ticket's subtree holds 18 orchestration runs on average
# and 209 at the largest, so the sentence names a count and never a list.

_MESSAGE_N2 = (
    "Orchestration lease expired: no in-flight agent run under orch_2b5b48 or the 2 "
    "orchestrations in its ticket subtree, and nothing has renewed it. Its driver is "
    "gone; the lane has been released."
)
_MESSAGE_N1 = (
    "Orchestration lease expired: no in-flight agent run under orch_2b5b48 or the 1 "
    "orchestration in its ticket subtree, and nothing has renewed it. Its driver is "
    "gone; the lane has been released."
)
_MESSAGE_N0 = (
    "Orchestration lease expired: no in-flight agent run under orch_2b5b48 and no "
    "nested orchestration beneath it, and nothing has renewed it. Its driver is "
    "gone; the lane has been released."
)


# AC7 — the three variants, byte for byte, at the lengths the spec measured.
@pytest.mark.parametrize(
    ("count", "expected", "length"),
    [(0, _MESSAGE_N0, 185), (1, _MESSAGE_N1, 191), (2, _MESSAGE_N2, 192)],
)
def test_the_expired_lease_message_is_exactly_this_sentence(count, expected, length):
    assert expired_lease_message("orch_2b5b48", count) == expected
    assert len(expected) == length


# AC10 — a count, never a list of ids or run codes. At the largest subtree
# measured (209) the sentence is still one short line, which an enumerating
# message would not be.
def test_the_message_names_a_count_not_an_enumeration():
    message = expired_lease_message("orch_2b5b48", 209)

    assert "or the 209 orchestrations in its ticket subtree" in message
    assert len(message) == 194


# AC12 — zero is worded, not templated. A dead leaf parent and an unrendered
# placeholder must not read the same.
def test_zero_and_one_nested_orchestrations_are_worded():
    zero = expired_lease_message("orch_2b5b48", 0)
    one = expired_lease_message("orch_2b5b48", 1)

    assert "and no nested orchestration beneath it" in zero
    assert "the 0" not in zero
    assert "orchestration(s)" not in zero
    assert "or the 1 orchestration in its ticket subtree" in one
    assert "orchestrations" not in one


# AC11 — TicketAttention renders only `firstLine(block)` and decides its "Read"
# affordance at `TEASER_CHARS = 140` (client/src/components/TicketAttention.tsx:15,20),
# so the clause that distinguishes the two cases has to land inside the teaser.
@pytest.mark.parametrize("count", [0, 1, 2, 209])
def test_the_message_is_one_line_and_distinguishes_itself_inside_the_teaser(count):
    message = expired_lease_message("orch_abc123", count)
    teaser = message[:140]

    assert "\n" not in message
    assert message.startswith(ORCHESTRATION_LEASE_EXPIRED_PREFIX)
    assert "orch_abc123" in teaser
    if count == 0:
        assert "no nested orchestration beneath it" in teaser
    else:
        assert str(count) in teaser


# AC13 — the prefix keeps the row labelled "Blocked · Harness" with its existing
# two controls; no new surface, state or affordance is introduced.
@pytest.mark.parametrize("count", [0, 1, 2, 209])
def test_a_formatted_expired_lease_message_is_still_a_harness_block(count):
    assert classify_block_message(expired_lease_message("orch_abc123", count)) is BlockKind.HARNESS


# AC8 — the count in the message is the count the predicate examined: the sweep
# builds it from the same helper, in the same session.
def test_the_settled_message_carries_the_subtree_count_it_examined(session, workspace):
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_dead")
    for index in (1, 2):
        child = _ticket(session, workspace, f"T-child-{index}", parent=parent)
        _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code=f"orch_child{index}")

    settle_expired_orchestration_leases(session)

    session.refresh(parent_orch)
    assert parent_orch.error_message == expired_lease_message(parent_orch.run_code, 2)


# AC8 — and the n = 0 wording for a dead leaf parent, which is what an operator
# meets on nearly half of today's failed runs (41 of 100, measured 2026-10-20).
def test_a_dead_leaf_parent_is_settled_with_the_zero_wording(session, workspace):
    run = _orch(session, _ticket(session, workspace), age=EXPIRED, last_seen=EXPIRED)

    settle_expired_orchestration_leases(session)

    session.refresh(run)
    assert run.error_message == expired_lease_message(run.run_code, 0)
    assert "and no nested orchestration beneath it" in run.error_message


# ---- the walk has to have edges ----------------------------------------
#
# lg-milestone-that-780, test-break. Widening a scope is the kind of change
# that is easy to over-apply: every assertion above holds just as well for an
# implementation that vetoes on *any* in-flight agent run in the workspace,
# because no shape above puts a live run anywhere the subtree does not reach.
# An unscoped veto would never reap anything while one unrelated ticket is
# being worked, which is the guard AC5 says must survive. These pin the
# edges of the walk: down only, from this ticket only.


def test_an_unrelated_tickets_live_run_does_not_hold_a_stale_run(session, workspace):
    """A live run somewhere else in the workspace is not this run's liveness."""
    subject = _ticket(session, workspace, "T-subject")
    subject_orch = _orch(session, subject, age=EXPIRED, last_seen=EXPIRED, run_code="orch_s")
    stranger = _ticket(session, workspace, "T-stranger")
    stranger_orch = _orch(
        session,
        stranger,
        age=timedelta(minutes=5),
        last_seen=timedelta(minutes=1),
        run_code="orch_x",
    )
    _agent_run(session, stranger, stranger_orch, run_code="run_elsewhere", status=RunStatus.RUNNING)

    assert orchestration_lease_expired(session, subject_orch) is True
    assert subtree_orchestration_ids(session, subject_orch) == []


def test_a_siblings_live_run_does_not_hold_a_stale_sibling(session, workspace):
    """Two children of one parent are each other's cousins, not each other's work."""
    parent = _ticket(session, workspace, "T-parent")
    _orch(
        session, parent, age=timedelta(minutes=1), last_seen=timedelta(minutes=1), run_code="orch_p"
    )
    dead = _ticket(session, workspace, "T-dead", parent=parent)
    dead_orch = _orch(session, dead, age=EXPIRED, last_seen=EXPIRED, run_code="orch_d")
    live = _ticket(session, workspace, "T-live", parent=parent)
    live_orch = _orch(
        session, live, age=timedelta(minutes=5), last_seen=timedelta(minutes=1), run_code="orch_l"
    )
    _agent_run(session, live, live_orch, run_code="run_sibling", status=RunStatus.RUNNING)

    assert orchestration_lease_expired(session, dead_orch) is True


def test_a_live_run_on_an_ancestor_does_not_hold_a_stale_descendant(session, workspace):
    """The walk goes down. A parent's own stage does not vouch for a child's run."""
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(
        session, parent, age=timedelta(minutes=5), last_seen=timedelta(minutes=1), run_code="orch_p"
    )
    _agent_run(session, parent, parent_orch, run_code="run_parent", status=RunStatus.RUNNING)
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")

    assert orchestration_lease_expired(session, child_orch) is True
    assert orchestration_lease_expired(session, parent_orch) is False


def test_another_orchestration_on_the_subjects_own_ticket_is_not_its_subtree(session, workspace):
    """Run 5 of a ticket is not work *beneath* run 4 of it.

    `subtree_orchestration_ids` excludes the subject run's id, which a walk
    rooted at the ticket *inclusive* also does — and then counts the ticket's
    own other attempts as nested work, so a re-run would vouch for the attempt
    it replaced and the sweep message would claim a subtree that does not exist.
    """
    ticket = _ticket(session, workspace, "T-rerun")
    stale = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED, run_code="orch_run4")
    rerun = _orch(
        session,
        ticket,
        age=timedelta(minutes=2),
        last_seen=timedelta(minutes=1),
        run_code="orch_run5",
    )
    _agent_run(session, ticket, rerun, run_code="run_fresh", status=RunStatus.RUNNING)

    assert subtree_orchestration_ids(session, stale) == []
    assert expired_lease_message(stale.run_code, 0) == expired_lease_message("orch_run4", 0)


# ---- what counts as a live descendant run ------------------------------


@pytest.mark.parametrize(
    "status", [RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.AWAITING_PERMISSION]
)
def test_every_live_status_under_a_descendant_holds_the_parent(session, workspace, status):
    """The widened veto must count what the narrow one counted, not a subset.

    QUEUED is the dispatched-but-not-yet-started child, and
    AWAITING_PERMISSION is a child stopped at the approval inbox — both hold a
    lane and neither renews the parent.
    """
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    _agent_run(session, child, child_orch, run_code="run_live", status=status)

    assert orchestration_lease_expired(session, parent_orch) is False


def test_a_live_run_under_a_terminal_child_orchestration_still_holds_the_parent(session, workspace):
    """The id set carries no status filter, and the predicate must not add one.

    This is 775's shape one level down: the child orchestration has already
    been marked FAILED (by this very sweep, on an earlier pass) while its agent
    run is alive and renewing. Filtering the subtree to active orchestrations
    would reap the parent on the next pass for exactly the reason this ticket
    exists.
    """
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    child_orch.status = OrchestrationRunStatus.FAILED
    session.add(child_orch)
    session.commit()
    _agent_run(session, child, child_orch, run_code="run_orphan", status=RunStatus.RUNNING)

    assert child_orch.id in subtree_orchestration_ids(session, parent_orch)
    assert orchestration_lease_expired(session, parent_orch) is False


# ---- the walk's own edges: depth, cycles, width -------------------------


def test_a_live_run_thirty_levels_down_holds_the_root(session, workspace):
    """`descendants_by_root` is one query per level, so depth is unbounded.

    A walk written with a depth cap — or one level of `children`, or two —
    passes the grandchild test above and still reaps a milestone whose work is
    four levels under it. Ticket trees here nest deeper than anyone assumes.
    """
    chain = [_ticket(session, workspace, "T-0")]
    root_orch = _orch(session, chain[0], age=EXPIRED, last_seen=EXPIRED, run_code="orch_root")
    for depth in range(1, 31):
        chain.append(_ticket(session, workspace, f"T-{depth}", parent=chain[-1]))
        _orch(session, chain[-1], age=EXPIRED, last_seen=EXPIRED, run_code=f"orch_{depth}")
    deepest = _ticket(session, workspace, "T-31", parent=chain[-1])
    bottom_orch = _orch(
        session,
        deepest,
        age=timedelta(minutes=2),
        last_seen=timedelta(minutes=1),
        run_code="orch_bottom",
    )
    _agent_run(session, deepest, bottom_orch, run_code="run_deepest", status=RunStatus.RUNNING)

    assert len(subtree_orchestration_ids(session, root_orch)) == 31
    assert orchestration_lease_expired(session, root_orch) is False
    assert settle_expired_orchestration_leases(session) == []


def test_a_parent_cycle_in_the_ticket_tree_does_not_hang_the_predicate(session, workspace):
    """A cycle is a data bug; the sweep still has to answer.

    `descendants_by_root` guards against revisiting a node, and the predicate
    inherits that. A hand-rolled recursive walk would spin here with the
    reconcile timer's thread, and nothing would ever expire again.
    """
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    parent.parent_ticket_id = child.id  # the cycle
    session.add(parent)
    session.commit()

    assert subtree_orchestration_ids(session, parent_orch) == [child_orch.id]
    assert orchestration_lease_expired(session, parent_orch) is True


def test_the_message_counts_a_wide_subtree_exactly_once_per_orchestration(session, workspace):
    """Fifty children, two orchestration runs each: the count is rows, not tickets.

    The largest subtree measured in the live database holds 209 orchestration
    runs (2026-10-20), so the count the operator reads has to survive a wide
    tree as well as a deep one, and must not dedupe by ticket or trim to a
    page of results.
    """
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_wide")
    for index in range(50):
        child = _ticket(session, workspace, f"T-c{index}", parent=parent)
        for attempt in (1, 2):
            _orch(
                session,
                child,
                age=EXPIRED,
                last_seen=EXPIRED,
                run_code=f"orch_c{index}_{attempt}",
            )

    ids = subtree_orchestration_ids(session, parent_orch)

    assert len(ids) == 100
    assert len(set(ids)) == 100

    settle_expired_orchestration_leases(session)

    session.refresh(parent_orch)
    assert parent_orch.error_message == expired_lease_message("orch_wide", 100)


# ---- the sweep settles each run against its own subtree ----------------


def test_a_dead_tree_settles_every_run_with_its_own_count(session, workspace):
    """One sweep, three runs, three different counts — and no run's message
    describes another run's subtree. The sweep settles candidates in whatever
    order the query returns them and marks each FAILED as it goes; a count
    computed once, or computed after the walk's rows were mutated, would make
    the messages disagree with the tree.
    """
    parent = _ticket(session, workspace, "T-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_p")
    child = _ticket(session, workspace, "T-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_c")
    grandchild = _ticket(session, workspace, "T-grandchild", parent=child)
    grandchild_orch = _orch(session, grandchild, age=EXPIRED, last_seen=EXPIRED, run_code="orch_g")

    settled = {run.id for run in settle_expired_orchestration_leases(session)}

    assert settled == {parent_orch.id, child_orch.id, grandchild_orch.id}
    for run, count in ((parent_orch, 2), (child_orch, 1), (grandchild_orch, 0)):
        session.refresh(run)
        assert run.status is OrchestrationRunStatus.FAILED
        assert run.error_message == expired_lease_message(run.run_code, count)


# ---- the message's own edges -------------------------------------------


def test_a_negative_subtree_count_is_never_rendered():
    """`len()` cannot go negative, so a negative count is a programming error.

    The spec says it may raise rather than render "the -1 orchestrations". Both
    answers are acceptable; silently printing a negative count to an operator
    is not, and a bare `f"the {n} orchestration(s)"` template does exactly that.
    """
    try:
        message = expired_lease_message("orch_abc123", -1)
    except ValueError:
        return
    assert "-1" not in message
    assert "-" not in message.removeprefix(ORCHESTRATION_LEASE_EXPIRED_PREFIX)


@pytest.mark.parametrize("count", [2, 18, 209, 1000, 10**6])
def test_the_message_stays_one_teaser_line_at_any_realistic_count(count):
    """AC11 is asserted at {0, 1, 2, 209}; the ceiling is not a measurement.

    A seven-digit count is not reachable today, but the teaser budget is
    `TEASER_CHARS = 140` and the clause lands at 117 characters with an
    11-character run code, so the invariant holds with room — and pinning it
    here means a reworded sentence that pushes the subtree clause past the
    teaser fails in this file rather than on an operator's screen.
    """
    message = expired_lease_message("orch_abc123", count)

    assert "\n" not in message
    assert f"the {count} orchestrations in its ticket subtree" in message[:140]
    assert message.endswith("Its driver is gone; the lane has been released.")


def test_the_message_is_deterministic_and_carries_no_state():
    """Same inputs, same sentence, whatever was asked before."""
    first = expired_lease_message("orch_2b5b48", 2)
    expired_lease_message("orch_other", 0)
    expired_lease_message("orch_other", 1)

    assert expired_lease_message("orch_2b5b48", 2) == first


def test_the_old_module_level_constant_is_gone(session):
    """AC15: removed, not shimmed. A left-behind `EXPIRED_LEASE_MESSAGE` is a
    second spelling of a sentence whose whole job is to be matched exactly, and
    a caller still passing it would silently un-do AC7 for every run it settles.
    """
    from loregarden.services import run_service

    assert not hasattr(run_service, "EXPIRED_LEASE_MESSAGE")
    with pytest.raises(TypeError):
        run_service.settle_expired_orchestration_leases(session, message="anything")


# ---- the reap has to be greppable where an operator looks ---------------
#
# AC9. The sentence reaches exactly two surfaces: `orchestration_runs.error_message`
# (asserted above) and the sweep's own warning log line. It reaches no ticket
# field and no client surface — `_finish_orchestration_run` writes neither
# `tickets.blocking_issues` nor an error artifact, and the operator's blocked
# row for a reaped parent is written two steps later in the same reconcile pass
# by `settle_stranded_stages`, carrying `STRANDED_STAGE_MESSAGE` (AC9b). So the
# log line is the only place a person diagnosing a reap *reads* this reason
# rather than querying for it, and a sweep that logs "lease expired, last seen
# X" without the formatted sentence says nothing about what it examined — which
# is the entire point of the widened predicate.


def test_the_sweep_logs_the_message_it_settled_the_run_with(session, workspace, caplog):
    """The formatted sentence is in the warning record, not only in the column."""
    parent = _ticket(session, workspace, "T-logged-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_logged")
    for index in (1, 2):
        child = _ticket(session, workspace, f"T-logged-child-{index}", parent=parent)
        _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code=f"orch_logchild{index}")

    with caplog.at_level(logging.WARNING, logger="loregarden.services.run_service"):
        settled = settle_expired_orchestration_leases(session)

    assert parent_orch.id in {run.id for run in settled}
    assert expired_lease_message(parent_orch.run_code, 2) in caplog.text


def test_the_logged_message_is_the_one_the_run_carries(session, workspace, caplog):
    """Log line and column cannot drift: a dead leaf parent gets the n = 0 wording
    in both, so the reason an operator greps for is the reason stored on the run.
    """
    run = _orch(
        session, _ticket(session, workspace, "T-logged-leaf"), age=EXPIRED, last_seen=EXPIRED
    )

    with caplog.at_level(logging.WARNING, logger="loregarden.services.run_service"):
        settle_expired_orchestration_leases(session)

    session.refresh(run)
    assert run.error_message == expired_lease_message(run.run_code, 0)
    assert run.error_message in caplog.text
    assert "and no nested orchestration beneath it" in caplog.text


# ---- the log line's own edges -------------------------------------------
#
# AC9 again, adversarially. "The formatted message appears in `caplog.text`"
# is satisfied by several sweeps that are worse than the one the criterion
# wants, and `caplog.text` is blind to every difference: it is the
# concatenation of the records' messages, with no level, no logger name and no
# record boundaries. So these four pin what the two tests above cannot see —
# which logger at which level, one record per settled run, nothing logged for a
# run that was spared, and nothing logged again on the next pass.


def _reap_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if ORCHESTRATION_LEASE_EXPIRED_PREFIX in record.getMessage()
    ]


def test_the_reap_is_logged_as_a_warning_on_the_sweeps_own_logger(session, workspace, caplog):
    """A reason recorded below WARNING is recorded nowhere.

    `logger.debug`/`logger.info` sit under the default handler level, so a
    sweep that moves this line down still passes an assertion on
    `caplog.text` under an explicit `at_level` and tells an operator nothing in
    a live server. The logger's name is pinned for the same reason: AC9 asks
    for the sentence in *this* sweep's record, and a line emitted from some
    other module is not greppable where the criterion says to look.
    """
    run = _orch(session, _ticket(session, workspace, "T-level"), age=EXPIRED, last_seen=EXPIRED)

    with caplog.at_level(logging.DEBUG):
        settle_expired_orchestration_leases(session)

    records = _reap_records(caplog)
    assert [record.name for record in records] == ["loregarden.services.run_service"]
    assert [record.levelno for record in records] == [logging.WARNING]
    assert expired_lease_message(run.run_code, 0) in records[0].getMessage()


def test_each_settled_run_is_logged_once_with_its_own_count(session, workspace, caplog):
    """Three runs reaped in one pass, three records, three different counts.

    The AC8 hazard on the log side: a `message` built once before the loop, or
    a single summary line for the whole sweep, both leave `caplog.text`
    carrying a sentence that matches the first run and describes the others'
    subtrees wrongly. Counting records — which `caplog.text` cannot do — is
    what separates them.
    """
    parent = _ticket(session, workspace, "T-each-parent")
    parent_orch = _orch(session, parent, age=EXPIRED, last_seen=EXPIRED, run_code="orch_each_p")
    child = _ticket(session, workspace, "T-each-child", parent=parent)
    child_orch = _orch(session, child, age=EXPIRED, last_seen=EXPIRED, run_code="orch_each_c")
    grandchild = _ticket(session, workspace, "T-each-grand", parent=child)
    grandchild_orch = _orch(
        session, grandchild, age=EXPIRED, last_seen=EXPIRED, run_code="orch_each_g"
    )

    with caplog.at_level(logging.WARNING, logger="loregarden.services.run_service"):
        settled = settle_expired_orchestration_leases(session)

    assert {run.id for run in settled} == {parent_orch.id, child_orch.id, grandchild_orch.id}
    logged = [record.getMessage() for record in _reap_records(caplog)]
    assert len(logged) == 3
    for run, count in ((parent_orch, 2), (child_orch, 1), (grandchild_orch, 0)):
        expected = expired_lease_message(run.run_code, count)
        assert [line for line in logged if expected in line] != []


def test_a_spared_parent_is_not_logged_as_reaped(session, workspace, caplog):
    """The observed shape (AC1) logs nothing at all.

    A sweep that formats and logs the sentence for every candidate before
    testing the predicate passes both AC9 tests above and announces a reap for
    every healthy parent waiting on a child — in the one place an operator
    diagnosing a reap reads. The whole widening would then be invisible to the
    person it was done for.
    """
    parent_orch, _child_orch = _nested_pair(session, workspace)

    with caplog.at_level(logging.DEBUG):
        assert settle_expired_orchestration_leases(session) == []

    assert _reap_records(caplog) == []
    assert parent_orch.run_code not in caplog.text


def test_a_second_sweep_does_not_log_the_reap_again(session, workspace, caplog):
    """Reconcile runs on a timer, so the line has to be written once per reap.

    The run is FAILED after the first pass and out of the candidate query; a
    sweep that re-settles terminal runs would repeat this warning on every tick
    for the life of the server, and the duplicate is only visible by counting
    records across two calls.
    """
    _orch(session, _ticket(session, workspace, "T-twice"), age=EXPIRED, last_seen=EXPIRED)

    with caplog.at_level(logging.WARNING, logger="loregarden.services.run_service"):
        first = settle_expired_orchestration_leases(session)
        assert len(_reap_records(caplog)) == 1
        second = settle_expired_orchestration_leases(session)

    assert len(first) == 1
    assert second == []
    assert len(_reap_records(caplog)) == 1
