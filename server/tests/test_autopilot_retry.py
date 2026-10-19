"""The autopilot picks a blocked ticket back up when a re-run can still clear it.

The plan graph reads every blocked ticket as BLOCKED and the autopilot only
picks READY work, so a block with a repair still available sat until a person
came back. These run the real orchestrator (agents faked, as in
`test_block_repair`) to pin that a retry actually re-runs the stage, and that
it never hands out a fresh budget or a second repair turn.
"""

from __future__ import annotations

from unittest.mock import patch

from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    BlockKind,
    InitiativePlan,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.autopilot_retry import retry_reason
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.initiative_autopilot import BREAKER_LIMIT, run_autopilot, set_autopilot
from loregarden.services.interruption_messages import INTERRUPTED_RUN_MESSAGE
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile, RetryBudgetConfig
from loregarden.services.queue_repair import unblock_for_rerun
from loregarden.services.stage_retry_budget import (
    record_stage_retry_block,
    resolve_stage_retry_budget,
    stage_retry_budget_state,
)
from loregarden.services.ticket_rollup import reconcile_ancestors
from loregarden.services.ticket_service import TicketService
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select
from tests.test_block_repair import (  # noqa: F401 - fixtures are used by name
    IMPLEMENT,
    REVIEWER,
    WORKER,
    _report,
    _script,
    ticket_fixture,
)
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _item,
    _queued,
    queue_fixture,
    workspaces_fixture,
)

PROFILE = OrchestrationProfile(slug="retry-test")
WORK_BLOCK = (RunStatus.SUCCEEDED, _report("blocked", kind="work"), "")
PASS = (RunStatus.SUCCEEDED, _report("pass"), "")


def _block_without_repair(session: Session, ticket: Ticket) -> None:
    """Block the stage on a run with auto-repair off: the repair turn stays unspent."""
    BuiltinOrchestrator(session).execute(ticket, PROFILE, max_stages=10, auto_repair=False)
    session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED


def _attempts(session: Session, ticket: Ticket) -> int:
    return stage_retry_budget_state(
        session, ticket, IMPLEMENT, resolve_stage_retry_budget(session, ticket)
    ).attempts


# ---- the retry itself, through the real orchestrator ----------------------


def test_a_retried_work_block_re_runs_the_stage_and_spends_its_repair_turn(
    db_session, ticket, monkeypatch
):
    dispatched = _script(monkeypatch, {WORKER: WORK_BLOCK, REPAIR_AGENT_ID: PASS})
    _block_without_repair(db_session, ticket)
    assert retry_reason(db_session, ticket) is not None

    unblock_for_rerun(db_session, ticket)
    BuiltinOrchestrator(db_session).execute(ticket, PROFILE, max_stages=10)

    db_session.refresh(ticket)
    # The worker again, blocking the same way; that block takes the repair turn.
    assert dispatched == [WORKER, WORKER, REPAIR_AGENT_ID, REVIEWER]
    assert ticket.state is not TicketState.BLOCKED


def test_queued_as_it_is_a_blocked_ticket_runs_nothing(db_session, ticket, monkeypatch):
    """Why the retry un-blocks first: the orchestration stops before its first stage."""
    dispatched = _script(monkeypatch, {WORKER: WORK_BLOCK, REPAIR_AGENT_ID: PASS})
    _block_without_repair(db_session, ticket)

    BuiltinOrchestrator(db_session).execute(ticket, PROFILE, max_stages=10)

    assert dispatched == [WORKER]


def test_un_blocking_keeps_the_stage_budget_already_spent(db_session, ticket, monkeypatch):
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    spent = _attempts(db_session, ticket)
    assert spent > 0

    unblock_for_rerun(db_session, ticket)

    db_session.refresh(ticket)
    assert ticket.state is not TicketState.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert ticket.block_kind is None
    assert _attempts(db_session, ticket) == spent


