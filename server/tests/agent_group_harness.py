"""Shared scaffolding for the agent-group dispatch and gate tests.

A workflow `s1 → s2 → s3 → after → done` whose first three steps can be put in
one agent group, real agents and skills for them (rows, not mocks, so the
prompt resolves them the way a dispatch does), gate commands that record their
own execution in a log file, and a scripted stand-in for the CLI executor that
behaves the way the stage prompt tells a grouped agent to: call
`complete_stage` for every covered step but the last, then end the turn with a
stage report that settles the last one.

The executor is the only thing replaced. Dispatch, routing, gates and rework
run for real.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    OrchestrationRun,
    RunStatus,
    Skill,
    StageStatus,
    StudioAgent,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import EventType
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, col, select

GROUP = "g"
GROUP_KEYS = ("s1", "s2", "s3")
AFTER = "after"
DONE = "done"

#: Three distinct agents on one adapter (claude), one per grouped step.
STEP_AGENTS = {"s1": "grp_one", "s2": "grp_two", "s3": "grp_three"}
STEP_SKILLS = {"s1": "grp-skill-one", "s2": "grp-skill-two", "s3": "grp-skill-three"}
STEP_ROLES = {
    key: f"ROLE-BODY-{key.upper()}: the role text of {key}'s agent." for key in GROUP_KEYS
}
STEP_SKILL_BODIES = {
    key: f"SKILL-BODY-{key.upper()}: the procedure for step {key}." for key in GROUP_KEYS
}
STEP_BRIEFS = {key: f"BRIEF-{key.upper()}: what step {key} must produce." for key in GROUP_KEYS}
AFTER_AGENT = "planner"

PASS_REPORT = (
    "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.95}\n'
    "<<<END_STAGE_REPORT>>>\n"
)

#: The control plane reaping a run whose lease nobody renewed: classified as a
#: transient (infrastructure) death by `stage_transient_retry`.
TRANSIENT_STDERR = "Agent run lease expired: nothing has renewed it for 11 minutes"


def recording_gate(name: str, log: Path, *, fail: bool = False) -> str:
    """A gate command that appends `name` to `log` when it runs, and fails if asked.

    Split with shlex and run without a shell, as `gate_runner` runs every gate.
    """
    script = 'printf "%s\\n" "$0" >> "$1"'
    if fail:
        script += '; echo "$0 failed: the combined gate found a defect"; exit 1'
    return shlex.join(["sh", "-c", script, name, str(log)])


def fail_once_gate(name: str, log: Path, marker: Path) -> str:
    """A recording gate that fails the first time it runs and passes every time after.

    `marker` is created on the first (failing) run, so a second round finds it.
    """
    script = (
        'printf "%s\\n" "$0" >> "$1"; '
        'if [ ! -e "$2" ]; then : > "$2"; echo "$0 failed: the combined gate found a defect"; '
        "exit 1; fi"
    )
    return shlex.join(["sh", "-c", script, name, str(log), str(marker)])


def executed_gates(log: Path) -> list[str]:
    """Every gate command that ran, in order, by the name it was given."""
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


def gate_evaluations(session: Session, ticket: Ticket) -> list[DomainEvent]:
    return list(
        session.exec(
            select(DomainEvent).where(
                DomainEvent.ticket_id == ticket.id,
                DomainEvent.type == EventType.GATE_EVALUATED,
            )
        ).all()
    )


def seed_group_agents(session: Session) -> None:
    """Studio agents and skills for the grouped steps, all on the claude adapter."""
    for key in GROUP_KEYS:
        slug = STEP_AGENTS[key]
        if session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first() is None:
            session.add(StudioAgent(slug=slug, name=slug, role_body=STEP_ROLES[key]))
        skill = STEP_SKILLS[key]
        if session.exec(select(Skill).where(Skill.slug == skill)).first() is None:
            session.add(Skill(slug=skill, name=skill, body=STEP_SKILL_BODIES[key]))
    session.commit()


def group_stages(
    *,
    grouped: bool = True,
    gate_commands: dict[str, list[str]] | None = None,
    required_evidence: dict[str, list[str]] | None = None,
) -> list[WorkflowStageDef]:
    """`s1, s2, s3` (grouped as `g` unless `grouped=False`), then `after`, then `done`."""
    commands = gate_commands or {}
    evidence = required_evidence or {}
    stages = [
        WorkflowStageDef(
            key=key,
            name=f"Step {key}",
            order=index,
            agent_id=STEP_AGENTS[key],
            skill_name=STEP_SKILLS[key],
            stage_brief=STEP_BRIEFS[key],
            gate_commands=commands.get(key, []),
            required_evidence=evidence.get(key, []),
            agent_group=GROUP if grouped else "",
        )
        for index, key in enumerate(GROUP_KEYS, start=1)
    ]
    stages.append(WorkflowStageDef(key=AFTER, name="After", order=4, agent_id=AFTER_AGENT))
    stages.append(WorkflowStageDef(key=DONE, name="Done", order=5, terminal=True))
    return stages


def repo_workspace(session: Session, repo: Path) -> Workspace:
    """A workspace over a real (empty) directory, so gate commands have a tree to run in."""
    repo.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(slug=f"agent-group-{uuid4().hex[:8]}", name="Group", repo_path=str(repo))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def build_ticket(
    session: Session,
    workspace: Workspace,
    stages: list[WorkflowStageDef],
    *,
    start_key: str | None = None,
    stages_json: str | None = None,
) -> Ticket:
    """A ticket at the first stage (or `start_key`) of a template built from `stages`."""
    ordered = sorted(stages, key=lambda stage: stage.order)
    transitions = [
        {"from": current.key, "to": following.key, "when": "pass"}
        for current, following in zip(ordered, ordered[1:], strict=False)
    ]
    template = WorkflowTemplate(
        slug=f"agent-group-tpl-{uuid4().hex[:8]}",
        name="Agent group",
        stages_json=json.dumps([stage.model_dump(mode="json") for stage in ordered]),
        transitions_json=json.dumps(transitions),
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    first = start_key or ordered[0].key
    ticket = Ticket(
        external_id=f"agent-group-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Agent group",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=first,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=first,
            stages_json=stages_json or initial_stages_json(ordered),
        )
    )
    session.commit()
    return ticket


def gated_profile() -> OrchestrationProfile:
    """Gates on, no profile-wide commands: only the stages' own `gate_commands` run."""
    return OrchestrationProfile(
        slug="agent-group",
        gates=GatesConfig(enabled=True, commands=[], autofix_commands=[]),
    )


