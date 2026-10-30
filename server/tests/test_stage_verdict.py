"""One pessimistic StageVerdict from live signals (lg-verdict-outcome-135).

Pins R1–R5, R7 mapper, and the R12 assembler pins. Writer call sites live in
`test_stage_verdict_writers.py`. complete+inferred is a PASS pin, not a fail pin.
Do not call standing_of.proves.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from dataclasses import fields
from pathlib import Path
from uuid import uuid4

from loregarden.agents.registry import SPEC_AGENT_ID, UI_DESIGN_AGENT_ID
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.models.domain import (
    AgentRun,
    ClassifyRoute,
    GitBoundary,
    ParallelAgentSpec,
    StageReportStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.handoff_store import build_handoff_doc, store_handoff
from loregarden.services.operation_criteria import pending_operation_criteria
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.stage_report import StageReport
from loregarden.services.stage_verdict import (
    StageVerdict,
    UnresolvedItem,
    VerdictSignal,
    claim_from_tool_outcome,
    collect_signals,
    derive_verdict,
    routing_outcome,
)
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _pass_report(**fields) -> StageReport:
    return StageReport(
        status="pass", confidence=0.9, reroute_to_stage=None, reroute_context="", **fields
    )


def _report(
    status: str, *, reroute_to_stage: str | None = None, reroute_context: str = ""
) -> StageReport:
    return StageReport(
        status=status,
        confidence=0.9,
        reroute_to_stage=reroute_to_stage,
        reroute_context=reroute_context,
    )


def _sig(
    outcome: StageReportStatus | None,
    *,
    source: str = "claimed",
    item_key: str = "report:pass",
) -> VerdictSignal:
    return VerdictSignal(source=source, item_key=item_key, outcome=outcome)


def _names(verdict: StageVerdict) -> list[str]:
    return [item.name for item in verdict.unresolved]


def _stage(
    key: str = "spec",
    *,
    agent_id: str = "spec",
    order: int = 2,
    required_evidence: list[str] | None = None,
    stage_type: str = "agent",
    parallel_agents: list[ParallelAgentSpec] | None = None,
    classify_routes: list[ClassifyRoute] | None = None,
) -> WorkflowStageDef:
    return WorkflowStageDef(
        key=key,
        name=key,
        agent_id=agent_id,
        order=order,
        stage_type=stage_type,
        required_evidence=required_evidence or [],
        parallel_agents=parallel_agents or [],
        classify_routes=classify_routes or [],
    )


def _ticket(
    session: Session,
    tmp_path: Path,
    *,
    repo_path: str | None = None,
    stage_key: str = "spec",
    agent_id: str = "spec",
    required_evidence: list[str] | None = None,
    stage_type: str = "agent",
    parallel_agents: list[ParallelAgentSpec] | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
    title: str = "Verdict ticket",
    extra_stages: list[WorkflowStageDef] | None = None,
    acceptance_criteria_json: str = "",
) -> tuple[Ticket, WorkflowStageDef, list[WorkflowStageDef]]:
    """A running stage with a workflow instance, no exit actions, no gates."""
    current = _stage(
        stage_key,
        agent_id=agent_id,
        order=2,
        required_evidence=required_evidence,
        stage_type=stage_type,
        parallel_agents=parallel_agents,
    )
    stages = [
        _stage("plan", agent_id="planner", order=1),
        current,
        _stage("implement", agent_id="backend_implementer", order=3),
        WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
        *(extra_stages or []),
    ]
    template = WorkflowTemplate(
        slug=f"verdict-tpl-{uuid4().hex[:8]}",
        name="Verdict",
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
        slug=f"verdict-ws-{uuid4().hex[:8]}",
        name="Verdict",
        repo_path=repo_path or str(tmp_path / "no-repo"),
        workflow_template_id=template.id,
    )
    session.add(workspace)
    session.commit()
    ticket = Ticket(
        external_id=f"verdict-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title=title,
        state=TicketState.IN_PROGRESS,
        work_item_type=work_item_type,
        workflow_stage_key=stage_key,
        workflow_stage_status=StageStatus.RUNNING,
        acceptance_criteria_json=acceptance_criteria_json,
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
    session.commit()
    return ticket, current, stages


def _handoff(
    session: Session,
    ticket: Ticket,
    checklist: list[dict],
    *,
    from_agent: str = "test_designer",
    to_agent: str = "test_breaker",
) -> None:
    required = [item for item in checklist if item.get("required", True)]
    met = sum(1 for item in required if item.get("status") == "complete")
    store_handoff(
        session,
        ticket=ticket,
        doc=build_handoff_doc(
            external_id=ticket.external_id,
            from_agent=from_agent,
            to_agent=to_agent,
            checklist=checklist,
            required_items_met=met,
            total_required_items=len(required),
            boundary=GitBoundary(),
        ),
    )
    session.commit()


def _item(
    item_key: str = "test_suite_complete",
    *,
    status: str = "complete",
    required: bool = True,
    certainty: str = "inferred",
    item: str = "Test suite complete",
) -> dict:
    return {
        "item_key": item_key,
        "item": item,
        "required": required,
        "status": status,
        "certainty": certainty,
        "evidence": "tests/x.py",
    }


_UNSET = object()


def _verdict(
    session: Session,
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    *,
    report: StageReport | None | object = _UNSET,
    tool_outcome: StageReportStatus | None = None,
    handoff_expected: bool = True,
    agent_id: str = "spec",
) -> StageVerdict:
    if report is _UNSET:
        report = _pass_report()
    signals = collect_signals(
        session,
        ticket,
        report=report,
        tool_outcome=tool_outcome,
        stage_def=stage_def,
        handoff_expected=handoff_expected,
        agent_id=agent_id,
    )
    return derive_verdict(signals)


def _collect(
    session: Session,
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    *,
    report: StageReport | None | object = _UNSET,
    tool_outcome: StageReportStatus | None = None,
    handoff_expected: bool = True,
    agent_id: str = "spec",
) -> list[VerdictSignal]:
    if report is _UNSET:
        report = _pass_report()
    return collect_signals(
        session,
        ticket,
        report=report,
        tool_outcome=tool_outcome,
        stage_def=stage_def,
        handoff_expected=handoff_expected,
        agent_id=agent_id,
    )


def _write_pair_catalog(repo: Path) -> None:
    gates = repo / "ci" / "scripts" / "gates"
    gates.mkdir(parents=True)
    (gates / "__init__.py").write_text("", encoding="utf-8")
    (gates / "handoff_validation_check.py").write_text(
        textwrap.dedent(
            """
            PAIR_CATALOGS = {
                ("test_designer", "test_breaker"): [
                    {
                        "item_key": "test_suite_complete",
                        "required": True,
                        "item": "Test suite complete",
                    },
                    {
                        "item_key": "test_all_runnable",
                        "required": True,
                        "item": "All tests runnable",
                    },
                ]
            }

            def run(inputs):
                return {"status": "PASS", "message": "ok"}
            """
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# R1 — pure derive_verdict
# --------------------------------------------------------------------------- #


def test_verdict_types_exist():
    """R1.5. The three types are instantiable with the frozen fields."""
    signal = VerdictSignal(source="claimed", item_key="report:pass", outcome=StageReportStatus.PASS)
    assert signal.outcome is StageReportStatus.PASS
    item = UnresolvedItem(name="report:fail")
    verdict = StageVerdict(status=StageReportStatus.FAIL, unresolved=(item,))
    assert verdict.status is StageReportStatus.FAIL
    assert verdict.unresolved == (item,)
    assert {"producer_agent_id", "producer_stage_key"} <= {f.name for f in fields(VerdictSignal)}
    assert {"producer_agent_id", "producer_stage_key"} <= {f.name for f in fields(UnresolvedItem)}


def test_derive_verdict_is_session_free():
    """R1 / AC12. derive_verdict has no Session, Ticket, or git in its signature."""
    params = inspect.signature(derive_verdict).parameters
    assert "session" not in params
    assert "ticket" not in params
    hints = str(inspect.signature(derive_verdict))
    assert "Session" not in hints
    assert "Ticket" not in hints


def test_derive_verdict_pass_then_fail_is_fail():
    """R1.1 / AC1. Last-written does not win; fail outranks pass either order."""
    pass_sig = _sig(StageReportStatus.PASS, item_key="report:pass")
    fail_sig = _sig(StageReportStatus.FAIL, item_key="report:fail")
    assert derive_verdict([pass_sig, fail_sig]).status is StageReportStatus.FAIL
    assert derive_verdict([fail_sig, pass_sig]).status is StageReportStatus.FAIL


def test_derive_verdict_needs_rework_and_blocked_is_blocked():
    """R1.2. blocked (3) outranks needs_rework (1)."""
    verdict = derive_verdict(
        [
            _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:x incomplete"),
            _sig(StageReportStatus.BLOCKED, item_key="operation_criteria pending"),
        ]
    )
    assert verdict.status is StageReportStatus.BLOCKED


def test_derive_verdict_only_pass_has_empty_unresolved():
    """R1.3 / R5.1. A pass carries no unresolved names."""
    verdict = derive_verdict([_sig(StageReportStatus.PASS)])
    assert verdict.status is StageReportStatus.PASS
    assert verdict.unresolved == ()


def test_derive_verdict_empty_signals_is_not_pass():
    """R1.4. Absence cannot produce a pass."""
    verdict = derive_verdict(())
    assert verdict.status is not StageReportStatus.PASS
    assert verdict.status is StageReportStatus.NEEDS_REWORK


def test_checklist_veto_of_claimed_pass_is_needs_rework_not_fail():
    """R1 / R4.7. A checklist veto of a claimed pass is needs_rework; a real fail still outranks it."""
    veto = _sig(
        StageReportStatus.NEEDS_REWORK,
        source="handoff",
        item_key="test_suite_complete incomplete",
    )
    claimed_pass = _sig(StageReportStatus.PASS, item_key="report:pass")
    claimed_fail = _sig(StageReportStatus.FAIL, item_key="report:fail")
    assert derive_verdict([claimed_pass, veto]).status is StageReportStatus.NEEDS_REWORK
    assert derive_verdict([claimed_fail, veto]).status is StageReportStatus.FAIL


# --------------------------------------------------------------------------- #
# R7 mapper
# --------------------------------------------------------------------------- #


def test_routing_outcome_pass_only_when_status_is_pass():
    """R7. routing_outcome is pass iff status is PASS; else reject."""
    passing = StageVerdict(status=StageReportStatus.PASS, unresolved=())
    assert routing_outcome(passing) == "pass"
    for status in (
        StageReportStatus.FAIL,
        StageReportStatus.NEEDS_REWORK,
        StageReportStatus.BLOCKED,
    ):
        verdict = StageVerdict(
            status=status,
            unresolved=(UnresolvedItem(name=f"report:{status.value}"),),
        )
        assert routing_outcome(verdict) == "reject"


def test_claim_from_tool_outcome_maps_mcp_reject_to_needs_rework():
    """Settled checkpoint: MCP reject is a needs_rework claim. Schema stays pass|reject."""
    assert claim_from_tool_outcome("pass") is StageReportStatus.PASS
    assert claim_from_tool_outcome("reject") is StageReportStatus.NEEDS_REWORK


def test_collect_signals_requires_agent_id():
    """Spec checkpoint: agent_id is a required kwarg; not looked up from AgentRun."""
    params = inspect.signature(collect_signals).parameters
    assert "agent_id" in params
    assert params["agent_id"].default is inspect.Parameter.empty
    assert "stages" not in params


def test_derive_verdict_copies_producer_onto_unresolved_item():
    """136 R1. Producer stamped at collect is copied onto the live unresolved item."""
    signal = VerdictSignal(
        source="handoff",
        item_key="handoff:test_suite_complete incomplete",
        outcome=StageReportStatus.NEEDS_REWORK,
        producer_agent_id="spec",
        producer_stage_key="",
    )
    verdict = derive_verdict([signal])
    assert verdict.unresolved[0].producer_agent_id == "spec"
    assert verdict.unresolved[0].producer_stage_key == ""


def test_producer_fields_do_not_change_ranking():
    """136 R1. Ranking is still pass < needs_rework < fail < blocked; producer is identity only."""
    pass_sig = VerdictSignal(
        source="claimed",
        item_key="report:pass",
        outcome=StageReportStatus.PASS,
        producer_agent_id="spec",
        producer_stage_key="spec",
    )
    fail_sig = VerdictSignal(
        source="claimed",
        item_key="report:fail",
        outcome=StageReportStatus.FAIL,
        producer_agent_id="other",
        producer_stage_key="implement",
    )
    assert derive_verdict([pass_sig, fail_sig]).status is StageReportStatus.FAIL
    assert derive_verdict([fail_sig, pass_sig]).status is StageReportStatus.FAIL


def test_handoff_signals_stamp_from_agent_and_empty_stage_key(db_session, tmp_path):
    """136 R1. Handoff producer is envelope.from_agent; stage_key empty-ok."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")], from_agent="spec")
    signals = _collect(db_session, ticket, stage, agent_id="test_designer")
    handoff = next(s for s in signals if s.item_key == "handoff:test_suite_complete incomplete")
    assert handoff.producer_agent_id == "spec"
    assert handoff.producer_stage_key == ""


