"""A group's gates run once, at its end, and a failure sends the group back as one unit.

Spec lg-run-economics-2636 §7–§8. AC4: after the group run completes every
step, the union of its steps' gate commands, required evidence and checklists
runs once, and no grouped step's command is missing from that run. AC6: a
failed combined gate reroutes the group once, resumes its session with the
findings when eligible, and counts once against MAX_REWORK_REROUTES. AC7: a
group of gate steps is one combined gate that fails if any member fails.

Gate commands are real processes that record their own execution in a log, so
"ran exactly once" is read off what actually ran, not off a mock.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    Artifact,
    ArtifactKind,
    CliAdapter,
    RunStatus,
    StageReportStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowStageDef,
)
from loregarden.models.domain.enums import ReworkArtifactKind, utcnow
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_rework_feedback,
    rework_feedback_artifact_title,
    rework_reroute_count,
)
from loregarden.services.rework_resume import plan_rework_resume
from loregarden.services.stage_retry_budget import (
    count_gate_fix_attempts,
    gate_failure_artifact_title,
)
from loregarden.services.workflow_state import stages_up_to_done_json
from loregarden.testing.factories import findings_fragment
from sqlmodel import Session, select
from tests.agent_group_harness import (
    AFTER,
    DONE,
    GROUP,
    GROUP_KEYS,
    STEP_AGENTS,
    ScriptedGroupAgent,
    Turn,
    build_ticket,
    executed_gates,
    fail_once_gate,
    gate_evaluations,
    gated_profile,
    group_stages,
    recording_gate,
    repo_workspace,
    runs_of,
    seed_group_agents,
    stage_map,
)

HEAD = GROUP_KEYS[0]
LAST = GROUP_KEYS[-1]


@pytest.fixture
def gate_log(tmp_path: Path) -> Path:
    return tmp_path / "gates.log"


def _drive(session: Session, ticket: Ticket, *, max_stages: int) -> None:
    BuiltinOrchestrator(session).execute(ticket, gated_profile(), max_stages=max_stages)
    session.refresh(ticket)


def _artifacts(session: Session, ticket: Ticket) -> list[Artifact]:
    return list(session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all())


# ---- AC4: the union runs once --------------------------------------------------------


@pytest.fixture
def union_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    """Overlapping per-step commands: cmd-shared is declared by both s2 and s3."""
    seed_group_agents(db_session)
    stages = group_stages(
        gate_commands={
            "s1": [recording_gate("cmd-a", gate_log)],
            "s2": [recording_gate("cmd-b", gate_log), recording_gate("cmd-shared", gate_log)],
            "s3": [recording_gate("cmd-shared", gate_log), recording_gate("cmd-c", gate_log)],
        }
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), stages)


def test_every_grouped_steps_gate_command_runs_exactly_once_after_the_group(
    db_session, union_ticket, monkeypatch, gate_log
):
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, union_ticket, max_stages=1)

    assert len(agent.observations) == 1
    # Nothing ran between the steps: the gates were deferred to the group's end.
    assert agent.observations[0].gates_run_before == []
    ran = executed_gates(gate_log)
    for command in ("cmd-a", "cmd-b", "cmd-c", "cmd-shared"):
        assert ran.count(command) == 1, f"{command} ran {ran.count(command)} times: {ran}"
    assert ran == ["cmd-a", "cmd-b", "cmd-shared", "cmd-c"], "ordered, de-duplicated union"


def test_the_union_is_one_gate_evaluation_from_the_last_step(
    db_session, union_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, union_ticket, max_stages=1)

    evaluations = gate_evaluations(db_session, union_ticket)
    assert len(evaluations) == 1, "one combined evaluation, not one per step"
    payload = json.loads(evaluations[0].payload_json)
    assert payload["from_stage"] == LAST
    assert payload["to_stage"] == AFTER
    db_session.refresh(union_ticket)
    assert union_ticket.workflow_stage_key == AFTER


@pytest.fixture
def evidence_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    """s1 asks for evidence nobody attaches; s3 declares none of its own."""
    seed_group_agents(db_session)
    stages = group_stages(
        gate_commands={"s3": [recording_gate("cmd-c", gate_log)]},
        required_evidence={"s1": ["real_surface"]},
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), stages)


def test_missing_union_evidence_holds_the_group_and_names_the_kind(
    db_session, evidence_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, evidence_ticket, max_stages=1)

    statuses = stage_map(db_session, evidence_ticket)
    # Mid-group the evidence is deferred, so s1 and s2 are recorded…
    assert statuses["s1"] == StageStatus.DONE
    assert statuses["s2"] == StageStatus.DONE
    # …and the end of the group is where it is demanded.
    assert statuses[AFTER] == StageStatus.PENDING
    assert all(run.stage_key != AFTER for run in runs_of(db_session, evidence_ticket))
    db_session.refresh(evidence_ticket)
    assert evidence_ticket.workflow_stage_key != AFTER
    said = (evidence_ticket.blocking_issues or "") + "".join(
        artifact.content_json for artifact in _artifacts(db_session, evidence_ticket)
    )
    assert "real_surface" in said, "the missing evidence kind must be named to the agent"


# ---- AC6: a failed combined gate sends the group back once ------------------------------


GATE_FINDING = "the combined gate found a defect"


@pytest.fixture
def failing_gate_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    """s2's command always fails; s1's always passes."""
    seed_group_agents(db_session)
    stages = group_stages(
        gate_commands={
            "s1": [recording_gate("cmd-a", gate_log)],
            "s2": [recording_gate("cmd-broken", gate_log, fail=True)],
        }
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), stages)


def test_a_failed_combined_gate_reroutes_the_whole_group_once(
    db_session, failing_gate_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, failing_gate_ticket, max_stages=1)

    ticket = failing_gate_ticket
    feedback = [
        artifact
        for artifact in _artifacts(db_session, ticket)
        if artifact.kind == ReworkArtifactKind.FEEDBACK
    ]
    assert [artifact.title for artifact in feedback] == [rework_feedback_artifact_title(HEAD)]
    assert GATE_FINDING in feedback[0].content_json
    assert rework_reroute_count(db_session, ticket, HEAD) == 1
    statuses = stage_map(db_session, ticket)
    assert [statuses[key] for key in GROUP_KEYS] == [StageStatus.PENDING] * 3
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == HEAD
    assert ticket.state != TicketState.BLOCKED


def test_a_group_reroute_is_not_charged_to_the_per_step_gate_fix_budget(
    db_session, failing_gate_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, failing_gate_ticket, max_stages=1)

    ticket = failing_gate_ticket
    assert executed_gates(gate_log)[-1:] == ["cmd-broken"], "the combined gate never ran"
    titles = {artifact.title for artifact in _artifacts(db_session, ticket)}
    for key in GROUP_KEYS:
        assert gate_failure_artifact_title(key) not in titles
        assert count_gate_fix_attempts(db_session, ticket.id, key) == 0


def test_at_the_reroute_cap_a_failed_combined_gate_blocks_for_a_person(
    db_session, failing_gate_ticket, monkeypatch, gate_log
):
    ticket = failing_gate_ticket
    for round_ in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage=HEAD,
            from_stage=LAST,
            context=f"an earlier combined-gate round {round_}",
        )
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, ticket, max_stages=3)

    assert executed_gates(gate_log)[-1:] == ["cmd-broken"], "the combined gate never ran"
    assert len(agent.observations) == 1, "a blocked group must not be re-dispatched"
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    pauses = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.REWORK_PAUSE
        )
    ).all()
    assert len(pauses) == 1


# ---- AC6: the re-run resumes the group's session -----------------------------------------


SESSION = "5b0e7a52-0c58-4f8e-9d3c-1a2b3c4d5e6f"
REPORT = (
    "<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.9, "reroute_to_stage": null, "reroute_context": ""}\n'
    "<<<END_STAGE_REPORT>>>"
)


def _stream() -> str:
    return "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": SESSION}),
            json.dumps(
                {"type": "result", "subtype": "success", "result": REPORT, "session_id": SESSION}
            ),
        ]
    )


@pytest.fixture
def resume_ticket(db_session: Session, tmp_path: Path) -> Ticket:
    seed_group_agents(db_session)
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), group_stages())


def _group_run(
    session: Session,
    ticket: Ticket,
    *,
    code: str,
    stage_key: str,
    covered: list[str],
    repo_root: Path,
    minutes_ago: int = 0,
    stdout: str = "",
) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=STEP_AGENTS[HEAD],
        stage_key=stage_key,
        status=RunStatus.SUCCEEDED,
        stdout=stdout,
        start_repo_path=str(repo_root),
        created_at=utcnow() - timedelta(minutes=minutes_ago),
        agent_group_key=GROUP,
        covered_stage_keys_json=json.dumps(covered),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _combined_gate_failure(session: Session, ticket: Ticket) -> Artifact:
    """What `_reroute_group` records: one finding, addressed to the group's head."""
    record_rework_feedback(
        session, ticket, target_stage=HEAD, from_stage=LAST, context=GATE_FINDING
    )
    return session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id,
            Artifact.title == rework_feedback_artifact_title(HEAD),
        )
    ).one()


