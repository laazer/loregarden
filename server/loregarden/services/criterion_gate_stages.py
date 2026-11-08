"""Which stage of a workflow is a ticket's gate, and whether entering it needs the criteria check.

Pure functions of the stage list, kept apart from `criterion_gate` (which runs
the check) so that the gate's signers (`criteria_sign_off`, reached from
`autopilot_sign_off`) can ask which stage is the gate without importing the
check and everything it runs.
"""

from __future__ import annotations

from loregarden.agents.registry import VERIFIER_AGENT_ID
from loregarden.models.domain import WorkflowStageDef
from loregarden.models.domain.stage_types import StageType
from loregarden.services.studio_routing import is_terminal_stage


def gate_of(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """The stage a ticket's criteria are checked before: its first gate, else its terminal."""
    ordered = sorted(stages, key=lambda s: s.order)
    return next((s for s in ordered if s.stage_type == StageType.GATE), None) or next(
        (s for s in ordered if is_terminal_stage(s)), None
    )


def verify_of(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    return next((s for s in stages if s.agent_id == VERIFIER_AGENT_ID), None)


def check_due(stages: list[WorkflowStageDef], target: WorkflowStageDef) -> bool:
    """Whether entering ``target`` needs the check: it is the gate, after a verify stage."""
    gate, verify = gate_of(stages), verify_of(stages)
    return (
        gate is not None
        and verify is not None
        and gate.key == target.key
        and verify.order < gate.order
    )