def test_claimed_and_folded_signals_stamp_finishing_run(db_session, tmp_path):
    """136 R1. Report / tool / folded producer is the finishing run's agent_id + stage_key."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        agent_id=UI_DESIGN_AGENT_ID,
        title="Group the monitor findings by ticket",
    )
    signals = _collect(
        db_session,
        ticket,
        stage,
        report=_report("fail"),
        agent_id=UI_DESIGN_AGENT_ID,
    )
    claimed = next(s for s in signals if s.item_key == "report:fail")
    assert claimed.producer_agent_id == UI_DESIGN_AGENT_ID
    assert claimed.producer_stage_key == stage.key

    folded = _collect(db_session, ticket, stage, agent_id=UI_DESIGN_AGENT_ID)
    ux = next(s for s in folded if s.item_key == "ux_answers missing")
    assert ux.producer_agent_id == UI_DESIGN_AGENT_ID
    assert ux.producer_stage_key == stage.key


def test_evidence_signals_stamp_declaring_stage(db_session, tmp_path, git_repo):
    """136 R1. Evidence producer_stage_key is the stage that declared required_evidence."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        required_evidence=["real_surface"],
        repo_path=str(git_repo),
        agent_id="spec",
        stage_key="spec",
    )
    signals = _collect(db_session, ticket, stage, agent_id="spec")
    missing = next(s for s in signals if s.item_key == "evidence:real_surface missing")
    assert missing.producer_agent_id == "spec"
    assert missing.producer_stage_key == "spec"


