"""Repro for lg-workflow-integrity-698: what does a repeated complete_stage do?

Written repro-first and deliberately: the ticket was filed from reading the
code, and the last two tickets filed that way (693, and 696's third criterion)
both changed shape on contact with real data. `apply_stage_route`'s `strict`
validates that the stage KEY exists, not the stage's STATUS, so on paper a
second complete_stage for an already-done stage re-routes and advances the
cursor again.

The shape matters because complete_stage is the write lg-workflow-integrity-687
showed being lost under load, so the retry that would trigger this is not
hypothetical.
"""

from __future__ import annotations

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select


def _setup(session: Session, external_id: str):
    sync_workflow_templates(session)
    template = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    stages = get_template_stages(template)
    first = stages[0].key

    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title="repeated complete_stage",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=first,
        workflow_stage_status=StageStatus.RUNNING,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=first,
            stages_json=initial_stages_json(stages),
        )
    )
    orch = OrchestrationRun(
        run_code=f"orch_{external_id}",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key=first,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(orch)
    session.commit()
    session.refresh(orch)
    return ticket, orch, first, [s.key for s in stages]


def test_a_repeated_complete_stage_does_not_advance_the_cursor_twice(db_session: Session):
    """The defect, stated as the behaviour that should hold.

    A caller that cannot tell whether its write committed — the exact position
    lg-workflow-integrity-696 documented, twice in one recovery — retries. The
    second call names a stage that is already done. It must not move the ticket
    on again.
    """
    ticket, orch, first, order = _setup(db_session, "repeat-698")
    svc = OrchestrationCallbackService(db_session)

    svc.complete_stage(orch, ticket, stage_key=first, outcome="pass")
    db_session.refresh(ticket)
    after_first = ticket.workflow_stage_key
    assert after_first != first, "the first call should have advanced the cursor"

    svc.complete_stage(orch, ticket, stage_key=first, outcome="pass")
    db_session.refresh(ticket)
    after_second = ticket.workflow_stage_key

    assert after_second == after_first, (
        f"a repeated complete_stage({first}) moved the cursor again: "
        f"{after_first} -> {after_second} (stage order: {order[:5]})"
    )


def test_what_makes_it_idempotent_is_named(db_session: Session):
    """AC2: the guard, identified rather than assumed.

    It is not `strict` — that only rejects an unknown stage key. It is that
    `apply_stage_route` routes by looking up the transition for `from_key` and
    then `reconcile_ticket` recomputes the cursor from the stage MAP. The map
    already records `first` as done and the next stage as pending, so a second
    pass over the same input lands on the same answer. The operation is a
    recomputation from state, not an increment.

    Pinned by asserting the stage map itself is unchanged by the repeat: if
    anyone later makes routing incremental, this fails before the cursor test
    above does, and says why.
    """
    ticket, orch, first, _order = _setup(db_session, "repeat-698-map")
    svc = OrchestrationCallbackService(db_session)

    svc.complete_stage(orch, ticket, stage_key=first, outcome="pass")
    db_session.refresh(ticket)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    db_session.refresh(instance)
    map_after_first = instance.stages_json

    svc.complete_stage(orch, ticket, stage_key=first, outcome="pass")
    db_session.refresh(instance)

    assert instance.stages_json == map_after_first, (
        "the repeat changed the stage map; routing has become incremental "
        "rather than a recomputation from state"
    )
