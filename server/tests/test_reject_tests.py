"""A reviewer's reject is a red test: it names a test that fails where it rejects.

The tests run for real: a throwaway git checkout holding real test files, run
by a real pytest command, as the criteria check runs them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.agents import stage_report_stop_hook as stop_hook
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.mcp.caller import RUN_ID_ENV
from loregarden.models.domain import (
    AgentRun,
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
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.criterion_check import check_criteria
from loregarden.services.criterion_ledger import GapKind, settle_met
from loregarden.services.finding_bar import (
    PriorFinding,
    RejectNotShown,
    refuse_unshown_reject,
    settled_as_recorded,
    unadmitted_reason,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import (
    CriterionTestCommand,
    GatesConfig,
    OrchestrationProfile,
)
from loregarden.services.orchestrator_decisions import decisions_of
from loregarden.services.parallel_stage import member_result
from loregarden.services.recheck_round import _findings_its_reject_stood_on
from loregarden.services.reject_tests import check_reject_tests, reject_tests_by_criterion
from loregarden.services.rework_brief import rework_context
from loregarden.services.rework_feedback import latest_rework_context
from loregarden.services.stage_report import Finding, parse_stage_report
from loregarden.services.workflow_state import stages_up_to_done_json
from sqlmodel import Session, select

CRITERION = "A missing Host header is a 400"
FAILS = "server/tests/test_host.py::test_missing_host_is_a_400"
PASSES = "server/tests/test_host.py::test_present_host_is_read"
NOT_RUN = "tools/check_host.py::test_host_tool"

TESTS_PY = """
def test_missing_host_is_a_400():
    assert 500 == 400


def test_present_host_is_read():
    assert True