# --------------------------------------------------------------------------- #
# R2 — unjudgeable
# --------------------------------------------------------------------------- #


def test_fail_plus_unjudgeable_evidence_stays_fail(db_session, tmp_path):
    """R2.1 / R12.9. Unjudgeable-required does not upgrade fail."""
    ticket, stage, _ = _ticket(
        db_session, tmp_path, required_evidence=["real_surface"], repo_path=str(tmp_path / "empty")
    )
    verdict = _verdict(db_session, ticket, stage, report=_report("fail"), agent_id="spec")
    assert verdict.status is StageReportStatus.FAIL
    assert "evidence:unjudgeable" in _names(verdict)


def test_claimed_pass_plus_unjudgeable_required_evidence_is_not_pass(db_session, tmp_path):
    """R2.2 / R12.10. Empty HEAD + declared required_evidence: claimed pass cannot win."""
    ticket, stage, _ = _ticket(
        db_session, tmp_path, required_evidence=["real_surface"], repo_path=str(tmp_path / "empty")
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is not StageReportStatus.PASS
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "evidence:unjudgeable" in _names(verdict)


def test_no_handoff_row_claimed_pass_is_pass(db_session, tmp_path):
    """R2.3 / R12.3. Missing row is not a signal even for a finishing agent."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert verdict.unresolved == ()


def test_parallel_member_with_no_handoff_and_report_pass_is_pass(db_session, tmp_path):
    """R2.4 / R12.4. Parallel member, no handoff, report pass → pass."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id="architecture_reviewer"),
            ParallelAgentSpec(agent_id="static_qa"),
        ],
    )
    verdict = _verdict(
        db_session,
        ticket,
        stage,
        handoff_expected=False,
        agent_id="architecture_reviewer",
    )
    assert verdict.status is StageReportStatus.PASS