def test_a_stage_whose_repair_turn_is_spent_is_not_retried(db_session, ticket, monkeypatch):
    _script(monkeypatch, {WORKER: WORK_BLOCK, REPAIR_AGENT_ID: WORK_BLOCK})
    BuiltinOrchestrator(db_session).execute(ticket, PROFILE, max_stages=10)
    db_session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED

    assert retry_reason(db_session, ticket) is None


def test_a_decision_is_not_retried(db_session, ticket, monkeypatch):
    _script(
        monkeypatch,
        {WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="decision", options=["a"]), "")},
    )
    BuiltinOrchestrator(db_session).execute(ticket, PROFILE, max_stages=10)
    db_session.refresh(ticket)

    assert retry_reason(db_session, ticket) is None


def test_a_state_a_person_set_is_not_retried(db_session, ticket, monkeypatch):
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    ticket.state_locked = True
    db_session.add(ticket)
    db_session.commit()

    assert retry_reason(db_session, ticket) is None


def test_a_block_a_rollup_wrote_is_not_retried(db_session, ticket, monkeypatch):
    """`tickets` means another ticket must move first; re-running this one won't."""
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    ticket.block_kind = BlockKind.TICKETS
    db_session.add(ticket)
    db_session.commit()

    assert retry_reason(db_session, ticket) is None


def test_a_block_with_a_question_in_the_inbox_is_not_retried(db_session, ticket, monkeypatch):
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    db_session.add(
        Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.REWORK_PAUSE,
            title="Rework paused",
        )
    )
    db_session.commit()

    assert retry_reason(db_session, ticket) is None


def test_the_retry_breakers_own_block_is_not_retried(db_session, ticket, monkeypatch):
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    record_stage_retry_block(db_session, ticket.id, IMPLEMENT)

    assert retry_reason(db_session, ticket) is None


def test_a_stage_at_its_dispatch_budget_is_not_retried(db_session, ticket, monkeypatch):
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    spent = RetryBudgetConfig().model_copy(
        update={"max_attempts_per_stage": _attempts(db_session, ticket)}
    )
    monkeypatch.setattr(
        "loregarden.services.autopilot_retry.resolve_stage_retry_budget", lambda *_: spent
    )

    assert retry_reason(db_session, ticket) is None


def test_a_failed_stage_run_with_budget_left_is_retried(db_session, ticket, monkeypatch):
    dispatched = _script(
        monkeypatch, {WORKER: [(RunStatus.FAILED, "", "Error: something broke"), PASS]}
    )
    _block_without_repair(db_session, ticket)
    assert retry_reason(db_session, ticket) == "its stage run failed with retry budget left"

    unblock_for_rerun(db_session, ticket)
    BuiltinOrchestrator(db_session).execute(ticket, PROFILE, max_stages=10)

    db_session.refresh(ticket)
    assert dispatched == [WORKER, WORKER, REVIEWER]
    assert ticket.state is not TicketState.BLOCKED


# ---- the autopilot's side --------------------------------------------------


