"""Subtree auto-mode helpers for the builtin orchestrator (ticket 164).

A parent ticket's auto_approve orchestration recurses through its whole
descendant subtree. The pieces that make that safe live here: the shared
subtree-wide stage budget, the auto-resolution of standard human gates (with
an audit trail), and the child ordering for sequential subtree runs.
"""

from __future__ import annotations

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    OrchestrationRun,
    StageStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select


class SubtreeBudget:
    """Shared, mutable stage counter for one top-level auto_approve run's whole
    subtree (ticket 164). Unlike ``max_stages_per_run`` — which resets every
    time ``execute()`` is called, including each nested child call — the same
    instance is threaded through every recursive ``execute()`` /
    ``_orchestrate_incomplete_children`` call in the tree, so the bound is
    enforced across the parent AND every descendant combined, not per ticket.

    A limit of 0 means unlimited: ``exhausted()`` is always False and
    ``consume()`` is a no-op, so callers never need a None check.
    """

    __slots__ = ("limit", "remaining")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.remaining = limit

    @classmethod
    def for_root(
        cls, existing: SubtreeBudget | None, profile: OrchestrationProfile
    ) -> SubtreeBudget:
        """The outermost call in a subtree creates the budget; nested calls
        receive and share the root's instance."""
        if existing is not None:
            return existing
        return cls(profile.max_subtree_stages_per_run)

    def exhausted(self) -> bool:
        return self.limit > 0 and self.remaining <= 0

    def consume(self, *, terminal: bool = False) -> None:
        """Count a completed stage. Terminal stages are free — finalizing a
        workflow must never be what the bound cuts off."""
        if self.limit > 0 and not terminal:
            self.remaining -= 1

    def pause_message(self, *, terminal: bool) -> str | None:
        if terminal or not self.exhausted():
            return None
        return f"Paused: subtree-wide stage bound ({self.limit}) reached"


def auto_resolve_awaiting_gate(
    session: Session, ticket: Ticket, orch_run: OrchestrationRun, stage_key: str
) -> bool:
    """Auto-resolve a pending WORKFLOW_GATE approval for ``stage_key`` under
    auto_approve, leaving the audit trail ``ApprovalService.auto_resolve``
    writes. Returns False (no-op) if there's no such pending approval — in
    particular this never touches a CLI_QUESTION approval, so an agent's
    clarifying question still pauses the run even in auto-mode.
    """
    approval = session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == stage_key,
            Approval.status == ApprovalStatus.PENDING,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).first()
    if not approval:
        return False
    ApprovalService(session).auto_resolve(approval.id, orchestration_run_id=orch_run.id)
    session.refresh(ticket)
    return True


def ticket_workflow_complete(orch: OrchestrationService, ticket: Ticket) -> bool:
    """Whether every required stage of the ticket's workflow is DONE/WONT_DO —
    complete children are skipped, not re-run, by a subtree pass."""
    instance, stages = orch._resolve_stages(ticket)
    if not instance or not stages:
        return True
    stage_map = parse_stage_map(instance, stages)
    required = [s for s in stages if not s.optional]
    return all(
        stage_map.get(s.key, StageStatus.PENDING) in (StageStatus.DONE, StageStatus.WONT_DO)
        for s in required
    )


def child_sort_key(ticket: Ticket) -> tuple:
    """Sequential subtree order: coarser work items first, then priority,
    then stable external id."""
    type_order = {
        WorkItemType.MILESTONE: 0,
        WorkItemType.FEATURE: 1,
        WorkItemType.CAPABILITY: 2,
        WorkItemType.TASK: 3,
        WorkItemType.BUG: 4,
    }
    return (type_order.get(ticket.work_item_type, 9), ticket.priority, ticket.external_id)
