"""Integration tests: the stage retry budget wired into
`BuiltinOrchestrator.execute()`'s dispatch loop (ticket 105).

Reproduces both real-world pathologies from the ticket on a single custom
3-member parallel stage ("script_review", mirroring the actual
gdscript_reviewer/static_qa/architecture_reviewer trio used in production):

- A stage whose agents keep self-reporting failure and rerouting back to
  themselves (the static_qa incident: 28 runs, 27 failed) — reproduced live,
  within a single `execute()` call: `_handle_parallel_stage_failures` applies
  an agent-specified `reroute_to_stage` equal to its own stage_key via
  `apply_stage_route`, which resets that stage back to PENDING, so the main
  loop's order-based `_next_executable_stage` picks it again immediately —
  with no real time gap between dispatches, which is exactly why the budget
  must be counted via an explicit `record_stage_dispatch` call
  (see test_stage_retry_budget.py) rather than any timestamp heuristic.
- A stage whose agents keep reporting `pass` without the workflow ever
  advancing past it (the gdscript_reviewer incident: 27 runs, 25 "successful"
  re-runs, 0 net progress). Unlike the failure case, a plain parallel-stage
  pass advances the cursor by stage *order* alone
  (`workflow_state.reconcile_workflow_state`) and never consults the
  template's transitions for that — so this pathology cannot be reproduced
  end-to-end without also reproducing the separate, out-of-scope routing bug
  that keeps re-selecting it (see the ticket's own spec checkpoint). What's
  tested here is the property Requirement 3 actually demands: the budget is
  status-blind, so N persisted dispatches that all reported `pass` exhaust it
  exactly like N that all reported `fail` would.

Both use a throwaway `WorkflowTemplate` built in-test (not the real
blobert-tdd/loregarden-tdd templates) so the transition table is fully
controlled.

Every test caps `max_stages` well above the configured retry budget (5) as a
safety net: if the budget check is missing entirely, `execute()`'s pre-existing
stage counter still stops the loop instead of hanging the test suite, and the
resulting "paused after N stage(s)" outcome fails the assertions below in an
obvious way rather than timing out.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRunStatus,
    ParallelAgentSpec,
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
from loregarden.services.orchestration_profile import OrchestrationProfile, RetryBudgetConfig
from loregarden.services.stage_retry_budget import count_stage_dispatches, record_stage_dispatch
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

SCRIPT_REVIEW_MEMBERS = ("gdscript_reviewer", "static_qa", "architecture_reviewer")


def _stage_report(status: str, confidence: float = 0.9, reroute_to_stage: str = "") -> str:
    extra = f', "reroute_to_stage": "{reroute_to_stage}"' if reroute_to_stage else ""
    return (
        "Narrative output from the agent.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}{extra}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _profile(*, max_attempts: int = 5, enabled: bool = True) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="retry-budget-test",
        retry_budget=RetryBudgetConfig(enabled=enabled, max_attempts_per_stage=max_attempts),
    )


def _build_fixture(db_session: Session) -> Ticket:
    # An absolute, nonexistent repo_path — repo_path="." would resolve to
    # settings.repo_root (this actual checkout), and a parallel stage dispatch
    # tries to `git checkout` a ticket branch there before running. Pointing at
    # nothing makes `_checkout_branch_for_parallel_stage` skip that step.
    ws = Workspace(
        slug=f"retry-budget-int-{uuid4()}",
        name="Retry Budget Integration",
        repo_path="/nonexistent/retry-budget-test-repo",
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    stages = [
        WorkflowStageDef(
            key="script_review",
            name="Script Review",
            stage_type="parallel",
            order=1,
            parallel_agents=[
                ParallelAgentSpec(agent_id=a, skill_name="review") for a in SCRIPT_REVIEW_MEMBERS
            ],
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    transitions = [
        {"from": "script_review", "to": "done", "when": "pass"},
    ]

    template = WorkflowTemplate(
        slug=f"retry-budget-test-tpl-{uuid4()}",
        name="Retry Budget Test Template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(transitions),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"retry-budget-ticket-{uuid4()}",
        workspace_id=ws.id,
        title="Retry budget integration test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    return ticket


def _seed_prior_budget_attempts(
    db_session: Session, ticket_id: str, stage_key: str, count: int, *, member_agent_ids=None
) -> None:
    """Simulate `count` past dispatch passes against this ticket+stage, as if
    from earlier, separate orchestration runs: one `record_stage_dispatch`
    call per pass (the actual budget counter) plus the AgentRun row(s) that
    pass would have left behind (realistic side effects, not the counter
    itself) — one row per name in `member_agent_ids`, or a single row if
    unset."""
    agent_ids = member_agent_ids or ("static_qa",)
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(count):
        record_stage_dispatch(db_session, ticket_id, stage_key)
        for m, agent_id in enumerate(agent_ids):
            db_session.add(
                AgentRun(
                    run_code=f"prior_{stage_key}_{i}_{m}",
                    ticket_id=ticket_id,
                    workspace_id="ws-prior",
                    agent_id=agent_id,
                    stage_key=stage_key,
                    status=RunStatus.FAILED,
                    created_at=base + timedelta(minutes=i * 10, seconds=m),
                )
            )
    db_session.commit()


def _script_review_run_count(db_session: Session, ticket_id: str) -> int:
    return len(
        db_session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket_id, AgentRun.stage_key == "script_review"
            )
        ).all()
    )


# -- AC1.1 / AC2.1 / AC2.2: a fresh orchestration run against an already-exhausted stage --


def test_new_run_does_not_dispatch_a_stage_already_at_its_budget(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = _build_fixture(db_session)
    _seed_prior_budget_attempts(db_session, ticket.id, "script_review", count=5)
    runs_before = _script_review_run_count(db_session, ticket.id)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):  # pragma: no cover
        raise AssertionError("must not dispatch script_review once its budget is exhausted")

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    orch_run = builtin.execute(ticket, _profile(max_attempts=5), max_stages=10)

    db_session.refresh(ticket)
    assert _script_review_run_count(db_session, ticket.id) == runs_before  # AC2.1: no new AgentRun
    assert ticket.state == TicketState.BLOCKED
    assert orch_run.status == OrchestrationRunStatus.BLOCKED
    assert "script_review" in ticket.blocking_issues  # AC2.2
    assert "5" in ticket.blocking_issues


def test_reinvoking_execute_on_a_still_blocked_ticket_dispatches_nothing_further(
    db_session: Session, monkeypatch
):
    """AC2.3: once blocked, a second execute() call must not create another AgentRun."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = _build_fixture(db_session)
    _seed_prior_budget_attempts(db_session, ticket.id, "script_review", count=5)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):  # pragma: no cover
        raise AssertionError("must not dispatch script_review at all in either call")

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, _profile(max_attempts=5), max_stages=10)
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    runs_after_first_block = _script_review_run_count(db_session, ticket.id)

    builtin.execute(ticket, _profile(max_attempts=5), max_stages=10)
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert _script_review_run_count(db_session, ticket.id) == runs_after_first_block


