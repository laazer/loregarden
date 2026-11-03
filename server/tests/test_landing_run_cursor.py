"""A run landing its work says so: its cursor is on the terminal stage.

Landing runs the tree's verify suite, minutes with no agent working. The run's
`current_stage_key` stayed on the last agent stage (`gate`) the whole time, and
the ticket page read an idle ticket offering "Continue Run".
"""

from unittest.mock import patch

from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    WorkflowStageDef,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from sqlmodel import Session
from tests.factories import make_workspace_ticket


def test_the_run_is_on_the_terminal_stage_before_landing_starts(db_session: Session):
    ticket = make_workspace_ticket(db_session, "landing-cursor")
    orch_run = OrchestrationRun(
        run_code="orch_landing",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
        current_stage_key="gate",
    )
    db_session.add(orch_run)
    db_session.commit()
    seen: list[str] = []

    def finalize(self: OrchestrationService, landing_ticket, *, force: bool = False):
        # Read from a fresh query, as the ticket page's poll does: committed, not just set.
        db_session.expire(orch_run)
        seen.append(db_session.get(OrchestrationRun, orch_run.id).current_stage_key)
        return landing_ticket

    terminal = WorkflowStageDef(key="done", name="Done", order=12, terminal=True)
    with patch.object(OrchestrationService, "finalize_workflow", finalize):
        BuiltinOrchestrator(db_session)._handle_agentless_stage(ticket, orch_run, terminal, "done")

    assert seen == ["done"]
