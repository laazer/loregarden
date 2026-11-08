"""The check before a ticket's gate: every acceptance criterion answered for.

Both drivers call `before_next_stage` before choosing a stage, the same step
the scope round takes (`scope_round.open_round_if_due`). When the stage they
are about to enter is the ticket's gate, which is its first gate-type stage
(or its terminal stage when it has none), the criteria are checked
(`criterion_check`):

- All answered for: the gate runs, and the history records which tests were
  rerun and which were only checked to exist.
- Gaps: the verify stage reopens, with the gaps as its brief, to settle them.
- The same gaps again after verify had its turn: the criteria behind them
  move to a sibling (`criterion_split`), and the ticket goes on with the rest.
- The tests could not run at all: an environment block, not rework.

No person is asked anything unless a split is refused, which happens when
a criterion fails in a second ticket after being split into it.
"""

from __future__ import annotations

from loregarden.agents.registry import VERIFIER_AGENT_ID
from loregarden.models.domain import (
    BlockOrigin,
    OrchestrationRun,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.models.domain.block_kinds import BlockKind
from loregarden.models.domain.stage_types import StageType
from loregarden.services.block_settlement import RepairPolicy
from loregarden.services.criterion_check import CriteriaCheck, check_criteria
from loregarden.services.criterion_split import SplitRefused, split_criteria
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.readiness_gate import block_if_unready
from loregarden.services.rework_feedback import latest_rework_context, record_rework_feedback
from loregarden.services.scope_round import open_round_if_due
from loregarden.services.studio_routing import is_terminal_stage
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    reconcile_workflow_state,
    set_stage_status,
)
from sqlmodel import Session


def gate_of(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """The stage a ticket's criteria are checked before: its first gate, else its terminal."""
    ordered = sorted(stages, key=lambda s: s.order)
    return next((s for s in ordered if s.stage_type == StageType.GATE), None) or next(
        (s for s in ordered if is_terminal_stage(s)), None
    )


def verify_of(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    return next((s for s in stages if s.agent_id == VERIFIER_AGENT_ID), None)


def check_due(stages: list[WorkflowStageDef], target: WorkflowStageDef) -> bool:
    """Whether entering ``target`` needs the check: it is the gate, after a verify stage."""
    gate, verify = gate_of(stages), verify_of(stages)
    return (
        gate is not None
        and verify is not None
        and gate.key == target.key
        and verify.order < gate.order
    )


def _brief(ticket: Ticket, target: WorkflowStageDef, check: CriteriaCheck) -> str:
    listed = "\n".join(gap.line() for gap in check.gaps)
    return (
        f"Before '{target.key}', {len(check.gaps)} acceptance criteria of {ticket.external_id} "
        f"are not answered for:\n\n{listed}\n\n"
        "Settle each with loregarden_settle_criterion: met, naming the tests that prove it "
        "(they are rerun at HEAD) or an evidence artifact, once it is actually met. If a test "
        "fails, report needs_rework to implement with the criterion named unmet. A criterion "
        "that cannot be met here is named unmet in a fail report; past the rework budget it "
        "moves to its own ticket."
    )


def _send_back(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    verify: WorkflowStageDef,
    target: WorkflowStageDef,
    brief: str,
) -> None:
    record_rework_feedback(
        session, ticket, target_stage=verify.key, from_stage=target.key, context=brief
    )
    set_stage_status(
        ticket, instance, stages, verify.key, StageStatus.PENDING, note="criteria check"
    )
    reconcile_workflow_state(ticket, instance, stages, persist=False)
    session.add_all([ticket, instance])
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.CRITERIA_SENT_TO_VERIFY,
        stage_key=target.key,
        reason=brief.split("\n\n", 1)[0],
    )
    session.commit()


def _block(
    session: Session, orch_run: OrchestrationRun, ticket: Ticket, message: str, kind: BlockKind
) -> bool:
    session.rollback()
    OrchestrationCallbackService(session).block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.CONTROL_PLANE,
        repair=RepairPolicy.withheld(
            "Another agent turn cannot change what the criteria check found."
        ),
        message=message,
        declared=kind,
    )
    return True


def _answer_gaps(
    session: Session,
    orch_run: OrchestrationRun,
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    target: WorkflowStageDef,
    check: CriteriaCheck,
) -> bool:
    """Send the gaps to verify, or split them off when verify already had them. True if blocked."""
    verify = verify_of(stages)
    instance = workflow_instance_for(session, ticket.id)
    brief = _brief(ticket, target, check)
    if verify is None or instance is None:
        return False
    if latest_rework_context(session, ticket, verify.key) != brief:
        _send_back(session, ticket, instance, stages, verify, target, brief)
        return False
    try:
        split_criteria(
            session,
            ticket,
            [gap.criterion for gap in check.gaps],
            stage_key=target.key,
            why="its verify stage was asked to answer for them and could not.",
        )
    except SplitRefused as exc:
        return _block(session, orch_run, ticket, str(exc), BlockKind.DECISION)
    return False


def _record_checked(
    session: Session, ticket: Ticket, target: WorkflowStageDef, check: CriteriaCheck
) -> None:
    unrerun = (
        f" {len(check.not_rerun)} tests were only checked to exist: no command reruns them."
        if check.not_rerun
        else ""
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.CRITERIA_CHECKED,
        stage_key=target.key,
        reason=f"Every acceptance criterion is answered for; {len(check.rerun)} tests passed a rerun at HEAD.{unrerun}",
        evidence={"rerun": check.rerun, "not_rerun": check.not_rerun},
    )
    session.commit()


def check_criteria_if_due(session: Session, orch_run: OrchestrationRun, ticket: Ticket) -> bool:
    """Check the criteria if the stage this ticket runs next is its gate. True when it blocked."""
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None or not stages:
        return False
    target_key = next_executable_stage(stages, parse_stage_map(instance, stages))
    target = next((s for s in stages if s.key == target_key), None)
    if target is None or not check_due(stages, target):
        return False
    check = check_criteria(session, ticket)
    if check.unavailable:
        return _block(
            session,
            orch_run,
            ticket,
            f"The criteria check could not run: {check.unavailable}",
            BlockKind.HARNESS,
        )
    if check.passed:
        _record_checked(session, ticket, target, check)
        return False
    return _answer_gaps(session, orch_run, ticket, stages, target, check)


def before_next_stage(session: Session, orch_run: OrchestrationRun, ticket: Ticket) -> bool:
    """What both drivers do before choosing a stage. True when the ticket blocked.

    The scope round first: a round reopens spec, so the gate is no longer next
    and the criteria check waits until the ticket comes back to it. Then the
    stage's declared inputs (`readiness_gate`): a criteria check that sent the
    ticket back to verify has changed which stage is next, so it goes last.
    """
    if open_round_if_due(session, orch_run, ticket):
        return True
    if check_criteria_if_due(session, orch_run, ticket):
        return True
    return block_if_unready(session, orch_run, ticket)
