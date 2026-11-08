"""The check before a ticket's gate: every acceptance criterion answered for.

Both drivers call `before_next_stage` before choosing a stage, the same step
the scope round takes (`scope_round.open_round_if_due`). When the stage they
are about to enter is the one the check guards (`criterion_gate_stages.check_due`:
the gate after a verify stage, or the stage after a gate that settles its own
criteria), the criteria are checked (`criterion_check`):

- All answered for: the gate runs, and the history records which tests were
  rerun and which were only checked to exist.
- Gaps: the stage that settles criteria (verify, or the AC gate) reopens,
  with the gaps as its brief, to settle them.
- The same gaps again after it had its turn: the criteria behind them
  move to a sibling (`criterion_split`), and the ticket goes on with the rest.
- The tests could not run at all: an environment block, not rework.

No person is asked anything unless a split is refused, which happens when
a criterion fails in a second ticket after being split into it.
"""

from __future__ import annotations

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
from loregarden.services.block_settlement import RepairPolicy
from loregarden.services.criterion_check import CriteriaCheck, check_criteria
from loregarden.services.criterion_gate_stages import check_due, settler_of
from loregarden.services.criterion_split import SplitRefused, split_criteria
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.gate_dispatch_check import GateCriteriaUnanswered, set_gate_check
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestrator_decisions import decisions_of, record_orchestrator_decision
from loregarden.services.rework_feedback import latest_rework_context, record_rework_feedback
from loregarden.services.scope_round import open_round_if_due
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    reconcile_workflow_state,
    set_stage_status,
)
from sqlmodel import Session


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
    settler: WorkflowStageDef,
    target: WorkflowStageDef,
    brief: str,
) -> None:
    record_rework_feedback(
        session, ticket, target_stage=settler.key, from_stage=target.key, context=brief
    )
    set_stage_status(
        ticket, instance, stages, settler.key, StageStatus.PENDING, note="criteria check"
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
    session: Session,
    orch_run: OrchestrationRun | None,
    ticket: Ticket,
    message: str,
    kind: BlockKind,
) -> bool:
    """Block the ticket under ``orch_run``. With no run (a stage started by hand,
    the terminal sweep) there is nothing to block under: the start is refused
    with the same message, which reaches the person or sweep that asked."""
    session.rollback()
    if orch_run is None:
        raise GateCriteriaUnanswered(message)
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
    orch_run: OrchestrationRun | None,
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    target: WorkflowStageDef,
    check: CriteriaCheck,
) -> bool:
    """Send the gaps to the stage that settles criteria, or split them off when it already had them.

    True if blocked.
    """
    settler = settler_of(stages)
    instance = workflow_instance_for(session, ticket.id)
    brief = _brief(ticket, target, check)
    if settler is None or instance is None:
        return False
    if latest_rework_context(session, ticket, settler.key) != brief:
        _send_back(session, ticket, instance, stages, settler, target, brief)
        return False
    try:
        split_criteria(
            session,
            ticket,
            [gap.criterion for gap in check.gaps],
            stage_key=target.key,
            why=f"its {settler.key} stage was asked to answer for them and could not.",
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
        evidence={
            "rerun": check.rerun,
            "not_rerun": check.not_rerun,
            # The commit the check answered for; the gate is signed on it only
            # while the ticket is still there (`criteria_sign_off`).
            "commit_sha": resolve_head_sha(session, ticket),
        },
    )
    session.commit()


def check_criteria_if_due(session: Session, orch_run: OrchestrationRun, ticket: Ticket) -> bool:
    """Check the criteria if the stage this ticket runs next is the one the check guards. True when it blocked."""
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None or not stages:
        return False
    stage_map = parse_stage_map(instance, stages)
    target_key = next_executable_stage(stages, stage_map)
    target = next((s for s in stages if s.key == target_key), None)
    if target is None or not check_due(stages, stage_map, target):
        return False
    return _check_at(session, orch_run, ticket, stages, target)[1]


def _check_at(
    session: Session,
    orch_run: OrchestrationRun | None,
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    target: WorkflowStageDef,
) -> tuple[CriteriaCheck, bool]:
    """Run the check before ``target`` and act on it. Returns it, and whether it blocked."""
    check = check_criteria(session, ticket)
    if check.unavailable:
        message = f"The criteria check could not run: {check.unavailable}"
        return check, _block(session, orch_run, ticket, message, BlockKind.HARNESS)
    if check.passed:
        _record_checked(session, ticket, target, check)
        return check, False
    return check, _answer_gaps(session, orch_run, ticket, stages, target, check)


#: The decisions that end one criteria check, whichever way it went.
_CHECK_OUTCOMES = frozenset(
    {
        OrchestratorDecision.CRITERIA_CHECKED.value,
        OrchestratorDecision.CRITERIA_SENT_TO_VERIFY.value,
        OrchestratorDecision.SPLIT_CRITERIA.value,
    }
)


def _passed_at_head(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """Whether the newest check on ``ticket`` passed before ``stage_key``, at the commit it is at now.

    The drivers check before they choose the stage, and `start_run` asks again
    when it starts it; this keeps that second ask from rerunning every test.
    """
    head = resolve_head_sha(session, ticket)
    for _event, payload in decisions_of(session, [ticket.id]):
        if payload.get("decision") not in _CHECK_OUTCOMES:
            continue
        return (
            payload.get("decision") == OrchestratorDecision.CRITERIA_CHECKED.value
            and payload.get("stage_key") == stage_key
            and bool(head)
            and payload.get("commit_sha") == head
        )
    return False


def check_before_dispatch(
    session: Session, ticket: Ticket, stage_key: str, orch_run: OrchestrationRun | None
) -> None:
    """The check for a path that starts ``stage_key`` itself (`gate_dispatch_check`).

    Nothing happens unless ``stage_key`` is the stage the check guards. Then it
    runs, unless it already passed there at HEAD, with the drivers' outcomes:
    checked, the gaps sent back, split off, or the ticket blocked. Anything but
    checked refuses the start.
    """
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    target = next((s for s in stages if s.key == stage_key), None)
    if instance is None or target is None:
        return
    if not check_due(stages, parse_stage_map(instance, stages), target):
        return
    if _passed_at_head(session, ticket, stage_key):
        return
    check, _blocked = _check_at(session, orch_run, ticket, stages, target)
    if not check.passed:
        raise GateCriteriaUnanswered(
            f"'{stage_key}' of {ticket.external_id} did not start: {len(check.gaps)} "
            "acceptance criteria are not answered for. The criteria check sent them back "
            "to the stage that settles them, split them off, or blocked the ticket; its "
            "history says which."
        )


# Installed here rather than imported by the dispatchers: see `gate_dispatch_check`.
set_gate_check(check_before_dispatch)


def before_next_stage(session: Session, orch_run: OrchestrationRun, ticket: Ticket) -> bool:
    """What both drivers do before choosing a stage. True when the ticket blocked.

    The scope round first: a round reopens spec, so the gate is no longer next
    and the criteria check waits until the ticket comes back to it.
    """
    if open_round_if_due(session, orch_run, ticket):
        return True
    return check_criteria_if_due(session, orch_run, ticket)
