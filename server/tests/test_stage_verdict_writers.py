"""Writer enumeration and call-site pins for the pessimistic StageVerdict.

Pattern: `test_block_settlement.BLOCK_WRITERS`. Every agent-facing function that
can mark a stage DONE on a claimed pass goes through `derive_verdict` before
`set_stage_status DONE`. Human `orchestration.advance_stage` is exempt.
"""

from __future__ import annotations

import ast
import functools
import json
import pathlib
from unittest.mock import patch
from uuid import uuid4

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    GitBoundary,
    OrchestrationRun,
    OrchestrationRunStatus,
    ParallelAgentSpec,
    RunStatus,
    StageReportStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.gate_recovery import GateDecision
from loregarden.services.handoff_store import build_handoff_doc, store_handoff
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.parallel_stage import (
    member_passed,
    member_result_from_run,
    reconcile_parallel_stage,
)
from loregarden.services.rework_feedback import render_rework_feedback, rework_reroute_count
from loregarden.services.stage_verdict import StageVerdict, routing_outcome
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select

_SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "loregarden"

#: Every agent-facing function that can mark a stage DONE on a claimed pass,
#: and how it reaches derive_verdict. Discovery fails if a new writer appears
#: unlisted. `exempt:` is for the operator override this ticket does not own.
VERDICT_WRITERS: dict[str, str] = {
    "services/run_completion.py:_pass_held": "derives",
    "services/run_completion.py:advance_stage_after_run": "delegates: _pass_held",
    "services/orchestration_callbacks.py:complete_stage": "derives",
    "services/parallel_stage.py:member_result": "derives",
    "services/parallel_stage.py:member_passed": "derives",
    "services/parallel_stage.py:reconcile_parallel_stage": "delegates: member_result",
    "services/orchestration.py:advance_stage": (
        "visible_unresolved check; refuse DONE/finish while names remain — "
        "not a derive site (136 closed the 135 Advance exemption)"
    ),
    "services/orchestration.py:route_workflow_stage": (
        "exempt: human/API reject routing, not DONE on a claimed pass — "
        "Advance refusal lives on advance_stage, not this router"
    ),
    "services/run_completion.py:_reroute_or_block_for_rework": (
        "delegates: _pass_held — reject settlement after derive; "
        "apply_stage_route here is the rework route, not DONE on a claimed pass"
    ),
}

_DONE_WRITERS = frozenset(
    {"set_stage_status", "finalize_stage", "_set_stage_status", "apply_stage_route"}
)
_DERIVE_NAMES = frozenset({"derive_verdict", "collect_signals"})

#: Every derive / DONE writer that must settle the unresolved ledger. Discovery
#: fails if a new site skips settle. Pattern: test_block_settlement.BLOCK_WRITERS.
UNRESOLVED_WRITERS: dict[str, str] = {
    "services/run_completion.py:_pass_held": "settles",
    "services/run_completion.py:_stamp_derived_report": "settles",
    "services/run_completion.py:_settled_pessimistic_report": "settles",
    "services/orchestration_callbacks.py:complete_stage": "settles",
    "services/parallel_stage.py:member_result": "settles",
    "services/orchestration.py:advance_stage": "visible_check",
}


def _pass_stdout() -> str:
    return (
        "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9}\n<<<END_STAGE_REPORT>>>\n'
    )


