"""_next_executable_stage must always run stages in template order.

The ticket's cursor (workflow_stage_key) can point past an earlier stage left
PENDING by an independent manual re-run — every stage in the workflow-lifecycle
UI has its own Run/Re-Run button, so a stage can be started without the ticket
having finished the stage before it. Trusting the cursor as a shortcut in that
state silently skips the unresolved earlier stage.
"""

from loregarden.models.domain import StageStatus, Ticket, WorkflowStageDef
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from sqlmodel import Session


def _stage(key: str, order: int) -> WorkflowStageDef:
    return WorkflowStageDef(key=key, name=key.title(), agent_id="agent", order=order)


def _ticket(workflow_stage_key: str) -> Ticket:
    return Ticket(
        external_id="next-executable-stage-test",
        workspace_id="ws-placeholder",
        title="Next executable stage test",
        workflow_stage_key=workflow_stage_key,
    )


def test_prefers_earlier_pending_stage_over_a_cursor_pointing_later(db_session: Session):
    orchestrator = BuiltinOrchestrator(db_session)
    stages = [_stage("implementation", 1), _stage("script_review", 2), _stage("ac_gate", 3)]
    stage_map = {
        "implementation": StageStatus.PENDING,
        "script_review": StageStatus.PENDING,
        "ac_gate": StageStatus.PENDING,
    }
    ticket = _ticket("script_review")

    assert orchestrator._next_executable_stage(ticket, stages, stage_map) == "implementation"


def test_uses_cursor_stage_when_no_earlier_stage_is_pending(db_session: Session):
    orchestrator = BuiltinOrchestrator(db_session)
    stages = [_stage("implementation", 1), _stage("script_review", 2), _stage("ac_gate", 3)]
    stage_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.PENDING,
        "ac_gate": StageStatus.PENDING,
    }
    ticket = _ticket("script_review")

    assert orchestrator._next_executable_stage(ticket, stages, stage_map) == "script_review"


def test_running_awaiting_and_blocked_stages_still_take_priority(db_session: Session):
    orchestrator = BuiltinOrchestrator(db_session)
    stages = [_stage("implementation", 1), _stage("script_review", 2), _stage("ac_gate", 3)]

    running_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.RUNNING,
        "ac_gate": StageStatus.PENDING,
    }
    assert (
        orchestrator._next_executable_stage(_ticket("ac_gate"), stages, running_map)
        == "script_review"
    )

    blocked_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.BLOCKED,
        "ac_gate": StageStatus.PENDING,
    }
    assert orchestrator._next_executable_stage(_ticket("ac_gate"), stages, blocked_map) is None
