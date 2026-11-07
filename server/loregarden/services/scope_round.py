"""The scope round: a ticket's spec owner decides every finding filed against it.

Findings reach a ticket from its review lanes, its test-break, its gate. The
reviewer who found one is the wrong person to decide whether it is this
ticket's work. That decision is the spec's, because the spec is what says
what this ticket's work *is*. Before this, the reviewer decided by default,
since "file a ticket and pass" had one outcome: a new ticket.

So before a ticket enters a gate or terminal stage with findings undecided,
its spec stage is reopened, and only that stage. The spec agent decides each
finding (`decide_finding`), and its pass is the round's end:

- With no ``AMEND``, the stages after spec stay done and the ticket goes on
  to the stage it was about to enter. A round that changes nothing costs one
  spec run.
- An ``AMEND`` adds a criterion, and a new criterion invalidates the tests
  and the code downstream of the spec. So every stage after spec reopens.

Rounds converge for two reasons. `MAX_AMEND_ROUNDS` bounds how many times a
ticket may widen itself. And a round whose brief repeats the last one's word
for word is what a spec agent that decides nothing looks like, so its findings
are deferred, recorded on the ticket's history, and the ticket goes on.
"""

from __future__ import annotations

from loregarden.agents.registry import SPEC_AGENT_ID
from loregarden.models.domain import (
    FindingDecision,
    OrchestrationRun,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    TicketDiscovery,
    TicketState,
    UpdateTicketRequest,
    WorkflowInstance,
    WorkflowStageDef,
    utcnow,
)
from loregarden.models.domain.stage_types import StageType
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.discovered_work import undecided, undecided_findings_of
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.rework_feedback import latest_rework_context, record_rework_feedback
from loregarden.services.studio_routing import is_terminal_stage
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    reconcile_workflow_state,
    set_stage_status,
)
from sqlmodel import Session, col, func, select

#: Rounds in which a ticket may amend its own criteria. One round is the spec
#: missing an instance of its own class; a second is a reviewer seeing what
#: the first amendment exposed. Past that, a finding is a follow-up, a
#: deferral or a drop: the ticket has said what it is.
MAX_AMEND_ROUNDS = 2

#: Who defers findings a spec would not decide.
DEFERRED_BY = "control_plane"


class ScopeRoundStuck(ValueError):
    """A round asked for the same decisions twice against the same tree."""


