"""A review, verify or gate reject stands only on a finding that shows its defect."""

from __future__ import annotations

import json

import pytest
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    FindingDisposition,
    RunStatus,
    StageStatus,
    StageType,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.finding_bar import admit_run_report, admitted, unadmitted_reason
from loregarden.services.orchestrator_decisions import decisions_of
from loregarden.services.stage_report import Finding, parse_stage_report
from loregarden.services.workflow_state import stages_up_to_done_json
from sqlmodel import Session, select

COMPLETE = {
    "summary": "parse_header raises on a missing Host header",
    "blocking": True,
    "trigger": "GET / with no Host header",
    "path": "server/http.py:parse_header -> headers['host']",
    "impact": "the request returns 500 instead of 400",
}
ASIDE = {
    "summary": "the helper could be shorter",
    "blocking": False,
    "disposition": "out_of_scope",
}


def _stdout(status: str, *, findings: list[dict] | None = None, **fields) -> str:
    payload = {"status": status, "confidence": 0.9, **fields}
    if findings is not None:
        payload["findings"] = findings
    return f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{json.dumps(payload)}\n<<<END_STAGE_REPORT>>>\n"


def _stage(stage_type: StageType) -> WorkflowStageDef:
    return WorkflowStageDef(key="review", name="Review", stage_type=stage_type)


def _report(status: str, findings: list[dict] | None = None, **fields):
    report = parse_stage_report(_stdout(status, findings=findings, **fields))
    assert report is not None
    return report


# ---- the report carries findings ---------------------------------------------


def test_findings_are_parsed_with_their_parts_and_dispositions():
    assigned = {"summary": "s", "disposition": "Assigned_Ticket", "ticket": "lg-x-1"}
    report = _report("needs_rework", [COMPLETE, ASIDE, assigned])

    blocking, aside, later = report.findings
    assert blocking.blocking and blocking.missing_parts() == []
    assert aside.disposition is FindingDisposition.OUT_OF_SCOPE and not aside.blocking
    assert later.disposition is FindingDisposition.ASSIGNED_TICKET and later.ticket == "lg-x-1"


def test_an_unknown_disposition_is_kept_as_written():
    [finding] = _report("pass", [{"summary": "s", "disposition": "later"}]).findings
    assert finding.disposition is None and finding.disposition_as_written == "later"


def test_a_malformed_findings_list_does_not_cost_the_report_its_status():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "needs_rework", "confidence": 0.8, "findings": "not a list"}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None and report.status == "needs_rework" and report.findings == []


def test_one_bad_finding_is_skipped_and_the_rest_kept():
    report = _report("needs_rework", [COMPLETE, {"summary": ["not", "text"]}])
    assert [finding.summary for finding in report.findings] == [COMPLETE["summary"]]


# ---- the bar ---------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["trigger", "path", "impact"])
def test_a_blocking_finding_missing_a_part_is_not_a_reject(missing):
    finding = {**COMPLETE, missing: ""}
    settled, reason = admitted(
        _report("needs_rework", [finding], reroute_to_stage="implement", unmet_criteria=["AC1"]),
        agent_id="architecture_reviewer",
        stage=_stage(StageType.PARALLEL),
    )
    assert settled.status == "pass"
    assert settled.reroute_to_stage is None and settled.unmet_criteria == []
    assert f"gives no {missing}" in reason


def test_a_reject_with_no_blocking_finding_is_not_a_reject():
    settled, reason = admitted(
        _report("fail", [ASIDE]), agent_id="verifier", stage=_stage(StageType.VERIFY)
    )
    assert settled.status == "pass" and reason == "it named no blocking finding"


def test_one_complete_blocking_finding_makes_the_reject_stand():
    report = _report("needs_rework", [{**COMPLETE, "impact": ""}, COMPLETE])
    settled, reason = admitted(report, agent_id="gatekeeper", stage=_stage(StageType.GATE))
    assert settled is report and reason == ""


def test_a_stage_that_does_not_judge_is_not_examined():
    report = _report("needs_rework")
    settled, reason = admitted(
        report, agent_id="backend_implementer", stage=_stage(StageType.AGENT)
    )
    assert settled is report and reason == ""


def test_a_custom_agent_on_a_verify_or_gate_stage_is_judged():
    for stage_type in (StageType.VERIFY, StageType.GATE):
        settled, _ = admitted(
            _report("needs_rework"), agent_id="my_checker", stage=_stage(stage_type)
        )
        assert settled.status == "pass"


