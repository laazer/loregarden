"""The prompt block that hands one agent turn every step of an agent group.

A group run (`services.agent_group_runs`) replaces the single stage's skill and
role sections with this block: how to walk the steps, the combined gate that
runs once at the end, and each covered step's brief, skill and role in order.
An ungrouped run gets nothing from here, and its prompt is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.agents.prompt_blocks import ROLE_BODY_CAP
from loregarden.agents.registry import get_agent
from loregarden.core.agent_groups import combined_gate_def, group_span
from loregarden.models.domain import AgentRun, Ticket, WorkflowStageDef
from loregarden.services.agent_group_runs import run_covered_keys
from loregarden.services.studio_agent_prompt import studio_agent_config
from loregarden.services.studio_routing import resolve_stage_execution
from loregarden.skills.registry import SkillNotFoundError, get_skill, skill_search_dirs
from sqlmodel import Session


def _gate_lines(stage: WorkflowStageDef) -> list[str]:
    lines = [f"- Gate command: `{command}`" for command in stage.gate_commands]
    lines += [f"- Required evidence: `{kind}`" for kind in stage.required_evidence]
    lines += [f"- Checklist: {item}" for item in stage.checklist]
    return lines or ["No gate commands, required evidence or checklist items."]


def _role_body(session: Session, agent_id: str) -> str:
    config = studio_agent_config(session, agent_id) or get_agent(agent_id) or {}
    return str(config.get("role_body") or "")[:ROLE_BODY_CAP]


def _step_lines(
    session: Session,
    ticket: Ticket,
    step: WorkflowStageDef,
    position: str,
    agent_context_dir: Path,
) -> list[str]:
    agent_id, skill_name = resolve_stage_execution(ticket, step)
    skill_body = (
        (get_skill(skill_name, agent_context_dir=agent_context_dir) or "") if skill_name else ""
    )
    if skill_name and not skill_body:
        raise SkillNotFoundError(
            skill_name, skill_search_dirs(agent_context_dir), agent_id=agent_id
        )
    lines = [
        "",
        f"### Step {position}: `{step.key}` — {step.name}",
        f"Agent: {agent_id} · Skill: {skill_name or '—'}",
    ]
    if step.stage_brief:
        lines += ["#### Brief", step.stage_brief]
    if skill_body:
        lines += ["#### Skill", skill_body]
    lines += ["#### Role", _role_body(session, agent_id)]
    return lines


def build_group_steps_block(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    stages: list[WorkflowStageDef],
    agent_context_dir: Path,
) -> list[str]:
    """The grouped-steps prompt lines for a group run; `[]` for an ungrouped run."""
    covered = run_covered_keys(run)
    if not covered:
        return []
    by_key = {stage.key: stage for stage in stages}
    count = len(covered)
    # Over the whole span, so the agent sees everything the end gate will check.
    combined = combined_gate_def(group_span(stages, covered[0]) or [by_key[covered[-1]]])
    lines = [
        "",
        f"## Grouped steps (one run, {count} steps)",
        f"This run covers {count} consecutive workflow steps as one agent turn. "
        "Work them in order.",
        "Gates are not checked between these steps; the combined gate below runs once, "
        "after the last step.",
        "After finishing each step except the last, call `loregarden_complete_stage` with "
        "`stage_key=<that step's key>` before starting the next.",
        f"Finish the last step (`{covered[-1]}`) with the stage report at the end of this "
        "prompt; it settles that step.",
        "If you send a step back (`outcome=reject`), stop after that call.",
        "",
        "### Combined end-of-group gate",
        *_gate_lines(combined),
    ]
    for index, key in enumerate(covered, start=1):
        lines += _step_lines(session, ticket, by_key[key], f"{index} of {count}", agent_context_dir)
    return lines