def test_empty_required_evidence_is_not_a_signal(db_session, tmp_path):
    """R2.5 / R4.2 / R12.8. Undeclared evidence cannot veto a claimed pass."""
    ticket, stage, _ = _ticket(db_session, tmp_path, required_evidence=[])
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert not any(name.startswith("evidence:") for name in _names(verdict))


def test_light_route_does_not_emit_evidence_signal(db_session, tmp_path):
    """R2.6. took_light_route true → evidence is not a signal even if declared."""
    extra = [
        _stage(
            "triage",
            agent_id="ticket_scoper",
            order=0,
            stage_type="classify",
            classify_routes=[
                ClassifyRoute(specialties=["typo"], agent_id="ticket_scoper", to_stage="spec"),
                ClassifyRoute(agent_id="ticket_scoper", default=True),
            ],
        )
    ]
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        required_evidence=["real_surface"],
        title="Fix typo in the settings header",
        extra_stages=extra,
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert not any(name.startswith("evidence:") for name in _names(verdict))
    assert verdict.status is StageReportStatus.PASS


# --------------------------------------------------------------------------- #
# R3 — handoff is status, not standing.proves
# --------------------------------------------------------------------------- #


def test_claimed_pass_with_incomplete_required_checklist_is_not_pass(db_session, tmp_path):
    """R3.1 / R12.1. outcome=pass cannot override status != complete."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")])
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is not StageReportStatus.PASS
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "handoff:test_suite_complete incomplete" in _names(verdict)


def test_complete_inferred_required_items_can_be_pass(db_session, tmp_path):
    """R3.2 / R12.2. certainty=inferred on a complete item is not a veto. PASS pin."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(
        db_session,
        ticket,
        [
            _item(status="complete", certainty="inferred"),
            _item(
                "test_all_runnable",
                status="complete",
                certainty="inferred",
                item="All tests runnable",
            ),
        ],
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert verdict.unresolved == ()


def test_required_catalog_key_absent_from_existing_row_is_missing(db_session, tmp_path):
    """R3.3. Catalog loadable + required key absent from the row → handoff:{key} missing."""
    repo = tmp_path / "catalog-repo"
    repo.mkdir()
    _write_pair_catalog(repo)
    ticket, stage, _ = _ticket(db_session, tmp_path, repo_path=str(repo))
    _handoff(db_session, ticket, [_item(status="complete", certainty="inferred")])
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is not StageReportStatus.PASS
    assert "handoff:test_all_runnable missing" in _names(verdict)


def test_unloadable_catalog_does_not_invent_missing_keys(db_session, tmp_path):
    """R3. Catalog cannot be loaded → skip absent-key detection; still judge items on the row."""
    ticket, stage, _ = _ticket(db_session, tmp_path, repo_path=str(tmp_path / "no-ci"))
    _handoff(db_session, ticket, [_item(status="complete", certainty="inferred")])
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert not any(name.endswith(" missing") for name in _names(verdict))


