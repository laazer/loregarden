"""A migrated stage still opens its gate, and the retired flag cannot un-gate one.

`0138_runtime_exit_actions` pops `gate_required` out of every `stages_json` and
writes `exit_actions_enabled` plus a typed `exit_actions` list in its place. It
was applied to the shared dev database before the code reading the new spelling
merged, so for a while a build modelling only the old name read those stages as
ungated and advanced them. Measured against the live `studio-loregarden-tdd-v3`
template: four stages — `plan-synthesis`, `ui-design`, `test-break` and `gate` —
carry an `operator_judgment` exit action.

There is now one model. These pin its two halves: the migrated shape gates, and
a stage still spelled with `gate_required` fails validation rather than being
read — SQLModel ignores unknown keys — as a stage with no gate at all.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import StudioWorkflowStage, WorkflowStageDef
from loregarden.services.exit_actions import resolve_exit_actions
from pydantic import ValidationError

#: A stage exactly as `_migrate_legacy_stage` leaves it, copied from the live
#: template rather than invented so the fixture cannot drift from the shape the
#: migration actually writes.
MIGRATED_GATE_STAGE = {
    "key": "gate",
    "name": "Quality Gate",
    "order": 11,
    "stage_type": "gate",
    "agent_id": "gatekeeper",
    "gate_commands": [],
    "exit_actions_enabled": True,
    "exit_actions": [
        {
            "key": "legacy-stage-sign-off",
            "label": "Approve Quality Gate completion",
            "requirement": {
                "kind": "operator_judgment",
                "decision_prompt": "Approve completion of stage 'Quality Gate'.",
            },
        }
    ],
}


def test_a_migrated_stage_still_opens_a_human_gate():
    """Parsed the way `stages_json` reaches the code: through JSON."""
    stage = WorkflowStageDef.model_validate(json.loads(json.dumps(MIGRATED_GATE_STAGE)))

    resolution = resolve_exit_actions(stage, None)

    assert [action.action_key for action in resolution.human_required_actions] == [
        "legacy-stage-sign-off"
    ]
    assert resolution.assigned_actions == []


@pytest.mark.parametrize("model", [WorkflowStageDef, StudioWorkflowStage])
@pytest.mark.parametrize("flag", [True, False])
def test_the_retired_gate_flag_is_rejected_not_ignored(model, flag):
    """Ignored, `gate_required: true` would validate as a stage with no gate."""
    legacy = {"key": "gate", "name": "Quality Gate", "gate_required": flag}

    with pytest.raises(ValidationError, match="0138_runtime_exit_actions"):
        model.model_validate(legacy)


def test_a_stage_with_no_exit_actions_opens_no_gate():
    """The control: the rejection must not turn every stage into a failure or a gate."""
    stage = WorkflowStageDef.model_validate(
        {"key": "implement", "name": "Implement", "order": 8, "exit_actions_enabled": False}
    )

    assert resolve_exit_actions(stage, None).human_required_actions == []