"""

FINDING = {
    "summary": "parse_header raises on a missing Host header",
    "blocking": True,
    "trigger": "GET / with no Host header",
    "path": "server/http.py:parse_header -> headers['host']",
    "impact": "the request returns 500 instead of 400",
}

PYTEST = CriterionTestCommand(
    prefix="server/",
    command=f"{sys.executable} -m pytest -q -p no:cacheprovider {{tests}}",
    strip_prefix=False,
)


@pytest.fixture(name="checkout")
def checkout_fixture(tmp_path: Path) -> Path:
    """A real git checkout with one failing test, one passing, and one no command runs."""
    (tmp_path / "server" / "tests").mkdir(parents=True)
    (tmp_path / "server" / "tests" / "test_host.py").write_text(TESTS_PY)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "check_host.py").write_text("def test_host_tool():\n    assert False\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
    }
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, env=env)
    return tmp_path


@pytest.fixture(name="runner", autouse=True)
def runner_fixture():
    with patch(
        "loregarden.services.named_tests.resolve_orchestration_profile",
        return_value=OrchestrationProfile(
            slug="t", name="t", gates=GatesConfig(criterion_test_commands=[PYTEST])
        ),
    ):
        yield


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, checkout: Path) -> Ticket:
    """A ticket at blobert-tdd's test-break stage, whose reject goes to test-design."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).one()
    ws = Workspace(slug="reject-tests", name="Reject tests", repo_path=str(checkout))
    db_session.add(ws)
    db_session.commit()
    row = Ticket(
        external_id="reject-tests",
        workspace_id=ws.id,
        title="Reject tests",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="test-break",
        workflow_stage_status=StageStatus.RUNNING,
        acceptance_criteria_json=serialize_criteria([CRITERION]),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    db_session.add(
        WorkflowInstance(
            ticket_id=row.id,
            template_id=template.id,
            current_stage_key="test-break",
            stages_json=stages_up_to_done_json(get_template_stages(template), "test-design"),
        )
    )
    db_session.commit()
    return row


def _stdout(test: str = "", **finding) -> str:
    payload = {
        "status": "needs_rework",
        "confidence": 0.9,
        "reroute_to_stage": "test-design",
        "reroute_context": "parse_header does not handle a missing Host header",
        "unmet_criteria": [CRITERION],
        "findings": [{**FINDING, "test": test, **finding}],
    }
    return f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{json.dumps(payload)}\n<<<END_STAGE_REPORT>>>\n"


def _complete(session: Session, ticket: Ticket, stdout: str) -> AgentRun:
    run = AgentRun(
        run_code=f"run_{len(stdout)}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="verifier",
        stage_key="test-break",
        status=RunStatus.QUEUED,
    )
    session.add(run)
    session.commit()
    OrchestrationService(session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=stdout, stderr=""
    )
    session.refresh(ticket)
    return run


def _decisions(session: Session, ticket: Ticket, kind: str) -> list[dict]:
    return [p for _, p in decisions_of(session, [ticket.id]) if p["decision"] == kind]


# ---- the shape: a test, or a reason no test can show it ----------------------


@pytest.mark.parametrize(
    ("extra", "admitted"),
    [
        ({}, False),
        ({"judgment": "it reads badly"}, False),
        ({"judgment": "the copy misleads; a test cannot judge wording"}, True),
        ({"test": FAILS}, True),
    ],
)
def test_a_blocking_finding_names_a_test_or_why_none_can_show_it(extra, admitted):
    finding = Finding(**{**FINDING, **extra})

    reason = unadmitted_reason([finding])

    assert (reason == "") is admitted
    if not admitted:
        assert "failing test or judgment" in reason


# ---- admission runs the test --------------------------------------------------


def test_a_reject_whose_test_fails_stands_and_its_test_is_the_rework(db_session, ticket):
    _complete(db_session, ticket, _stdout(FAILS))

    assert ticket.workflow_stage_key == "test-design"
    [recorded] = _decisions(db_session, ticket, "reject_tests_recorded")
    assert recorded["tests"] == [FAILS] and recorded["failing"] == [FAILS]
    assert recorded["criteria"] == [CRITERION]
    assert reject_tests_by_criterion(db_session, ticket) == {CRITERION: [FAILS]}
    assert FAILS in latest_rework_context(db_session, ticket, "test-design")


def test_a_reject_whose_test_passes_does_not_stand(db_session, ticket):
    _complete(db_session, ticket, _stdout(PASSES))

    assert ticket.workflow_stage_key != "test-design"
    [recorded] = _decisions(db_session, ticket, "reject_not_admitted")
    assert "passes at the rejected commit" in recorded["reason"]
    assert reject_tests_by_criterion(db_session, ticket) == {}


def test_a_reject_naming_a_test_not_in_the_checkout_does_not_stand(db_session, ticket):
    _complete(db_session, ticket, _stdout("server/tests/test_host.py::test_never_written"))

    assert ticket.workflow_stage_key != "test-design"
    [recorded] = _decisions(db_session, ticket, "reject_not_admitted")
    assert "not in the checkout" in recorded["reason"]


def test_a_test_no_command_runs_is_recorded_unverified_and_the_reject_stands(db_session, ticket):
    _complete(db_session, ticket, _stdout(NOT_RUN))

    assert ticket.workflow_stage_key == "test-design"
    [recorded] = _decisions(db_session, ticket, "reject_tests_recorded")
    assert recorded["tests"] == [NOT_RUN] and recorded["failing"] == []
    assert recorded["unverified"] == [{"test": NOT_RUN, "why": "no configured command runs it"}]


def test_a_judged_finding_stands_without_running_anything(db_session, ticket):
    judgment = "the error copy misleads the operator; no test can judge wording"

    _complete(db_session, ticket, _stdout(judgment=judgment))

    assert ticket.workflow_stage_key == "test-design"
    [recorded] = _decisions(db_session, ticket, "reject_tests_recorded")
    assert recorded["tests"] == []


def test_one_refuted_test_leaves_the_reject_standing_on_the_other(db_session, checkout, ticket):
    findings = [{**FINDING, "test": PASSES}, {**FINDING, "summary": "second", "test": FAILS}]

    check = check_reject_tests(db_session, ticket, [Finding(**f) for f in findings])

    assert check.failing == [FAILS] and [n.test for n in check.refuted] == [PASSES]


# ---- the tool path refuses while the agent is live ------------------------------


def test_complete_stage_refuses_a_reject_whose_test_passes(db_session, ticket):
    stage = WorkflowStageDef(key="test-break", name="Test break", agent_id="verifier")

    with pytest.raises(RejectNotShown, match="passes at the rejected commit"):
        refuse_unshown_reject(
            db_session,
            ticket,
            stage,
            None,
            outcome="reject",
            findings=[Finding(**{**FINDING, "test": PASSES})],
        )
    refuse_unshown_reject(
        db_session,
        ticket,
        stage,
        None,
        outcome="reject",
        findings=[Finding(**{**FINDING, "test": FAILS})],
    )
    [recorded] = _decisions(db_session, ticket, "reject_tests_recorded")
    assert recorded["tests"] == [FAILS] and recorded["criteria"] == []
    assert "named no unmet criterion" in recorded["reason"]


# ---- the Stop hook refuses a sentinel reject while the turn is open ------------


def _hook_decides(session: Session, ticket: Ticket, stdout: str, state: Path) -> str | None:
    """The Stop hook's answer to a builtin-driver run that printed ``stdout`` and stopped."""
    run = AgentRun(
        run_code=f"hook_{len(stdout)}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="verifier",
        stage_key="test-break",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    hook_input = stop_hook.StopHookInput(session_id="s-reject", last_assistant_message=stdout)
    with (
        patch.dict(os.environ, {RUN_ID_ENV: run.id}),
        patch.object(stop_hook, "_STATE_DIR", state),
    ):
        return stop_hook.decide(hook_input, [], db_path=Path(session.get_bind().url.database))


def test_the_stop_hook_refuses_a_reject_whose_test_passes(db_session, ticket, tmp_path):
    reason = _hook_decides(db_session, ticket, _stdout(PASSES), tmp_path / "state")

    assert reason is not None and "passes at the rejected commit" in reason
    assert "write your stage report again" in reason
    # Read-only: the refusal records nothing; completion admits and records.
    assert _decisions(db_session, ticket, "reject_tests_recorded") == []


@pytest.mark.parametrize(
    "finding",
    [
        {"test": FAILS},
        {"test": NOT_RUN},
        {"judgment": "the error copy misleads the operator; no test can judge wording"},
    ],
)
def test_the_stop_hook_lets_a_reject_that_stands_end_the_turn(
    db_session, ticket, tmp_path, finding, capsys
):
    assert _hook_decides(db_session, ticket, _stdout(**finding), tmp_path / "state") is None
    # Checked, not let through because the check could not run.
    assert "report not checked" not in capsys.readouterr().err


def test_the_stop_hook_leaves_a_recheck_rounds_reject_to_completion(db_session, ticket, tmp_path):
    """A recheck round's reject stands on the prior findings it answers; new ones never block."""
    prior = PriorFinding("P1", Finding(**{**FINDING, "test": FAILS}))

    with patch("loregarden.agents.stage_report_reject.prior_findings", return_value=[prior]):
        reason = _hook_decides(db_session, ticket, _stdout(PASSES), tmp_path / "state")

    assert reason is None


# ---- a re-judged lane reads back what the run found ----------------------------


def test_a_lane_rejudged_after_completion_keeps_the_recorded_outcome(db_session, ticket):
    run = _complete(db_session, ticket, _stdout(PASSES))

    rejudged = member_result(
        "verifier",
        status=RunStatus.SUCCEEDED,
        stdout=run.stdout or _stdout(PASSES),
        stderr="",
        session=db_session,
        run_id=run.id,
    )

    assert not rejudged.failure
    report = parse_stage_report(_stdout(PASSES))
    assert settled_as_recorded(db_session, run.id, report).status == "pass"


# ---- the gate reruns every recorded reject test ------------------------------


def test_the_criteria_check_reruns_a_recorded_reject_test(db_session, checkout, ticket):
    _complete(db_session, ticket, _stdout(FAILS))
    # Verify settles the criterion on a test of its own choosing, which passes.
    settle_met(
        db_session,
        ticket,
        CRITERION,
        tests=[PASSES],
        evidence_artifact_id=None,
        commit_sha="",
        settled_by="run:verify",
        reason="the header is read",
    )

    [gap] = check_criteria(db_session, ticket).gaps
    assert gap.criterion == CRITERION and gap.kind is GapKind.TESTS_FAILED

    (checkout / "server" / "tests" / "test_host.py").write_text(
        TESTS_PY.replace("assert 500 == 400", "assert 400 == 400")
    )
    check = check_criteria(db_session, ticket)
    assert check.passed and set(check.rerun) == {PASSES, FAILS}


def test_a_deleted_reject_test_is_a_gap_at_the_gate(db_session, checkout, ticket):
    _complete(db_session, ticket, _stdout(FAILS))
    settle_met(
        db_session,
        ticket,
        CRITERION,
        tests=[PASSES],
        evidence_artifact_id=None,
        commit_sha="",
        settled_by="run:verify",
        reason="the header is read",
    )
    (checkout / "server" / "tests" / "test_host.py").write_text(
        TESTS_PY.split("def test_present_host_is_read")[0].replace(
            "def test_missing_host_is_a_400():\n    assert 500 == 400\n", ""
        )
        + "def test_present_host_is_read():\n    assert True\n"
    )

    [gap] = check_criteria(db_session, ticket).gaps
    assert gap.kind is GapKind.TESTS_FAILED and "not found" in gap.detail


# ---- every lens reaches the brief ----------------------------------------------


def test_the_brief_carries_every_lens_its_criteria_and_its_tests():
    lenses = [
        (
            "architecture_reviewer",
            parse_stage_report(_stdout(FAILS)),
        ),
        (
            "security_reviewer",
            parse_stage_report(_stdout(PASSES).replace(CRITERION, "Errors name the header")),
        ),
    ]

    brief = rework_context(lenses, "")

    assert "architecture_reviewer: parse_header" in brief
    assert "security_reviewer: parse_header" in brief
    assert CRITERION in brief and "Errors name the header" in brief
    assert FAILS in brief and PASSES in brief


def test_a_recheck_round_asks_only_about_findings_whose_test_failed(db_session, ticket):
    payload = json.loads(_stdout(FAILS).split("\n")[2])
    payload["findings"].append({**FINDING, "summary": "refuted", "test": PASSES})
    stdout = "<<<LOREGARDEN_STAGE_REPORT>>>\n" + json.dumps(payload) + "\n<<<END_STAGE_REPORT>>>\n"
    run = _complete(db_session, ticket, stdout)
    lane = WorkflowStageDef(key="test-break", name="Review", stage_type=StageType.PARALLEL)

    priors = _findings_its_reject_stood_on(db_session, ticket, lane, run)

    assert [prior.finding.test for prior in priors] == [FAILS]