def _fail_stdout() -> str:
    return (
        "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "fail", "confidence": 0.9, "reroute_to_stage": "plan"}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _pass_stdout_with_context(context: str) -> str:
    payload = json.dumps({"status": "pass", "confidence": 0.9, "reroute_context": context})
    return f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{payload}\n<<<END_STAGE_REPORT>>>\n"


def _stage(
    key: str,
    *,
    agent_id: str = "",
    order: int = 0,
    stage_type: str = "agent",
    required_evidence: list[str] | None = None,
    parallel_agents: list[ParallelAgentSpec] | None = None,
    terminal: bool = False,
) -> WorkflowStageDef:
    return WorkflowStageDef(
        key=key,
        name=key,
        agent_id=agent_id,
        order=order,
        stage_type=stage_type,
        required_evidence=required_evidence or [],
        parallel_agents=parallel_agents or [],
        terminal=terminal,
        exit_actions_enabled=False,
        exit_actions=[],
    )


def _running_ticket(
    session: Session,
    tmp_path,
    *,
    stage_key: str = "spec",
    agent_id: str = "spec",
    stage_type: str = "agent",
    required_evidence: list[str] | None = None,
    parallel_agents: list[ParallelAgentSpec] | None = None,
) -> tuple[Ticket, OrchestrationRun, WorkflowStageDef, list[WorkflowStageDef]]:
    current = _stage(
        stage_key,
        agent_id=agent_id,
        order=2,
        stage_type=stage_type,
        required_evidence=required_evidence,
        parallel_agents=parallel_agents,
    )
    stages = [
        _stage("plan", agent_id="planner", order=1),
        current,
        _stage("implement", agent_id="backend_implementer", order=3),
        _stage("done", order=4, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"vw-tpl-{uuid4().hex[:8]}",
        name="Verdict writers",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(
            [
                {"from": "plan", "to": stage_key, "when": "pass"},
                {"from": stage_key, "to": "implement", "when": "pass"},
                {"from": stage_key, "to": "plan", "when": "reject"},
                {"from": "implement", "to": "done", "when": "pass"},
            ]
        ),
    )
    session.add(template)
    session.commit()
    workspace = Workspace(
        slug=f"vw-ws-{uuid4().hex[:8]}",
        name="Verdict writers",
        repo_path=str(tmp_path),
        workflow_template_id=template.id,
    )
    session.add(workspace)
    session.commit()
    ticket = Ticket(
        external_id=f"vw-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Writer ticket",
        state=TicketState.IN_PROGRESS,
        workflow_stage_key=stage_key,
        workflow_stage_status=StageStatus.RUNNING,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=stage_key,
            stages_json=initial_stages_json(stages),
        )
    )
    orch = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        current_stage_key=stage_key,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(orch)
    session.commit()
    session.refresh(orch)
    return ticket, orch, current, stages


def _incomplete_handoff(session: Session, ticket: Ticket) -> None:
    store_handoff(
        session,
        ticket=ticket,
        doc=build_handoff_doc(
            external_id=ticket.external_id,
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=[
                {
                    "item_key": "test_suite_complete",
                    "item": "Test suite complete",
                    "required": True,
                    "status": "incomplete",
                    "certainty": "inferred",
                    "evidence": "",
                }
            ],
            required_items_met=0,
            total_required_items=1,
            boundary=GitBoundary(),
        ),
    )
    session.commit()


def _agent_run(
    session: Session,
    ticket: Ticket,
    orch: OrchestrationRun,
    *,
    agent_id: str,
    stage_key: str,
    stdout: str = "",
    status: RunStatus = RunStatus.SUCCEEDED,
    skill_name: str = "",
) -> AgentRun:
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        skill_name=skill_name,
        status=status,
        stdout=stdout,
        stderr="",
        orchestration_run_id=orch.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _stage_status(session: Session, ticket: Ticket, key: str) -> StageStatus:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)[key]


def _stage_report_artifact(session: Session, ticket: Ticket, title: str) -> Artifact | None:
    return session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.title == title)
    ).first()


# --------------------------------------------------------------------------- #
# R7 / R12 write-path pins
# --------------------------------------------------------------------------- #


def test_complete_stage_pass_with_incomplete_checklist_does_not_advance(db_session, tmp_path):
    """R7.1 / R12.12. complete_stage(outcome=pass) with incomplete required checklist
    does not mark DONE; routing_outcome is reject."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    OrchestrationCallbackService(db_session).complete_stage(
        orch, ticket, stage_key=stage.key, outcome="pass"
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE
    assert ticket.workflow_stage_key == stage.key


def test_sentinel_pass_with_incomplete_checklist_does_not_advance_clean_exit(db_session, tmp_path):
    """R7.2 / R12.13. Sentinel claimed pass with incomplete checklist does not
    call `_advance_clean_exit` and does not mark DONE."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    with patch("loregarden.services.run_completion._advance_clean_exit") as advance:
        OrchestrationService(db_session).complete_run(
            run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
        )
    db_session.refresh(ticket)
    assert advance.call_count == 0
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE


