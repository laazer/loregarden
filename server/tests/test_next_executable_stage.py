"""next_executable_stage must always run stages in template order.

The ticket's cursor (workflow_stage_key) can point past an earlier stage left
PENDING by an independent manual re-run — every stage in the workflow-lifecycle
UI has its own Run/Re-Run button, so a stage can be started without the ticket
having finished the stage before it. Trusting the cursor as a shortcut in that
state silently skips the unresolved earlier stage, which is why the picker takes
no ticket at all.
"""

from loregarden.models.domain import StageStatus, WorkflowStageDef
from loregarden.services.workflow_state import next_executable_stage


def _stage(key: str, order: int) -> WorkflowStageDef:
    return WorkflowStageDef(key=key, name=key.title(), agent_id="agent", order=order)


STAGES = [_stage("implementation", 1), _stage("script_review", 2), _stage("ac_gate", 3)]


def test_prefers_earlier_pending_stage_over_a_later_one():
    stage_map = {
        "implementation": StageStatus.PENDING,
        "script_review": StageStatus.PENDING,
        "ac_gate": StageStatus.PENDING,
    }

    assert next_executable_stage(STAGES, stage_map) == "implementation"


def test_picks_the_first_unresolved_stage_once_earlier_ones_are_done():
    stage_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.PENDING,
        "ac_gate": StageStatus.PENDING,
    }

    assert next_executable_stage(STAGES, stage_map) == "script_review"


def test_running_awaiting_and_blocked_stages_still_take_priority():
    running_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.RUNNING,
        "ac_gate": StageStatus.PENDING,
    }
    assert next_executable_stage(STAGES, running_map) == "script_review"

    blocked_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.BLOCKED,
        "ac_gate": StageStatus.PENDING,
    }
    assert next_executable_stage(STAGES, blocked_map) is None


def test_nothing_left_to_run_returns_none():
    stage_map = dict.fromkeys(("implementation", "script_review", "ac_gate"), StageStatus.DONE)

    assert next_executable_stage(STAGES, stage_map) is None