def test_the_rerouted_group_resumes_its_earlier_session_with_the_gate_finding(
    db_session, resume_ticket, tmp_path
):
    repo_root = tmp_path / "repo"
    # The earlier group run ended on its last step: run identity moved with the agent.
    prior = _group_run(
        db_session,
        resume_ticket,
        code="run_group_1",
        stage_key=LAST,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=10,
        stdout=_stream(),
    )
    feedback = _combined_gate_failure(db_session, resume_ticket)
    rerun = _group_run(
        db_session,
        resume_ticket,
        code="run_group_2",
        stage_key=HEAD,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
    )

    plan = plan_rework_resume(
        db_session, resume_ticket, rerun, adapter=CliAdapter.CLAUDE, repo_root=repo_root
    )

    assert plan is not None and plan.resumes, getattr(plan, "skip_reason", "no plan")
    db_session.refresh(rerun)
    assert rerun.resumed_from_run_id == prior.id
    assert rerun.rework_feedback_id == feedback.id
    assert GATE_FINDING in plan.follow_up


def test_a_rerouted_group_in_another_checkout_runs_fresh_and_says_why(
    db_session, resume_ticket, tmp_path
):
    repo_root = tmp_path / "repo"
    other_checkout = tmp_path / "other-checkout"
    _group_run(
        db_session,
        resume_ticket,
        code="run_group_1",
        stage_key=LAST,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=10,
        stdout=_stream(),
    )
    _combined_gate_failure(db_session, resume_ticket)
    rerun = _group_run(
        db_session,
        resume_ticket,
        code="run_group_2",
        stage_key=HEAD,
        covered=list(GROUP_KEYS),
        repo_root=other_checkout,
    )

    plan = plan_rework_resume(
        db_session, resume_ticket, rerun, adapter=CliAdapter.CLAUDE, repo_root=other_checkout
    )

    assert plan is not None and not plan.resumes
    db_session.refresh(rerun)
    assert rerun.resumed_from_run_id == ""
    assert str(repo_root) in rerun.resume_skip_reason
    assert str(other_checkout) in rerun.resume_skip_reason


