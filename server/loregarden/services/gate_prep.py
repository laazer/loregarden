"""What the last stage that writes code owes the human gate after it.

A human gate is a sign-off, not a build step: whatever a person needs in order
to judge the change has to exist before they get there. #968 asked that stage
for a review kit and did not check; a gate that opened without one told the
person to judge the change against its acceptance criteria, which are written
for agents to prove.
"""

from __future__ import annotations

from loregarden.models.domain import StageStatus, Ticket, WorkflowStageDef
from loregarden.services.review_kit import review_kits_for
from loregarden.services.studio_routing import is_agentless_stage
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session

#: Stage types whose agent authors the code — the ones that can still build
#: whatever a downstream human gate will need to run.
AUTHORING_STAGE_TYPES = frozenset({"agent", "classify"})


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


def missing_review_kit(session: Session, ticket: Ticket, *, stage_key: str) -> str:
    """Why a passing ``stage_key`` leaves its human gate with nothing to ask, or "".

    A kit counts when every step is a judgment (stored kits from before that
    was required do not), or when it says there is nothing for a person to
    judge. A gate ruled out for this ticket needs no kit.
    """
    _, stages = resolve_ticket_stages(session, ticket)
    gate = gate_prep_target(stages, stage_key)
    instance = workflow_instance_for(session, ticket.id)
    if gate is None or instance is None:
        return ""
    if parse_stage_map(instance, stages).get(gate.key) == StageStatus.WONT_DO:
        return ""
    kit = review_kits_for(session, [ticket.id]).get(ticket.id)
    if kit is not None:
        if kit.nothing_to_judge:
            return ""
        if kit.steps and all(step.judges is not None for step in kit.steps):
            return ""
    return (
        f"{ticket.external_id} parks at the '{gate.name}' human gate after this stage, and no "
        "review kit says what a person has to judge there. Attach one with "
        "`loregarden_attach_evidence` (evidence_kind real_surface, content_json.review): `steps`, "
        "each with `judges` and `why_person`, or `nothing_to_judge` saying what proved the change "
        "instead. Then report pass again."
    )


__all__ = ["AUTHORING_STAGE_TYPES", "gate_prep_target", "missing_review_kit"]
