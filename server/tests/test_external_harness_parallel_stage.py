"""A parallel stage driven from a harness outside this control plane.

`resolve_stage_execution` returns no agent for a parallel stage — its members
live in `parallel_agents` and fanning them out is the driver's job. The external
protocol had no fan-out, so `begin_external_stage` walked into `start_run`'s
empty-agent branch and was told the stage was "a human approval gate", which it
is not. On the live `studio-loregarden-tdd-v3` workflow that stopped an outside
harness at stage two of twelve.

The fix hands the harness one runnable entry per member and settles the stage
only when the last of them comes back — reconciled by
`services.parallel_stage`, the same code the built-in driver reconciles with,
so the two drivers cannot drift apart.
"""

from __future__ import annotations

import json
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ExternalHarness,
    OrchestratorDecision,
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
from loregarden.services import builtin_orchestrator, external_harness, parallel_stage
from loregarden.services.external_harness import (
    begin_external_stage,
    finish_external_stage,
    start_external_orchestration,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds

PLAN_STAGE = "plan"
#: Three lenses on one plan — the shape the live workflow uses. Same agent in
#: every lane, so only the skill tells the members apart: the case that breaks
#: any bookkeeping keyed on agent_id alone.
PLAN_LANES = ("plan-simplest", "plan-risk", "plan-seams")


def _report(status: str, *, reroute_to_stage: str = "", context: str = "") -> str:
    payload = {"status": status, "confidence": 0.9}
    if reroute_to_stage:
        payload["reroute_to_stage"] = reroute_to_stage
    if context:
        payload["reroute_context"] = context
    return (
        "Narrative output from the harness.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f"{json.dumps(payload)}\n"
        "<<<END_STAGE_REPORT>>>\n"
    )


def _throwaway_repo(root):
    """A real git repo — rendering a stage prompt resolves a live tree."""
    root.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=root, check=True, capture_output=True)
    run_git(["config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    run_git(["config", "user.name", "Test"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("# external parallel stage\n", encoding="utf-8")
    run_git(["add", "."], cwd=root, check=True, capture_output=True)
    run_git(["commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    """A ticket parked on a three-member parallel `plan` stage."""
    workspace = Workspace(
        slug=f"external-parallel-{uuid4()}",
        name="External parallel stage",
        repo_path=str(_throwaway_repo(tmp_path / "external-parallel-repo")),
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    stages = [
        WorkflowStageDef(key="scope", name="Scope", order=1, agent_id="backend_implementer"),
        WorkflowStageDef(
            key=PLAN_STAGE,
            name="Plan",
            order=2,
            stage_type="parallel",
            parallel_agents=[
                ParallelAgentSpec(agent_id="planner", skill_name=skill) for skill in PLAN_LANES
            ],
        ),
        WorkflowStageDef(
            key="implement", name="Implement", order=3, agent_id="backend_implementer"
        ),
        WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
    ]
    transitions = [
        {"from": "scope", "to": PLAN_STAGE, "when": "pass"},
        {"from": PLAN_STAGE, "to": "implement", "when": "pass"},
        {"from": PLAN_STAGE, "to": "scope", "when": "reject"},
    ]
    template = WorkflowTemplate(
        slug=f"external-parallel-tpl-{uuid4()}",
        name="External parallel template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(transitions),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"external-parallel-{uuid4()}",
        workspace_id=workspace.id,
        title="Driven from someone else's terminal, in parallel",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=PLAN_STAGE,
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=PLAN_STAGE,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()
    return ticket


def _checkout(db_session: Session, ticket: Ticket):
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    return orch_run, begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)


def _stage_status(db_session: Session, ticket: Ticket) -> StageStatus:
    return OrchestrationService(db_session).stage_status(ticket, PLAN_STAGE)


def test_a_parallel_stage_checks_out_one_run_per_member(db_session: Session, ticket: Ticket):
    """The defect: this used to refuse, calling the stage a human approval gate."""
    _, stage = _checkout(db_session, ticket)

    assert stage.parallel
    assert stage.message == ""
    assert len(stage.runs) == len(PLAN_LANES)
    # Each member is its own run, under its own lens, with its own prompt.
    assert {r.skill_name for r in stage.runs} == set(PLAN_LANES)
    assert len({r.agent_run_id for r in stage.runs}) == len(PLAN_LANES)
    assert all(r.prompt for r in stage.runs)
    assert all(r.agent_id == "planner" for r in stage.runs)
    # One tree, resolved before any member started — concurrent members that
    # each resolved their own would end up in different checkouts.
    assert len({r.repo_path for r in stage.runs}) == 1
    assert _stage_status(db_session, ticket) == StageStatus.RUNNING


def test_re_checking_the_stage_out_re_serves_the_same_runs(db_session: Session, ticket: Ticket):
    """A harness asking again mid-stage is not starting a second attempt."""
    orch_run, first = _checkout(db_session, ticket)

    again = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert {r.agent_run_id for r in again.runs} == {r.agent_run_id for r in first.runs}
    assert len(
        db_session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket.id, AgentRun.stage_key == PLAN_STAGE
            )
        ).all()
    ) == len(PLAN_LANES)


def test_the_stage_settles_only_once_the_last_member_is_back(db_session: Session, ticket: Ticket):
    _, stage = _checkout(db_session, ticket)
    runs = [db_session.get(AgentRun, r.agent_run_id) for r in stage.runs]

    for index, run in enumerate(runs[:-1]):
        result = finish_external_stage(db_session, run, transcript=_report("pass"))
        assert not result.stage_finalized
        assert result.outstanding_members == len(runs) - index - 1
        assert result.workflow_stage_status == StageStatus.RUNNING
        assert _stage_status(db_session, ticket) == StageStatus.RUNNING

    last = finish_external_stage(db_session, runs[-1], transcript=_report("pass"))

    assert last.stage_finalized
    assert last.outstanding_members == 0
    assert _stage_status(db_session, ticket) == StageStatus.DONE
    # One per-member stage-report artifact, named after its lane, as the
    # built-in driver files at reconciliation.
    titles = [
        a.title
        for a in db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    ]
    assert len([t for t in titles if t.startswith(f"Stage report — {PLAN_STAGE} (")]) == len(runs)


def test_a_rejecting_member_reroutes_the_whole_stage(db_session: Session, ticket: Ticket):
    """One member's rejection routes the stage's rework — not the member's."""
    _, stage = _checkout(db_session, ticket)
    runs = [db_session.get(AgentRun, r.agent_run_id) for r in stage.runs]

    finish_external_stage(db_session, runs[0], transcript=_report("pass"))
    finish_external_stage(db_session, runs[1], transcript=_report("pass"))
    result = finish_external_stage(
        db_session,
        runs[2],
        transcript=_report(
            "needs_rework", reroute_to_stage="scope", context="the scope misses the reported case"
        ),
    )

    assert result.stage_finalized
    assert _stage_status(db_session, ticket) != StageStatus.DONE
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == "scope"
    assert "the scope misses the reported case" in ticket.blocking_issues


def test_a_member_that_never_reported_fails_the_stage_closed(db_session: Session, ticket: Ticket):
    """No stage report is not a pass — the same fail-closed rule the driver uses."""
    _, stage = _checkout(db_session, ticket)
    runs = [db_session.get(AgentRun, r.agent_run_id) for r in stage.runs]

    finish_external_stage(db_session, runs[0], transcript=_report("pass"))
    finish_external_stage(db_session, runs[1], transcript=_report("pass"))
    finish_external_stage(db_session, runs[2], transcript="I forgot the report block.")

    assert _stage_status(db_session, ticket) != StageStatus.DONE


def test_both_drivers_reconcile_through_the_same_function():
    """The point of the extraction: one implementation, not two that drift.

    Asserted by identity rather than by behaviour, because two copies that agree
    today is exactly the state this guards against.
    """
    assert external_harness.reconcile_parallel_stage is parallel_stage.reconcile_parallel_stage
    assert builtin_orchestrator.reconcile_parallel_stage is parallel_stage.reconcile_parallel_stage


def test_an_unrunnable_stage_no_longer_claims_to_be_an_approval_gate(
    db_session: Session, ticket: Ticket
):
    """A parallel stage started without naming a member is a routing defect."""
    with pytest.raises(ValueError, match="resolved no agent"):
        OrchestrationService(db_session).start_run(ticket, stage_key=PLAN_STAGE)


def test_the_checked_out_view_says_what_the_stage_is(db_session: Session, ticket: Ticket):
    """The harness reads `runs`; the shape line says why there are three."""
    _, view = _checkout(db_session, ticket)

    assert view.stage_shape.startswith(f"{PLAN_STAGE}: parallel")
    assert "not a human gate" in view.stage_shape


# --- lg-milestone-that-917, test-break ----------------------------------------


def test_every_member_of_a_parallel_stage_is_told_about_the_tree_they_share(
    db_session: Session, ticket: Ticket, tmp_path
):
    """AC14 fills `target_branch` and `tree_warning` in `_checked_out_run_view`,
    which runs **once per member**. A parallel stage hands out three prompts
    against one tree, so an implementation that computes the status in
    `begin_external_stage` and attaches it to the first view — or a test that
    only ever reads `runs[0]`, as the sequential tests do — leaves two of the
    three lanes committing on a base they were never told was stale.

    401 of 2437 runs have taken the external path, and the live workflow's plan
    stage is exactly this shape: three lanes, one tree.
    """
    from pathlib import Path

    orch_run, first = _checkout(db_session, ticket)
    shared = Path(first.runs[0].repo_path)
    assert len(first.runs) == len(PLAN_LANES)
    # Land something on the branch the tree was cut from, after it was cut, and
    # leave the tree dirty so the refresh is skipped rather than catching up.
    repo = Path(ticket_repo_path(db_session, ticket))
    run_git(
        ["commit", "--allow-empty", "-m", "a sibling landed"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (shared / "in-flight.txt").write_text("a lane is mid-edit\n", encoding="utf-8")

    again = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert len(again.runs) == len(PLAN_LANES)
    assert {view.target_branch for view in again.runs} == {again.runs[0].target_branch}
    assert again.runs[0].target_branch, "no lane was told which branch its tree should carry"
    for view in again.runs:
        assert view.tree_warning, f"lane {view.skill_name} was not told its base is stale"
        assert view.tree_warning in view.prompt, (
            f"lane {view.skill_name} reads a prompt that does not say its base is stale"
        )


def ticket_repo_path(db_session: Session, ticket: Ticket) -> str:
    """The workspace checkout the ticket's tree was cut from."""
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    return workspace.repo_path


# --- re-checkout after some or all members came back ---------------------------


def _plan_runs(db_session: Session, ticket: Ticket) -> list[AgentRun]:
    return list(
        db_session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket.id, AgentRun.stage_key == PLAN_STAGE
            )
        ).all()
    )


def test_a_member_that_passed_is_not_offered_again(db_session: Session, ticket: Ticket):
    orch_run, first = _checkout(db_session, ticket)
    runs = [db_session.get(AgentRun, r.agent_run_id) for r in first.runs]
    finish_external_stage(db_session, runs[0], transcript=_report("pass"))

    again = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert {r.agent_run_id for r in again.runs} == {runs[1].id, runs[2].id}
    assert len(_plan_runs(db_session, ticket)) == len(PLAN_LANES)


def test_a_stage_whose_members_all_settled_elsewhere_is_settled_on_checkout(
    db_session: Session, ticket: Ticket
):
    """Members finished outside `finish_external_stage` leave the stage RUNNING; the
    next checkout must settle it rather than hand back nothing, forever."""
    orch_run, first = _checkout(db_session, ticket)
    orch = OrchestrationService(db_session)
    for view in first.runs:
        run = db_session.get(AgentRun, view.agent_run_id)
        orch.complete_run(
            run, status=RunStatus.SUCCEEDED, stdout=_report("pass"), advance_workflow=False
        )
    assert _stage_status(db_session, ticket) == StageStatus.RUNNING

    again = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert (again.runs, again.message) == ([], external_harness._SETTLED_MESSAGE)
    assert _stage_status(db_session, ticket) == StageStatus.DONE
    db_session.refresh(orch_run)
    assert orch_run.current_stage_key == PLAN_STAGE


def test_a_tree_that_cannot_be_prepared_hands_out_no_prompts(db_session: Session, ticket: Ticket):
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    refused = "The ticket's tree could not be refreshed."

    with mock.patch.object(
        external_harness, "prepare_tree_for_parallel_stage", return_value=refused
    ):
        stage = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert (stage.runs, stage.message) == ([], refused)


def test_a_fresh_attempt_supersedes_members_left_running(db_session: Session, ticket: Ticket):
    """The stage was reset under live members: a new checkout retires them first."""
    orch_run, first = _checkout(db_session, ticket)
    stale = {r.agent_run_id for r in first.runs}
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    stages = json.loads(instance.stages_json)
    for entry in stages:
        if entry.get("key") == PLAN_STAGE:
            entry["status"] = StageStatus.PENDING.value
    instance.stages_json = json.dumps(stages)
    ticket.workflow_stage_status = StageStatus.PENDING
    db_session.add_all([instance, ticket])
    db_session.commit()

    again = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert stale.isdisjoint({r.agent_run_id for r in again.runs})
    for run_id in stale:
        assert db_session.get(AgentRun, run_id).status != RunStatus.RUNNING


def test_a_rework_round_re_runs_only_the_lens_that_rejected(db_session: Session, ticket: Ticket):
    """Nothing changed in the tree since two lenses passed, so their passes carry
    forward; the lens that rejected is the only one handed out again."""
    orch_run, first = _checkout(db_session, ticket)
    runs = [db_session.get(AgentRun, r.agent_run_id) for r in first.runs]
    finish_external_stage(db_session, runs[0], transcript=_report("pass"))
    finish_external_stage(db_session, runs[1], transcript=_report("pass"))
    finish_external_stage(
        db_session,
        runs[2],
        transcript=_report("needs_rework", reroute_to_stage="scope", context="scope it again"),
    )
    scope = begin_external_stage(db_session, orch_run, stage_key="scope")
    finish_external_stage(
        db_session, db_session.get(AgentRun, scope.runs[0].agent_run_id), transcript=_report("pass")
    )

    second = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert [r.skill_name for r in second.runs] == [runs[2].skill_name]
    # A lens that quietly did not run must not look like one that ran and found
    # nothing: each carried-forward pass is on the history rail.
    assert (
        decision_kinds(db_session, ticket.id).count(OrchestratorDecision.CARRIED_FORWARD_PASS) == 2
    )


def _head(path) -> str:
    return run_git(
        ["rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_each_member_records_the_commit_it_started_on_and_keeps_it(
    db_session: Session, ticket: Ticket
):
    from pathlib import Path

    orch_run, first = _checkout(db_session, ticket)
    tree = Path(first.runs[0].repo_path)
    started_on = _head(tree)
    runs = [db_session.get(AgentRun, r.agent_run_id) for r in first.runs]
    assert {run.start_head_sha for run in runs} == {started_on}

    run_git(
        ["commit", "--allow-empty", "-m", "a lane committed"],
        cwd=tree,
        check=True,
        capture_output=True,
    )
    begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    for run in runs:
        db_session.refresh(run)
        assert run.start_head_sha == started_on


def test_a_single_agent_checkout_records_the_commit_it_started_on_and_keeps_it(
    db_session: Session, ticket: Ticket
):
    from pathlib import Path

    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    stage = begin_external_stage(db_session, orch_run, stage_key="scope")

    tree = Path(stage.runs[0].repo_path)
    started_on = _head(tree)
    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)
    assert run.start_head_sha == started_on

    run_git(["commit", "--allow-empty", "-m", "work"], cwd=tree, check=True, capture_output=True)
    again = begin_external_stage(db_session, orch_run, stage_key="scope")

    assert again.runs[0].agent_run_id == run.id
    db_session.refresh(run)
    assert run.start_head_sha == started_on