def test_an_early_ended_group_continues_its_session_from_the_first_incomplete_step(
    db_session, resume_ticket, tmp_path
):
    repo_root = tmp_path / "repo"
    prior = _group_run(
        db_session,
        resume_ticket,
        code="run_group_1",
        stage_key="s2",
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=10,
        stdout=_stream(),
    )
    continuation = _group_run(
        db_session,
        resume_ticket,
        code="run_group_2",
        stage_key="s2",
        covered=["s2", "s3"],
        repo_root=repo_root,
    )

    plan = plan_rework_resume(
        db_session, resume_ticket, continuation, adapter=CliAdapter.CLAUDE, repo_root=repo_root
    )

    assert plan is not None and plan.resumes, getattr(plan, "skip_reason", "no plan")
    assert plan.prior_run_id == prior.id
    assert plan.feedback_id == ""
    assert "Continue from step 's2'" in plan.follow_up


def test_a_group_resume_never_selects_another_tickets_run(db_session, tmp_path):
    seed_group_agents(db_session)
    workspace = repo_workspace(db_session, tmp_path / "repo")
    mine = build_ticket(db_session, workspace, group_stages())
    theirs = build_ticket(db_session, workspace, group_stages())
    repo_root = tmp_path / "repo"
    _group_run(
        db_session,
        theirs,
        code="run_theirs",
        stage_key=LAST,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=10,
        stdout=_stream(),
    )
    _combined_gate_failure(db_session, mine)
    rerun = _group_run(
        db_session,
        mine,
        code="run_mine",
        stage_key=HEAD,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
    )

    plan = plan_rework_resume(
        db_session, mine, rerun, adapter=CliAdapter.CLAUDE, repo_root=repo_root
    )

    assert plan is None or not plan.resumes
    db_session.refresh(rerun)
    assert rerun.resumed_from_run_id == ""


