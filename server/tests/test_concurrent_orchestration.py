"""Two tickets orchestrated at the same time through the normal start flow.

The blocker was never the dispatch loop — the slot pool and per-slot lanes
already admit three tickets at once, and the start endpoint is a sync handler
that FastAPI runs in a threadpool. What made concurrency unsafe was the shared
checkout every run executed in. With each ticket in its own worktree, this test
pins the property that was previously impossible: two orchestrations genuinely
overlapping, in two trees, over one database.

The barrier is the proof of overlap. If anything serialised the two runs, the
second would never arrive and the first would time out waiting for it.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.queue_admission import QueueAdmissionService
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session
from tests.worktree_helpers import head_branch, make_repo

PASS_REPORT = (
    '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "pass", "confidence": 0.9}\n<<<END_STAGE_REPORT>>>\n'
)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


def _template(session: Session) -> WorkflowTemplate:
    stages = [
        WorkflowStageDef(
            key="implement",
            name="Implement",
            stage_type="agent",
            order=1,
            agent_id="backend_implementer",
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    template = WorkflowTemplate(
        slug=f"concurrent-test-{uuid4()}",
        name="Concurrent test template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def _ticket(session: Session, workspace_id: str, template_id: str, code: str) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace_id,
        title=f"Work on {code}",
        branch=f"loregarden/{code.lower()}",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.PENDING,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template_id,
            current_stage_key="implement",
            stages_json=initial_stages_json(
                [
                    WorkflowStageDef(
                        key="implement",
                        name="Implement",
                        stage_type="agent",
                        order=1,
                        agent_id="backend_implementer",
                    ),
                    WorkflowStageDef(
                        key="done", name="Done", order=2, terminal=True, stage_type="agent"
                    ),
                ]
            ),
        )
    )
    session.commit()
    return ticket


def test_two_tickets_orchestrate_concurrently_in_their_own_worktrees(isolated_db, repo):
    with Session(isolated_db) as setup:
        workspace = Workspace(slug="proj", name="proj", repo_path=str(repo))
        setup.add(workspace)
        setup.commit()
        setup.refresh(workspace)
        template = _template(setup)
        ticket_ids = [_ticket(setup, workspace.id, template.id, f"LG-{n}").id for n in (1, 2)]

    barrier = threading.Barrier(2, timeout=30)
    roots: dict[str, str] = {}
    lock = threading.Lock()

    def fake_execute(self, run, ticket, *, advance_workflow=True, skip_git_branch=False):
        ws = self.session.get(Workspace, ticket.workspace_id)
        root = resolve_execution_root(self.session, run, ticket, ws)
        with lock:
            roots[ticket.id] = str(root)
        # Both stages must be in flight at once, or the two orchestrations are
        # taking turns and this feature does not exist.
        barrier.wait()
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=PASS_REPORT,
            advance_workflow=advance_workflow,
        )

    def orchestrate(ticket_id):
        with Session(isolated_db) as session:
            ticket = session.get(Ticket, ticket_id)
            admission = QueueAdmissionService(session, max_concurrent=3)
            reservation = admission.reserve_orchestration(ticket)
            assert reservation.admitted, "the pool refused a second concurrent ticket"
            run = BuiltinOrchestrator(session).execute(
                ticket, OrchestrationProfile(slug="concurrent-test"), max_stages=2
            )
            # An orchestration run, not an agent run: `execute` returns the
            # former, and the two bind to different slot columns.
            reservation.bind(orchestration_run_id=run.id)
            return run.status

    with patch.object(CliAgentExecutor, "execute", fake_execute):
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(orchestrate, ticket_ids))

    assert OrchestrationRunStatus.FAILED not in statuses
    assert len(set(roots.values())) == 2, roots
    # Two live tickets, and the checkout they were both cut from never moved.
    assert head_branch(repo) == "main"

    with Session(isolated_db) as session:
        for ticket_id in ticket_ids:
            assert session.get(Ticket, ticket_id).workflow_stage_key == "done"