def spec_stage(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """The stage the spec agent runs, which owns the ticket's scope."""
    return next((s for s in stages if s.agent_id == SPEC_AGENT_ID), None)


def round_due(
    session: Session, ticket: Ticket, stages: list[WorkflowStageDef], target: WorkflowStageDef
) -> bool:
    """Whether ``ticket`` must decide its findings before it enters ``target``."""
    if target.stage_type != StageType.GATE and not is_terminal_stage(target):
        return False
    spec = spec_stage(stages)
    if spec is None or spec.order >= target.order:
        return False
    return bool(undecided_findings_of(session, ticket))


def _brief(session: Session, ticket: Ticket, target: WorkflowStageDef) -> str:
    findings = undecided_findings_of(session, ticket)
    listed = "\n".join(f"- {filed.external_id}: {filed.title}" for _, filed in findings)
    return (
        f"Scope round before '{target.key}': runs filed {len(findings)} finding(s) against "
        f"{ticket.external_id}, and its spec decides whether each is this ticket's work.\n\n"
        f"{listed}\n\n"
        "Read each one (loregarden_get_ticket) and decide it with loregarden_decide_finding: "
        "amend (it is this ticket's work; give the criterion), follow_up (its parent's work), "
        "defer (not this tree's work) or drop. Findings about the tests, guards or "
        "fixtures this ticket built are its own work: amend. Add anything you rule out of "
        "scope to the ticket's out_of_scope list with the reason. Report pass when every "
        "finding is decided."
    )


def open_round(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    target: WorkflowStageDef,
) -> None:
    """Reopen the spec stage alone, with the findings as its brief. Does not commit.

    Raises `ScopeRoundStuck` when the previous round asked for exactly this:
    every finding it listed is still undecided and none was added, so that
    spec decided nothing. The brief is compared, not the tree: a round's
    progress is its decisions, which change the brief and not the code.
    """
    spec = spec_stage(stages)
    if spec is None:
        raise ValueError(f"{ticket.external_id}'s workflow has no spec stage")
    brief = _brief(session, ticket, target)
    if latest_rework_context(session, ticket, spec.key) == brief:
        # The same findings, still undecided, after a round asked for them:
        # that spec decided nothing, and asking again will not differ.
        raise ScopeRoundStuck(
            f"{ticket.external_id}'s spec stage was asked to decide these findings and decided "
            "none of them. Decide them with loregarden_decide_finding, then requeue."
        )
    record_rework_feedback(
        session, ticket, target_stage=spec.key, from_stage=target.key, context=brief
    )
    set_stage_status(ticket, instance, stages, spec.key, StageStatus.PENDING, note="scope round")
    reconcile_workflow_state(ticket, instance, stages, persist=False)
    session.add_all([ticket, instance])


def _amend_rounds(session: Session, source_id: str) -> int:
    return session.exec(
        select(func.count(func.distinct(TicketDiscovery.decided_by))).where(
            TicketDiscovery.source_ticket_id == source_id,
            TicketDiscovery.decision == FindingDecision.AMEND,
            col(TicketDiscovery.decided_by).startswith("run:"),
        )
    ).one()


def _reopen_after_spec(orch: OrchestrationService, source: Ticket) -> None:
    """Every stage after spec back to pending: a new criterion invalidates them."""
    instance = workflow_instance_for(orch.session, source.id)
    _, stages = resolve_ticket_stages(orch.session, source)
    spec = spec_stage(stages)
    if instance is None or spec is None:
        return
    for stage in sorted(stages, key=lambda s: s.order):
        if stage.order > spec.order:
            set_stage_status(source, instance, stages, stage.key, StageStatus.PENDING)
    orch.session.add_all([source, instance])


def decide_finding(
    session: Session,
    finding: Ticket,
    decision: FindingDecision,
    *,
    reason: str,
    decided_by: str,
    criterion: str = "",
) -> TicketDiscovery:
    """Record the scope decision on one finding and apply it. Commits.

    ``criterion`` is required for AMEND: it is what the source ticket will be
    held to. ``decided_by`` is ``run:<id>`` for a spec run, which is what
    `MAX_AMEND_ROUNDS` counts.
    """
    discovery = undecided(session, finding.id)
    if discovery is None:
        raise ValueError(f"{finding.external_id} is not a finding waiting for a scope decision")
    if not reason.strip():
        raise ValueError("A reason is required: it is the record of why this was decided.")
    orch = OrchestrationService(session)
    source = session.get(Ticket, discovery.source_ticket_id)
    if decision is FindingDecision.AMEND:
        if not criterion.strip():
            raise ValueError("amend needs the criterion the source ticket will be held to")
        rounds = _amend_rounds(session, discovery.source_ticket_id)
        if (
            decided_by.startswith("run:")
            and rounds >= MAX_AMEND_ROUNDS
            and not _same_round(session, discovery.source_ticket_id, decided_by)
        ):
            raise ValueError(
                f"{source.external_id} has amended its scope in {rounds} rounds, the most a "
                "ticket may. Decide this finding follow_up, defer or drop."
            )
        criteria = load_criteria(source.acceptance_criteria_json)
        orch.update_ticket_manual(
            source, UpdateTicketRequest(acceptance_criteria=[*criteria, criterion.strip()])
        )
        _reopen_after_spec(orch, source)
    if decision in (FindingDecision.AMEND, FindingDecision.DROP):
        orch.update_ticket_manual(finding, UpdateTicketRequest(state=TicketState.WONT_DO))
        finding.blocking_issues = (
            f"Folded into {source.external_id} as a criterion — {reason}"
            if decision is FindingDecision.AMEND
            else f"Dropped by scope decision — {reason}"
        )
        session.add(finding)
    discovery.decision = decision
    discovery.decided_at = utcnow()
    discovery.decided_by = decided_by
    discovery.decision_reason = reason.strip()
    session.add(discovery)
    session.commit()
    return discovery


def _same_round(session: Session, source_id: str, decided_by: str) -> bool:
    """Whether ``decided_by`` already amended in this round: its later amends are free."""
    return (
        session.exec(
            select(TicketDiscovery.id).where(
                TicketDiscovery.source_ticket_id == source_id,
                TicketDiscovery.decision == FindingDecision.AMEND,
                TicketDiscovery.decided_by == decided_by,
            )
        ).first()
        is not None
    )


def _defer_undecided(session: Session, ticket: Ticket, target: WorkflowStageDef) -> None:
    """Defer every finding the spec would not decide, and say so on the history. Commits."""
    findings = undecided_findings_of(session, ticket)
    for _, filed in findings:
        decide_finding(
            session,
            filed,
            FindingDecision.DEFER,
            reason=(
                "Deferred by the control plane: the spec was asked twice to decide it and "
                "decided nothing. A person can still promote it."
            ),
            decided_by=DEFERRED_BY,
        )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.DEFERRED_UNDECIDED_FINDINGS,
        stage_key=target.key,
        reason=(
            f"The spec decided none of {len(findings)} findings in two scope rounds, so they "
            f"were deferred and {ticket.external_id} went on to '{target.key}'."
        ),
        evidence={"findings": [filed.external_id for _, filed in findings]},
    )
    session.commit()


def open_round_or_defer(
    session: Session,
    ticket: Ticket,
    target: WorkflowStageDef,
) -> None:
    """Open a round before ``target``, or defer its findings when the last round decided none.

    Shared by both drivers, so the builtin loop and an external harness hand the
    ticket back to its spec by the same rule. A spec that decides nothing twice
    will not decide on a third ask either, and the findings are not this
    ticket's work until someone decides they are, so they are deferred rather
    than holding the ticket for a person.
    """
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None:
        raise ValueError(f"{ticket.external_id} has no workflow instance")
    try:
        open_round(session, ticket, instance, stages, target)
    except ScopeRoundStuck:
        session.rollback()
        _defer_undecided(session, ticket, target)
        return
    session.commit()


def open_round_if_due(session: Session, orch_run: OrchestrationRun, ticket: Ticket) -> bool:
    """Open a round if the stage this ticket runs next needs one. Never blocks: False.

    Kept returning a bool, and taking the run, so both drivers' "blocked?" step
    reads the same as the criteria check that follows it (`criterion_gate`).

    The step both drivers take before choosing a stage. When a round opens,
    the spec stage is the next pending one, so the caller's own pick finds it.
    """
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None or not stages:
        return False
    target_key = next_executable_stage(stages, parse_stage_map(instance, stages))
    target = next((s for s in stages if s.key == target_key), None)
    if target is None or not round_due(session, ticket, stages, target):
        return False
    open_round_or_defer(session, ticket, target)
    return False