def stage_map(session: Session, ticket: Ticket) -> dict[str, StageStatus]:
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    session.refresh(instance)
    template = session.get(WorkflowTemplate, instance.template_id)
    stages = [WorkflowStageDef.model_validate(item) for item in json.loads(template.stages_json)]
    return parse_stage_map(instance, stages)


def runs_of(session: Session, ticket: Ticket) -> list[AgentRun]:
    return list(
        session.exec(
            select(AgentRun)
            .where(AgentRun.ticket_id == ticket.id)
            .order_by(col(AgentRun.created_at))
        ).all()
    )


def covered_keys(run: AgentRun) -> list[str]:
    """The run's covered step keys; [] for an ungrouped run (or before the column exists)."""
    return json.loads(getattr(run, "covered_stage_keys_json", "[]") or "[]")


@dataclass
class Turn:
    """What one dispatch of the scripted agent does.

    `steps_before_end`: how many covered steps to pass through `complete_stage`
    before ending the turn; None means every covered step but the last, which
    is what the group prompt asks for. `out_of_order`: a step key to try to
    complete right after the first `complete_stage`, recording the refusal.
    """

    status: RunStatus = RunStatus.SUCCEEDED
    stdout: str = PASS_REPORT
    stderr: str = ""
    steps_before_end: int | None = None
    out_of_order: str = ""


@dataclass
class Observation:
    """What one dispatch saw, captured inside the executor while the run was live."""

    run_id: str
    agent_id: str
    stage_key_at_start: str
    covered: list[str]
    agent_group_key: str
    gates_run_before: list[str]
    gate_evaluations_before: int
    completed_steps: list[str] = field(default_factory=list)
    after_first_step: dict[str, object] = field(default_factory=dict)
    out_of_order_error: str = ""


class ScriptedGroupAgent:
    """Replace `CliAgentExecutor.execute` with a scripted agent; record every dispatch."""

    def __init__(self, monkeypatch, gate_log: Path, turns: list[Turn] | None = None) -> None:
        self.gate_log = gate_log
        self.turns = list(turns or [])
        self.observations: list[Observation] = []
        agent = self

        def fake_execute(executor, run: AgentRun, worker_ticket: Ticket, **_kwargs):
            return agent._turn(executor.session, run, worker_ticket)

        monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    def _next_turn(self) -> Turn:
        if len(self.turns) > 1:
            return self.turns.pop(0)
        return self.turns[0] if self.turns else Turn()

    def _turn(self, session: Session, run: AgentRun, ticket: Ticket) -> AgentRun:
        turn = self._next_turn()
        covered = covered_keys(run)
        observation = Observation(
            run_id=run.id,
            agent_id=run.agent_id,
            stage_key_at_start=run.stage_key,
            covered=covered,
            agent_group_key=getattr(run, "agent_group_key", ""),
            gates_run_before=executed_gates(self.gate_log),
            gate_evaluations_before=len(gate_evaluations(session, ticket)),
        )
        self.observations.append(observation)
        to_complete = covered[:-1]
        if turn.steps_before_end is not None:
            to_complete = to_complete[: turn.steps_before_end]
        orch_run = session.get(OrchestrationRun, run.orchestration_run_id)
        callbacks = OrchestrationCallbackService(session)
        for index, key in enumerate(to_complete):
            callbacks.complete_stage(orch_run, ticket, stage_key=key)
            observation.completed_steps.append(key)
            if index == 0:
                session.refresh(run)
                observation.after_first_step = {
                    "stage_map": stage_map(session, ticket),
                    "run_stage_key": run.stage_key,
                }
                if turn.out_of_order:
                    try:
                        callbacks.complete_stage(orch_run, ticket, stage_key=turn.out_of_order)
                    except ValueError as exc:
                        observation.out_of_order_error = str(exc)
        session.refresh(run)
        return OrchestrationService(session).complete_run(
            run, status=turn.status, stdout=turn.stdout, stderr=turn.stderr
        )