def test_handoff_row_on_parallel_member_with_incomplete_item_is_not_pass(db_session, tmp_path):
    """R3.4 / R12.5. A row that exists is judged even on a parallel member."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id="architecture_reviewer"),
            ParallelAgentSpec(agent_id="static_qa"),
        ],
    )
    _handoff(db_session, ticket, [_item(status="incomplete")])
    verdict = _verdict(
        db_session,
        ticket,
        stage,
        handoff_expected=False,
        agent_id="architecture_reviewer",
    )
    assert verdict.status is not StageReportStatus.PASS
    assert "handoff:test_suite_complete incomplete" in _names(verdict)


def test_deferred_or_blocked_required_item_vetoes_pass(db_session, tmp_path):
    """R3.5. status != complete (deferred or blocked) is the same veto as incomplete.

    CHECKPOINT: the name table only has incomplete|missing. A present required
    item whose status is not complete uses the `incomplete` suffix, not a third one.
    """
    for status in ("deferred", "blocked"):
        ticket, stage, _ = _ticket(db_session, tmp_path)
        _handoff(db_session, ticket, [_item(status=status)])
        verdict = _verdict(db_session, ticket, stage, agent_id="spec")
        assert verdict.status is not StageReportStatus.PASS, status
        assert "handoff:test_suite_complete incomplete" in _names(verdict), status


def test_collect_signals_does_not_use_standing_of_proves():
    """R3.6. AC3 is status, not standing_of.proves — factory-killer if copied."""
    source = Path(__file__).resolve().parents[1] / "loregarden" / "services" / "stage_verdict.py"
    text = source.read_text(encoding="utf-8")
    assert "standing_of" not in text
    assert ".proves" not in text


# --------------------------------------------------------------------------- #
# R4 — evidence-missing and folded _pass_held
# --------------------------------------------------------------------------- #


def test_claimed_pass_with_missing_required_evidence_is_not_pass(db_session, git_repo):
    """R4.1 / R12.7. Declared kind missing at a readable HEAD → evidence:{kind} missing."""
    ticket, stage, _ = _ticket(
        db_session,
        git_repo,
        required_evidence=["real_surface"],
        repo_path=str(git_repo),
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is not StageReportStatus.PASS
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "evidence:real_surface missing" in _names(verdict)


def test_missing_ux_answers_vetoes_claimed_pass(db_session, tmp_path):
    """R4.3. Same agent gate as today: ui-design agent only."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        stage_key="ui-design",
        agent_id=UI_DESIGN_AGENT_ID,
        title="Group the monitor findings by ticket",
    )
    design = _verdict(db_session, ticket, stage, agent_id=UI_DESIGN_AGENT_ID)
    assert design.status is StageReportStatus.NEEDS_REWORK
    assert "ux_answers missing" in _names(design)
    other = _verdict(db_session, ticket, stage, agent_id="planner")
    assert "ux_answers missing" not in _names(other)


def test_missing_fix_coverage_vetoes_claimed_pass(db_session, tmp_path):
    """R4.4. Same agent/type gate as today: spec agent + bug."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        work_item_type=WorkItemType.BUG,
        title="A ticket stays blocked after the repair",
    )
    spec = _verdict(db_session, ticket, stage, agent_id=SPEC_AGENT_ID)
    assert spec.status is StageReportStatus.NEEDS_REWORK
    assert "fix_coverage missing" in _names(spec)
    other = _verdict(db_session, ticket, stage, agent_id="planner")
    assert "fix_coverage missing" not in _names(other)


def test_pending_operation_criteria_blocks_claimed_pass_for_spec_agent(db_session, tmp_path):
    """R4.5. operation_criteria pending + spec agent → blocked, not rework."""
    criterion = (
        "AC-B5: the 7 commits are NOT re-signed in place; the ruleset-19407647 admin "
        "bypass is NOT used; and the force-update of the published ref plus the new PR "
        "are raised through loregarden_request_approval."
    )
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        work_item_type=WorkItemType.FEATURE,
        acceptance_criteria_json=serialize_criteria([criterion]),
    )
    assert pending_operation_criteria(db_session, ticket)
    verdict = _verdict(db_session, ticket, stage, agent_id=SPEC_AGENT_ID)
    assert verdict.status is StageReportStatus.BLOCKED
    assert "operation_criteria pending" in _names(verdict)


def test_claimed_fail_is_not_upgraded_to_blocked_by_operation_criteria(db_session, tmp_path):
    """R4.6. Folded predicates emit only when a claimed pass exists."""
    criterion = (
        "AC-B5: the 7 commits are NOT re-signed in place; the ruleset-19407647 admin "
        "bypass is NOT used; and the force-update of the published ref plus the new PR "
        "are raised through loregarden_request_approval."
    )
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        work_item_type=WorkItemType.FEATURE,
        acceptance_criteria_json=serialize_criteria([criterion]),
    )
    verdict = _verdict(db_session, ticket, stage, report=_report("fail"), agent_id=SPEC_AGENT_ID)
    assert verdict.status is StageReportStatus.FAIL
    assert "operation_criteria pending" not in _names(verdict)


def test_fail_outranks_evidence_veto_of_a_claimed_pass(db_session, git_repo):
    """R4.7. A real claimed fail outranks an evidence veto of a claimed pass."""
    ticket, stage, _ = _ticket(
        db_session,
        git_repo,
        required_evidence=["real_surface"],
        repo_path=str(git_repo),
    )
    verdict = _verdict(db_session, ticket, stage, report=_report("fail"), agent_id="spec")
    assert verdict.status is StageReportStatus.FAIL
    assert "evidence:real_surface missing" in _names(verdict)


# --------------------------------------------------------------------------- #
# R5 — named unresolved list, live carry
# --------------------------------------------------------------------------- #


def test_non_pass_verdict_names_are_sorted_unique(db_session, tmp_path):
    """R5. Unresolved tuple is sorted lexicographically by name, unique."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(
        db_session,
        ticket,
        [_item(status="incomplete"), _item(status="incomplete")],
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    names = _names(verdict)
    assert names == sorted(names)
    assert len(names) == len(set(names))
    assert "handoff:test_suite_complete incomplete" in names