def test_disabled_budget_never_blocks_even_when_exhausted(db_session: Session, monkeypatch):
    """AC4.3 (integration side): enabled=False is a full escape hatch."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = _build_fixture(db_session)
    _seed_prior_budget_attempts(db_session, ticket.id, "script_review", count=5)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED
        run.stdout = _stage_report("pass")
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, _profile(max_attempts=5, enabled=False), max_stages=10)

    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED


# -- AC1.2 / AC1.3: a stage under budget dispatches normally, parallel members count as one --


def test_a_parallel_stage_under_budget_dispatches_its_fifth_pass_normally(
    db_session: Session, monkeypatch
):
    """AC1.2 + AC1.3: 4 prior 3-member passes (12 AgentRun rows) have consumed
    4 of 5 attempts, not 12 — so the 5th pass is still allowed to run, and
    (since it passes and routes to a real next stage) the ticket finishes.
    The 5th pass's dispatch must ALSO be recorded exactly once, not 3 times,
    for its own 3 members — the actual Requirement 1 correction under test."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = _build_fixture(db_session)
    _seed_prior_budget_attempts(
        db_session, ticket.id, "script_review", count=4, member_agent_ids=SCRIPT_REVIEW_MEMBERS
    )
    assert _script_review_run_count(db_session, ticket.id) == 12
    assert count_stage_dispatches(db_session, ticket.id, "script_review") == 4

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED
        run.stdout = _stage_report("pass")
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, _profile(max_attempts=5), max_stages=10)

    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED
    assert ticket.state == TicketState.DONE
    assert _script_review_run_count(db_session, ticket.id) == 15  # 12 prior + 3 new members
    assert count_stage_dispatches(db_session, ticket.id, "script_review") == 5


