"""A run orphaned mid-flight must not spend a stage retry attempt.

`lg-workflow-integrity-697`. The retry budget is charged PRE-dispatch and never
reconciled against the outcome, so a run that was dispatched legitimately,
produced nothing, and was then killed because its parent orchestration went
terminal had still consumed one of the stage's attempts.

Three such runs are recorded live — `run_57700a`, `run_02de25`, `run_44db18` —
each with zero bytes of output, each STARTED BEFORE its parent finished (by 20,
41 and 78 minutes). They were not misdispatched; the parent was blocked
underneath them. Their fixture shape is reproduced here rather than invented.

The breaker exists to stop a stage looping on work that keeps failing. An
orphan is not that: the stage never got to fail on its merits, because nothing
ran. Spending an attempt on it walks a healthy stage toward a circuit breaker
that then costs an operator a deliberate requeue and a reason string to clear.
"""

from __future__ import annotations

from datetime import timedelta

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
)
from loregarden.models.domain.enums import StageBudgetArtifactKind
from loregarden.services.run_service import settle_orphaned_agent_runs
from loregarden.services.stage_retry_budget import (
    count_stage_dispatches,
    record_stage_dispatch,
)
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

STAGE = "implement"


def _orphan_fixture(
    session: Session,
    name: str,
    *,
    parent_status: OrchestrationRunStatus = OrchestrationRunStatus.BLOCKED,
    run_status: RunStatus = RunStatus.RUNNING,
) -> tuple[Ticket, AgentRun]:
    """A run in flight beneath a parent that has already gone terminal.

    Built in a helper because the rows have to be real: foreign keys are
    enforced on this engine, so a made-up orchestration id is rejected rather
    than quietly stored.
    """
    ticket = make_workspace_ticket(session, name)
    parent = OrchestrationRun(
        run_code=f"orch_{name}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=parent_status,
    )
    session.add(parent)
    session.commit()
    session.refresh(parent)

    # The charge the dispatch pass made, before the run existed.
    record_stage_dispatch(session, ticket.id, STAGE)

    run = AgentRun(
        run_code=f"run_{name}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=STAGE,
        status=run_status,
        orchestration_run_id=parent.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return ticket, run


def test_an_orphaned_run_hands_its_attempt_back(db_session: Session):
    """AC1. The whole ticket."""
    ticket, _ = _orphan_fixture(db_session, "orphan-1")
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1

    settled = settle_orphaned_agent_runs(db_session)

    assert len(settled) == 1
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0


def test_a_run_that_failed_on_its_own_work_still_pays(db_session: Session):
    """AC2, and the reason the refund is narrow.

    If a refund widened to every failure the breaker would stop working, which
    is the unbounded redispatch this whole module exists to prevent. A run whose
    parent is still live is not orphaned, so the sweep does not touch it and its
    attempt stands.
    """
    ticket, _ = _orphan_fixture(
        db_session, "orphan-2", parent_status=OrchestrationRunStatus.RUNNING
    )
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1

    settled = settle_orphaned_agent_runs(db_session)

    assert settled == []
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1, (
        "a live parent means the run is still working; its attempt must stand"
    )


def test_a_second_sweep_does_not_credit_the_stage_twice(db_session: Session):
    """AC3. Idempotent by construction rather than by a flag: the sweep selects
    only runs still IN FLIGHT, and the first pass made this one terminal."""
    ticket, _ = _orphan_fixture(db_session, "orphan-3")
    record_stage_dispatch(db_session, ticket.id, STAGE)  # a second, real attempt
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 2

    settle_orphaned_agent_runs(db_session)
    after_first = count_stage_dispatches(db_session, ticket.id, STAGE)
    settle_orphaned_agent_runs(db_session)
    after_second = count_stage_dispatches(db_session, ticket.id, STAGE)

    assert after_first == after_second, "the second sweep refunded again"


def _dispatch_markers(session: Session, ticket_id: str) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                Artifact.ticket_id == ticket_id,
                Artifact.kind == StageBudgetArtifactKind.DISPATCH,
            )
        ).all()
    )


def test_the_refund_takes_this_run_s_charge_not_a_later_one(db_session: Session):
    """The hazard `refund_stage_dispatch_budget` documents, arriving through a
    different door.

    That helper drops the NEWEST marker, which is exact only for a caller
    refunding inside its own pass. The sweeper runs on the reconcile timer, so a
    newer pass may have charged by then; dropping the newest there would credit
    an attempt that is still live and quietly extend a spent budget.

    Asserted on marker IDS, not timestamps: SQLite returns these naive, so a
    tz-aware comparison silently answers False and the test fails for a reason
    that has nothing to do with the behaviour. The first version of this did
    exactly that.
    """
    ticket, run = _orphan_fixture(db_session, "orphan-4")
    charged_for_this_run = _dispatch_markers(db_session, ticket.id)
    assert len(charged_for_this_run) == 1
    this_runs_marker = charged_for_this_run[0].id

    # A later pass charges after the orphaned run began.
    record_stage_dispatch(db_session, ticket.id, STAGE)
    later_marker = next(
        m.id for m in _dispatch_markers(db_session, ticket.id) if m.id != this_runs_marker
    )
    later = db_session.get(Artifact, later_marker)
    later.created_at = run.created_at + timedelta(minutes=5)
    db_session.add(later)
    db_session.commit()

    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 2
    settle_orphaned_agent_runs(db_session)

    surviving = {m.id for m in _dispatch_markers(db_session, ticket.id)}
    assert surviving == {later_marker}, (
        "the sweeper refunded the wrong attempt: this run's charge must go and "
        "the later pass's charge must stand"
    )


def test_a_run_with_no_stage_key_is_settled_without_a_refund(db_session: Session):
    """Chat turns and branch triage carry no stage, so there is no budget to
    hand back and no title to look one up by."""
    ticket, run = _orphan_fixture(db_session, "orphan-5")
    run.stage_key = ""
    db_session.add(run)
    db_session.commit()

    settled = settle_orphaned_agent_runs(db_session)

    assert len(settled) == 1
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1
