"""Auditable trace for transition-gate evaluations.

A gate that ran and passed and a gate that never ran used to be
indistinguishable: both produced ``ok=True`` with an empty message and left no
record anywhere, so no operator (and no UI) could tell "verified clean" from
"never checked". Recording every evaluation — with its explicit outcome and its
preserved message — is what closes that gap.

Kept out of ``builtin_orchestrator`` deliberately: that module is already at its
size ceiling, and reporting *about* a gate run is a separate concern from
driving the workflow.
"""

from __future__ import annotations

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    ArtifactKind,
    EventType,
    GateFixTier,
    GateOutcome,
    OrchestrationRun,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.gate_runner import GateRunResult, run_transition_gates, strip_ansi
from loregarden.services.orchestration_profile import OrchestrationProfile
from sqlmodel import Session


def clean_gate_detail(result: GateRunResult) -> str:
    """A human-readable failure detail for a gate result, naming the command."""
    detail = result.message or result.stderr or "Transition gate failed"
    if result.command:
        detail = f"{detail} (command: {result.command})"
    return strip_ansi(detail)


def run_and_record_gates(
    session: Session,
    callbacks,
    ticket: Ticket,
    orch_run: OrchestrationRun | None,
    profile: OrchestrationProfile,
    workspace: Workspace,
    stage_def: WorkflowStageDef,
    *,
    from_stage: str,
    to_stage: str,
    fix_tier: GateFixTier,
) -> str:
    """Re-run the transition gates after a fix attempt, and RECORD the result.

    This replaces a pure re-check that recorded nothing. That was defensible in
    the abstract and wrong for its only caller: when mechanical fixers cleared a
    gate, the successful re-evaluation produced no event at all, so the log
    showed the failure and then silence — "failed once and got fixed" read as
    "failed", the exact confusion `record_gate_evaluation`'s own docstring says
    must not happen (lg-workflow-integrity-683).
    """
    result = run_transition_gates(
        session,
        profile,
        workspace,
        ticket,
        from_stage=from_stage,
        to_stage=to_stage,
        stage_def=stage_def,
    )
    record_gate_evaluation(
        session,
        callbacks,
        ticket,
        orch_run,
        result,
        from_stage=from_stage,
        to_stage=to_stage,
        fix_tier=fix_tier,
    )
    return "" if result.ok else clean_gate_detail(result)


def gate_evaluation_title(outcome: str, from_stage: str, to_stage: str) -> str:
    """Artifact title naming the outcome, so "passed" and "skipped" read
    differently in the context tab without any client-side change."""
    return f"Gate {outcome} — {from_stage} → {to_stage}"


def record_gate_evaluation(
    session: Session,
    callbacks,
    ticket: Ticket,
    orch_run: OrchestrationRun | None,
    result: GateRunResult,
    *,
    from_stage: str,
    to_stage: str,
    fix_tier: GateFixTier = GateFixTier.NONE,
) -> None:
    """Emit a GATE_EVALUATED event and an outcome-titled context artifact.

    One row per evaluation, never upserted in place, so repeated failures across
    bounded autofix retries stay individually visible — "failed once and got
    fixed" must not read the same as "has failed on every retry and still does".
    """
    resolved = result.outcome or (GateOutcome.PASSED if result.ok else GateOutcome.FAILED)
    outcome = resolved.value
    message = result.message or (result.stderr if not result.ok else "")
    event_bus.publish(
        session,
        EventType.GATE_EVALUATED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        # Not run_id: that column references agent_runs, and a gate is evaluated
        # by the orchestrator between stages, not by an agent. Carrying the
        # orchestration run in the payload matches STAGE_STARTED.
        payload={
            "outcome": outcome,
            "message": message,
            "from_stage": from_stage,
            "to_stage": to_stage,
            "stage_key": from_stage,
            "command": result.command,
            # What had already tried to fix it. Without this a pass cannot say
            # why it passed, and neither recovery tier can be judged on evidence
            # (lg-workflow-integrity-683).
            "fix_tier": fix_tier.value,
            "orchestration_run_id": orch_run.id if orch_run else None,
        },
    )
    title = gate_evaluation_title(outcome, from_stage, to_stage)
    callbacks.attach_artifact(
        ticket,
        kind=ArtifactKind.CONTEXT,
        title=title,
        content={
            "title": title,
            "rows": [
                {"k": "Outcome", "v": outcome},
                {"k": "Transition", "v": f"{from_stage} → {to_stage}"},
                {"k": "Detail", "v": message},
                *(
                    [{"k": "After", "v": f"{fix_tier.value} fix"}]
                    if fix_tier is not GateFixTier.NONE
                    else []
                ),
            ],
        },
        # Same reason as the event above: `Artifact.run_id` references
        # agent_runs, and no agent ran this gate.
    )
