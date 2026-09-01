"""Running a ticket from a harness outside this control plane.

The operator copies a prompt into their own Claude Code / Codex session; that
session drives the ticket over MCP. Three things have to hold for the results to
be worth anything: the run is attributed to the harness, it is timed, and it
never touches the lane queue — it spawns nothing on this machine.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.external_harness import (
    _FINISHED_MESSAGE,
    EXTERNAL_HARNESS_COMMAND_PREFIX,
    begin_external_stage,
    build_external_harness_prompt,
    finish_external_stage,
    start_external_orchestration,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_interruption import INTERRUPTED_RUN_MESSAGE
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
)
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select

TICKET_SLUG = "03-wire-cli-agent-runner"

PASSING_REPORT = """
Did the work.

<<<LOREGARDEN_STAGE_REPORT>>>
{"status": "pass", "confidence": 0.9}
<<<END_STAGE_REPORT>>>
"""


def _ticket(session: Session) -> Ticket:
    return session.exec(select(Ticket).where(Ticket.legacy_external_id == TICKET_SLUG)).first()


def test_prompt_names_the_harness_and_the_calls_that_record_progress(db_session: Session):
    view = build_external_harness_prompt(
        db_session, _ticket(db_session), harness=ExternalHarness.CODEX
    )

    assert view.harness == ExternalHarness.CODEX
    prompt = view.prompt
    # The identity that makes the run comparable, and the tools that record it.
    assert 'external_harness="codex"' in prompt
    assert "loregarden_start_orchestration" in prompt
    assert "loregarden_begin_external_stage" in prompt
    assert "loregarden_finish_external_stage" in prompt
    assert "loregarden_complete_orchestration" in prompt
    assert view.ticket_id in prompt
    assert view.external_id in prompt


def test_copying_a_prompt_starts_nothing(client: TestClient, db_session: Session):
    ticket = _ticket(db_session)
    before = len(db_session.exec(select(OrchestrationRun)).all())

    response = client.post(
        f"/api/tickets/{ticket.id}/external_harness_prompt",
        json={"harness": "claude_code"},
    )

    assert response.status_code == 200
    assert response.json()["harness"] == "claude_code"
    # A prompt copied and never pasted must not leave a run open, or its start
    # time would be when the operator clicked a menu item.
    assert len(db_session.exec(select(OrchestrationRun)).all()) == before


def test_external_orchestration_never_reserves_a_lane(db_session: Session):
    ticket = _ticket(db_session)

    def _fail(*args, **kwargs):
        raise AssertionError("an external-harness run must not go through admission")

    with patch("loregarden.mcp.tools.start_orchestration_admitted", _fail):
        payload = json.loads(
            execute_tool(
                db_session,
                "loregarden_start_orchestration",
                {"ticket_id": ticket.id, "external_harness": "claude_code"},
            )
        )

    assert payload["external_harness"] == "claude_code"
    assert payload["status"] == OrchestrationRunStatus.RUNNING.value
    assert payload["started_at"]
    assert db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).all() == []


def test_the_stage_pair_is_reachable_over_mcp(db_session: Session):
    """The harness only ever reaches these through the MCP transport."""
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)

    stage = json.loads(
        execute_tool(db_session, "loregarden_begin_external_stage", {"run_id": orch_run.id})
    )
    assert len(stage["runs"]) == 1
    assert stage["runs"][0]["agent_run_id"]
    assert stage["runs"][0]["prompt"]

    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_finish_external_stage",
            {"agent_run_id": stage["runs"][0]["agent_run_id"], "transcript": PASSING_REPORT},
        )
    )
    assert result["status"] == RunStatus.SUCCEEDED.value
    assert result["finished_at"]


def test_stage_round_trip_is_attributed_and_timed(db_session: Session):
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)

    stage = begin_external_stage(db_session, orch_run)
    assert len(stage.runs) == 1
    assert stage.runs[0].agent_run_id
    assert stage.runs[0].prompt
    assert stage.stage_key

    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)
    assert run.external_harness == ExternalHarness.CLAUDE_CODE
    assert run.command.startswith(EXTERNAL_HARNESS_COMMAND_PREFIX)
    assert run.orchestration_run_id == orch_run.id

    result = finish_external_stage(db_session, run, transcript=PASSING_REPORT)

    assert result.status == RunStatus.SUCCEEDED
    assert result.duration_seconds >= 0
    assert result.finished_at is not None
    # The stage report routed the workflow, exactly as it would for a supervised run.
    assert result.workflow_stage_status == StageStatus.DONE
    assert not result.blocking_issues


def test_a_stage_report_that_rejects_reroutes_the_same_way(db_session: Session):
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)
    stage = begin_external_stage(db_session, orch_run)
    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)

    result = finish_external_stage(
        db_session,
        run,
        transcript=(
            "<<<LOREGARDEN_STAGE_REPORT>>>\n"
            '{"status": "needs_rework", "reroute_context": "tests miss the reported case"}\n'
            "<<<END_STAGE_REPORT>>>\n"
        ),
    )

    assert result.status == RunStatus.SUCCEEDED
    assert result.workflow_stage_status != StageStatus.DONE


def test_the_restart_reapers_leave_external_runs_alone(db_session: Session):
    """A harness in someone's terminal is not orphaned by this server restarting."""
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    stage = begin_external_stage(db_session, orch_run)

    fail_interrupted_runs(db_session)
    fail_interrupted_orchestration_runs(db_session)

    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)
    db_session.refresh(run)
    db_session.refresh(orch_run)
    assert run.status == RunStatus.RUNNING
    assert orch_run.status == OrchestrationRunStatus.RUNNING