def test_parallel_all_pass_stdout_with_incomplete_member_checklist_does_not_finalize_done(
    db_session, tmp_path
):
    """R7.3 / R12.14. All members' stdout status=pass but one existing handoff
    row is incomplete → reconcile_parallel_stage does not finalize_stage(DONE)."""
    members = [
        ParallelAgentSpec(agent_id="architecture_reviewer"),
        ParallelAgentSpec(agent_id="static_qa"),
    ]
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=members,
    )
    _incomplete_handoff(db_session, ticket)
    runs = [
        _agent_run(
            db_session,
            ticket,
            orch,
            agent_id=spec.agent_id,
            stage_key=stage.key,
            stdout=_pass_stdout(),
        )
        for spec in members
    ]
    results = [member_result_from_run(run) for run in runs]
    with patch.object(OrchestrationService, "finalize_stage") as finalize:
        reconcile_parallel_stage(db_session, ticket, orch, stage.key, results)
    db_session.refresh(ticket)
    done_calls = [
        call
        for call in finalize.call_args_list
        if call.kwargs.get("status") is StageStatus.DONE
        or (call.args and StageStatus.DONE in call.args)
    ]
    assert done_calls == []
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE


def test_derived_fail_needs_rework_blocked_route_as_reject_not_linear_next(db_session, tmp_path):
    """R7.4 / R12.11. Derived fail/needs_rework/blocked map to reject and must
    not take the pass/default/linear next."""
    ticket, _orch, stage, stages = _running_ticket(db_session, tmp_path)
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    transitions = OrchestrationService(db_session)._resolve_transitions(ticket)
    assert instance is not None
    for status in (
        StageReportStatus.FAIL,
        StageReportStatus.NEEDS_REWORK,
        StageReportStatus.BLOCKED,
    ):
        verdict = StageVerdict(status=status, unresolved=())
        outcome = routing_outcome(verdict)
        assert outcome == "reject"
        route = StateMachine.resolve_next_stage_key(
            resolved or stages, transitions, stage.key, outcome=outcome
        )
        linear = StateMachine.next_stage_key(resolved or stages, stage.key)
        if route is not None:
            assert route.to_key != linear, status
            assert route.outcome == "reject"


def test_report_fail_then_complete_stage_pass_stays_fail(db_session, tmp_path):
    """R7.5 / R12.6. Last-written tool pass cannot override a fail report."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    run = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id="spec",
        stage_key=stage.key,
        stdout=_fail_stdout(),
        status=RunStatus.RUNNING,
    )
    OrchestrationCallbackService(db_session).complete_stage(
        orch, ticket, stage_key=stage.key, outcome="pass"
    )
    db_session.refresh(ticket)
    db_session.refresh(run)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE
    assert ticket.workflow_stage_key != "implement"


def test_member_passed_is_false_when_stdout_pass_but_derived_not_pass(db_session, tmp_path):
    """R7.7. member_passed is true only when the run succeeded AND derive_verdict
    status is pass, not merely parse_stage_report(stdout).status == pass."""
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=[ParallelAgentSpec(agent_id="architecture_reviewer")],
    )
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id="architecture_reviewer",
        stage_key=stage.key,
        stdout=_pass_stdout(),
    )
    assert member_passed(run) is False


def test_mcp_complete_stage_schema_stays_pass_reject():
    """R7.6. MCP schema stays pass|reject; mcp/tools.py is not the derivation."""
    from loregarden.mcp.tool_ids import McpTool
    from loregarden.mcp.tools import TOOL_DEFINITIONS

    schema = next(item for item in TOOL_DEFINITIONS if item["name"] == McpTool.COMPLETE_STAGE)
    outcome = schema["inputSchema"]["properties"]["outcome"]
    assert set(outcome["enum"]) == {"pass", "reject"}


# --------------------------------------------------------------------------- #
# R8 — TOOL then SENTINEL
# --------------------------------------------------------------------------- #


def test_tool_then_sentinel_does_not_upgrade_or_double_reroute(db_session, tmp_path):
    """R8.1. Tool pass vetoed by incomplete checklist, then sentinel pass for
    the same run: stage is not DONE; rework budget is not incremented twice."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationCallbackService(db_session).complete_stage(
        orch, ticket, stage_key=stage.key, outcome="pass"
    )
    after_tool = rework_reroute_count(db_session, ticket, stage.key)
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE
    after_both = rework_reroute_count(db_session, ticket, stage.key)
    assert after_both == after_tool or after_both == 1
    assert after_both < 2 or after_tool == after_both


