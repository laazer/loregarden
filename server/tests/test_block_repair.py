"""The orchestrator's one repair turn on a block an agent can clear (750).

Before this, a `harness` or `work` block stopped the ticket until a person
asked what happened, asked for a fix, and requeued by hand. Now the blocked
stage is re-armed with the `repair` agent pinned and the loop re-runs it
inline; the rerun passing is the fix. A decision never gets a repair turn —
the agent that hit it already knows the options — and a repair that blocks
goes to a person with the kind it named. Never a second repair.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    BlockKind,
    EventType,
    OrchestrationRun,
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
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

IMPLEMENT = "implement"
REVIEW = "review"
WORKER = "implementation_frontend"
REVIEWER = "architecture_reviewer"


def _report(status: str, *, kind: str | None = None, options: list[str] | None = None) -> str:
    payload: dict = {"status": status, "confidence": 0.9, "reroute_context": "the block text"}
    if kind:
        payload["blocked_kind"] = kind
    if options:
        payload["options"] = options
    return f"<<<LOREGARDEN_STAGE_REPORT>>>\n{json.dumps(payload)}\n<<<END_STAGE_REPORT>>>\n"


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    ws = Workspace(slug=f"repair-{uuid4()}", name="Repair", repo_path="/nonexistent/repair-repo")
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    stages = [
        WorkflowStageDef(key=IMPLEMENT, name="Implement", order=1, agent_id=WORKER),
        # A non-terminal stage after implement, so implement has an exit gate.
        WorkflowStageDef(key=REVIEW, name="Review", order=2, agent_id=REVIEWER),
        WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"repair-tpl-{uuid4()}",
        name="Repair",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(
            [
                {"from": IMPLEMENT, "to": REVIEW, "when": "pass"},
                {"from": REVIEW, "to": "done", "when": "pass"},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    ticket = Ticket(
        external_id=f"repair-{uuid4()}",
        workspace_id=ws.id,
        title="Repair me",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=IMPLEMENT,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=IMPLEMENT,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()
    return ticket


Outcome = tuple[RunStatus, str, str]


def _script(monkeypatch, outcomes: dict[str, Outcome | list[Outcome]]) -> list[str]:
    """Fake the executor: each agent id gets (status, stdout, stderr), or a list
    consumed in order (the last repeats). Records who ran."""
    dispatched: list[str] = []
    queues: dict[str, list[Outcome]] = {
        agent: list(o) if isinstance(o, list) else [o] for agent, o in outcomes.items()
    }
    queues.setdefault(REVIEWER, [(RunStatus.SUCCEEDED, _report("pass"), "")])

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        dispatched.append(run.agent_id)
        queue = queues[run.agent_id]
        status, stdout, stderr = queue.pop(0) if len(queue) > 1 else queue[0]
        # The real executor settles the run itself; so must the fake.
        return OrchestrationService(self.session).complete_run(
            run, status=status, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    return dispatched


def _decisions(session: Session, ticket: Ticket) -> list[str]:
    return [
        json.loads(e.payload_json or "{}")["decision"]
        for e in event_bus.ticket_history(session, ticket.id)
        if e.type == EventType.ORCHESTRATOR_DECISION
    ]


def _runs(session: Session, ticket: Ticket) -> list[AgentRun]:
    return list(
        session.exec(
            select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at)
        ).all()
    )


def test_a_work_block_gets_one_repair_turn_and_the_rerun_passing_is_the_fix(
    db_session, ticket, monkeypatch
):
    dispatched = _script(
        monkeypatch,
        {
            WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="work"), ""),
            REPAIR_AGENT_ID: (RunStatus.SUCCEEDED, _report("pass"), ""),
        },
    )

    orch_run = BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=10
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER, REPAIR_AGENT_ID, REVIEWER]
    assert ticket.state is not TicketState.BLOCKED
    assert ticket.blocking_issues == ""
    assert ticket.block_kind is None
    assert orch_run.status is OrchestrationRunStatus.SUCCEEDED
    kinds = _decisions(db_session, ticket)
    assert "dispatched_repair" in kinds
    assert "repaired" in kinds
    # The repair run saw the block in its brief.
    repair_run = [r for r in _runs(db_session, ticket) if r.agent_id == REPAIR_AGENT_ID][0]
    assert repair_run.stage_key == IMPLEMENT


def test_a_harness_failure_gets_the_environment_retries_first_then_one_repair_turn(
    db_session, ticket, monkeypatch
):
    """The control plane's own retries run first — a lease expiry is usually
    gone by the second try — and only once they are spent does an agent look."""
    dispatched = _script(
        monkeypatch,
        {
            WORKER: (RunStatus.FAILED, "", "Error: 503 service unavailable"),
            REPAIR_AGENT_ID: (RunStatus.SUCCEEDED, _report("pass"), ""),
        },
    )
    profile = OrchestrationProfile(slug="repair-test")

    BuiltinOrchestrator(db_session).execute(ticket, profile, max_stages=20)

    db_session.refresh(ticket)
    retries = profile.retry_budget.max_transient_retries
    assert dispatched == [WORKER] * (retries + 1) + [REPAIR_AGENT_ID, REVIEWER]
    assert ticket.state is not TicketState.BLOCKED
    assert ticket.block_kind is None
    kinds = _decisions(db_session, ticket)
    assert "dispatched_repair" in kinds and "repaired" in kinds


def test_with_auto_repair_off_the_block_waits_for_a_person(db_session, ticket, monkeypatch):
    dispatched = _script(
        monkeypatch, {WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="work"), "")}
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=10, auto_repair=False
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER]
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.WORK
    assert "dispatched_repair" not in _decisions(db_session, ticket)


def test_a_decision_never_gets_a_repair_turn(db_session, ticket, monkeypatch):
    """The agent that hit it already knows the options; spending a run to
    re-derive them is the waste this removes. It goes straight to the inbox."""
    dispatched = _script(
        monkeypatch,
        {
            WORKER: (
                RunStatus.SUCCEEDED,
                _report("blocked", kind="decision", options=["a", "b"]),
                "",
            )
        },
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=10
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER]
    assert ticket.block_kind is BlockKind.DECISION
    assert (
        db_session.exec(
            select(Approval).where(
                Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.BLOCK_DECISION
            )
        ).first()
        is not None
    )


def test_a_repair_that_blocks_escalates_and_is_never_repaired_again(
    db_session, ticket, monkeypatch
):
    dispatched = _script(
        monkeypatch,
        {
            WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="work"), ""),
            REPAIR_AGENT_ID: (
                RunStatus.SUCCEEDED,
                _report("blocked", kind="decision", options=["relax", "rethink"]),
                "",
            ),
        },
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=10
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER, REPAIR_AGENT_ID]  # and no third
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.DECISION
    kinds = _decisions(db_session, ticket)
    assert kinds.count("dispatched_repair") == 1
    assert "repair_escalated" in kinds


def test_a_stage_never_gets_a_second_repair_until_a_person_requeues(
    db_session, ticket, monkeypatch
):
    """Repair passes the stage back as rework; the worker blocks again on the
    same stage. The cap holds: no second repair, the block goes to a person."""
    dispatched = _script(
        monkeypatch,
        {
            WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="work"), ""),
            REPAIR_AGENT_ID: (
                RunStatus.SUCCEEDED,
                "<<<LOREGARDEN_STAGE_REPORT>>>\n"
                + json.dumps(
                    {
                        "status": "needs_rework",
                        "confidence": 0.9,
                        "reroute_to_stage": IMPLEMENT,
                        "reroute_context": "cause found; the worker should redo it",
                        "unmet_criteria": ["AC1"],
                    }
                )
                + "\n<<<END_STAGE_REPORT>>>\n",
                "",
            ),
        },
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=20
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER, REPAIR_AGENT_ID, WORKER]
    assert ticket.state is TicketState.BLOCKED
    kinds = _decisions(db_session, ticket)
    assert kinds.count("dispatched_repair") == 1
    assert "repair_escalated" in kinds


def test_the_exit_gate_does_not_run_on_an_unrepaired_stage(db_session, ticket, monkeypatch):
    """Between the block and the repair turn the stage is PENDING; advancing
    past it would run its exit gate on a tree nobody has fixed yet."""
    from loregarden.services import gate_recovery

    _script(
        monkeypatch,
        {
            WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="work"), ""),
            REPAIR_AGENT_ID: (RunStatus.SUCCEEDED, _report("pass"), ""),
        },
    )
    gated_after: list[str] = []
    original = gate_recovery.GateRecovery.run_gates_with_autofix

    def spy(self, ticket_, profile, stage_def, instance, stages, orch_run, **kwargs):
        if kwargs.get("from_stage") != IMPLEMENT:
            return original(self, ticket_, profile, stage_def, instance, stages, orch_run, **kwargs)
        gated_after.append(
            db_session.exec(
                select(AgentRun)
                .where(AgentRun.ticket_id == ticket_.id)
                .order_by(AgentRun.created_at.desc())
            )
            .first()
            .agent_id
        )
        return original(self, ticket_, profile, stage_def, instance, stages, orch_run, **kwargs)

    monkeypatch.setattr(gate_recovery.GateRecovery, "run_gates_with_autofix", spy)

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=10
    )

    # The only exit-gate evaluation of `implement` happens after the repair ran.
    assert gated_after == [REPAIR_AGENT_ID]


def test_a_control_plane_death_is_left_to_the_resume_not_repaired(db_session, ticket, monkeypatch):
    """A server reload killing the run is `harness`, but the startup resume
    already owns it. A repair turn here would re-arm the stage under the dead
    orchestration and hide the interruption from the resume."""
    from loregarden.services.interruption_messages import INTERRUPTED_RUN_MESSAGE

    dispatched = _script(
        monkeypatch,
        {
            WORKER: (RunStatus.FAILED, "", INTERRUPTED_RUN_MESSAGE),
            REPAIR_AGENT_ID: (RunStatus.SUCCEEDED, _report("pass"), ""),
        },
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=10
    )

    db_session.refresh(ticket)
    assert REPAIR_AGENT_ID not in dispatched
    assert ticket.block_kind is BlockKind.HARNESS
    assert "dispatched_repair" not in _decisions(db_session, ticket)


def test_a_gate_stage_is_not_offered_a_repair_turn(db_session, monkeypatch, tmp_path):
    """The pin cannot resolve on a gate or parallel stage: the stage would
    re-arm with its own agent and the pin never consumed. The live case was a
    `gate` blocked on a missing handoff check (lg-initiatives-cross-747)."""
    from loregarden.services.block_repair import offer_repair

    ws = Workspace(slug=f"gate-{uuid4()}", name="G", repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    stages = [
        WorkflowStageDef(
            key="gate", name="Gate", order=1, stage_type="gate", agent_id="gatekeeper"
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"gate-tpl-{uuid4()}",
        name="G",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json="[]",
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    ticket = Ticket(
        external_id=f"gate-{uuid4()}",
        workspace_id=ws.id,
        title="gated",
        state=TicketState.BLOCKED,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="gate",
        workflow_stage_status=StageStatus.BLOCKED,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="gate",
        stages_json=initial_stages_json(stages),
    )
    parent = OrchestrationRun(
        run_code="orch_gate",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add_all([instance, parent])
    db_session.commit()
    db_session.refresh(instance)
    db_session.refresh(parent)

    offered = offer_repair(
        db_session,
        ticket,
        parent,
        instance=instance,
        stages=stages,
        transitions=[],
        stage_key="gate",
        kind=BlockKind.WORK,
        message="no handoff gate",
        failed_agent="gatekeeper",
    )

    assert offered is False
    assert ticket.next_agent != REPAIR_AGENT_ID
    assert "repair_escalated" in _decisions(db_session, ticket)


def test_a_classify_stage_gets_its_repair_turn_and_never_spins(db_session, monkeypatch, tmp_path):
    """The live shape that spent 12 dispatches in a minute: `implement` is a
    classify stage, classify routing ran before the pin was consulted, the
    worker reran, the pin was never consumed, and every failure re-armed it.
    Now: worker fails → one repair turn → repair fails → a person."""
    from loregarden.models.domain import ClassifyRoute

    ws = Workspace(slug=f"cls-{uuid4()}", name="C", repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    stages = [
        WorkflowStageDef(
            key=IMPLEMENT,
            name="Implement",
            order=1,
            stage_type="classify",
            agent_id=WORKER,
            classify_routes=[ClassifyRoute(agent_id=WORKER, default=True)],
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"cls-tpl-{uuid4()}",
        name="C",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": IMPLEMENT, "to": "done", "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    ticket = Ticket(
        external_id=f"cls-{uuid4()}",
        workspace_id=ws.id,
        title="classified",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=IMPLEMENT,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=IMPLEMENT,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()
    dispatched = _script(
        monkeypatch,
        {
            WORKER: (RunStatus.FAILED, "", "the tests are red and I could not see why"),
            REPAIR_AGENT_ID: (RunStatus.FAILED, "", "still red; the fixture is wrong"),
        },
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="repair-test"), max_stages=20
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER, REPAIR_AGENT_ID]
    assert ticket.state is TicketState.BLOCKED
    kinds = _decisions(db_session, ticket)
    assert kinds.count("dispatched_repair") == 1
    assert "repair_escalated" in kinds