def test_unresolved_items_section_present_when_nonempty_absent_when_empty(db_session, tmp_path):
    """R5.3 / R12.16. Heading exactly `## Unresolved items`; empty list adds no section."""
    ticket, stage, stages = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")])
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="spec",
        stage_key=stage.key,
    )
    nonempty = build_orchestration_context(
        ticket=ticket, run=run, stage_def=stage, stages=stages, session=db_session
    )
    assert "## Unresolved items" in nonempty
    assert "`handoff:test_suite_complete incomplete`" in nonempty

    ticket_ok, stage_ok, stages_ok = _ticket(db_session, tmp_path)
    run_ok = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket_ok.id,
        workspace_id=ticket_ok.workspace_id,
        agent_id="spec",
        stage_key=stage_ok.key,
    )
    empty = build_orchestration_context(
        ticket=ticket_ok,
        run=run_ok,
        stage_def=stage_ok,
        stages=stages_ok,
        session=db_session,
    )
    assert "## Unresolved items" not in empty


def test_build_orchestration_context_skips_unresolved_when_session_is_none(db_session, tmp_path):
    """R5.5. session is None: no unresolved section (same as skipped rework_feedback)."""
    ticket, stage, stages = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")])
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="spec",
        stage_key=stage.key,
    )
    text = build_orchestration_context(
        ticket=ticket, run=run, stage_def=stage, stages=stages, session=None
    )
    assert "## Unresolved items" not in text


def test_unresolved_context_lines_reads_visible_unresolved():
    """136 R3 / R11. Prompt assembler reads the ledger, never live derive alone."""
    path = Path(__file__).resolve().parents[1] / "loregarden" / "agents" / "stage_context.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_unresolved_context_lines"
    )
    called = {
        sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
        for sub in ast.walk(fn)
        if isinstance(sub, ast.Call)
    }
    assert "visible_unresolved" in called


def test_later_complete_handoff_drops_resolved_handoff_names(db_session, tmp_path):
    """R5.4. Next stage re-derives from live signals; a later complete handoff drops names."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")])
    first = _verdict(db_session, ticket, stage, agent_id="spec")
    assert "handoff:test_suite_complete incomplete" in _names(first)
    _handoff(db_session, ticket, [_item(status="complete", certainty="inferred")])
    later = _verdict(db_session, ticket, stage, agent_id="spec")
    assert later.status is StageReportStatus.PASS
    assert "handoff:test_suite_complete incomplete" not in _names(later)


def test_tool_reject_is_named_on_unresolved(db_session, tmp_path):
    """R5 name table: MCP reject → tool:reject."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    verdict = _verdict(
        db_session,
        ticket,
        stage,
        report=None,
        tool_outcome=claim_from_tool_outcome("reject"),
        agent_id="spec",
    )
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "tool:reject" in _names(verdict)


