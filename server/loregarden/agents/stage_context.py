"""Map Loregarden workflow stage keys to agent-facing run context."""

from __future__ import annotations

from loregarden.models.domain import AgentRun, Ticket, WorkflowStageDef
from loregarden.services.compatibility_posture import ResolvedPosture
from loregarden.services.rework_feedback import render_rework_feedback
from loregarden.services.studio_routing import is_agentless_stage
from sqlmodel import Session

#: Stage types whose agent authors the code — the ones that can still build
#: whatever a downstream human gate will need to run.
AUTHORING_STAGE_TYPES = frozenset({"agent", "classify"})

# Legacy ticket / workflow-enforcement stage names agents recognize.
LEGACY_STAGE_ALIASES: dict[str, str] = {
    "planning": "PLANNING",
    "context": "CONTEXT_GATHERING",
    "specification": "SPECIFICATION",
    "test_design": "TEST_DESIGN",
    "test_break": "TEST_BREAK",
    "implementation": "IMPLEMENTATION_BACKEND",
    "testing": "STATIC_QA",
    "review": "GATEKEEPER_REVIEW",
    "approval": "AWAITING_APPROVAL",
    "done": "COMPLETE",
}


def gate_prep_target(
    stages: list[WorkflowStageDef] | None, stage_key: str
) -> WorkflowStageDef | None:
    """The human gate this stage is the last chance to prepare for, if any.

    A human gate is a sign-off, not a build step: whatever it needs in order to
    be run — a test scene, a level, a harness, a fixture — has to exist before
    the operator gets there. That work belongs to the last stage that authors
    code ahead of the gate, so the brief goes to that stage and to no other.
    Reviews and gates sitting between the two are not authoring stages and are
    skipped over.
    """
    ordered = sorted(stages or [], key=lambda s: s.order)
    authoring: WorkflowStageDef | None = None
    for stage in ordered:
        if is_agentless_stage(stage) and stage.checklist:
            if authoring is not None and authoring.key == stage_key:
                return stage
            # A later gate is prepared by a later authoring stage, not this one.
            authoring = None
            continue
        if stage.stage_type in AUTHORING_STAGE_TYPES and stage.agent_id:
            authoring = stage
    return None


def build_orchestration_context(
    *,
    ticket: Ticket,
    run: AgentRun,
    stage_def: WorkflowStageDef | None,
    stages: list[WorkflowStageDef] | None = None,
    posture: ResolvedPosture | None = None,
    session: Session | None = None,
) -> str:
    stage_key = run.stage_key or ticket.workflow_stage_key
    display_name = stage_def.name if stage_def else stage_key
    legacy_stage = LEGACY_STAGE_ALIASES.get(stage_key, stage_key.upper())
    skill = run.skill_name or (stage_def.skill_name if stage_def else "")

    lines = [
        "## Loregarden run context (authoritative for this run)",
        "This stage was started by the Loregarden control plane. The values below are the truth",
        "for this run — they override any other stage or agent you infer from elsewhere.",
        "",
        f"- Loregarden stage key: `{stage_key}`",
        f"- Display name: {display_name}",
        f"- Legacy workflow alias: {legacy_stage}",
        f"- Assigned agent: {run.agent_id}",
        f"- Skill: {skill or '—'}",
        "",
        "This ticket has no markdown file. Ticket data — description, acceptance criteria, stage",
        "cursor — lives in Loregarden's database and is reachable only via the MCP tools. Do not",
        "search the repo for a ticket file, and do not write ticket content to one; `project_board/`",
        "holds checkpoint and handoff artifacts only. Complete this stage, then record changes",
        "through MCP.",
    ]

    # Without the real key list, `reroute_to_stage` is a guess — and a plausible
    # invented key (e.g. "implementation" where this workflow says "implement")
    # gets dropped, sending rework to the wrong stage.
    upstream = [stage.key for stage in sorted(stages or [], key=lambda s: s.order)]
    if stage_key in upstream:
        upstream = upstream[: upstream.index(stage_key)]
    if upstream:
        lines += [
            "",
            "### Valid `reroute_to_stage` values for this workflow",
            "If your stage report rejects this work, `reroute_to_stage` MUST be one of these exact",
            "keys (upstream of your own stage) — anything else is discarded and the rework is routed",
            "to the immediately preceding stage instead. Use `null` if none applies.",
            "",
            ", ".join(f"`{key}`" for key in upstream),
        ]

    gate = gate_prep_target(stages, stage_key)
    if gate is not None:
        lines += [
            "",
            f"### You are the last stage before the `{gate.key}` human gate",
            f"When this work passes review it parks at '{gate.name}', where a person runs it by "
            "hand. That gate is a sign-off, not a build step — nothing gets authored there. Any "
            "scene, level, harness, fixture, or entry point needed to exercise this change must "
            "exist and be committed when you finish, and your stage report must name the files "
            "the operator should open and run.",
        ]

    if posture is not None:
        lines += [
            "",
            "### Compatibility posture — how freely you may change existing code",
            f"**{posture.posture.value}** (source: {posture.source})",
            "",
            "This is the authoritative answer to 'am I allowed to break this?' for this work item.",
            "It overrides any general instinct — or any role/module text — telling you to preserve",
            "existing behaviour by default.",
            "",
            posture.contract,
        ]

    # Prefer the full rework-feedback ledger over ticket.blocking_issues: the
    # latter is capped for the workflow pane (a 200-char pointer for anything
    # longer), so on a reroute it can carry no actionable detail at all. The
    # ledger holds every prior round's feedback in full — the union across
    # rounds, not just the latest framing. Fall back to blocking_issues when no
    # ledger exists (older tickets, or non-reroute blocks).
    feedback = render_rework_feedback(session, ticket, stage_key) if session else ""
    feedback = feedback or ticket.blocking_issues
    if feedback:
        lines += [
            "",
            "## Why you're here — prior stage feedback",
            "This ticket was routed back to this stage. Address every point below before "
            "reporting `pass` — each is from a prior reviewer or verifier that rejected this "
            "work. Later rounds do not supersede earlier ones; all must be resolved:",
            "",
            feedback,
        ]

    return "\n".join(lines)
