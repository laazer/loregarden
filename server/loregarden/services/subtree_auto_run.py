"""Subtree auto-mode helpers for the builtin orchestrator (ticket 164).

A parent ticket's auto_approve orchestration recurses through its whole
descendant subtree. The pieces that make that safe live here: the shared
subtree-wide stage budget, the auto-resolution of standard human gates (with
an audit trail), and the child ordering for sequential subtree runs.
"""

from __future__ import annotations

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    OrchestrationRun,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.studio_routing import is_terminal_stage
from loregarden.services.ticket_rollup import reconcile_ancestors
from loregarden.services.ticket_state_service import choose
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, select


def finalize_aggregator_ticket(
    session: Session, orch: OrchestrationService, ticket: Ticket
) -> None:
    """Complete a parent ticket without running its own stages, once every child is
    done. Finalizes through the terminal stage when the workflow has one; falls back
    to marking it DONE directly when it doesn't (an older instance / template drift,
    which would otherwise raise "Workflow has no done stage" and block the parent).
    A parent whose own stage is mid-flight (RUNNING/AWAITING from a pre-aggregator
    run) is left as-is rather than force-finalized."""
    if ticket.state in StateMachine.TERMINAL_TICKET_STATES or ticket.workflow_stage_status in (
        StageStatus.RUNNING,
        StageStatus.AWAITING,
    ):
        return
    _, stages = orch._resolve_stages(ticket)
    if stages and any(is_terminal_stage(s) for s in stages):
        orch.finalize_workflow(ticket, force=True)
    else:
        mark_aggregator_done(session, orch, ticket)


def mark_aggregator_done(session: Session, orch: OrchestrationService, ticket: Ticket) -> None:
    """Complete an aggregator parent whose workflow has no terminal stage: settle
    every still-open stage as WONT_DO and set the ticket DONE. Used when
    finalize_workflow can't run because the instance lacks a done stage (an older
    instance or template drift) — the parent's children carry the work, so its own
    stage shape must not block it."""
    instance, stages = orch._resolve_stages(ticket)
    if instance and stages:
        stage_map = parse_stage_map(instance, stages)
        for stage in stages:
            if stage_map.get(stage.key) not in (StageStatus.DONE, StageStatus.WONT_DO):
                set_stage_status(ticket, instance, stages, stage.key, StageStatus.WONT_DO)
        session.add(instance)
    choose(session, ticket, TicketState.DONE, actor="orchestrator", emit=False)
    ticket.state_locked = True
    ticket.blocking_issues = ""
    session.add(ticket)
    session.commit()
    # This parent is itself a child: finishing a feature can finish the
    # milestone above it, and nothing else would notice.
    reconcile_ancestors(session, ticket)


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
    complete children are skipped, not re-run, by a subtree pass.

    A ticket already in a terminal state (DONE/WONT_DO) is complete regardless
    of its per-stage instance bookkeeping. A ticket can reach DONE without every
    stage instance ticked to DONE (manual completion, a review shortcut, an
    imported/older ticket, stage-map resets), and execute() short-circuits on
    that same terminal state (see the guard at the top of its loop) — so if this
    predicate disagreed, the subtree loop would judge a DONE child "incomplete",
    re-enter execute() which does nothing, still see "incomplete", and pause the
    whole subtree there, never advancing to the remaining siblings.
    """
    if ticket.state in (TicketState.DONE, TicketState.WONT_DO):
        return True
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
    """Stable tie-break order for siblings with no dependency constraint between
    them: coarser work items first, then priority, then external id. Actual run
    order is the dependency-aware topological sort in order_children_for_subtree;
    this only breaks ties among items that topo-sort as equally ready."""
    type_order = {
        WorkItemType.MILESTONE: 0,
        WorkItemType.FEATURE: 1,
        WorkItemType.CAPABILITY: 2,
        WorkItemType.TASK: 3,
        WorkItemType.BUG: 4,
    }
    return (type_order.get(ticket.work_item_type, 9), ticket.priority, ticket.external_id)


def order_children_for_subtree(
    children: list[Ticket], prereqs: dict[str, set[str]]
) -> list[Ticket]:
    """Best-effort dependency-aware run order for one parent's children (Kahn's
    algorithm). A child runs after every prerequisite that is also in this sibling
    set; ties among ready children break by ``child_sort_key``. Prerequisites
    outside the set are ignored (order-only, best-effort). A dependency cycle
    among siblings can't happen — edges are kept acyclic on insert — but if one
    somehow existed, the remaining children are emitted in child_sort_key order
    rather than dropped, so ordering degrades gracefully instead of hanging.
    """
    ids = {c.id for c in children}
    by_id = {c.id: c for c in children}
    remaining = {c.id: {p for p in prereqs.get(c.id, set()) if p in ids} for c in children}
    ordered: list[Ticket] = []
    done: set[str] = set()
    while len(ordered) < len(children):
        ready = [by_id[cid] for cid in ids if cid not in done and not (remaining[cid] - done)]
        if not ready:  # cycle / unsatisfiable — emit the rest deterministically
            ready = [by_id[cid] for cid in ids if cid not in done]
        nxt = min(ready, key=child_sort_key)
        ordered.append(nxt)
        done.add(nxt.id)
    return ordered