def test_report_fail_is_named_on_unresolved(db_session, tmp_path):
    """R5 name table: claimed sentinel fail → report:fail."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    verdict = _verdict(db_session, ticket, stage, report=_report("fail"), agent_id="spec")
    assert "report:fail" in _names(verdict)


# --------------------------------------------------------------------------- #
# Breaker — ranking, dual claim, optional items, unjudgeable leftover
# --------------------------------------------------------------------------- #


def test_derive_verdict_fail_and_blocked_is_blocked_either_order():
    """Breaker / R1. blocked (3) outranks fail (2); last-written still does not win."""
    fail_sig = _sig(StageReportStatus.FAIL, item_key="report:fail")
    blocked_sig = _sig(StageReportStatus.BLOCKED, item_key="report:blocked")
    assert derive_verdict([fail_sig, blocked_sig]).status is StageReportStatus.BLOCKED
    assert derive_verdict([blocked_sig, fail_sig]).status is StageReportStatus.BLOCKED


def test_derive_verdict_needs_rework_and_fail_is_fail_either_order():
    """Breaker / R1. fail (2) outranks needs_rework (1) either order.

    Every live pessimistic input is still named (R5), not only the max rank.
    """
    rework = _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:x incomplete")
    fail_sig = _sig(StageReportStatus.FAIL, item_key="report:fail")
    forward = derive_verdict([rework, fail_sig])
    reverse = derive_verdict([fail_sig, rework])
    assert forward.status is StageReportStatus.FAIL
    assert reverse.status is StageReportStatus.FAIL
    assert "handoff:x incomplete" in _names(forward)
    assert "report:fail" in _names(forward)
    assert _names(forward) == _names(reverse)


def test_derive_verdict_all_four_ranks_is_blocked_either_order():
    """Breaker / R1. Max among the full rank set is blocked regardless of order."""
    signals = [
        _sig(StageReportStatus.PASS, item_key="report:pass"),
        _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:x incomplete"),
        _sig(StageReportStatus.FAIL, item_key="report:fail"),
        _sig(StageReportStatus.BLOCKED, item_key="operation_criteria pending"),
    ]
    assert derive_verdict(signals).status is StageReportStatus.BLOCKED
    assert derive_verdict(list(reversed(signals))).status is StageReportStatus.BLOCKED


def test_derive_verdict_equal_rank_unions_sorted_unique_names():
    """Breaker / R1. Tie keeps that rank; unresolved is the union of names, sorted unique."""
    verdict = derive_verdict(
        [
            _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:b incomplete"),
            _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:a incomplete"),
            _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:b incomplete"),
        ]
    )
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert _names(verdict) == ["handoff:a incomplete", "handoff:b incomplete"]


def test_unjudgeable_none_outcome_is_omitted_from_max_but_named():
    """Breaker / R1.5 / R2. outcome=None is unjudgeable-required: omitted from max,
    name still carried. A real fail still wins."""
    unjudgeable = _sig(None, item_key="evidence:unjudgeable")
    fail_sig = _sig(StageReportStatus.FAIL, item_key="report:fail")
    pass_sig = _sig(StageReportStatus.PASS, item_key="report:pass")
    verdict = derive_verdict([pass_sig, unjudgeable, fail_sig])
    assert verdict.status is StageReportStatus.FAIL
    assert "evidence:unjudgeable" in _names(verdict)
    only_none = derive_verdict([unjudgeable])
    assert only_none.status is not StageReportStatus.PASS
    assert "evidence:unjudgeable" in _names(only_none)


def test_claimed_pass_does_not_appear_as_an_unresolved_name():
    """Breaker / R5. Pass claims produce no unresolved name; the veto's name does."""
    verdict = derive_verdict(
        [
            _sig(StageReportStatus.PASS, item_key="report:pass"),
            _sig(
                StageReportStatus.NEEDS_REWORK,
                source="handoff",
                item_key="handoff:test_suite_complete incomplete",
            ),
        ]
    )
    names = _names(verdict)
    assert "report:pass" not in names
    assert "handoff:test_suite_complete incomplete" in names


def test_derive_verdict_is_deterministic():
    """Breaker matrix: same signals twice, identical status and names."""
    signals = [
        _sig(StageReportStatus.FAIL, item_key="report:fail"),
        _sig(StageReportStatus.NEEDS_REWORK, item_key="handoff:x incomplete"),
        _sig(None, item_key="evidence:unjudgeable"),
    ]
    first = derive_verdict(signals)
    second = derive_verdict(signals)
    assert first.status is second.status
    assert _names(first) == _names(second)


def test_report_needs_rework_and_blocked_are_named(db_session, tmp_path):
    """Breaker / R5 name table: report:{status} for needs_rework and blocked."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    rework = _verdict(db_session, ticket, stage, report=_report("needs_rework"), agent_id="spec")
    assert rework.status is StageReportStatus.NEEDS_REWORK
    assert "report:needs_rework" in _names(rework)
    blocked = _verdict(db_session, ticket, stage, report=_report("blocked"), agent_id="spec")
    assert blocked.status is StageReportStatus.BLOCKED
    assert "report:blocked" in _names(blocked)


def test_report_fail_and_tool_pass_at_collect_is_fail(db_session, tmp_path):
    """Breaker / AC1 / R7.5 at the assembler: last-written tool pass cannot win."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    verdict = _verdict(
        db_session,
        ticket,
        stage,
        report=_report("fail"),
        tool_outcome=StageReportStatus.PASS,
        agent_id="spec",
    )
    assert verdict.status is StageReportStatus.FAIL
    assert "report:fail" in _names(verdict)


def test_report_pass_and_tool_reject_at_collect_is_needs_rework(db_session, tmp_path):
    """Breaker / R5 / R7. Tool reject is the claim `tool:reject`; it outranks pass."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    verdict = _verdict(
        db_session,
        ticket,
        stage,
        report=_pass_report(),
        tool_outcome=claim_from_tool_outcome("reject"),
        agent_id="spec",
    )
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "tool:reject" in _names(verdict)
    assert "report:pass" not in _names(verdict)


def test_neither_report_nor_tool_outcome_cannot_be_pass(db_session, tmp_path):
    """Breaker / R1.4. Absence of both claims cannot produce a pass."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    verdict = _verdict(db_session, ticket, stage, report=None, tool_outcome=None, agent_id="spec")
    assert verdict.status is not StageReportStatus.PASS


