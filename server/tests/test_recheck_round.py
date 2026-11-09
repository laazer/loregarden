"""A review lane re-run after its reject rechecks only whether the fix answered its findings."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    RecheckStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.finding_bar import PriorFinding, recheck_answers, rechecked
from loregarden.services.orchestrator_decisions import decisions_of
from loregarden.services.recheck_round import prior_findings
from loregarden.services.stage_report import Finding, parse_stage_report
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select

ORIGINAL = {
    "summary": "the loader skips the last row",
    "blocking": True,
    "trigger": "a file whose last line has no newline",
    "path": "load_rows -> lines[:-1]",
    "impact": "the operator's last record never appears",
}
NEW = {
    "summary": "the error message names the wrong file",
    "blocking": True,
    "trigger": "a missing include",
    "path": "resolve_include -> raise",
    "impact": "the operator looks in the wrong place",
}


def _stdout(status: str, *, findings=(), rechecks=None, **fields) -> str:
    payload = {"status": status, "confidence": 0.9, "findings": list(findings), **fields}
    if rechecks is not None:
        payload["rechecks"] = rechecks
    return f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{json.dumps(payload)}\n<<<END_STAGE_REPORT>>>\n"


def _report(status: str, **kwargs):
    report = parse_stage_report(_stdout(status, **kwargs))
    assert report is not None
    return report


PRIORS = [PriorFinding("P1", Finding(**ORIGINAL))]


# ---- the report carries rechecks --------------------------------------------


def test_rechecks_are_parsed_and_a_bad_one_is_skipped():
    report = _report(
        "pass",
        rechecks=[{"id": "P1", "resolved": True, "note": "row kept"}, {"id": "P2"}, "nope"],
    )
    assert [(r.id, r.resolved, r.note) for r in report.rechecks] == [("P1", True, "row kept")]


def test_each_prior_finding_is_resolved_unresolved_or_unanswered():
    priors = [*PRIORS, PriorFinding("P2", Finding(summary="b")), PriorFinding("P3", Finding("c"))]
    report = _report(
        "needs_rework",
        rechecks=[{"id": "P1", "resolved": True}, {"id": "P2", "resolved": False}],
    )
    assert [(p.id, status) for p, status, _ in recheck_answers(report, priors)] == [
        ("P1", RecheckStatus.RESOLVED),
        ("P2", RecheckStatus.UNRESOLVED),
        ("P3", RecheckStatus.UNANSWERED),
    ]


# ---- the recheck rule --------------------------------------------------------


def test_a_recheck_reject_naming_only_a_new_finding_settles_as_a_pass():
    settled, reason = rechecked(
        _report(
            "needs_rework",
            findings=[NEW],
            rechecks=[{"id": "P1", "resolved": True}],
            reroute_to_stage="implement",
            unmet_criteria=["AC1"],
        ),
        PRIORS,
    )
    assert settled.status == "pass" and settled.reroute_to_stage is None
    assert settled.unmet_criteria == []
    assert [f.blocking for f in settled.findings] == [False]
    assert "named none" in reason


def test_a_recheck_reject_naming_a_prior_finding_unresolved_stands():
    report = _report(
        "needs_rework",
        findings=[NEW],
        rechecks=[{"id": "P1", "resolved": False}],
        reroute_to_stage="implement",
    )
    settled, reason = rechecked(report, PRIORS)
    assert settled.status == "needs_rework" and settled.reroute_to_stage == "implement"
    assert reason == "" and [f.blocking for f in settled.findings] == [False]


def test_an_unresolved_answer_for_an_unknown_id_does_not_make_a_reject():
    settled, _ = rechecked(_report("fail", rechecks=[{"id": "P9", "resolved": False}]), PRIORS)
    assert settled.status == "pass"


def test_no_priors_is_not_a_recheck_round():
    report = _report("needs_rework", findings=[NEW])
    assert rechecked(report, []) == (report, "")


# ---- end to end: a parallel review stage, rejected, reworked, rechecked -----


@pytest.fixture
def review(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).one()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="recheck-round",
        workspace_id=ws.id,
        title="Recheck round",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="script_review",
            stages_json=initial_stages_json(stages),
        )
    )
    orch_run = OrchestrationRun(
        run_code="orch_recheck",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="script_review",
    )
    db_session.add(orch_run)
    db_session.commit()
    return ticket, orch_run, next(s for s in stages if s.key == "script_review"), stages


def _round(db_session, monkeypatch, review, static_qa_stdout: str) -> list[tuple[str, str]]:
    """Run the review stage once with static_qa reporting `static_qa_stdout`."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, stage_def, _ = review
    ran: list[tuple[str, str]] = []

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED
        run.stdout = static_qa_stdout if run.agent_id == "static_qa" else _stdout("pass")
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        ran.append((run.agent_id, run.id))
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    # The simulated rework writes no code, which the diff readiness check
    # rightly refuses; this test is about the review, not the rework.
    with patch("loregarden.services.orchestration.refuse_unready_dispatch"):
        BuiltinOrchestrator(db_session)._execute_parallel_stage(
            ticket, orch_run, stage_def, "script_review"
        )
    db_session.refresh(ticket)
    return ran