def _plan_root(session: Session, ticket: Ticket) -> Ticket:
    root = Ticket(
        external_id=f"root-{ticket.external_id}",
        workspace_id=ticket.workspace_id,
        title="Root",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(root)
    session.commit()
    ticket.parent_ticket_id = root.id
    session.add(ticket)
    session.commit()
    set_autopilot(session, root.id, AutopilotUpdate(enabled=True), actor="t")
    return root


def _events(session: Session, root: Ticket, action: AutopilotAction) -> list[str | None]:
    return list(
        session.exec(
            select(AutopilotEvent.ticket_id).where(
                AutopilotEvent.initiative_id == root.id, AutopilotEvent.action == action
            )
        ).all()
    )


def _block_again(session: Session, ticket: Ticket) -> None:
    session.refresh(ticket)
    ticket.state = TicketState.BLOCKED
    ticket.workflow_stage_status = StageStatus.BLOCKED
    ticket.block_kind = BlockKind.WORK
    session.add(ticket)
    session.commit()


def test_the_autopilot_retries_a_blocked_ticket_it_can_clear(
    db_session, ticket, monkeypatch, queue
):
    lanes, _ = queue
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    root = _plan_root(db_session, ticket)

    assert run_autopilot(db_session, root.id, now=NOW) == 1

    assert _queued(lanes) == [ticket.id]
    assert _events(db_session, root, AutopilotAction.RETRIED) == [ticket.id]
    db_session.refresh(ticket)
    assert ticket.state is not TicketState.BLOCKED


def test_it_retries_a_ticket_once_each_time_it_is_turned_on(db_session, ticket, monkeypatch, queue):
    lanes, _ = queue
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    root = _plan_root(db_session, ticket)
    run_autopilot(db_session, root.id, now=NOW)
    _block_again(db_session, ticket)

    assert run_autopilot(db_session, root.id, now=NOW) == 0

    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=False), actor="t")
    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=True), actor="t")
    assert run_autopilot(db_session, root.id, now=NOW) == 1
    assert _queued(lanes) == [ticket.id, ticket.id]


def _blocked_children(session: Session, workspace: Workspace) -> tuple[Ticket, list[Ticket]]:
    root = TicketService(session).create_ticket(
        workspace_slug=workspace.slug, title="Root", work_item_type=WorkItemType.MILESTONE
    )
    children = []
    for n in range(BREAKER_LIMIT):
        child = TicketService(session).create_ticket(
            workspace_slug=workspace.slug,
            title=f"b{n}",
            parent_ticket_id=root.id,
            work_item_type=WorkItemType.FEATURE,
        )
        child.state = TicketState.BLOCKED
        session.add(child)
        children.append(child)
    session.commit()
    set_autopilot(session, root.id, AutopilotUpdate(enabled=True), actor="t")
    return root, children


def _tick(session: Session, root: Ticket, *, retryable: bool) -> None:
    with (
        patch(
            "loregarden.services.initiative_autopilot.retry_reason",
            return_value="r" if retryable else None,
        ),
        patch("loregarden.services.initiative_autopilot.prepare_rerun", return_value=""),
    ):
        run_autopilot(session, root.id, now=NOW)


def test_a_block_it_has_yet_to_retry_does_not_trip_the_breaker(db_session, workspaces, queue):
    here, _ = workspaces
    root, children = _blocked_children(db_session, here)
    for child in children:
        db_session.add(
            AutopilotEvent(
                initiative_id=root.id, action=AutopilotAction.DISPATCHED, ticket_id=child.id
            )
        )
    db_session.commit()

    _tick(db_session, root, retryable=True)
    assert db_session.get(InitiativePlan, root.id).autopilot is True

    _tick(db_session, root, retryable=False)
    assert db_session.get(InitiativePlan, root.id).autopilot is False


def test_a_ticket_it_retried_that_blocks_again_trips_the_breaker(db_session, workspaces, queue):
    """Blocked before the autopilot ever queued them: only the retry is its start."""
    here, _ = workspaces
    root, _ = _blocked_children(db_session, here)

    _tick(db_session, root, retryable=True)
    assert _events(db_session, root, AutopilotAction.RETRIED)
    assert db_session.get(InitiativePlan, root.id).autopilot is True

    _tick(db_session, root, retryable=True)
    assert db_session.get(InitiativePlan, root.id).autopilot is False


def test_a_ticket_it_cannot_un_block_is_refused_out_loud_not_queued_blocked(
    db_session, ticket, monkeypatch, queue
):
    lanes, _ = queue
    _script(monkeypatch, {WORKER: WORK_BLOCK})
    _block_without_repair(db_session, ticket)
    root = _plan_root(db_session, ticket)
    ticket.workflow_disabled = True
    db_session.add(ticket)
    db_session.commit()

    with patch("loregarden.services.initiative_autopilot.retry_reason", return_value="r"):
        assert run_autopilot(db_session, root.id, now=NOW) == 0

    assert lanes.add_to_lane.call_count == 0
    assert _events(db_session, root, AutopilotAction.REFUSED) == [ticket.id]
    db_session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED


