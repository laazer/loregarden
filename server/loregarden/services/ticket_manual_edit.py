"""Applying a human's (or an agent's) direct edits to a ticket.

Split out of `services.orchestration`, which had reached its 1500-line cap. The
cluster is a single concern — everything reachable from the PATCH endpoint and
`loregarden_update_ticket` — and it was also the part of that module growing
fastest, so it is the right piece to move rather than the most convenient one.

These are free functions taking the `OrchestrationService` rather than methods,
because the service is what owns the session and the workflow helpers they need
(`_resolve_stages`, `_reconcile_workflow`, `refresh_stage_retry_budget`). The
service keeps a thin `update_ticket_manual` delegate so every existing caller —
`api/tickets.py`, `mcp/ticket_ops_tools.py`, `mcp/ticket_edit_tools.py` — is
unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from loregarden.core.event_bus import EventType, event_bus
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.compatibility_posture import apply_compatibility_posture
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.hierarchy_service import reparent_ticket
from loregarden.services.ticket_rollup import reconcile_ancestors
from loregarden.services.ticket_state_service import choose
from loregarden.services.ticket_tags import serialize_tags
from loregarden.services.workflow_service import WorkflowService
from loregarden.services.workflow_state import (
    parse_stage_map,
    parse_stage_notes,
    serialize_stage_map,
    set_stage_status,
)
from loregarden.services.worktree_lifecycle import release_ticket_worktree

if TYPE_CHECKING:
    from loregarden.services.orchestration import OrchestrationService


def _content_fields(ticket: Ticket) -> tuple[str, str, str, str]:
    """The stored form of every field a content edit can touch.

    Snapshotting once before and once after is what lets each field below be a
    plain assignment: no per-field compare-then-flag, and no way to add a field
    to the edit path but forget to make it bump the revision.
    """
    return (
        ticket.title,
        ticket.description,
        ticket.acceptance_criteria_json,
        ticket.tags_json,
    )


def _apply_content_edits(ticket: Ticket, body: UpdateTicketRequest) -> bool:
    """The fields whose change earns a revision bump. Returns whether any did."""
    before = _content_fields(ticket)

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise ValueError("Title cannot be empty")
        ticket.title = title

    if body.description is not None:
        ticket.description = body.description

    if body.acceptance_criteria is not None:
        ticket.acceptance_criteria_json = serialize_criteria(body.acceptance_criteria)

    if body.tags is not None:
        ticket.tags_json = serialize_tags(body.tags)

    return _content_fields(ticket) != before


def _apply_operator_edits(ticket: Ticket, body: UpdateTicketRequest) -> None:
    """Apply the fields a human edits directly (title, description, criteria, posture).

    Module-level rather than another branch inside update_ticket_manual, which is
    already well past its statement budget.
    """
    content_updated = _apply_content_edits(ticket, body)

    if body.priority is not None:
        if body.priority < 1 or body.priority > 3:
            raise ValueError("Priority must be between 1 and 3")
        if ticket.priority != body.priority:
            ticket.priority = body.priority
            content_updated = True

    if body.compatibility_posture is not None:
        apply_compatibility_posture(ticket, body.compatibility_posture)

    if body.git_automation is not None:
        # serialize_override drops unknown keys and stores "" for an empty
        # override, so {} means "inherit the workspace policy again".
        ticket.git_automation_json = serialize_override(body.git_automation)

    if content_updated:
        ticket.revision += 1
        ticket.last_updated_by = "human"


def _apply_workflow_template(
    orch: OrchestrationService, ticket: Ticket, body: UpdateTicketRequest
) -> None:
    """Attach, replace or clear the ticket's workflow template.

    An empty slug means "clear it", which is why the field is `str | None` and
    not just `str`: None is "leave it alone". Extracted so `update_ticket_manual`
    stays a flat list of the fields a caller may set.

    Imported at module level, unlike the copy this replaced: the function-local
    import existed only because `services.orchestration` and
    `services.workflow_service` could not import each other, and this module has
    no such cycle.
    """
    if body.workflow_template_slug is None:
        return
    wf = WorkflowService(orch.session)
    if not body.workflow_template_slug.strip():
        wf.clear_ticket_workflow(ticket)
    else:
        wf.set_ticket_workflow_template(ticket, body.workflow_template_slug)
    orch.session.refresh(ticket)


def _apply_state_edit(
    orch: OrchestrationService, ticket: Ticket, body: UpdateTicketRequest
) -> None:
    """Set the ticket's state, and decide whether that choice is pinned.

    `state_locked` is how an operator says "I decided this", which stops
    `derive` recomputing it out from under them. Naming a state pins it;
    `auto_state=True` releases it; `wont_do` pins it hard, because abandoning is
    a statement about the ticket rather than a tally of its parts.
    """
    if body.auto_state is True:
        ticket.state_locked = False
    elif body.auto_state is False or body.state is not None:
        ticket.state_locked = True

    if body.state is None:
        return
    # The one writer that took any value from the API and wrote it unchecked.
    # `choose` validates it as a move somebody decided on.
    choose(orch.session, ticket, body.state, actor="human", emit=False)
    if body.state == TicketState.WONT_DO:
        ticket.state_locked = True


def update_ticket_manual(
    orch: OrchestrationService, ticket: Ticket, body: UpdateTicketRequest
) -> Ticket:
    _apply_operator_edits(ticket, body)

    _apply_workflow_template(orch, ticket, body)

    instance, stages = orch._resolve_stages(ticket)
    _apply_state_edit(orch, ticket, body)

    if body.parent_ticket_id is not None:
        reparent_ticket(orch.session, ticket, body.parent_ticket_id.strip() or None)

    if body.branch is not None:
        ticket.branch = body.branch.strip()
        ticket.revision += 1
        ticket.last_updated_by = "human"

    _apply_manual_stage_edits(orch, ticket, instance, stages, body)

    ticket.updated_at = datetime.now(timezone.utc)
    if instance:
        orch.session.add(instance)
    orch.session.add(ticket)
    orch.session.commit()

    if body.state is not None:
        _settle_manual_state_change(orch, ticket)
    return ticket


def _apply_manual_stage_edits(
    orch: OrchestrationService,
    ticket: Ticket,
    instance: WorkflowInstance | None,
    stages: list[WorkflowStageDef],
    body: UpdateTicketRequest,
) -> None:
    """The stage half of a manual ticket edit.

    Four mutually exclusive spellings of "move the workflow cursor", in
    precedence order: a batch of stage updates, one stage set explicitly,
    the workflow_stage_* pair, or nothing but a request to recompute.
    Extracted so `update_ticket_manual` stays a readable list of the fields
    a caller may set — the chain is one concern, and it was most of the
    function's complexity.
    """
    if body.stage_updates and instance and stages:
        _apply_manual_stage_updates(
            orch,
            ticket,
            instance,
            stages,
            body.stage_updates,
            auto_state=body.auto_state is True or not ticket.state_locked,
        )
        return
    if body.stage_key and body.stage_status and instance and stages:
        set_stage_status(ticket, instance, stages, body.stage_key, body.stage_status)
        if body.stage_status == StageStatus.PENDING:
            orch.refresh_stage_retry_budget(ticket, body.stage_key)
        return
    if not (instance and stages):
        return
    if not (body.workflow_stage_key or body.workflow_stage_status):
        if body.auto_state is True or not ticket.state_locked:
            orch._reconcile_workflow(ticket, instance, stages)
        return

    _move_workflow_cursor(ticket, instance, stages, body)
    if body.auto_state is True or not ticket.state_locked:
        orch._reconcile_workflow(ticket, instance, stages)


def _move_workflow_cursor(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    body: UpdateTicketRequest,
) -> None:
    """Point the workflow at a stage, and record the status it is in there.

    The `workflow_stage_key` / `workflow_stage_status` pair, which is the
    operator-facing spelling of "put this ticket on that stage". Split from
    the dispatch above so each stays under the complexity cap; the caller
    owns whether a reconcile follows, because that is true of every branch
    rather than this one.
    """
    stage_map = parse_stage_map(instance, stages)
    if body.workflow_stage_key:
        if body.workflow_stage_key not in stage_map:
            raise ValueError(f"Unknown stage key: {body.workflow_stage_key}")
        ticket.workflow_stage_key = body.workflow_stage_key
    if body.workflow_stage_status:
        ticket.workflow_stage_status = body.workflow_stage_status
    key = ticket.workflow_stage_key
    if key in stage_map:
        stage_map[key] = ticket.workflow_stage_status
        instance.stages_json = serialize_stage_map(
            stage_map, stages, notes=parse_stage_notes(instance)
        )
    instance.current_stage_key = ticket.workflow_stage_key


def _settle_manual_state_change(orch: OrchestrationService, ticket: Ticket) -> None:
    """What follows a human (or MCP) setting a ticket's state by hand."""
    # Abandoning or finishing a ticket retires its tree too; otherwise the
    # directory and its branch checkout outlive every reason they existed.
    release_ticket_worktree(orch.session, ticket)
    # Closing the last open child finishes its parent, and a human closing
    # it by hand is no different from an agent doing so.
    reconcile_ancestors(orch.session, ticket)
    event_bus.publish(
        orch.session,
        EventType.TICKET_STATE_CHANGED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={"state": ticket.state.value, "manual": True},
    )


def _apply_manual_stage_updates(
    orch: OrchestrationService,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    stage_updates: dict[str, StageStatus],
    *,
    auto_state: bool,
) -> None:
    stage_map = parse_stage_map(instance, stages)
    for key, status in stage_updates.items():
        if key not in stage_map:
            raise ValueError(f"Unknown stage key: {key}")
        stage_map[key] = status
        if status == StageStatus.PENDING:
            orch.refresh_stage_retry_budget(ticket, key)
    instance.stages_json = serialize_stage_map(stage_map, stages, notes=parse_stage_notes(instance))
    if auto_state:
        orch._reconcile_workflow(ticket, instance, stages)