def test_pass_and_blocked_are_not_examined():
    for status in ("pass", "blocked"):
        report = _report(status)
        assert admitted(report, agent_id="verifier", stage=_stage(StageType.VERIFY)) == (report, "")


def test_the_reason_names_each_incomplete_finding():
    reason = unadmitted_reason(
        [Finding(summary="a", blocking=True, trigger="t"), Finding(summary="b", blocking=True)]
    )
    assert "'a' gives no path, impact" in reason and "'b' gives no trigger, path, impact" in reason


# ---- through run completion: rerouted, or not, and the history says why ------


@pytest.fixture
def at_test_break(db_session: Session, tmp_path) -> Ticket:
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).one()
    ws = Workspace(slug="finding-bar", name="Finding bar", repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="finding-bar",
        workspace_id=ws.id,
        title="Finding bar",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="test-break",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="test-break",
            stages_json=stages_up_to_done_json(stages, "test-design"),
        )
    )
    db_session.commit()
    return ticket


def _complete(db_session: Session, ticket: Ticket, stdout: str, *, agent_id: str = "verifier"):
    from loregarden.services.orchestration import OrchestrationService

    run = AgentRun(
        run_code=f"run_{agent_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key="test-break",
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=stdout, stderr=""
    )
    db_session.refresh(ticket)
    return run


def _decisions(db_session: Session, ticket: Ticket, kind: str) -> list[dict]:
    return [
        payload
        for _, payload in decisions_of(db_session, [ticket.id])
        if payload["decision"] == kind
    ]


def test_a_reject_with_a_complete_blocking_finding_reroutes(db_session, at_test_break):
    _complete(
        db_session,
        at_test_break,
        _stdout("needs_rework", findings=[COMPLETE, ASIDE], reroute_to_stage="test-design"),
    )

    assert at_test_break.workflow_stage_key == "test-design"
    assert _decisions(db_session, at_test_break, "reject_not_admitted") == []
    [recorded] = _decisions(db_session, at_test_break, "findings_recorded")
    assert [f["summary"] for f in recorded["findings"]] == [ASIDE["summary"]]
    assert recorded["findings"][0]["disposition"] == "out_of_scope"


def test_a_reject_missing_its_impact_does_not_reroute_and_says_why(db_session, at_test_break):
    run = _complete(
        db_session,
        at_test_break,
        _stdout(
            "needs_rework", findings=[{**COMPLETE, "impact": ""}], reroute_to_stage="test-design"
        ),
    )

    assert at_test_break.workflow_stage_key != "test-design"
    [recorded] = _decisions(db_session, at_test_break, "reject_not_admitted")
    assert "gives no impact" in recorded["reason"] and "verifier" in recorded["reason"]
    assert recorded["findings"][0]["summary"] == COMPLETE["summary"]
    assert recorded["claimed"] == "needs_rework"
    events = [event for event, _ in decisions_of(db_session, [at_test_break.id])]
    assert {event.run_id for event in events} == {run.id}


def test_an_implementer_reject_is_untouched(db_session, at_test_break):
    _complete(
        db_session,
        at_test_break,
        _stdout("needs_rework", reroute_to_stage="test-design"),
        agent_id="test_breaker",
    )

    assert at_test_break.workflow_stage_key == "test-design"
    assert _decisions(db_session, at_test_break, "reject_not_admitted") == []


def test_a_pass_with_findings_that_do_not_block_puts_them_on_record(db_session, at_test_break):
    assigned = {
        "summary": "retry is unbounded",
        "disposition": "assigned_ticket",
        "ticket": "lg-x-9",
    }
    _complete(db_session, at_test_break, _stdout("pass", findings=[ASIDE, assigned]))

    [recorded] = _decisions(db_session, at_test_break, "findings_recorded")
    assert [(f["disposition"], f["ticket"]) for f in recorded["findings"]] == [
        ("out_of_scope", ""),
        ("assigned_ticket", "lg-x-9"),
    ]


def test_a_run_settled_twice_is_recorded_once(db_session, at_test_break):
    run = _complete(
        db_session,
        at_test_break,
        _stdout("needs_rework", findings=[{**COMPLETE, "path": ""}]),
    )
    settled = admit_run_report(
        db_session, at_test_break, run, parse_stage_report(run.stdout or ""), priors=[]
    )

    assert settled is not None and settled.status == "pass"
    assert len(_decisions(db_session, at_test_break, "reject_not_admitted")) == 1