def test_sentinel_then_tool_does_not_mark_done(db_session, tmp_path):
    """R8.2. Inverse order also cannot mark DONE on a claimed pass the other
    channel vetoed."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    OrchestrationCallbackService(db_session).complete_stage(
        orch, ticket, stage_key=stage.key, outcome="pass"
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE


# --------------------------------------------------------------------------- #
# R9 — _advance_after_stage DONE-guard
# --------------------------------------------------------------------------- #


def test_advance_after_stage_skips_forward_gates_after_write_path_veto(db_session, tmp_path):
    """R9.1 / R12.15. After a write-path veto that left the stage not DONE,
    `_advance_after_stage` does not call run_gates_with_autofix and does not
    resolve the linear next as pass."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE

    builtin = BuiltinOrchestrator(db_session)
    profile = OrchestrationProfile(slug="verdict-test")
    with (
        patch.object(builtin.gates, "run_gates_with_autofix") as gates,
        patch.object(StateMachine, "resolve_next_stage_key") as resolve,
    ):
        builtin._advance_after_stage(ticket, profile, stage, orch, stage.key)
    assert gates.call_count == 0
    assert resolve.call_count == 0


def test_advance_after_stage_runs_gates_after_derived_pass_left_done(db_session, tmp_path):
    """R9.2. After a derived pass that left the stage DONE, transition command
    gates still run. Do not re-derive here."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    db_session.refresh(ticket)
    # Force DONE so this pin is about the guard, not about whether this fixture
    # earned a pass (no handoff row is not a signal).
    assert _stage_status(db_session, ticket, stage.key) is StageStatus.DONE

    builtin = BuiltinOrchestrator(db_session)
    profile = OrchestrationProfile(slug="verdict-test")
    with patch.object(
        builtin.gates, "run_gates_with_autofix", return_value=GateDecision.PASS
    ) as gates:
        builtin._advance_after_stage(ticket, profile, stage, orch, stage.key)
    assert gates.call_count == 1


def test_advance_after_stage_awaiting_still_pauses(db_session, tmp_path):
    """R9.3. AWAITING still pauses orchestration (existing branch, before the
    new guard's skip of non-DONE)."""
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    instance, _ = OrchestrationService(db_session)._resolve_stages(ticket)
    from loregarden.services.workflow_state import set_stage_status

    set_stage_status(ticket, instance, stages, stage.key, StageStatus.AWAITING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()

    builtin = BuiltinOrchestrator(db_session)
    profile = OrchestrationProfile(slug="verdict-test")
    with (
        patch.object(builtin, "_pause_orchestration", wraps=builtin._pause_orchestration) as pause,
        patch.object(builtin.gates, "run_gates_with_autofix") as gates,
    ):
        result = builtin._advance_after_stage(ticket, profile, stage, orch, stage.key)
    assert pause.call_count == 1
    assert gates.call_count == 0
    assert result is not None


# --------------------------------------------------------------------------- #
# R10 — evidence is not an independent post-DONE verdict
# --------------------------------------------------------------------------- #


def test_run_gates_does_not_treat_missing_evidence_as_gate_decision_failure(
    db_session, tmp_path, git_repo
):
    """R10.1. run_gates_with_autofix does not treat missing required evidence as
    a GateDecision failure. Command-gate failures still do."""
    ticket, orch, stage, stages = _running_ticket(
        db_session,
        tmp_path,
        required_evidence=["real_surface"],
    )
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.repo_path = str(git_repo)
    db_session.add(workspace)
    db_session.commit()
    instance, _ = OrchestrationService(db_session)._resolve_stages(ticket)
    profile = OrchestrationProfile(slug="verdict-test")
    profile.gates.enabled = False
    decision = BuiltinOrchestrator(db_session).gates.run_gates_with_autofix(
        ticket,
        profile,
        stage,
        instance,
        stages,
        orch,
        from_stage=stage.key,
        to_stage="implement",
    )
    assert decision is GateDecision.PASS


# --------------------------------------------------------------------------- #
# R5.2 / R6 persist
# --------------------------------------------------------------------------- #


def test_reroute_context_carries_unresolved_names(db_session, tmp_path):
    """R5.2. Sentinel/tool reroute of a derived non-pass puts names in
    reroute_context so rework_feedback injects them."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    told = render_rework_feedback(db_session, ticket, stage.key)
    assert "handoff:test_suite_complete incomplete" in told


def test_stage_report_artifact_persists_derived_status_after_sentinel_veto(db_session, tmp_path):
    """R6.1. After a sentinel claimed pass vetoed to needs_rework, the context
    artifact titled `Stage report — {stage_key}` has content.status == needs_rework."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    artifact = _stage_report_artifact(db_session, ticket, f"Stage report — {stage.key}")
    assert artifact is not None
    content = json.loads(artifact.content_json or "{}")
    assert content["status"] == "needs_rework"
    rows = {row["k"]: row["v"] for row in content.get("rows", [])}
    assert rows.get("status") == "needs_rework"


def test_parallel_member_artifact_persists_derived_status(db_session, tmp_path):
    """R6.2. Parallel member artifact uses derived status the same way."""
    spec = ParallelAgentSpec(agent_id="architecture_reviewer")
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id=spec.agent_id,
        stage_type="parallel",
        parallel_agents=[spec],
    )
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id=spec.agent_id,
        stage_key=stage.key,
        stdout=_pass_stdout(),
    )
    reconcile_parallel_stage(db_session, ticket, orch, stage.key, [member_result_from_run(run)])
    artifact = _stage_report_artifact(
        db_session, ticket, f"Stage report — {stage.key} ({spec.agent_id})"
    )
    assert artifact is not None
    content = json.loads(artifact.content_json or "{}")
    assert content["status"] != "pass"
    assert content["status"] == "needs_rework"