# ---- parents: a paused or interrupted parent run is picked back up ---------


def _tree(session: Session, workspace: Workspace) -> tuple[Ticket, Ticket, Ticket]:
    """A plan root, a parent feature under it, and one child under that."""
    root = TicketService(session).create_ticket(
        workspace_slug=workspace.slug, title="Root", work_item_type=WorkItemType.MILESTONE
    )
    parent = _item(session, workspace, root, "parent")
    child = _item(session, workspace, parent, "child")
    orch = OrchestrationService(session)
    for ticket in (parent, child):
        orch.ensure_workflow_instance(ticket, commit=True)
    set_autopilot(session, root.id, AutopilotUpdate(enabled=True), actor="t")
    return root, parent, child


def _block_stage(session: Session, ticket: Ticket, *, message: str, kind: BlockKind) -> None:
    _, stages = resolve_ticket_stages(session, ticket)
    instance = workflow_instance_for(session, ticket.id)
    set_stage_status(ticket, instance, stages, ticket.workflow_stage_key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    ticket.block_kind = kind
    ticket.blocking_issues = message
    session.add(instance)
    session.add(ticket)
    session.commit()
    reconcile_ancestors(session, ticket)


def test_retrying_a_child_lifts_the_block_the_rollup_put_on_its_parent(
    db_session, workspaces, queue
):
    """The parent was blocked only because its child was. Once the child is
    un-blocked for its retry, the parent must not still read blocked, or it
    looks stuck and counts toward the breaker for a block that is gone."""
    here, _ = workspaces
    root, parent, child = _tree(db_session, here)
    _block_stage(db_session, child, message="tests fail", kind=BlockKind.WORK)
    db_session.refresh(parent)
    assert parent.state is TicketState.BLOCKED
    assert parent.block_kind is BlockKind.TICKETS

    with patch("loregarden.services.initiative_autopilot.retry_reason", return_value="r"):
        run_autopilot(db_session, root.id, now=NOW)

    assert _queued(queue[0]) == [child.id]
    db_session.refresh(parent)
    assert parent.state is not TicketState.BLOCKED


def test_a_paused_parent_with_nothing_running_under_it_is_started_again(
    db_session, workspaces, queue
):
    """A parent's run pauses (ends SUCCEEDED) on a held child or its stage limit,
    leaving the parent in progress with no run. Nothing resumes that drive but
    the autopilot."""
    here, _ = workspaces
    root, parent, child = _tree(db_session, here)
    child.state = TicketState.DONE
    parent.state = TicketState.IN_PROGRESS
    db_session.add(child)
    db_session.add(parent)
    db_session.commit()

    run_autopilot(db_session, root.id, now=NOW)

    assert _queued(queue[0]) == [parent.id]


def test_an_interrupted_parent_run_is_retried_without_rewinding_its_stage(
    db_session, workspaces, queue
):
    """The orchestrator recovers an interrupted stage itself, resuming rather than
    restarting it, so the retry queues the parent as it is."""
    here, _ = workspaces
    root, parent, child = _tree(db_session, here)
    child.state = TicketState.DONE
    db_session.add(child)
    db_session.commit()
    _block_stage(db_session, parent, message=INTERRUPTED_RUN_MESSAGE, kind=BlockKind.HARNESS)
    stage = parent.workflow_stage_key

    run_autopilot(db_session, root.id, now=NOW)

    assert _queued(queue[0]) == [parent.id]
    assert _events(db_session, root, AutopilotAction.RETRIED) == [parent.id]
    db_session.refresh(parent)
    assert parent.workflow_stage_key == stage
    assert parent.workflow_stage_status is StageStatus.BLOCKED