def _checked_out_external_run(session: Session) -> tuple[Ticket, OrchestrationRun, AgentRun]:
    """One external-harness stage, checked out and still RUNNING."""
    ticket = _ticket(session)
    orch_run = start_external_orchestration(session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    stage = begin_external_stage(session, orch_run)
    run = session.get(AgentRun, stage.runs[0].agent_run_id)
    assert run.status == RunStatus.RUNNING
    return ticket, orch_run, run


def _park_stage(session: Session, ticket: Ticket, stage_key: str, status: StageStatus) -> None:
    """Move the stage cursor off RUNNING, so the next checkout is a fresh attempt."""
    instance, _ = OrchestrationService(session).ensure_workflow_instance(ticket)
    _, stages = resolve_ticket_stages(session, ticket)
    set_stage_status(ticket, instance, stages, stage_key, status)
    session.add(ticket)
    session.add(instance)
    session.commit()


def test_a_superseded_predecessor_is_not_blamed_on_a_server_reload(db_session: Session):
    """No restart happened, so the run must not be told one did.

    A fresh checkout of a stage whose previous run is still RUNNING supersedes
    that run — `start_run_async`'s ticket+stage-scoped reap claims external runs
    deliberately. The forensics on this ticket show the predecessor's
    `finished_at` equal to the successor's `created_at` to the second, with
    lifetimes of 8 and 17 seconds: a supersession wearing a reload's message.
    """
    ticket, orch_run, predecessor = _checked_out_external_run(db_session)
    _park_stage(db_session, ticket, predecessor.stage_key, StageStatus.BLOCKED)

    successor = begin_external_stage(db_session, orch_run, stage_key=predecessor.stage_key)

    assert successor.runs
    assert successor.runs[0].agent_run_id != predecessor.id
    db_session.refresh(predecessor)
    reason = predecessor.stderr or ""
    assert "supersede" in reason.lower(), (
        f"the superseded run says {reason!r}; nothing restarted, so the message must "
        "name the re-checkout that claimed it"
    )
    assert reason != INTERRUPTED_RUN_MESSAGE
    assert "reload" not in reason.lower()


def test_a_superseded_predecessor_is_still_settled(db_session: Session):
    """The trap in the obvious fix, pinned.

    `run_has_renewer` is False for an external run, so `settle_expired_agent_runs`
    never judges one and `complete_orchestration` does not settle child runs.
    This reap is the only thing that terminates an abandoned external run:
    deleting it to silence the false label would strand the row at RUNNING
    forever, holding every future drain open and reading as a live agent.
    """
    ticket, orch_run, predecessor = _checked_out_external_run(db_session)
    _park_stage(db_session, ticket, predecessor.stage_key, StageStatus.BLOCKED)

    begin_external_stage(db_session, orch_run, stage_key=predecessor.stage_key)

    db_session.refresh(predecessor)
    assert predecessor.status not in (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION), (
        "the superseded run was left in flight — nothing else will ever settle an "
        "external-harness run"
    )
    assert predecessor.finished_at is not None


def test_resuming_a_single_agent_stage_does_not_reap_the_run_it_resumes(db_session: Session):
    """The guard the parallel path already has (`_begin_parallel_stage`).

    A stage still RUNNING that is checked out again is being *re-served*, not
    restarted: the run the harness is asking about is the one it already holds.
    Reaping it kills live work and hands the harness a second run for a stage
    that can only have one.
    """
    _ticket_row, orch_run, run = _checked_out_external_run(db_session)

    resumed = begin_external_stage(db_session, orch_run, stage_key=run.stage_key)

    db_session.refresh(run)
    assert run.status == RunStatus.RUNNING, (
        f"a resume settled the run it was resuming as {run.status}: {run.stderr!r}"
    )
    assert not run.stderr
    in_flight = db_session.exec(
        select(AgentRun).where(
            AgentRun.ticket_id == run.ticket_id,
            AgentRun.stage_key == run.stage_key,
            AgentRun.status == RunStatus.RUNNING,
        )
    ).all()
    assert len(in_flight) == 1, (
        "a resume created a second in-flight run for a single-agent stage; the "
        "parallel path re-serves the member already in flight instead"
    )
    assert resumed.runs and resumed.runs[0].agent_run_id == run.id


def test_a_resume_never_adopts_a_run_this_process_supervises(db_session: Session):
    """A resume may only re-serve a run the harness path already owns.

    Stamping `external_harness` onto a supervised run double-books it: the
    control plane's own thread is still working it, while the harness has been
    handed the same run to work as well. It also strands it — `run_has_renewer`
    flips to False, so `settle_expired_agent_runs` stops judging it, and with no
    `handoff_pid` the drain filter stops counting it too.
    """
    ticket = _ticket(db_session)
    supervised = OrchestrationService(db_session).start_run(ticket)
    assert supervised.status == RunStatus.RUNNING
    assert supervised.external_harness is None
    stage_key = supervised.stage_key
    assert OrchestrationService(db_session).stage_status(ticket, stage_key) == StageStatus.RUNNING

    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    view = begin_external_stage(db_session, orch_run, stage_key=stage_key)

    db_session.refresh(supervised)
    assert supervised.external_harness is None, (
        "a resume claimed a run this process supervises; nothing settles a run "
        "once it reads as externally harnessed"
    )
    assert [r.agent_run_id for r in view.runs] != [supervised.id], (
        "the harness was handed the run the control plane is already working"
    )
    assert supervised.status not in (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION), (
        f"the superseded supervised run was left in flight as {supervised.status}"
    )


def test_a_resume_leaves_no_sibling_run_in_flight(db_session: Session):
    """Whatever a resume does not adopt has to be settled.

    Nothing else ever settles an external run: the unscoped sweep exempts them,
    `run_has_renewer` is False, and with no `handoff_pid` the lease never
    expires. A sibling left RUNNING by a resume is stuck at RUNNING forever.
    """
    ticket, orch_run, first = _checked_out_external_run(db_session)
    sibling = AgentRun(
        run_code=f"{first.run_code}-sib",
        ticket_id=first.ticket_id,
        workspace_id=first.workspace_id,
        orchestration_run_id=first.orchestration_run_id,
        agent_id=first.agent_id,
        external_harness=first.external_harness,
        stage_key=first.stage_key,
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(sibling)
    db_session.commit()

    begin_external_stage(db_session, orch_run, stage_key=first.stage_key)

    db_session.refresh(first)
    db_session.refresh(sibling)
    left_running = [
        run
        for run in (first, sibling)
        if run.status in (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)
    ]
    assert not left_running, (
        "a resume left an in-flight run on a single-agent stage; a run it did "
        "not adopt has no path to settlement — the unscoped sweep exempts "
        "external runs, run_has_renewer is False for them, and with no pid the "
        "lease never expires. Ambiguity must settle every candidate, not all "
        "but one."
    )


def test_finishing_a_run_no_harness_checked_out_is_refused(db_session: Session):
    """A supervised run must not settle through the external path."""
    run = OrchestrationService(db_session).start_run(_ticket(db_session))

    with pytest.raises(ValueError, match="external harness"):
        finish_external_stage(db_session, run, transcript=PASSING_REPORT)


def test_a_state_locked_ticket_still_reports_its_finished_workflow(db_session: Session):
    """The stage map, not the ticket row, decides whether anything is left to run.

    `state_locked` holds `ticket.state` wherever it was — a mid-run write can
    leave it `in_progress` with every stage DONE. Answering from the ticket row
    then reported the terminal stage as a human approval gate, and an autonomous
    harness told to wait for the inbox waits forever: no approval exists, and
    none is ever going to.
    """
    ticket = _ticket(db_session)
    instance, _ = OrchestrationService(db_session).ensure_workflow_instance(ticket)
    _, stages = resolve_ticket_stages(db_session, ticket)
    terminal = find_terminal_stage(stages)
    assert terminal is not None

    for stage in stages:
        if stage.key != terminal.key:
            set_stage_status(ticket, instance, stages, stage.key, StageStatus.DONE)
    ticket.state = TicketState.IN_PROGRESS
    ticket.state_locked = True
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()

    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    view = begin_external_stage(db_session, orch_run)

    db_session.refresh(ticket)
    assert ticket.state not in StateMachine.TERMINAL_TICKET_STATES
    assert view.runs == []
    assert view.message == _FINISHED_MESSAGE


def test_a_harness_reports_its_own_usage_and_changed_paths_over_mcp(db_session: Session):
    """There is no adapter on this path, so nothing here read a usage event or
    diffed the tree. Every externally driven run before this reported neither —
    0 of the 22 runs on ticket 181 had changed paths recorded — which is why
    the harness has to be able to say so itself.
    """
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    stage = json.loads(
        execute_tool(db_session, "loregarden_begin_external_stage", {"run_id": orch_run.id})
    )
    agent_run_id = stage["runs"][0]["agent_run_id"]

    execute_tool(
        db_session,
        "loregarden_finish_external_stage",
        {
            "agent_run_id": agent_run_id,
            "transcript": PASSING_REPORT,
            "input_tokens": 12_000,
            "output_tokens": 3_400,
            "cache_read_tokens": 88_000,
            "cache_write_tokens": 0,
            "model": "claude-opus-5",
            "effort": "high",
            "changed_paths": ["server/loregarden/api/runs.py", "server/tests/test_runs.py"],
        },
    )

    run = db_session.get(AgentRun, agent_run_id)
    db_session.refresh(run)
    assert run.input_tokens == 12_000
    assert run.output_tokens == 3_400
    assert run.cache_read_tokens == 88_000
    assert run.cache_write_tokens == 0
    assert run.model == "claude-opus-5"
    assert run.effort == "high"
    assert json.loads(run.changed_paths_json) == [
        "server/loregarden/api/runs.py",
        "server/tests/test_runs.py",
    ]


def test_a_harness_that_reports_nothing_leaves_the_figures_unmeasured(db_session: Session):
    """Silence must not be recorded as zero. A harness that cannot read its own
    usage leaves NULL behind, and NULL is what keeps it out of a cost average.
    """
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)
    stage = json.loads(
        execute_tool(db_session, "loregarden_begin_external_stage", {"run_id": orch_run.id})
    )
    agent_run_id = stage["runs"][0]["agent_run_id"]

    execute_tool(
        db_session,
        "loregarden_finish_external_stage",
        {"agent_run_id": agent_run_id, "transcript": PASSING_REPORT},
    )

    run = db_session.get(AgentRun, agent_run_id)
    db_session.refresh(run)
    assert run.input_tokens is None
    assert run.output_tokens is None
    assert run.model is None
    assert run.effort is None


def test_a_harness_reporting_a_genuine_zero_is_not_recorded_as_unmeasured(db_session: Session):
    """The other half of the same distinction, on the surface an outside
    harness actually uses: a reported 0 is stored as 0, not dropped to NULL."""
    ticket = _ticket(db_session)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)
    stage = json.loads(
        execute_tool(db_session, "loregarden_begin_external_stage", {"run_id": orch_run.id})
    )
    agent_run_id = stage["runs"][0]["agent_run_id"]

    execute_tool(
        db_session,
        "loregarden_finish_external_stage",
        {
            "agent_run_id": agent_run_id,
            "transcript": PASSING_REPORT,
            "input_tokens": 0,
            "output_tokens": 0,
        },
    )

    run = db_session.get(AgentRun, agent_run_id)
    db_session.refresh(run)
    assert run.input_tokens == 0
    assert run.output_tokens == 0