def _back_at_review(db_session, review) -> None:
    """The rework is done: the ticket is at the review stage again."""
    ticket, _, _, stages = review
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    instance.stages_json = initial_stages_json(stages)
    instance.current_stage_key = "script_review"
    ticket.workflow_stage_key = "script_review"
    ticket.workflow_stage_status = StageStatus.RUNNING
    db_session.add_all([instance, ticket])
    db_session.commit()


def _first_reject(db_session, monkeypatch, review) -> None:
    _round(
        db_session,
        monkeypatch,
        review,
        _stdout("needs_rework", findings=[ORIGINAL], reroute_to_stage="implement"),
    )
    assert review[0].workflow_stage_key == "implement"
    _back_at_review(db_session, review)


def _review_status(db_session, review) -> StageStatus:
    ticket, _, _, stages = review
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    return parse_stage_map(instance, stages)["script_review"]


def _decisions(db_session, ticket, kind: str) -> list[dict]:
    return [p for _, p in decisions_of(db_session, [ticket.id]) if p["decision"] == kind]


def test_a_recheck_round_reporting_only_a_new_finding_does_not_send_the_ticket_back(
    db_session, monkeypatch, review
):
    _first_reject(db_session, monkeypatch, review)

    ran = _round(
        db_session,
        monkeypatch,
        review,
        _stdout(
            "needs_rework",
            findings=[NEW],
            rechecks=[{"id": "P1", "resolved": True, "note": "last row kept"}],
            reroute_to_stage="implement",
        ),
    )

    ticket = review[0]
    assert ticket.workflow_stage_key != "implement"
    assert _review_status(db_session, review) is StageStatus.DONE
    rerun = db_session.get(AgentRun, next(run_id for agent, run_id in ran if agent == "static_qa"))
    assert [p.finding.summary for p in prior_findings(db_session, ticket, rerun)] == [
        ORIGINAL["summary"]
    ]


def test_a_recheck_round_naming_its_finding_unresolved_sends_it_back(
    db_session, monkeypatch, review
):
    _first_reject(db_session, monkeypatch, review)

    _round(
        db_session,
        monkeypatch,
        review,
        _stdout(
            "needs_rework",
            rechecks=[{"id": "P1", "resolved": False, "note": "still drops it"}],
            reroute_to_stage="implement",
        ),
    )

    # The reject stood: the review did not pass. (Whether the ticket goes back
    # to implement or the loop cap files a pause is the reroute's business.)
    assert _review_status(db_session, review) is not StageStatus.DONE
    assert _decisions(db_session, review[0], "reject_not_admitted") == []


def test_a_lane_that_passed_last_time_reviews_afresh(db_session, monkeypatch, review):
    _first_reject(db_session, monkeypatch, review)
    ticket = review[0]
    passed = db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.agent_id != "static_qa")
    ).first()
    later = AgentRun(
        run_code="later",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=passed.agent_id,
        skill_name=passed.skill_name,
        stage_key="script_review",
    )
    db_session.add(later)
    db_session.commit()
    assert prior_findings(db_session, ticket, later) == []


def test_the_recheck_prompt_lists_the_prior_findings_and_the_diff(db_session, monkeypatch, review):
    _first_reject(db_session, monkeypatch, review)
    ticket, _, stage_def, stages = review
    rerun = AgentRun(
        run_code="rerun",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        stage_key="script_review",
    )
    db_session.add(rerun)
    db_session.commit()

    context = build_orchestration_context(
        ticket=ticket, run=rerun, stage_def=stage_def, stages=stages, session=db_session
    )

    assert "## This is a recheck round" in context
    assert "Your prior findings:" in context
    assert f"**P1**: {ORIGINAL['summary']}" in context
    assert "git diff " in context and '"rechecks"' in context