# ---- AC7: a group of gate steps is one combined gate --------------------------------------


GATEKEEPER = "gatekeeper"
FAIL_REPORT = (
    "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "fail", "confidence": 0.9, "reroute_to_stage": "work",'
    f' "reroute_context": "the work does not meet the bar"{findings_fragment(StageReportStatus.FAIL)}}}\n'
    "<<<END_STAGE_REPORT>>>\n"
)


def _gate_group_stages(gate_log: Path, *, gate_2_fails: bool) -> list[WorkflowStageDef]:
    return [
        WorkflowStageDef(key="work", name="Work", order=1, agent_id="planner"),
        WorkflowStageDef(
            key="g1",
            name="Gate one",
            order=2,
            stage_type="gate",
            agent_id=GATEKEEPER,
            gate_commands=[recording_gate("gate-1", gate_log)],
            agent_group="gates",
        ),
        WorkflowStageDef(
            key="g2",
            name="Gate two",
            order=3,
            stage_type="gate",
            agent_id=GATEKEEPER,
            gate_commands=[recording_gate("gate-2", gate_log, fail=gate_2_fails)],
            agent_group="gates",
        ),
        WorkflowStageDef(key=AFTER, name="After", order=4, agent_id="planner"),
        WorkflowStageDef(key=DONE, name="Done", order=5, terminal=True),
    ]


def _gate_group_ticket(session: Session, repo: Path, gate_log: Path, *, gate_2_fails: bool):
    stages = _gate_group_stages(gate_log, gate_2_fails=gate_2_fails)
    return build_ticket(
        session,
        repo_workspace(session, repo),
        stages,
        start_key="g1",
        stages_json=stages_up_to_done_json(stages, "work"),
    )


def test_a_passing_gate_group_is_one_run_and_one_combined_gate(
    db_session, tmp_path, monkeypatch, gate_log
):
    ticket = _gate_group_ticket(db_session, tmp_path / "repo", gate_log, gate_2_fails=False)
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, ticket, max_stages=1)

    assert len(agent.observations) == 1
    assert agent.observations[0].covered == ["g1", "g2"]
    assert executed_gates(gate_log) == ["gate-1", "gate-2"]
    assert len(gate_evaluations(db_session, ticket)) == 1
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == AFTER


def test_a_gate_group_fails_when_any_member_gate_fails(db_session, tmp_path, monkeypatch, gate_log):
    ticket = _gate_group_ticket(db_session, tmp_path / "repo", gate_log, gate_2_fails=True)
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, ticket, max_stages=1)

    assert len(agent.observations) == 1
    assert agent.observations[0].covered == ["g1", "g2"]
    assert executed_gates(gate_log) == ["gate-1", "gate-2"]
    statuses = stage_map(db_session, ticket)
    assert statuses["g1"] != StageStatus.DONE or statuses["g2"] != StageStatus.DONE
    assert statuses[AFTER] == StageStatus.PENDING
    assert all(run.stage_key != AFTER for run in runs_of(db_session, ticket))
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key != AFTER


def test_a_member_gatekeeper_reject_runs_no_combined_gate(
    db_session, tmp_path, monkeypatch, gate_log
):
    ticket = _gate_group_ticket(db_session, tmp_path / "repo", gate_log, gate_2_fails=False)
    agent = ScriptedGroupAgent(
        monkeypatch, gate_log, [Turn(steps_before_end=0, stdout=FAIL_REPORT)]
    )

    _drive(db_session, ticket, max_stages=1)

    assert len(agent.observations) == 1
    assert agent.observations[0].covered == ["g1", "g2"]
    assert executed_gates(gate_log) == []
    assert gate_evaluations(db_session, ticket) == []
    assert stage_map(db_session, ticket)["g2"] != StageStatus.DONE


def test_combined_gate_failure_writes_no_error_artifact(
    db_session, failing_gate_ticket, monkeypatch, gate_log
):
    """D9: the evaluation is still on the Timeline; no per-step ERROR artifact is attached."""
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, failing_gate_ticket, max_stages=1)

    evaluations = gate_evaluations(db_session, failing_gate_ticket)
    assert len(evaluations) == 1
    assert json.loads(evaluations[0].payload_json)["from_stage"] == LAST
    errors = [
        artifact
        for artifact in _artifacts(db_session, failing_gate_ticket)
        if artifact.kind == ArtifactKind.ERROR
        and artifact.title in {gate_failure_artifact_title(key) for key in GROUP_KEYS}
    ]
    assert errors == []


