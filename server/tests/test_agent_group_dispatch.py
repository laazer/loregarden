"""An agent group dispatches as one CLI run and walks the stage map step by step.

Spec lg-run-economics-2636 §5–§6. AC3: one run for the whole group, given
every grouped step's role, skill and brief in order; the stage map advances as
the agent completes each step; `agent_runs` records the group key and the steps
it covered. AC5: a run that ends before the last step runs no combined gate and
is re-dispatched from the first incomplete step. Plus the regressions the spec
lists beside them: a grouped step cannot fan out, and the stop hook lets a
grouped turn end only once its last step is reported.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import stage_report_stop_hook as hook
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.mcp import caller
from loregarden.models.domain import (
    AgentRun,
    DockerFootprint,
    MemoryBriefingAssembly,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.stage_docker_capacity import group_docker_def
from loregarden.services.stage_fanout_service import launch_fanout
from loregarden.services.stage_retry_budget import count_stage_dispatches
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, select
from tests.agent_group_harness import (
    AFTER,
    GROUP,
    GROUP_KEYS,
    STEP_AGENTS,
    STEP_BRIEFS,
    STEP_ROLES,
    STEP_SKILL_BODIES,
    STEP_SKILLS,
    TRANSIENT_STDERR,
    ScriptedGroupAgent,
    Turn,
    build_ticket,
    executed_gates,
    gate_evaluations,
    gated_profile,
    group_stages,
    recording_gate,
    repo_workspace,
    runs_of,
    seed_group_agents,
    stage_map,
)

GROUP_STAGE_KEYS_ENV_NAME = "LOREGARDEN_GROUP_STAGE_KEYS"


@pytest.fixture
def gate_log(tmp_path: Path) -> Path:
    return tmp_path / "gates.log"


@pytest.fixture
def grouped_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    """`s1, s2, s3` in group `g`, each with a gate command that records it ran."""
    seed_group_agents(db_session)
    stages = group_stages(
        gate_commands={key: [recording_gate(f"gate-{key}", gate_log)] for key in GROUP_KEYS}
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo"), stages)


@pytest.fixture
def ungrouped_ticket(db_session: Session, tmp_path: Path) -> Ticket:
    """The same template with `agent_group` cleared on every step."""
    seed_group_agents(db_session)
    workspace = repo_workspace(db_session, tmp_path / "repo-ungrouped")
    return build_ticket(db_session, workspace, group_stages(grouped=False))


@pytest.fixture
def prompt_ticket(db_session: Session) -> Ticket:
    """A grouped ticket in the seeded workspace, whose agent context resolves for a prompt."""
    seed_group_agents(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    return build_ticket(db_session, workspace, group_stages())


def _drive(session: Session, ticket: Ticket, *, max_stages: int) -> None:
    BuiltinOrchestrator(session).execute(ticket, gated_profile(), max_stages=max_stages)
    session.refresh(ticket)


# ---- AC3: one run for the whole group -------------------------------------------------


def test_a_group_dispatches_one_run_that_records_its_group_and_covered_steps(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, grouped_ticket, max_stages=1)

    group_runs = [run for run in runs_of(db_session, grouped_ticket) if run.stage_key != AFTER]
    assert len(group_runs) == 1, "three grouped steps must take one CLI run, not one each"
    run = group_runs[0]
    assert getattr(run, "agent_group_key", None) == GROUP
    assert json.loads(getattr(run, "covered_stage_keys_json", "[]")) == list(GROUP_KEYS)
    assert [obs.completed_steps for obs in agent.observations] == [["s1", "s2"]]
    statuses = stage_map(db_session, grouped_ticket)
    assert [statuses[key] for key in GROUP_KEYS] == [StageStatus.DONE] * 3


def test_the_stage_map_advances_as_the_agent_completes_each_step(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    agent = ScriptedGroupAgent(monkeypatch, gate_log, [Turn(out_of_order="s3")])

    _drive(db_session, grouped_ticket, max_stages=1)

    assert agent.observations, "the group was never dispatched"
    first = agent.observations[0]
    assert first.stage_key_at_start == "s1"
    after_s1 = first.after_first_step
    assert after_s1, "the agent never completed s1 inside the group run"
    assert after_s1["stage_map"]["s1"] == StageStatus.DONE
    # D4: the next grouped step stays PENDING while the agent works it.
    assert after_s1["stage_map"]["s2"] == StageStatus.PENDING
    assert after_s1["run_stage_key"] == "s2"


def test_completing_a_step_out_of_order_is_refused_naming_both_steps(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    agent = ScriptedGroupAgent(monkeypatch, gate_log, [Turn(out_of_order="s3")])

    _drive(db_session, grouped_ticket, max_stages=1)

    assert agent.observations, "the group was never dispatched"
    refusal = agent.observations[0].out_of_order_error
    assert refusal, "complete_stage(s3) while the run is on s2 must raise ValueError"
    assert "'s3'" in refusal and "'s2'" in refusal
    assert GROUP in refusal


def test_ungrouped_steps_still_dispatch_one_run_each(
    db_session, ungrouped_ticket, monkeypatch, gate_log
):
    """AC8 in miniature: the same template without a group runs exactly as before."""
    ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, ungrouped_ticket, max_stages=3)

    runs = runs_of(db_session, ungrouped_ticket)
    assert [run.stage_key for run in runs] == list(GROUP_KEYS)
    for run in runs:
        assert getattr(run, "agent_group_key", "") == ""
        assert getattr(run, "covered_stage_keys_json", "[]") == "[]"


def test_the_group_prompt_carries_every_steps_role_skill_and_brief_in_order(
    db_session, prompt_ticket
):
    workspace = db_session.get(Workspace, prompt_ticket.workspace_id)
    run = AgentRun(
        run_code="run_group_prompt",
        ticket_id=prompt_ticket.id,
        workspace_id=workspace.id,
        agent_id=STEP_AGENTS["s1"],
        stage_key="s1",
        skill_name=STEP_SKILLS["s1"],
        agent_group_key=GROUP,
        covered_stage_keys_json=json.dumps(list(GROUP_KEYS)),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    stage_def = next(stage for stage in group_stages() if stage.key == "s1")

    prompt = CliAgentExecutor(db_session)._build_prompt(
        prompt_ticket,
        run,
        {"role_body": STEP_ROLES["s1"], "adapter": "claude"},
        resolve_agent_context_dir(workspace),
        workspace,
        stage_def,
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )

    assert "## Grouped steps (one run, 3 steps)" in prompt
    headers = [prompt.find(f"### Step {i} of 3: `{key}`") for i, key in enumerate(GROUP_KEYS, 1)]
    assert all(position >= 0 for position in headers), headers
    assert headers == sorted(headers), "steps must appear in group order"
    bounds = [*headers, len(prompt)]
    for index, key in enumerate(GROUP_KEYS):
        section = prompt[bounds[index] : bounds[index + 1]]
        assert STEP_ROLES[key] in section, f"{key}'s role body is not under its own header"
        assert STEP_SKILL_BODIES[key] in section, f"{key}'s skill body is not under its header"
        assert STEP_BRIEFS[key] in section, f"{key}'s brief is not under its own header"


def test_an_ungrouped_prompt_has_no_grouped_steps_block(db_session, prompt_ticket):
    workspace = db_session.get(Workspace, prompt_ticket.workspace_id)
    run = AgentRun(
        run_code="run_plain_prompt",
        ticket_id=prompt_ticket.id,
        workspace_id=workspace.id,
        agent_id=STEP_AGENTS["s1"],
        stage_key="s1",
        skill_name=STEP_SKILLS["s1"],
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    stage_def = next(stage for stage in group_stages(grouped=False) if stage.key == "s1")

    prompt = CliAgentExecutor(db_session)._build_prompt(
        prompt_ticket,
        run,
        {"role_body": STEP_ROLES["s1"], "adapter": "claude"},
        resolve_agent_context_dir(workspace),
        workspace,
        stage_def,
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )

    assert "## Grouped steps" not in prompt
    assert STEP_ROLES["s1"] in prompt and STEP_SKILL_BODIES["s1"] in prompt


# ---- AC5: a run that ends early -----------------------------------------------------


def test_a_run_that_exits_on_s2_runs_no_gate_and_resumes_from_s3(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    """The agent passes s1, then ends its turn with a report that settles s2."""
    agent = ScriptedGroupAgent(monkeypatch, gate_log, [Turn(steps_before_end=1), Turn()])

    _drive(db_session, grouped_ticket, max_stages=2)

    assert len(agent.observations) == 2, "the incomplete group must be dispatched again"
    first, second = agent.observations
    assert first.covered == list(GROUP_KEYS)
    # Nothing gated the incomplete group: no command ran, no evaluation recorded.
    assert second.gates_run_before == []
    assert second.gate_evaluations_before == 0
    assert second.stage_key_at_start == "s3"
    assert second.covered == ["s3"]
    assert second.agent_group_key == GROUP


def test_the_span_state_after_an_early_exit_leaves_the_rest_pending(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(monkeypatch, gate_log, [Turn(steps_before_end=1)])

    _drive(db_session, grouped_ticket, max_stages=1)

    statuses = stage_map(db_session, grouped_ticket)
    assert statuses["s1"] == StageStatus.DONE
    assert statuses["s2"] == StageStatus.DONE
    assert statuses["s3"] == StageStatus.PENDING
    assert executed_gates(gate_log) == []


def test_a_transient_death_after_s1_redispatches_from_s2_and_charges_s2(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    """The run passes s1, then dies of infrastructure while on s2 (R1: the moved key)."""
    agent = ScriptedGroupAgent(
        monkeypatch,
        gate_log,
        [
            Turn(steps_before_end=1, status=RunStatus.FAILED, stdout="", stderr=TRANSIENT_STDERR),
            Turn(),
        ],
    )

    _drive(db_session, grouped_ticket, max_stages=2)

    assert len(agent.observations) == 2, "a transient death must be re-dispatched"
    first, second = agent.observations
    assert first.covered == list(GROUP_KEYS)
    assert second.gates_run_before == []
    assert second.stage_key_at_start == "s2"
    assert second.covered == ["s2", "s3"]
    # The completed step is never re-run or re-blocked.
    assert "s1" not in second.covered
    assert count_stage_dispatches(db_session, grouped_ticket.id, "s1") == 1
    assert count_stage_dispatches(db_session, grouped_ticket.id, "s2") == 1


def test_s1_stays_done_after_a_transient_death_on_s2(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(
        monkeypatch,
        gate_log,
        [Turn(steps_before_end=1, status=RunStatus.FAILED, stdout="", stderr=TRANSIENT_STDERR)],
    )

    _drive(db_session, grouped_ticket, max_stages=1)

    statuses = stage_map(db_session, grouped_ticket)
    assert statuses["s1"] == StageStatus.DONE
    assert statuses["s2"] == StageStatus.PENDING
    assert statuses["s3"] == StageStatus.PENDING
    assert executed_gates(gate_log) == []


# ---- §5.7: a grouped step cannot fan out ----------------------------------------------


def test_a_grouped_step_refuses_to_fan_out_before_any_write(
    db_session, grouped_ticket, monkeypatch
):
    def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("a refused fan-out must not dispatch an attempt")

    monkeypatch.setattr(CliAgentExecutor, "execute", must_not_dispatch)

    with pytest.raises(ValueError) as excinfo:
        launch_fanout(db_session, grouped_ticket, "s1", 2)

    message = str(excinfo.value)
    assert "'s1'" in message and f"agent group '{GROUP}'" in message
    assert runs_of(db_session, grouped_ticket) == []


# ---- §6.3: the stop hook waits for the last grouped step ------------------------------


def _assistant(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _ok(tool_use_id: str) -> str:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": False, "content": "ok"}
    return json.dumps({"type": "user", "message": {"content": [block]}})


def _mcp_complete(stage_key: str) -> list[str]:
    call = {
        "type": "tool_use",
        "id": "t1",
        "name": "mcp__loregarden__loregarden_complete_stage",
        "input": {"stage_key": stage_key},
    }
    return [_assistant(call), _ok("t1")]


def _bash_complete(stage_key: str) -> list[str]:
    command = (
        "./scripts/loregarden-cli.sh mcp call loregarden_complete_stage "
        f"ticket_id=x stage_key={stage_key}"
    )
    call = {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": command}}
    return [_assistant(call), _ok("t2")]


def _stop_input() -> hook.StopHookInput:
    return hook.StopHookInput.model_validate(
        {"session_id": "s-1", "last_assistant_message": "", "background_tasks": []}
    )


def test_the_group_env_var_is_named_beside_the_run_id_env():
    assert getattr(caller, "GROUP_STAGE_KEYS_ENV", None) == GROUP_STAGE_KEYS_ENV_NAME


@pytest.mark.parametrize("transcript", [_mcp_complete, _bash_complete], ids=["mcp", "bash"])
def test_a_grouped_turn_may_not_stop_after_completing_only_a_middle_step(transcript):
    with mock.patch.dict(os.environ, {GROUP_STAGE_KEYS_ENV_NAME: "s1,s2,s3"}):
        assert hook.has_reported(_stop_input(), transcript("s2")) is False


@pytest.mark.parametrize("transcript", [_mcp_complete, _bash_complete], ids=["mcp", "bash"])
def test_a_grouped_turn_may_stop_once_the_last_step_is_completed(transcript):
    with mock.patch.dict(os.environ, {GROUP_STAGE_KEYS_ENV_NAME: "s1,s2,s3"}):
        assert hook.has_reported(_stop_input(), transcript("s3")) is True


def test_without_the_group_env_any_complete_stage_still_counts():
    env = {k: v for k, v in os.environ.items() if k != GROUP_STAGE_KEYS_ENV_NAME}
    with mock.patch.dict(os.environ, env, clear=True):
        assert hook.has_reported(_stop_input(), _mcp_complete("s1")) is True


# ---- test-break: what a lazy implementation would get past --------------------------------


def test_an_early_ended_group_is_gated_over_its_whole_span_once_the_continuation_completes(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    """D7a: the combined gate covers every member, not only the steps the last run covered.

    A gate built from the continuation run's covered keys (["s3"]) would run gate-s3
    alone and silently drop the gates of s1 and s2.
    """
    ScriptedGroupAgent(monkeypatch, gate_log, [Turn(steps_before_end=1), Turn()])

    _drive(db_session, grouped_ticket, max_stages=2)

    ran = executed_gates(gate_log)
    assert sorted(ran) == ["gate-s1", "gate-s2", "gate-s3"], ran
    assert ran == ["gate-s1", "gate-s2", "gate-s3"], "the union runs in member order"
    assert len(gate_evaluations(db_session, grouped_ticket)) == 1
    db_session.refresh(grouped_ticket)
    assert grouped_ticket.workflow_stage_key == AFTER


def test_a_group_resumed_after_a_transient_death_is_gated_over_its_whole_span(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    ScriptedGroupAgent(
        monkeypatch,
        gate_log,
        [
            Turn(steps_before_end=1, status=RunStatus.FAILED, stdout="", stderr=TRANSIENT_STDERR),
            Turn(),
        ],
    )

    _drive(db_session, grouped_ticket, max_stages=2)

    assert executed_gates(gate_log) == ["gate-s1", "gate-s2", "gate-s3"]
    assert len(gate_evaluations(db_session, grouped_ticket)) == 1
    statuses = stage_map(db_session, grouped_ticket)
    assert [statuses[key] for key in GROUP_KEYS] == [StageStatus.DONE] * 3


def test_completing_a_step_outside_the_group_mid_run_is_refused(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    """The guard covers an uncovered key, not only an out-of-order covered one."""
    agent = ScriptedGroupAgent(monkeypatch, gate_log, [Turn(out_of_order=AFTER)])

    _drive(db_session, grouped_ticket, max_stages=1)

    assert agent.observations, "the group was never dispatched"
    refusal = agent.observations[0].out_of_order_error
    assert refusal, f"complete_stage({AFTER}) during the group run must raise ValueError"
    assert f"'{AFTER}'" in refusal and "'s2'" in refusal and GROUP in refusal
    assert all(run.stage_key != AFTER for run in runs_of(db_session, grouped_ticket))


def test_a_group_that_ends_on_its_first_step_every_time_is_bounded_by_the_retry_budget(
    db_session, grouped_ticket, monkeypatch, gate_log
):
    """AC5 'under the retry budget': endless transient deaths on s1 stop, they do not loop."""
    agent = ScriptedGroupAgent(
        monkeypatch,
        gate_log,
        [Turn(steps_before_end=0, status=RunStatus.FAILED, stdout="", stderr=TRANSIENT_STDERR)],
    )

    _drive(db_session, grouped_ticket, max_stages=25)

    assert 1 <= len(agent.observations) < 25, "the retry budget never stopped the re-dispatch"
    assert all(obs.stage_key_at_start == "s1" for obs in agent.observations)
    assert all(obs.covered == list(GROUP_KEYS) for obs in agent.observations)
    assert executed_gates(gate_log) == []
    assert gate_evaluations(db_session, grouped_ticket) == []


@pytest.fixture
def ungrouped_gated_ticket(db_session: Session, tmp_path: Path, gate_log: Path) -> Ticket:
    seed_group_agents(db_session)
    stages = group_stages(
        grouped=False,
        gate_commands={key: [recording_gate(f"gate-{key}", gate_log)] for key in GROUP_KEYS},
    )
    return build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo-u"), stages)


def test_ungrouped_steps_still_run_their_own_gate_after_each_step(
    db_session, ungrouped_gated_ticket, monkeypatch, gate_log
):
    """AC8: deferring gates to a group's end must not defer them for ungrouped steps."""
    agent = ScriptedGroupAgent(monkeypatch, gate_log)

    _drive(db_session, ungrouped_gated_ticket, max_stages=3)

    assert [obs.stage_key_at_start for obs in agent.observations] == list(GROUP_KEYS)
    assert [obs.gates_run_before for obs in agent.observations] == [
        [],
        ["gate-s1"],
        ["gate-s1", "gate-s2"],
    ]
    assert executed_gates(gate_log) == ["gate-s1", "gate-s2", "gate-s3"]
    assert len(gate_evaluations(db_session, ungrouped_gated_ticket)) == 3


# ---- §5.6: a group run holds its heaviest member's docker footprint ------------------


def test_a_group_run_holds_the_heaviest_covered_members_docker_footprint(db_session, tmp_path):
    seed_group_agents(db_session)
    stages = group_stages()
    sizes = {"s1": DockerFootprint.LIGHT, "s2": DockerFootprint.STACK, "s3": DockerFootprint.NONE}
    stages = [
        stage.model_copy(update={"docker_footprint": sizes[stage.key]})
        if stage.key in sizes
        else stage
        for stage in stages
    ]
    ticket = build_ticket(db_session, repo_workspace(db_session, tmp_path / "repo-d"), stages)
    run = AgentRun(
        run_code="run_group_docker",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=STEP_AGENTS["s1"],
        stage_key="s1",
        agent_group_key=GROUP,
        covered_stage_keys_json=json.dumps(list(GROUP_KEYS)),
    )
    db_session.add(run)
    db_session.commit()

    heaviest = group_docker_def(db_session, run)

    assert heaviest is not None and heaviest.key == "s2"
