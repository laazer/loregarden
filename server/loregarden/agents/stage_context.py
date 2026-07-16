"""Map Loregarden workflow stage keys to agent-facing run context."""

from __future__ import annotations

from loregarden.models.domain import AgentRun, Ticket, WorkflowStageDef

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


def build_orchestration_context(
    *,
    ticket: Ticket,
    run: AgentRun,
    stage_def: WorkflowStageDef | None,
    stages: list[WorkflowStageDef] | None = None,
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

    if ticket.blocking_issues:
        lines += [
            "",
            "## Why you're here — prior stage feedback",
            "This ticket was routed back to this stage. Address the following before reporting `pass`:",
            "",
            ticket.blocking_issues,
        ]

    return "\n".join(lines)