# --------------------------------------------------------------------------- #
# R11 — writer enumeration / no new store
# --------------------------------------------------------------------------- #


@functools.cache
def _sources() -> dict[str, str]:
    return {
        path.relative_to(_SOURCE_ROOT).as_posix(): path.read_text()
        for path in sorted(_SOURCE_ROOT.rglob("*.py"))
    }


@functools.cache
def _tree(relative: str) -> ast.Module:
    return ast.parse(_sources()[relative], filename=relative)


def _called_names(node: ast.AST) -> set[str]:
    return {
        sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
    }


def _writes_done(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        called = (
            sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
        )
        if called not in _DONE_WRITERS:
            continue
        arguments = list(sub.args) + [kw.value for kw in sub.keywords]
        if any(isinstance(a, ast.Attribute) and a.attr == "DONE" for a in arguments):
            return True
        if called == "apply_stage_route":
            return True
    return False


def _mentions_pass_claim(node: ast.AST) -> bool:
    names = _called_names(node)
    if names & {
        "parse_stage_report",
        "derive_verdict",
        "collect_signals",
        "claim_from_tool_outcome",
    }:
        return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value in {"pass", "reject"}:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in {"PASS", "_PASSING_REPORT_STATUS"}:
            return True
    return False


def _deriving_names(tree: ast.Module) -> set[str]:
    """derive_verdict, plus this module's own wrappers around it."""
    deriving = set(_DERIVE_NAMES)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    changed = True
    while changed:
        changed = False
        for name, node in functions.items():
            if name not in deriving and _called_names(node) & deriving:
                deriving.add(name)
                changed = True
    return deriving


def _discovered_claimed_pass_done_writers() -> dict[str, bool]:
    """Functions in the three write-path modules that mark DONE on a claimed pass."""
    found: dict[str, bool] = {}
    scoped = (
        "services/run_completion.py",
        "services/orchestration_callbacks.py",
        "services/parallel_stage.py",
        "services/orchestration.py",
    )
    for relative in scoped:
        tree = _tree(relative)
        deriving = _deriving_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _writes_done(node) and _mentions_pass_claim(node):
                key = f"{relative}:{node.name}"
                found[key] = bool(_called_names(node) & deriving)
    return found


def test_every_agent_facing_done_on_claimed_pass_writer_goes_through_derive_verdict():
    """R11.1 / R12.17. A new path that can mark DONE on a claimed pass must say
    which it is, and `derives` entries must actually call derive_verdict."""
    discovered = _discovered_claimed_pass_done_writers()

    unenumerated = sorted(set(discovered) - set(VERDICT_WRITERS))
    assert not unenumerated, (
        "These can mark a stage DONE on a claimed pass and are not in VERDICT_WRITERS. "
        "Route the claim through derive_verdict (or through a function that does) and "
        f"record which here: {unenumerated}"
    )

    judges = {
        "services/run_completion.py:_pass_held",
        "services/parallel_stage.py:member_result",
        "services/parallel_stage.py:member_passed",
        "services/run_completion.py:advance_stage_after_run",
        "services/parallel_stage.py:reconcile_parallel_stage",
        "services/orchestration_callbacks.py:complete_stage",
    }
    missing_judges = sorted(judges - set(VERDICT_WRITERS))
    assert not missing_judges, missing_judges

    liars = sorted(
        key
        for key, answer in VERDICT_WRITERS.items()
        if answer == "derives" and not discovered.get(key, False) and key in discovered
    )
    # A `derives` judge that doesn't write DONE is still required to call derive.
    source_liars = []
    for key, answer in VERDICT_WRITERS.items():
        if answer != "derives":
            continue
        relative, name = key.split(":")
        tree = _tree(relative)
        deriving = _deriving_names(tree)
        if name not in deriving:
            source_liars.append(key)
    assert not source_liars, (
        f"Enumerated as 'derives' but never calls derive_verdict: {source_liars}"
    )
    assert not liars, f"Enumerated as 'derives' but the DONE-write scan disagrees: {liars}"


def test_every_exemption_gives_a_reason():
    """The gate can only check that a reason exists; a reviewer checks it is true."""
    thin = sorted(
        key
        for key, answer in VERDICT_WRITERS.items()
        if answer.startswith("exempt") and len(answer) < len("exempt: ") + 40
    )
    assert not thin, f"These exemptions say nothing a reader can check: {thin}"


def test_unresolved_kinds_exist_without_artifactkind_or_column_or_migration():
    """136 R2. UnresolvedArtifactKind exists; no ArtifactKind member, column, or migration."""
    from loregarden.models.domain import ArtifactKind, Ticket
    from loregarden.models.domain.enums import UnresolvedArtifactKind

    assert UnresolvedArtifactKind.OPEN == "unresolved_open"
    assert UnresolvedArtifactKind.WAIVER == "unresolved_waiver"
    assert UnresolvedArtifactKind.CLEAR == "unresolved_clear"
    assert not any("unresolved" in member.value for member in ArtifactKind)
    assert "unresolved_json" not in Ticket.model_fields
    versions = pathlib.Path(__file__).resolve().parents[1] / "loregarden" / "db" / "versions"
    assert list(versions.glob("*unresolved*")) == []


def _function_def(relative: str, name: str) -> ast.FunctionDef | None:
    tree = _tree(relative)
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )


