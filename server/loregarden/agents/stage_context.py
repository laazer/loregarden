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
) -> str:
    stage_key = run.stage_key or ticket.workflow_stage_key
    display_name = stage_def.name if stage_def else stage_key
    legacy_stage = LEGACY_STAGE_ALIASES.get(stage_key, stage_key.upper())
    skill = run.skill_name or (stage_def.skill_name if stage_def else "")

    lines = [
        "## Loregarden run context (authoritative for this run)",
        "This stage was started by the Loregarden control plane. Execute the work below even if",
        "the project_board ticket markdown WORKFLOW STATE section shows a different legacy Stage.",
        "",
        f"- Loregarden stage key: `{stage_key}`",
        f"- Display name: {display_name}",
        f"- Legacy workflow alias: {legacy_stage}",
        f"- Assigned agent: {run.agent_id}",
        f"- Skill: {skill or '—'}",
        "",
        "Do not refuse work because the ticket file still says IMPLEMENTATION or names a different",
        "next agent. Complete this Loregarden stage, then update the ticket file only if your",
        "role requires it.",
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
