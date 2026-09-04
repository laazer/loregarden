"""Validation for a Studio workflow's stages, run on create, update and publish.

Split out of `studio_service` when that module hit its size cap. The cut is not
arbitrary: every function here answers "may this stage list be saved?", takes
stages (and at most a session) and returns nothing, and each one exists because
a specific bad shape reached a live run. `StudioService` orchestrates; these
decide what it is allowed to write.

`available_agent_ids` and `available_skills` come along because they answer the
same question — does the thing a stage names actually exist — and `StudioService`
reuses them when building a generate prompt.
"""

from __future__ import annotations

from loregarden.agents.registry import list_agents as list_builtin_agents
from loregarden.models.domain import StudioAgent, StudioWorkflow, StudioWorkflowStage
from loregarden.services.studio_drift import StageRemovalNeedsConfirmation, detect_drift
from loregarden.services.studio_routing import TERMINAL_STAGE_KEY
from loregarden.skills.registry import list_skills
from sqlmodel import Session, select


def available_agent_ids(session: Session) -> list[str]:
    custom = [agent.slug for agent in session.exec(select(StudioAgent)).all()]
    builtin = [item["id"] for item in list_builtin_agents()]
    seen: set[str] = set()
    out: list[str] = []
    for slug in [*custom, *builtin]:
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return sorted(out, key=str.lower)


def _collect_stage_agent_ids(stages: list[StudioWorkflowStage]) -> set[str]:
    ids: set[str] = set()
    for stage in stages:
        if stage.agent_id:
            ids.add(stage.agent_id)
        for route in stage.classify_routes or []:
            if route.agent_id:
                ids.add(route.agent_id)
        for spec in stage.parallel_agents or []:
            if spec.agent_id:
                ids.add(spec.agent_id)
    return ids


def validate_stage_agent_ids(session: Session, stages: list[StudioWorkflowStage]) -> None:
    """Reject a workflow whose stages reference agents that do not exist. Nothing
    validated this before, which let a template ship pointing at a missing agent."""
    available = set(available_agent_ids(session))
    unknown = sorted(_collect_stage_agent_ids(stages) - available)
    if unknown:
        raise ValueError(f"Workflow references unknown agent(s): {', '.join(unknown)}")


def validate_stage_route_targets(stages: list[StudioWorkflowStage]) -> None:
    """Reject a classify branch pointing at a stage the workflow doesn't have.

    A phantom target would otherwise raise at routing time, mid-run, not on save.
    """
    keys = {stage.key for stage in stages}
    unknown = sorted(
        {
            route.to_stage
            for stage in stages
            for route in stage.classify_routes or []
            if route.to_stage and route.to_stage not in keys
        }
    )
    if unknown:
        raise ValueError(f"Workflow routes branch to unknown stage(s): {', '.join(unknown)}")


def validate_classify_routes_are_selectable(stages: list[StudioWorkflowStage]) -> None:
    """Reject a classify route nothing can choose on purpose.

    `_select_classify_route` picks a route one of three ways: content scoring on
    `specialties`/`languages`, the pin when it names exactly one route, or the
    route marked `default`. A route with no specialties, no languages and no
    default flag is reachable by none of them — it can only be reached by being
    first in the list, which is position, not intent. The author almost
    certainly meant it to be the default.

    This is a forward guard, not a fix for anything shipped: every classify route
    in every live template is currently selectable. It is here because the defect
    it prevents is the neighbour of one that did ship — a pin matching on agent
    picked `classify_routes[0]` for 471 tickets, and "whichever is first" is the
    same failure wearing different clothes.

    Deliberately NOT rejected: two routes naming the same agent with different
    branches. `studio-loregarden-tdd-v3`'s triage stage does exactly that — the
    scoper triages everything, and typo/docs work skips ahead to `test-design` —
    and it is a reasonable thing to express. The pin cannot disambiguate it, so
    `_select_classify_route` declines to let the pin steer there at all; the
    template is fine, and forbidding it would remove a working shortcut.
    """
    unreachable = sorted(
        {
            f"{stage.key}[{index}] -> {route.agent_id or '(no agent)'}"
            for stage in stages
            for index, route in enumerate(stage.classify_routes or [])
            if not (route.specialties or route.languages or route.default)
        }
    )
    if unreachable:
        raise ValueError(
            "Classify route(s) can only be chosen by list position, which is not a "
            f"routing rule: {', '.join(unreachable)}. Give each one specialties or "
            "languages to match on, or mark one `default`."
        )