# -- Requirement 3 (AC3.1 / AC3.2): a stage that keeps *passing* without advancing --


def test_a_stage_that_kept_passing_without_advancing_still_trips_the_breaker(
    db_session: Session, monkeypatch
):
    """AC3.1: the gdscript_reviewer pathology — every one of the stage's prior
    dispatches reported `pass` (SUCCEEDED runs with a `pass` stage report),
    yet the workflow never advanced past it (whatever repeatedly re-selected
    it is a separate, out-of-scope routing bug — see module docstring). The
    breaker must still fire on the next dispatch: it does not require any
    attempt to have reported failure, only that the budget is exhausted.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = _build_fixture(db_session)
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(5):
        record_stage_dispatch(db_session, ticket.id, "script_review")
        db_session.add(
            AgentRun(
                run_code=f"prior_pass_{i}",
                ticket_id=ticket.id,
                workspace_id="ws-prior",
                agent_id="gdscript_reviewer",
                stage_key="script_review",
                status=RunStatus.SUCCEEDED,
                stdout=_stage_report("pass"),
                created_at=base + timedelta(minutes=i * 10),
            )
        )
    db_session.commit()
    assert count_stage_dispatches(db_session, ticket.id, "script_review") == 5
    runs_before = _script_review_run_count(db_session, ticket.id)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):  # pragma: no cover
        raise AssertionError("must not dispatch script_review a 6th time")

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    orch_run = builtin.execute(ticket, _profile(max_attempts=5), max_stages=10)

    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert orch_run.status == OrchestrationRunStatus.BLOCKED
    assert _script_review_run_count(db_session, ticket.id) == runs_before  # no 6th dispatch

    # AC3.2: wording must be accurate to a success-loop, not a failure loop.
    lowered = ticket.blocking_issues.lower()
    assert "kept failing" not in lowered
    assert "repeated failure" not in lowered
    assert "script_review" in lowered
    assert "5" in ticket.blocking_issues


def test_self_redo_reject_loop_trips_the_breaker_live_like_the_static_qa_incident(
    db_session: Session, monkeypatch
):
    """AC1.1/AC3.1 sibling: the real static_qa incident shape, reproduced live
    within a single `execute()` call — an agent's own stage report reroutes
    to its own stage_key on every failure, so the main loop redispatches it
    over and over with zero wall-clock gap in between. This is the scenario
    that specifically rules out any timestamp-based dispatch counting."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = _build_fixture(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED  # clean exit, but self-reports failure
        run.stdout = _stage_report("fail", reroute_to_stage="script_review")
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    orch_run = builtin.execute(ticket, _profile(max_attempts=5), max_stages=10)

    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert orch_run.status == OrchestrationRunStatus.BLOCKED
    # 5 passes x 3 members = 15 AgentRun rows, but exactly 5 recorded attempts.
    assert _script_review_run_count(db_session, ticket.id) == 15
    assert count_stage_dispatches(db_session, ticket.id, "script_review") == 5
