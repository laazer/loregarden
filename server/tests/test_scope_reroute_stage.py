"""End-to-end coverage for the cross-scope handoff: a scoped implementer denied a
write onto the sibling's subtree hands the stage off instead of blocking.

Two layers:
* the permission bridge, which turns the scope denial into a reroute pin, and
* the sequential-stage runner, which must treat that pin as a handoff (keep
  processing so the stage re-dispatches to the sibling) rather than as an ordinary
  failed run (which blocks the ticket).
"""

import json

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
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
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session
from tests.factories import make_agent_run, select


def _setup_implementation(db_session: Session) -> tuple[Ticket, OrchestrationRun]:
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert template and ws
    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="scope-reroute-impl",
        workspace_id=ws.id,
        title="Needs a server change",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implementation",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implementation",
            stages_json=initial_stages_json(stages),
        )
    )
    orch_run = OrchestrationRun(
        run_code="orch_scope_reroute",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="implementation",
    )
    db_session.add(orch_run)
    db_session.commit()
    return ticket, orch_run


def test_sequential_runner_continues_on_scope_reroute_pin(db_session: Session, monkeypatch):
    ticket, orch_run = _setup_implementation(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        # Mimic the permission bridge's cross-scope handoff: the run fails, but a
        # sibling has been pinned and the stage re-armed to PENDING.
        run.status = RunStatus.FAILED
        run.stdout = ""
        run.stderr = "out of scope"
        worker_ticket.scope_reroute_agent = "backend_implementer"
        worker_ticket.workflow_stage_status = StageStatus.PENDING
        self.session.add(run)
        self.session.add(worker_ticket)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    stop = builtin._run_sequential_stage(
        ticket,
        orch_run,
        "implementation",
        auto_approve=True,
        stop_at_stage_key=None,
    )

    # False = keep processing this pass (re-dispatch), rather than stopping/blocking.
    assert stop is False
    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED
    assert ticket.scope_reroute_agent == "backend_implementer"


def test_sequential_runner_still_blocks_a_plain_failure(db_session: Session, monkeypatch):
    """Guardrail: an ordinary FAILED run with no reroute pin must still block, so
    the pin check doesn't accidentally swallow genuine stage failures."""
    ticket, orch_run = _setup_implementation(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.FAILED
        run.stdout = ""
        run.stderr = "genuine crash"
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    stop = builtin._run_sequential_stage(
        ticket,
        orch_run,
        "implementation",
        auto_approve=True,
        stop_at_stage_key=None,
    )

    assert stop is True
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED


# --------------------------------------------------------------------------- #
# Permission-bridge layer: scope denial -> reroute pin                         #
# --------------------------------------------------------------------------- #


class _FakeStdout:
    """Feeds a fixed sequence of stream-json lines to the permission loop, then
    reports EOF (closed) — mirrors the scaffolding in test_permission_bridge."""

    def __init__(self, lines):
        self.lines = list(lines)
        self._closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0) + "\n"
        self._closed = True
        return ""


class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []

    def write(self, data):
        self.writes.append(data.decode("utf-8") if isinstance(data, bytes) else data)

    def flush(self):
        return None


class _FakeProc:
    returncode = 0

    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stdin = _FakeStdin()
        self.stderr = None

    def poll(self):
        return 0 if self.stdout._closed else None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = 1


def test_permission_bridge_reroutes_cross_scope_write_to_sibling(tmp_path):
    """A scoped implementer denied a write onto the sibling's subtree hands the
    stage off to that sibling instead of blocking the ticket. Here a
    frontend_implementer run tries to Edit under /server/**; the implementation
    stage's owner is backend_implementer, so the denial pins backend_implementer
    and resets the stage to re-run rather than halting for a human."""
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
    from loregarden.models.domain import Approval, Workspace
    from loregarden.services.seed import seed_database
    from sqlmodel import SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket.workflow_stage_key == "implementation"

        repo_root = tmp_path / "repo"
        (repo_root / "server" / "loregarden").mkdir(parents=True)
        (repo_root / "client").mkdir()
        workspace = session.get(Workspace, ticket.workspace_id)
        workspace.repo_path = str(repo_root)
        session.add(workspace)
        session.commit()

        run = AgentRun(
            run_code="run_reroute_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="frontend_implementer",
            stage_key="implementation",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("wire the endpoint", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude", prompt_file=prompt_file, workspace_root=repo_root
        )

        target = str(repo_root / "server" / "loregarden" / "main.py")
        lines = [
            json.dumps(
                {
                    "type": "control_request",
                    "request_id": "perm_reroute_1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "Edit",
                        "tool_input": {"file_path": target, "old_string": "a", "new_string": "b"},
                    },
                }
            ),
            json.dumps({"type": "result", "session_id": "sess_reroute", "subtype": "success"}),
        ]

        def fake_spawn(*args, **kwargs):
            return _FakeProc(lines)

        def fake_wait(approval_id, **kwargs):
            raise AssertionError("must not wait for human approval")

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="wire the endpoint",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.FAILED
        assert session.exec(select(Approval).where(Approval.run_id == run.id)).first() is None
        session.refresh(ticket)
        # Handed off, not blocked: sibling pinned, stage re-armed, no human halt.
        assert ticket.scope_reroute_agent == "backend_implementer"
        assert ticket.state != TicketState.BLOCKED
        assert ticket.workflow_stage_status == StageStatus.PENDING
        assert ticket.blocking_issues == ""


def test_scope_reroute_budget_exhaustion_blocks(tmp_path):
    """Cross-scope handoffs share one durable budget so a ticket that genuinely
    needs both implementers cannot ping-pong forever: after the cap, the next
    denial declines to reroute (and clears the pin) so the caller blocks."""
    from loregarden.agents.executors.permission_bridge import (
        PermissionBridgeRunner,
        _RunContext,
    )
    from loregarden.services.rework_feedback import MAX_REWORK_REROUTES
    from loregarden.services.seed import seed_database
    from sqlmodel import SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()

        repo_root = tmp_path / "repo"
        ctx = _RunContext(
            workspace_slug="ws",
            workspace_root=str(repo_root),
            auto_approve=False,
            agent_id="frontend_implementer",
            agent_name="Frontend Implementer Agent",
        )
        tool_input = {"file_path": str(repo_root / "server" / "loregarden" / "main.py")}
        bridge = PermissionBridgeRunner(session)

        from loregarden.agents.executors.permission_bridge import ApprovalScope

        results = [
            bridge._try_scope_reroute(
                ctx=ctx,
                scope=ApprovalScope.for_ticket(ticket),
                tool_input=tool_input,
                run_id=make_agent_run(
                    session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
                ).id,
                message="denied",
            )
            for _ in range(MAX_REWORK_REROUTES + 1)
        ]

        assert results[:MAX_REWORK_REROUTES] == ["backend_implementer"] * MAX_REWORK_REROUTES
        assert results[MAX_REWORK_REROUTES] is None
        # Budget spent: no pin left dangling, so the caller falls back to block.
        session.refresh(ticket)
        assert ticket.scope_reroute_agent == ""


# --------------------------------------------------------------------------- #
# Full execute() loop: scope reroute must re-dispatch, not advance-and-block   #
# (regression for the interaction with #81 retry budget / #84 gate path)       #
# --------------------------------------------------------------------------- #


def _classify_impl_template(db_session: Session) -> Ticket:
    """A ticket whose `implement` stage is a classify stage offering both
    implementers, defaulting to frontend, with a downstream review + done. Built
    in-test so the transition table (and thus the exit gate the old code ran) is
    fully controlled."""
    import json
    from uuid import uuid4

    from loregarden.models.domain import ClassifyRoute, WorkflowTemplate

    ws = Workspace(
        slug=f"scope-reroute-int-{uuid4()}",
        name="Scope Reroute Integration",
        repo_path="/nonexistent/scope-reroute-test-repo",
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    stages = [
        WorkflowStageDef(
            key="implement",
            name="Implement",
            stage_type="classify",
            order=1,
            classify_routes=[
                ClassifyRoute(
                    specialties=["frontend"],
                    agent_id="frontend_implementer",
                    skill_name="apply_patch",
                    default=True,
                ),
                ClassifyRoute(
                    specialties=["backend"],
                    agent_id="backend_implementer",
                    skill_name="apply_patch",
                ),
            ],
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    transitions = [
        {"from": "implement", "to": "done", "when": "pass"},
    ]
    template = WorkflowTemplate(
        slug=f"scope-reroute-tpl-{uuid4()}",
        name="Scope Reroute Test Template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(transitions),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"scope-reroute-ticket-{uuid4()}",
        workspace_id=ws.id,
        title="Add a thing",
        description="No strong keywords, defaults to the frontend implementer.",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()
    return ticket


def _pass_report() -> str:
    return (
        "done\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.95}\n<<<END_STAGE_REPORT>>>\n'
    )


def test_execute_reroutes_scope_denied_frontend_to_backend_without_blocking(
    db_session: Session, monkeypatch
):
    """The real end-to-end bug: a frontend implementer scope-denied on server code
    must hand off to the backend implementer and let the ticket proceed — not
    block. Reproduces the interaction where the post-stage advance ran the exit
    gate on the re-armed stage and blocked before the sibling ever ran."""
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.orchestration import OrchestrationService
    from loregarden.services.orchestration_profile import OrchestrationProfile
    from loregarden.services.workflow_routing import apply_stage_route
    from loregarden.services.workflow_state import set_stage_status

    ticket = _classify_impl_template(db_session)
    dispatched: list[str] = []

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        dispatched.append(run.agent_id)
        orch = OrchestrationService(self.session)
        inst, stages = orch._resolve_stages(worker_ticket)
        if run.agent_id == "frontend_implementer":
            # Mimic permission_bridge._try_scope_reroute on a server/** denial.
            set_stage_status(worker_ticket, inst, stages, run.stage_key, StageStatus.PENDING)
            worker_ticket.scope_reroute_agent = "backend_implementer"
            run.status = RunStatus.FAILED
            run.stderr = "scoped to client/** and cannot Edit server/..."
        else:
            # A real agent's completion advances the workflow off its stage report;
            # the mock replaces the executor, so advance the pass route here.
            run.status = RunStatus.SUCCEEDED
            run.stdout = _pass_report()
            run.stderr = ""
            apply_stage_route(
                worker_ticket,
                inst,
                stages,
                orch._resolve_transitions(worker_ticket),
                from_key=run.stage_key,
                outcome="pass",
            )
        self.session.add(worker_ticket)
        self.session.add(inst)
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, OrchestrationProfile(slug="scope-reroute-test"), max_stages=12)

    db_session.refresh(ticket)
    # The sibling actually ran (the handoff completed) ...
    assert "backend_implementer" in dispatched
    # ... the frontend ran exactly once (the pin steered the re-dispatch to backend,
    # not back to frontend) ...
    assert dispatched.count("frontend_implementer") == 1
    # ... the pin was consumed, and the ticket was not blocked by the handoff.
    assert ticket.scope_reroute_agent == ""
    assert ticket.state != TicketState.BLOCKED


def test_scope_reroute_pin_exempts_stage_retry_budget(db_session: Session):
    """The sibling handoff must not be counted or blocked by #81's per-stage retry
    breaker: even with the stage already at its dispatch budget, a pending pin
    proceeds (the scope-reroute ledger is its separate, own bound)."""
    from loregarden.models.domain import OrchestrationRun
    from loregarden.services.orchestration_profile import RetryBudgetConfig
    from loregarden.services.stage_retry_budget import (
        count_stage_dispatches,
        enforce_stage_retry_budget,
        record_stage_dispatch,
    )

    ticket = _setup_implementation(db_session)[0]
    orch_run = OrchestrationRun(
        run_code="orch_exempt",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        current_stage_key="implementation",
    )
    db_session.add(orch_run)
    db_session.commit()

    # Drive the stage to its budget so a normal dispatch here would block.
    for _ in range(5):
        record_stage_dispatch(db_session, ticket.id, "implementation")
    before = count_stage_dispatches(db_session, ticket.id, "implementation")

    ticket.scope_reroute_agent = "backend_implementer"
    builtin = BuiltinOrchestrator(db_session)
    result = enforce_stage_retry_budget(
        db_session,
        builtin.callbacks,
        orch_run,
        ticket,
        "implementation",
        RetryBudgetConfig(enabled=True, max_attempts_per_stage=5),
    )

    assert result is None  # exempt: proceed to dispatch the sibling
    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED
    # The handoff didn't consume the stage's dispatch budget either.
    assert count_stage_dispatches(db_session, ticket.id, "implementation") == before