def test_a_recheck_round_is_on_the_history_per_finding(db_session, monkeypatch, review):
    from loregarden.services.finding_bar import admit_run_report

    _first_reject(db_session, monkeypatch, review)
    ticket = review[0]
    rerun = AgentRun(
        run_code="rerun-history",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        stage_key="script_review",
        stdout=_stdout("needs_rework", findings=[NEW], rechecks=[{"id": "P1", "resolved": True}]),
    )
    db_session.add(rerun)
    db_session.commit()

    priors = prior_findings(db_session, ticket, rerun)
    settled = admit_run_report(
        db_session, ticket, rerun, parse_stage_report(rerun.stdout), priors=priors
    )

    assert settled.status == "pass"
    [recorded] = _decisions(db_session, ticket, "recheck_recorded")
    assert recorded["rechecks"] == [
        {"id": "P1", "summary": ORIGINAL["summary"], "status": "resolved", "note": ""}
    ]
    assert [f["summary"] for f in recorded["new_findings"]] == [NEW["summary"]]
    assert recorded["new_findings"][0]["blocking"] is False
    assert len(_decisions(db_session, ticket, "reject_not_admitted")) == 1


def _lane_run(db_session, ticket, code: str, stdout: str = "", **fields) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        stage_key=fields.pop("stage_key", "script_review"),
        agent_id=fields.pop("agent_id", "static_qa"),
        status=fields.pop("status", RunStatus.SUCCEEDED),
        stdout=stdout,
        **fields,
    )
    db_session.add(run)
    db_session.commit()
    return run


SECOND = {**ORIGINAL, "summary": "the header row is counted as data"}


def test_only_the_findings_left_unresolved_carry_into_the_next_round(db_session, review):
    ticket = review[0]
    _lane_run(db_session, ticket, "r1", _stdout("needs_rework", findings=[ORIGINAL, SECOND]))
    _lane_run(
        db_session,
        ticket,
        "r2",
        _stdout(
            "needs_rework",
            rechecks=[{"id": "P1", "resolved": True}, {"id": "P2", "resolved": False}],
        ),
    )
    third = _lane_run(db_session, ticket, "r3", status=RunStatus.QUEUED)

    assert [(p.id, p.finding.summary) for p in prior_findings(db_session, ticket, third)] == [
        ("P2", SECOND["summary"])
    ]


def test_a_pass_carries_nothing_forward_even_a_finding_it_marked_blocking(db_session, review):
    ticket = review[0]
    _lane_run(db_session, ticket, "r1", _stdout("pass", findings=[ORIGINAL]))
    later = _lane_run(db_session, ticket, "r2", status=RunStatus.QUEUED)
    assert prior_findings(db_session, ticket, later) == []


def test_another_skill_of_the_same_agent_is_another_lane(db_session, review):
    ticket = review[0]
    _lane_run(
        db_session, ticket, "r1", _stdout("needs_rework", findings=[ORIGINAL]), skill_name="lens-a"
    )
    later = _lane_run(db_session, ticket, "r2", status=RunStatus.QUEUED, skill_name="lens-b")
    assert prior_findings(db_session, ticket, later) == []


def test_a_single_stage_judge_is_not_a_recheck_round(db_session, review):
    ticket = review[0]
    for code in ("v1", "v2"):
        last = _lane_run(
            db_session,
            ticket,
            code,
            _stdout("needs_rework", findings=[ORIGINAL]),
            stage_key="ac_gate",
            agent_id="ac_gatekeeper",
        )
    assert prior_findings(db_session, ticket, last) == []


def test_run_completion_records_the_recheck(db_session, review):
    from loregarden.services.orchestration import OrchestrationService

    ticket = review[0]
    _lane_run(db_session, ticket, "r1", _stdout("needs_rework", findings=[ORIGINAL]))
    rerun = _lane_run(db_session, ticket, "r2", status=RunStatus.QUEUED)

    OrchestrationService(db_session).complete_run(
        rerun,
        status=RunStatus.SUCCEEDED,
        stdout=_stdout("pass", rechecks=[{"id": "P1", "resolved": True, "note": "fixed"}]),
        stderr="",
    )

    [recorded] = _decisions(db_session, ticket, "recheck_recorded")
    assert recorded["rechecks"][0]["status"] == "resolved"
    assert recorded["rechecks"][0]["note"] == "fixed"
