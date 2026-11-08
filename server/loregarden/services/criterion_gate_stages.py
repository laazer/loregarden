"""Which stage of a workflow is a ticket's gate, which settles its criteria, and when to check.

Pure functions of the stage list, kept apart from `criterion_gate` (which runs
the check) so that the gate's signers (`criteria_sign_off`, reached from
`autopilot_sign_off`) can ask which stage is the gate without importing the
check and everything it runs.

Two workflow shapes settle criteria:

- **A verify stage before the gate** (Loregarden TDD V3). The verifier settles
  each criterion met; the check runs on entering the gate.
- **A gate that is itself the AC gatekeeper** (Blobert TDD, whose `ac_gate`
  maps each criterion to its evidence and has no verify stage before it). The
  gatekeeper settles them, so the check runs on leaving the gate: on entering
  the first stage after it that has not been ruled out.

Either way the gaps go back to the settling stage.
"""

from __future__ import annotations

from loregarden.agents.registry import AC_GATEKEEPER_AGENT_ID, VERIFIER_AGENT_ID
from loregarden.models.domain import WorkflowStageDef
from loregarden.models.domain.enums import StageStatus
from loregarden.models.domain.stage_types import StageType
from loregarden.services.studio_routing import is_terminal_stage


def gate_of(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """The stage a ticket's criteria are checked before: its first gate, else its terminal."""
    ordered = sorted(stages, key=lambda s: s.order)
    return next((s for s in ordered if s.stage_type == StageType.GATE), None) or next(
        (s for s in ordered if is_terminal_stage(s)), None
    )


def settler_of(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """The stage that settles criteria met: a verify stage before the gate, else an AC-gatekeeper gate."""
    gate = gate_of(stages)
    if gate is None:
        return None
    verify = next((s for s in stages if s.agent_id == VERIFIER_AGENT_ID), None)
    if verify is not None and verify.order < gate.order:
        return verify
    return gate if gate.agent_id == AC_GATEKEEPER_AGENT_ID else None


def runs_check(stages: list[WorkflowStageDef]) -> bool:
    """Whether this workflow checks its tickets' criteria at all."""
    return settler_of(stages) is not None


def check_due(
    stages: list[WorkflowStageDef],
    stage_map: dict[str, StageStatus],
    target: WorkflowStageDef,
) -> bool:
    """Whether entering ``target`` needs the check.

    After a verify stage: ``target`` is the gate. After a gate that settles its
    own criteria: ``target`` is the first stage after the gate, not counting
    stages ruled out (``wont_do``, as an optional playtest is), and has not
    started. A stage already running or awaiting a person was checked on entry.
    """
    gate, settler = gate_of(stages), settler_of(stages)
    if gate is None or settler is None:
        return False
    if settler.key != gate.key:
        return target.key == gate.key
    if target.order <= gate.order or stage_map.get(target.key) != StageStatus.PENDING:
        return False
    between = [s for s in stages if gate.order < s.order < target.order]
    return all(stage_map.get(s.key) == StageStatus.WONT_DO for s in between)