def test_every_unresolved_writer_persists():
    """136 R6 / R11. A new derive/DONE site that skips settle_unresolved fails this test.

    Literal name: invariant unresolved_open_shrink_is_audited.prevented_by points here.
    """
    must = {
        "services/run_completion.py:_pass_held",
        "services/run_completion.py:_stamp_derived_report",
        "services/run_completion.py:_settled_pessimistic_report",
        "services/orchestration_callbacks.py:complete_stage",
        "services/parallel_stage.py:member_result",
        "services/orchestration.py:advance_stage",
    }
    missing = sorted(must - set(UNRESOLVED_WRITERS))
    assert not missing, f"Must-settle writers are not enumerated: {missing}"

    liars = []
    for key, answer in UNRESOLVED_WRITERS.items():
        relative, name = key.split(":")
        node = _function_def(relative, name)
        assert node is not None, f"{key} does not exist"
        called = _called_names(node)
        if answer == "settles" and "settle_unresolved" not in called:
            liars.append(key)
        if answer == "visible_check" and "visible_unresolved" not in called:
            liars.append(key)
    assert not liars, f"Enumerated writers do not call the ledger seam: {liars}"

    discovered = _discovered_claimed_pass_done_writers()
    skips = sorted(
        key
        for key in discovered
        if key not in UNRESOLVED_WRITERS
        and not str(VERDICT_WRITERS.get(key, "")).startswith("exempt")
        and not str(VERDICT_WRITERS.get(key, "")).startswith("delegates")
    )
    assert not skips, (
        "These can mark DONE on a claimed pass and are not in UNRESOLVED_WRITERS. "
        f"Call settle_unresolved (or visible_unresolved for Advance) and record which: {skips}"
    )