def validate_has_terminal_stage(stages: list[StudioWorkflowStage]) -> None:
    """Reject a workflow with no terminal stage. Without one the orchestrator has
    nowhere to finalize on: a passing final stage re-loops instead of completing
    the ticket (the studio-loregarden-tdd v2/v3 templates shipped this way and
    cycled back to implement after the gate passed). A stage is terminal via the
    `terminal` flag or the historical `done` key — matching is_terminal_stage.
    """
    if stages and not any(stage.terminal or stage.key == TERMINAL_STAGE_KEY for stage in stages):
        raise ValueError(
            "Workflow must have a terminal stage (set `terminal: true`, or add a "
            "`done` stage) so the orchestrator can finalize the ticket."
        )


def validate_alternative_groups(stages: list[StudioWorkflowStage]) -> None:
    """Reject an alternative group that cannot mean what it says.

    A group's whole content is the invariant "at least one of these must run",
    which `skip_stage` enforces by refusing to prune the last surviving member.
    Two shapes make that invariant unstatable, and both are author errors worth
    catching on save rather than mid-run:

      * A member that is not `optional` cannot be pruned at all, so the group is
        a no-op — it reads as a constraint while enforcing nothing, which is
        worse than no group.
      * The terminal stage is never prunable (`is_prunable_stage`), so a group
        containing it has the same problem plus a misleading name on the stage
        that ends the workflow.

    A one-member group is deliberately allowed: it is what an author has while
    adding the second member, and it enforces correctly in the meantime.
    """
    groups: dict[str, list[StudioWorkflowStage]] = {}
    for stage in stages:
        if stage.alternative_group:
            groups.setdefault(stage.alternative_group, []).append(stage)

    for group, members in sorted(groups.items()):
        terminal = [m.key for m in members if m.terminal or m.key == TERMINAL_STAGE_KEY]
        if terminal:
            raise ValueError(
                f"Alternative group '{group}' contains the terminal stage "
                f"{', '.join(sorted(terminal))} — the stage that ends the workflow "
                "can never be pruned, so the group would enforce nothing."
            )
        required = sorted(m.key for m in members if not m.optional)
        if required:
            raise ValueError(
                f"Alternative group '{group}' contains required stage(s) "
                f"{', '.join(required)} — every member must be `optional`, or the "
                "group's 'at least one must run' rule is already satisfied and "
                "enforces nothing."
            )


def refuse_unconfirmed_stage_removal(session: Session, workflow: StudioWorkflow) -> None:
    """Raise if publishing this draft would strand a live ticket mid-workflow.

    Only stages that a live ticket is CURRENTLY sitting on count. A removed stage
    nobody has reached is an ordinary edit, and requiring confirmation for it
    would make the prompt routine — which is how a confirmation stops being read.
    """
    drift = detect_drift(session, workflow)
    if not drift.stranded.count:
        return
    raise StageRemovalNeedsConfirmation(
        f"Publishing '{workflow.slug}' removes stage(s) "
        f"{', '.join(drift.stranded.stage_keys)}, which {drift.stranded.count} live "
        f"ticket(s) are currently on: {', '.join(drift.stranded.ticket_ids[:10])}. "
        "Their cursor would point at a stage that no longer exists. Re-publish with "
        "confirm_stage_removal to proceed."
    )


def available_skills() -> list[str]:
    return list_skills()


def _collect_stage_skill_names(stages: list[StudioWorkflowStage]) -> set[str]:
    names: set[str] = set()
    for stage in stages:
        if stage.skill_name:
            names.add(stage.skill_name)
        for route in stage.classify_routes or []:
            if route.skill_name:
                names.add(route.skill_name)
        for spec in stage.parallel_agents or []:
            if spec.skill_name:
                names.add(spec.skill_name)
    return names


def validate_stage_skill_names(stages: list[StudioWorkflowStage]) -> None:
    """Reject a workflow whose stages declare skills that do not exist.

    The mirror of validate_stage_agent_ids, and missing for the same reason it
    was: nothing checked skills on the way in, so a dangling name saved cleanly
    and raised SkillNotFoundError at prompt-build time instead — a run that dies
    several steps from the edit that caused it.
    """
    unknown = sorted(_collect_stage_skill_names(stages) - set(available_skills()))
    if unknown:
        raise ValueError(f"Workflow references unknown skill(s): {', '.join(unknown)}")


def validate_default_skill(default_skill: str) -> None:
    """Reject an agent whose default skill does not exist. Every stage that
    dispatches the agent would otherwise fail at render, not at save."""
    if default_skill and default_skill not in set(available_skills()):
        raise ValueError(f"Agent references unknown skill: {default_skill}")
