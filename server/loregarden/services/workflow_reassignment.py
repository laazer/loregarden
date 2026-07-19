"""What a workflow reassignment would destroy, so it can be shown before it happens.

Changing a ticket's workflow rewrites its stage map and resets the cursor to the
first stage. That is the right behaviour at commit time, when the ticket is
seconds old, and quietly destructive on a ticket that has been running for days.

This reports the loss rather than preventing it: an operator moving a ticket
onto a different pipeline usually means it, and a block would just be worked
around. What they need is to know the cost first.
"""

from __future__ import annotations

from loregarden.core.workflow_loader import get_template_stages
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowTemplate,
)
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select

#: Statuses representing work that actually happened. WONT_DO is deliberately
#: absent — a skipped stage loses nothing by being reset.
_RESOLVED = (StageStatus.DONE, StageStatus.AWAITING, StageStatus.BLOCKED, StageStatus.RUNNING)


def describe_reassignment(
    session: Session, ticket: Ticket, template_slug: str
) -> dict[str, object]:
    """What changing this ticket to `template_slug` would cost.

    `destructive` is the question a caller should gate a confirmation on: it is
    False for a ticket that has not started, so a fresh ticket does not prompt.
    """
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    target = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == template_slug)
    ).first()

    summary: dict[str, object] = {
        "destructive": False,
        "current_stage_key": "",
        "current_template_slug": "",
        "target_template_slug": template_slug,
        "target_template_name": target.name if target else "",
        "completed_stages": [],
        "resets_to_stage_key": "",
    }
    if not instance:
        # Nothing to lose: the ticket has no workflow yet.
        return summary

    current = session.get(WorkflowTemplate, instance.template_id)
    summary["current_template_slug"] = current.slug if current else ""
    summary["current_stage_key"] = instance.current_stage_key or ""

    if target:
        target_stages = get_template_stages(target)
        if target_stages:
            summary["resets_to_stage_key"] = min(target_stages, key=lambda s: s.order).key

    if not current:
        return summary

    stages = get_template_stages(current)
    stage_map = parse_stage_map(instance, stages)
    completed = [
        stage.key
        for stage in sorted(stages, key=lambda s: s.order)
        if stage_map.get(stage.key) in _RESOLVED
    ]
    summary["completed_stages"] = completed
    # Reassigning the template a ticket is already on still rewrites its stage
    # map, so it is destructive on the same terms.
    summary["destructive"] = bool(completed)
    return summary
