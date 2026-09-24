"""A migrated stage still opens its gate.

`0138_runtime_exit_actions` pops `gate_required` out of every `stages_json` —
`workflow_templates`, `studio_workflows` and `workflow_instances` — and writes
`exit_actions_enabled` plus a typed `exit_actions` list in its place. It is
already applied to the shared dev database while the branch that reads the new
spelling is unmerged, so a build modelling only the old name meets data written
by the new one.

SQLModel ignores unknown fields, so that build does not fail. It reads
`gate_required=False` and marks the stage DONE. Measured against the live
`studio-loregarden-tdd-v3` template: four stages — `plan-synthesis`,
`ui-design`, `test-break` and `gate` — carry `exit_actions_enabled: true` with
an `operator_judgment` requirement, and every one of them would advance without
opening a gate. A gate that never opens and a gate a person approved are the
same row afterwards, which is the failure this repo calls a silent one.
"""

from __future__ import annotations

import json

from loregarden.models.domain import WorkflowStageDef

#: A stage exactly as `_migrate_legacy_stage` leaves it: no `gate_required`
#: key at all, the flag and the typed action in its place. Copied from the
#: live template rather than invented, so the fixture cannot drift from the
#: shape the migration actually writes.
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

#: The same stage before the migration ran.
LEGACY_GATE_STAGE = {
    "key": "gate",
    "name": "Quality Gate",
    "order": 11,
    "stage_type": "gate",
    "agent_id": "gatekeeper",
    "gate_commands": [],
    "gate_required": True,
}


def test_a_migrated_stage_still_requires_sign_off():
    """The regression. Before the predicate this read False and advanced."""
    stage = WorkflowStageDef.model_validate(MIGRATED_GATE_STAGE)

    assert stage.requires_human_sign_off is True
    # The old field really is absent — the test would pass for the wrong reason
    # if the fixture happened to carry both spellings.
    assert "gate_required" not in MIGRATED_GATE_STAGE
    assert stage.gate_required is False


def test_a_stage_written_before_the_migration_still_requires_sign_off():
    """Both sides of the migration, because a shared database holds both."""
    stage = WorkflowStageDef.model_validate(LEGACY_GATE_STAGE)

    assert stage.requires_human_sign_off is True
    assert "exit_actions_enabled" not in LEGACY_GATE_STAGE


def test_a_stage_that_gates_on_neither_spelling_advances():
    """The predicate must not turn every stage into a gate."""
    stage = WorkflowStageDef.model_validate(
        {"key": "implement", "name": "Implement", "order": 8, "exit_actions_enabled": False}
    )

    assert stage.requires_human_sign_off is False


def test_the_migrated_shape_round_trips_through_json():
    """`stages_json` is how this reaches the code, so parse it that way."""
    stage = WorkflowStageDef.model_validate(json.loads(json.dumps(MIGRATED_GATE_STAGE)))

    assert stage.key == "gate"
    assert stage.requires_human_sign_off is True