# ---- test-break: what a lazy implementation would get past --------------------------------


@pytest.fixture
def first_member_fails_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    """The *first* member's command fails and the last member's passes.

    An implementation that judges the combined gate by the last command's exit code,
    or by the last member's commands only, passes the s2-fails case and not this one.
    """
    seed_group_agents(db_session)
    stages = group_stages(
        gate_commands={
            "s1": [recording_gate("cmd-first-broken", gate_log, fail=True)],
            "s3": [recording_gate("cmd-last-ok", gate_log)],
        }
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), stages)


def test_a_failing_first_member_command_fails_the_combined_gate(
    db_session, first_member_fails_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, first_member_fails_ticket, max_stages=1)

    ticket = first_member_fails_ticket
    assert "cmd-first-broken" in executed_gates(gate_log), "s1's gate was dropped"
    assert rework_reroute_count(db_session, ticket, HEAD) == 1
    statuses = stage_map(db_session, ticket)
    assert [statuses[key] for key in GROUP_KEYS] == [StageStatus.PENDING] * 3
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == HEAD


@pytest.fixture
def flaky_once_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    """s2's command fails on the first combined gate and passes on the second."""
    seed_group_agents(db_session)
    stages = group_stages(
        gate_commands={
            "s1": [recording_gate("cmd-a", gate_log)],
            "s2": [fail_once_gate("cmd-once", gate_log, tmp_path / "cmd-once.marker")],
        }
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), stages)


def test_a_rerouted_group_is_redispatched_as_one_unit_and_counted_once(
    db_session, flaky_once_ticket, monkeypatch, gate_log
):
    """AC6 end to end: the reroute re-dispatches the whole group from its head, once.

    A reroute charged per member, or a re-dispatch that starts at the failing member
    (s2) instead of the head, or one that runs the members one run each, fails here.
    """
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, flaky_once_ticket, max_stages=2)

    ticket = flaky_once_ticket
    assert len(agent.observations) == 2, [obs.stage_key_at_start for obs in agent.observations]
    first, second = agent.observations
    assert first.covered == list(GROUP_KEYS)
    assert second.stage_key_at_start == HEAD
    assert second.covered == list(GROUP_KEYS)
    assert second.completed_steps == ["s1", "s2"]
    assert second.gates_run_before == ["cmd-a", "cmd-once"], "round one ran the union once"
    assert executed_gates(gate_log) == ["cmd-a", "cmd-once", "cmd-a", "cmd-once"]
    assert len(gate_evaluations(db_session, ticket)) == 2
    assert rework_reroute_count(db_session, ticket, HEAD) == 1
    for key in GROUP_KEYS[1:]:
        assert rework_reroute_count(db_session, ticket, key) == 0, f"{key} was charged too"
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == AFTER
    assert ticket.state != TicketState.BLOCKED


def test_a_group_resume_picks_this_tickets_run_over_a_newer_one_on_another_ticket(
    db_session, tmp_path
):
    """Non-vacuous ticket isolation: both tickets have a resumable group run, theirs newer."""
    seed_group_agents(db_session)
    workspace = repo_workspace(db_session, tmp_path / "repo")
    mine = build_ticket(db_session, workspace, group_stages())
    theirs = build_ticket(db_session, workspace, group_stages())
    repo_root = tmp_path / "repo"
    my_prior = _group_run(
        db_session,
        mine,
        code="run_mine_1",
        stage_key=LAST,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=20,
        stdout=_stream(),
    )
    _group_run(
        db_session,
        theirs,
        code="run_theirs_1",
        stage_key=LAST,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=5,
        stdout=_stream(),
    )
    _combined_gate_failure(db_session, mine)
    rerun = _group_run(
        db_session,
        mine,
        code="run_mine_2",
        stage_key=HEAD,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
    )

    plan = plan_rework_resume(
        db_session, mine, rerun, adapter=CliAdapter.CLAUDE, repo_root=repo_root
    )

    assert plan is not None and plan.resumes, getattr(plan, "skip_reason", "no plan")
    db_session.refresh(rerun)
    assert rerun.resumed_from_run_id == my_prior.id