def test_settled_pessimistic_report_collects_derives_and_settles():
    """136 R6.2. Claimed-fail must collect+derive+settle so live names first-persist.

    The writer census only checks settle_unresolved. An empty-live settle would
    pass that check and skip first-persist.
    """
    node = _function_def("services/run_completion.py", "_settled_pessimistic_report")
    assert node is not None, "_settled_pessimistic_report does not exist"
    called = _called_names(node)
    missing = sorted({"collect_signals", "derive_verdict", "settle_unresolved"} - called)
    assert not missing, (
        f"_settled_pessimistic_report must collect+derive+settle; missing: {missing}"
    )


def test_apply_stage_route_with_routing_outcome_reject_does_not_take_linear_next(
    db_session, tmp_path
):
    """R7.4 extra: apply_stage_route given routing_outcome(non-pass) does not
    walk the pass/default/linear next."""
    ticket, _orch, stage, stages = _running_ticket(db_session, tmp_path)
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    transitions = OrchestrationService(db_session)._resolve_transitions(ticket)
    verdict = StageVerdict(status=StageReportStatus.FAIL, unresolved=())
    apply_stage_route(
        ticket,
        instance,
        resolved or stages,
        transitions,
        from_key=stage.key,
        outcome=routing_outcome(verdict),
        orch_run=None,
        strict=True,
    )
    db_session.add(ticket)
    if instance is not None:
        db_session.add(instance)
    db_session.commit()
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key != "implement"
    assert ticket.workflow_stage_key == "plan"


# --------------------------------------------------------------------------- #
# Breaker — R5 carry, R8 fail channel, R10 hole, R11 driver isolation
# --------------------------------------------------------------------------- #


def test_run_gates_ignores_missing_evidence_even_when_gates_enabled(db_session, tmp_path, git_repo):
    """Breaker / R10.1. Designer pin used gates.enabled=False. Missing evidence
    must not be a GateDecision failure when gates are on and the command would pass."""
    ticket, orch, stage, stages = _running_ticket(
        db_session, tmp_path, required_evidence=["real_surface"]
    )
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.repo_path = str(git_repo)
    db_session.add(workspace)
    db_session.commit()
    instance, _ = OrchestrationService(db_session)._resolve_stages(ticket)
    profile = OrchestrationProfile(slug="verdict-test")
    profile.gates.enabled = True
    profile.gates.commands = ["true"]
    profile.gates.autofix_commands = []
    profile.gates.autofix_agent_fallback = False
    decision = BuiltinOrchestrator(db_session).gates.run_gates_with_autofix(
        ticket,
        profile,
        stage,
        instance,
        stages,
        orch,
        from_stage=stage.key,
        to_stage="implement",
    )
    assert decision is GateDecision.PASS


def test_command_gate_failure_still_is_not_pass(db_session, tmp_path, git_repo):
    """Breaker / R10.1. Command-gate failures still fail after evidence leaves the gate."""
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.repo_path = str(git_repo)
    db_session.add(workspace)
    db_session.commit()
    instance, _ = OrchestrationService(db_session)._resolve_stages(ticket)
    profile = OrchestrationProfile(slug="verdict-test")
    profile.gates.enabled = True
    profile.gates.commands = ["false"]
    profile.gates.autofix_commands = []
    profile.gates.autofix_agent_fallback = False
    decision = BuiltinOrchestrator(db_session).gates.run_gates_with_autofix(
        ticket,
        profile,
        stage,
        instance,
        stages,
        orch,
        from_stage=stage.key,
        to_stage="implement",
    )
    assert decision is not GateDecision.PASS


