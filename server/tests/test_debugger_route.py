"""A failing test goes to the debugger, not back to the agent that just passed (#4)."""

from loregarden.agents.registry import DEBUGGER_AGENT_ID, get_agent
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator

PYTEST_FAILURE = """
=================================== FAILURES ===================================
tests/test_routing.py::test_reroute FAILED
E   AssertionError: assert 'spec' == 'plan'
=========================== short test summary info ============================
1 failed, 42 passed in 3.10s
"""

RUFF_FAILURE = """
pre-commit: running Ruff on staged files...
F401 [*] `subprocess` imported but unused
 --> tests/test_evidence.py:3:8
Found 1 error.
"""


def _agent_for(session, detail: str) -> str:
    return BuiltinOrchestrator(session)._gate_failure_agent(detail)


def test_failing_tests_hand_over_to_the_debugger(db_session):
    """The agent that declared success is the one whose model of the code is
    wrong, so a second pass by it tends to patch the symptom."""
    assert _agent_for(db_session, PYTEST_FAILURE) == DEBUGGER_AGENT_ID


def test_lint_failures_stay_with_the_stage_agent(db_session):
    """A format or lint failure is the stage's own mess and it can clear it —
    handing that to a debugger would waste a run."""
    assert _agent_for(db_session, RUFF_FAILURE) == ""


def test_a_clean_lint_message_is_not_mistaken_for_tests(db_session):
    """ "All checks passed!" must not read as a test summary."""
    assert _agent_for(db_session, "All checks passed!\n209 files already formatted") == ""


def test_vitest_failures_also_reach_the_debugger(db_session):
    assert _agent_for(db_session, "Tests  3 passed, 1 failed") == DEBUGGER_AGENT_ID


def test_debugger_is_registered_with_a_role():
    agent = get_agent(DEBUGGER_AGENT_ID)
    assert agent is not None
    body = agent["role_body"]
    # The rule the role exists to enforce, and the escape hatches it closes.
    assert "runtime state" in body.lower()
    assert "delet" in body.lower() and "skip" in body.lower()


def test_reroute_hands_the_stage_to_the_debugger(db_session):
    """End to end: the stage re-runs as the debugger, with the failure in context."""
    from loregarden.models.domain import (
        OrchestrationRun,
        StageStatus,
        Ticket,
        TicketState,
        WorkflowInstance,
        WorkflowStageDef,
        WorkItemType,
        Workspace,
    )
    from loregarden.services.workflow_state import initial_stages_json
    from sqlmodel import select

    ws = db_session.exec(select(Workspace)).first()
    stages = [
        WorkflowStageDef(
            key="implement", name="Implement", agent_id="backend_implementer", order=1
        ),
        WorkflowStageDef(key="review", name="Review", agent_id="architecture_reviewer", order=2),
    ]
    ticket = Ticket(
        external_id="dbg-route",
        workspace_id=ws.id,
        title="Debugger route",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="backend_implementer",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id="tpl",
        current_stage_key="implement",
        stages_json=initial_stages_json(stages),
    )
    orch_run = OrchestrationRun(run_code="orun_dbg", ticket_id=ticket.id, workspace_id=ws.id)
    db_session.add(instance)
    db_session.add(orch_run)
    db_session.commit()

    BuiltinOrchestrator(db_session)._reroute_for_agent_fix(
        ticket, instance, stages, orch_run, "implement", PYTEST_FAILURE
    )

    db_session.refresh(ticket)
    assert ticket.next_agent == DEBUGGER_AGENT_ID
    assert ticket.workflow_stage_key == "implement"  # self-redo, not a jump
    # The instruction that keeps a symptom fix from counting as a pass.
    assert "root cause" in ticket.blocking_issues.lower()
    assert "do not delete" in ticket.blocking_issues.lower()