def test_a_group_resume_ignores_an_ungrouped_run_on_the_head_step(
    db_session, resume_ticket, tmp_path
):
    """The scope is the group key, not the head's stage key: an older ungrouped run on s1
    (same ticket, same checkout, a session) must not be resumed in place of the group's run."""
    repo_root = tmp_path / "repo"
    group_prior = _group_run(
        db_session,
        resume_ticket,
        code="run_group_1",
        stage_key=LAST,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=20,
        stdout=_stream(),
    )
    loose = _group_run(
        db_session,
        resume_ticket,
        code="run_loose_s1",
        stage_key=HEAD,
        covered=[],
        repo_root=repo_root,
        minutes_ago=5,
        stdout=_stream(),
    )
    loose.agent_group_key = ""
    db_session.add(loose)
    db_session.commit()
    _combined_gate_failure(db_session, resume_ticket)
    rerun = _group_run(
        db_session,
        resume_ticket,
        code="run_group_2",
        stage_key=HEAD,
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
    )

    plan = plan_rework_resume(
        db_session, resume_ticket, rerun, adapter=CliAdapter.CLAUDE, repo_root=repo_root
    )

    assert plan is not None and plan.resumes, getattr(plan, "skip_reason", "no plan")
    db_session.refresh(rerun)
    assert rerun.resumed_from_run_id == group_prior.id


def test_an_early_ended_group_continues_its_session_only_once(db_session, resume_ticket, tmp_path):
    """§8: a prior run that already continued a session after an early end is not continued
    again; the continuation runs fresh and says why."""
    repo_root = tmp_path / "repo"
    original = _group_run(
        db_session,
        resume_ticket,
        code="run_group_0",
        stage_key="s2",
        covered=list(GROUP_KEYS),
        repo_root=repo_root,
        minutes_ago=20,
        stdout=_stream(),
    )
    prior = _group_run(
        db_session,
        resume_ticket,
        code="run_group_1",
        stage_key="s2",
        covered=["s2", "s3"],
        repo_root=repo_root,
        minutes_ago=10,
        stdout=_stream(),
    )
    prior.resumed_from_run_id = original.id
    prior.rework_feedback_id = ""
    db_session.add(prior)
    db_session.commit()
    continuation = _group_run(
        db_session,
        resume_ticket,
        code="run_group_2",
        stage_key="s2",
        covered=["s2", "s3"],
        repo_root=repo_root,
    )

    plan = plan_rework_resume(
        db_session, resume_ticket, continuation, adapter=CliAdapter.CLAUDE, repo_root=repo_root
    )

    assert plan is not None and not plan.resumes
    assert "already continued" in plan.skip_reason
    db_session.refresh(continuation)
    assert continuation.resumed_from_run_id == ""
    assert "already continued" in continuation.resume_skip_reason


@pytest.mark.parametrize("failing", ["g1", "g2"])
def test_a_gate_group_fails_whichever_member_gate_fails(
    db_session, tmp_path, monkeypatch, gate_log, failing
):
    stages = [
        WorkflowStageDef(key="work", name="Work", order=1, agent_id="planner"),
        *[
            WorkflowStageDef(
                key=key,
                name=key,
                order=order,
                stage_type="gate",
                agent_id=GATEKEEPER,
                gate_commands=[recording_gate(f"gate-{key}", gate_log, fail=key == failing)],
                agent_group="gates",
            )
            for order, key in ((2, "g1"), (3, "g2"))
        ],
        WorkflowStageDef(key=AFTER, name="After", order=4, agent_id="planner"),
        WorkflowStageDef(key=DONE, name="Done", order=5, terminal=True),
    ]
    ticket = build_ticket(
        db_session,
        repo_workspace(db_session, tmp_path / "repo"),
        stages,
        start_key="g1",
        stages_json=stages_up_to_done_json(stages, "work"),
    )
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, ticket, max_stages=1)

    assert len(agent.observations) == 1
    assert agent.observations[0].covered == ["g1", "g2"]
    assert f"gate-{failing}" in executed_gates(gate_log)
    assert len(gate_evaluations(db_session, ticket)) == 1
    assert all(run.stage_key != AFTER for run in runs_of(db_session, ticket))
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key != AFTER