def test_claimed_fail_with_incomplete_checklist_keeps_agent_reroute(db_session, tmp_path):
    """Breaker / R5. Claimed fail keeps the agent's reroute_to_stage; checklist
    veto of a claimed pass is the only case that pins current stage."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_fail_stdout()
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE
    assert ticket.workflow_stage_key == "plan"


def test_checklist_veto_of_claimed_pass_reroutes_to_current_stage(db_session, tmp_path):
    """Breaker / R5. Veto of a claimed pass sets reroute_to_stage to this stage."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    artifact = _stage_report_artifact(db_session, ticket, f"Stage report — {stage.key}")
    assert artifact is not None
    content = json.loads(artifact.content_json or "{}")
    assert content["status"] == "needs_rework"
    assert content["reroute_to_stage"] == stage.key


def test_reroute_context_names_are_one_per_line_sorted(db_session, tmp_path):
    """Breaker / R5.2. Names go in reroute_context one per line, sorted."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    store_handoff(
        db_session,
        ticket=ticket,
        doc=build_handoff_doc(
            external_id=ticket.external_id,
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=[
                {
                    "item_key": "test_suite_complete",
                    "item": "Test suite complete",
                    "required": True,
                    "status": "incomplete",
                    "certainty": "inferred",
                    "evidence": "",
                },
                {
                    "item_key": "test_all_runnable",
                    "item": "All tests runnable",
                    "required": True,
                    "status": "incomplete",
                    "certainty": "inferred",
                    "evidence": "",
                },
            ],
            required_items_met=0,
            total_required_items=2,
            boundary=GitBoundary(),
        ),
    )
    db_session.commit()
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    told = render_rework_feedback(db_session, ticket, stage.key)
    expected = "handoff:test_all_runnable incomplete\nhandoff:test_suite_complete incomplete"
    assert expected in told
    assert "handoff:test_all_runnable incomplete" in told.splitlines()
    assert "handoff:test_suite_complete incomplete" in told.splitlines()


def test_agent_reroute_context_is_kept_above_unresolved_names(db_session, tmp_path):
    """Breaker / R5. Agent-written reroute_context stays above a blank line, then names."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=_pass_stdout_with_context("still owe the catalog"),
    )
    told = render_rework_feedback(db_session, ticket, stage.key)
    assert told.startswith("still owe the catalog\n\n")
    assert "handoff:test_suite_complete incomplete" in told.splitlines()


def test_tool_pass_then_sentinel_fail_does_not_upgrade_or_double_reroute(db_session, tmp_path):
    """Breaker / R8. Tool pass vetoed, then sentinel fail: fail wins, one rework round."""
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationCallbackService(db_session).complete_stage(
        orch, ticket, stage_key=stage.key, outcome="pass"
    )
    after_tool = rework_reroute_count(db_session, ticket, stage.key)
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_fail_stdout()
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE
    after_both = rework_reroute_count(db_session, ticket, stage.key)
    assert after_both == after_tool or after_both == 1
    assert after_both < 2 or after_tool == after_both


def test_member_result_from_run_is_a_failure_when_derived_not_pass(db_session, tmp_path):
    """Breaker / R7.3. member_result (via from_run) must fail closed on an
    incomplete required checklist, not only member_passed."""
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=[ParallelAgentSpec(agent_id="architecture_reviewer")],
    )
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id="architecture_reviewer",
        stage_key=stage.key,
        stdout=_pass_stdout(),
    )
    result = member_result_from_run(run)
    assert result.failure != ""
    assert result.transient is False


def test_parallel_drivers_and_hotspots_do_not_call_derive_verdict():
    """Breaker / R7 / R11. Derivation lives in parallel_stage / the three writers.
    Drivers and mcp/tools.py / orchestration.py must not rank."""
    forbidden = (
        "services/builtin_orchestrator.py",
        "services/external_harness.py",
        "services/review_relens.py",
        "services/run_resupervise.py",
        "services/orchestration.py",
        "mcp/tools.py",
    )
    for relative in forbidden:
        called = _called_names(_tree(relative))
        assert "derive_verdict" not in called, relative
        assert "collect_signals" not in called, relative