def test_optional_incomplete_item_does_not_veto_pass(db_session, tmp_path):
    """Breaker / R3. Required means required is not false. Optional incomplete is not a signal."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(
        db_session,
        ticket,
        [
            _item(status="complete", certainty="inferred"),
            _item("notes", status="incomplete", required=False, item="Notes"),
        ],
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert not any(name.startswith("handoff:notes") for name in _names(verdict))


def test_catalog_optional_key_absent_from_row_is_not_missing(db_session, tmp_path):
    """Breaker / R3.3. Only required catalog keys emit `handoff:{key} missing`."""
    repo = tmp_path / "catalog-optional"
    repo.mkdir()
    gates = repo / "ci" / "scripts" / "gates"
    gates.mkdir(parents=True)
    (gates / "__init__.py").write_text("", encoding="utf-8")
    (gates / "handoff_validation_check.py").write_text(
        textwrap.dedent(
            """
            PAIR_CATALOGS = {
                ("test_designer", "test_breaker"): [
                    {
                        "item_key": "test_suite_complete",
                        "required": True,
                        "item": "Test suite complete",
                    },
                    {
                        "item_key": "notes",
                        "required": False,
                        "item": "Notes",
                    },
                ]
            }

            def run(inputs):
                return {"status": "PASS", "message": "ok"}
            """
        ),
        encoding="utf-8",
    )
    ticket, stage, _ = _ticket(db_session, tmp_path, repo_path=str(repo))
    _handoff(db_session, ticket, [_item(status="complete", certainty="inferred")])
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert "handoff:notes missing" not in _names(verdict)


def test_two_incomplete_required_keys_are_both_named_sorted(db_session, tmp_path):
    """Breaker / R5. Two live handoff vetoes are both named, lexicographically, unique."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(
        db_session,
        ticket,
        [
            _item(status="incomplete"),
            _item(
                "test_all_runnable",
                status="incomplete",
                item="All tests runnable",
            ),
        ],
    )
    names = _names(_verdict(db_session, ticket, stage, agent_id="spec"))
    assert names == sorted(set(names))
    assert "handoff:test_all_runnable incomplete" in names
    assert "handoff:test_suite_complete incomplete" in names


def test_empty_strings_in_required_evidence_are_dropped(db_session, tmp_path):
    """Breaker / R4. Empty strings in required_evidence are not a kind and not a signal."""
    ticket, stage, _ = _ticket(db_session, tmp_path, required_evidence=["", ""])
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.PASS
    assert not any(name.startswith("evidence:") for name in _names(verdict))


def test_empty_head_plus_leftover_evidence_is_unjudgeable_not_a_hit(db_session, tmp_path):
    """Breaker / R2. Empty HEAD must not call has_evidence(commit_sha='') — leftover
    evidence of the required kind is not a pass and not evidence:{kind} missing."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        required_evidence=["real_surface"],
        repo_path=str(tmp_path / "empty"),
    )
    OrchestrationCallbackService(db_session).attach_artifact(
        ticket,
        kind="evidence",
        title="leftover proof from another commit",
        content={},
        evidence_kind="real_surface",
        commit_sha="not-this-head",
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is not StageReportStatus.PASS
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "evidence:unjudgeable" in _names(verdict)
    assert "evidence:real_surface missing" not in _names(verdict)


def test_one_declared_kind_present_other_missing(db_session, git_repo):
    """Breaker / R4.1. Only the missing declared kind is named; a present kind is not."""
    ticket, stage, _ = _ticket(
        db_session,
        git_repo,
        required_evidence=["real_surface", "test_red_green"],
        repo_path=str(git_repo),
    )
    head = resolve_head_sha(db_session, ticket)
    assert head
    OrchestrationCallbackService(db_session).attach_artifact(
        ticket,
        kind="evidence",
        title="surface proof",
        content={},
        evidence_kind="real_surface",
        commit_sha=head,
    )
    verdict = _verdict(db_session, ticket, stage, agent_id="spec")
    assert verdict.status is StageReportStatus.NEEDS_REWORK
    assert "evidence:test_red_green missing" in _names(verdict)
    assert "evidence:real_surface missing" not in _names(verdict)


def test_missing_ux_answers_is_not_emitted_on_claimed_fail(db_session, tmp_path):
    """Breaker / R4.6. Folded ux_answers veto emits only when a claimed pass exists."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        stage_key="ui-design",
        agent_id=UI_DESIGN_AGENT_ID,
        title="Group the monitor findings by ticket",
    )
    verdict = _verdict(
        db_session,
        ticket,
        stage,
        report=_report("fail"),
        agent_id=UI_DESIGN_AGENT_ID,
    )
    assert verdict.status is StageReportStatus.FAIL
    assert "ux_answers missing" not in _names(verdict)


def test_missing_fix_coverage_is_not_emitted_on_claimed_fail(db_session, tmp_path):
    """Breaker / R4.6. Folded fix_coverage veto emits only when a claimed pass exists."""
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        work_item_type=WorkItemType.BUG,
        title="A ticket stays blocked after the repair",
    )
    verdict = _verdict(db_session, ticket, stage, report=_report("fail"), agent_id=SPEC_AGENT_ID)
    assert verdict.status is StageReportStatus.FAIL
    assert "fix_coverage missing" not in _names(verdict)


def test_claimed_blocked_outranks_checklist_veto(db_session, tmp_path):
    """Breaker / R4.7. A real claimed blocked still outranks a checklist veto."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")])
    verdict = _verdict(db_session, ticket, stage, report=_report("blocked"), agent_id="spec")
    assert verdict.status is StageReportStatus.BLOCKED
    assert "handoff:test_suite_complete incomplete" in _names(verdict)
