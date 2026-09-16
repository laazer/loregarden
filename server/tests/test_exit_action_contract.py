"""Behavioral contracts for typed, runtime-evaluated stage exit actions."""

import json

import pytest
from loregarden.core.workflow_loader import get_template_stages_at_version
from loregarden.models.domain import (
    ApprovalAction,
    ApprovalView,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    WorkflowStageDef,
    WorkflowTemplate,
)
from loregarden.services.studio_generation import parse_workflow_generate_payload
from loregarden.services.studio_service import StudioService
from pydantic import ValidationError
from sqlmodel import Session, select

EXIT_ACTIONS = [
    {
        "key": "run-api-smoke",
        "label": "Run the API smoke test",
        "description": "Exercise the published HTTP surface.",
        "requirement": {
            "kind": "runtime_capability",
            "capability_id": "http_test_client",
        },
    },
    {
        "key": "read-provider-usage",
        "label": "Read provider usage",
        "requirement": {
            "kind": "credential",
            "credential_key": "claude_profile",
        },
    },
    {
        "key": "publish-release",
        "label": "Publish the release",
        "requirement": {
            "kind": "authority",
            "authority_scope": "release:publish",
        },
    },
    {
        "key": "accept-residual-risk",
        "label": "Accept residual risk",
        "requirement": {
            "kind": "operator_judgment",
            "decision_prompt": "Are the documented residual risks acceptable?",
        },
    },
]

# Operator judgment has no server catalog identifier, so it is the stable fixture
# for end-to-end persistence tests. The model contract above still proves all four
# discriminated requirement shapes survive validation without collapsing to prose.
PERSISTED_EXIT_ACTIONS = [EXIT_ACTIONS[3]]


def _stages(actions: list[dict] | None = None) -> list[StudioWorkflowStage]:
    return [
        StudioWorkflowStage.model_validate(
            {
                "key": "verify",
                "name": "Verify",
                "agent_id": "verifier",
                "order": 1,
                "exit_actions_enabled": True,
                "exit_actions": actions or PERSISTED_EXIT_ACTIONS,
            }
        ),
        StudioWorkflowStage(key="done", name="Done", order=2, terminal=True),
    ]


def _action_payload(stage: StudioWorkflowStage | WorkflowStageDef) -> list[dict]:
    return [action.model_dump(mode="json") for action in stage.exit_actions]


def test_stage_models_expose_only_the_typed_exit_action_contract():
    for model in (WorkflowStageDef, StudioWorkflowStage):
        stage = model.model_validate(
            {
                "key": "verify",
                "name": "Verify",
                "exit_actions_enabled": True,
                "exit_actions": EXIT_ACTIONS,
            }
        )

        assert "gate_required" not in model.model_fields
        assert stage.exit_actions_enabled is True
        assert _action_payload(stage) == EXIT_ACTIONS


@pytest.mark.parametrize(
    ("actions", "message"),
    [
        ([{**EXIT_ACTIONS[0], "key": "not valid"}], "action key"),
        ([EXIT_ACTIONS[0], {**EXIT_ACTIONS[1], "key": EXIT_ACTIONS[0]["key"]}], "duplicate"),
        ([{**EXIT_ACTIONS[0], "label": "   "}], "label"),
        (
            [
                {
                    **EXIT_ACTIONS[0],
                    "requirement": {
                        "kind": "runtime_capability",
                        "capability_id": "",
                    },
                }
            ],
            "capability_id",
        ),
        (
            [
                {
                    **EXIT_ACTIONS[3],
                    "requirement": {
                        "kind": "operator_judgment",
                        "decision_prompt": " ",
                    },
                }
            ],
            "decision_prompt",
        ),
        (
            [
                {
                    **EXIT_ACTIONS[0],
                    "requirement": {
                        "kind": "runtime_capability",
                        "capability_id": "http_test_client",
                        "credential_key": "claude_profile",
                    },
                }
            ],
            "credential_key",
        ),
        (
            [
                {
                    **EXIT_ACTIONS[1],
                    "requirement": {"kind": "credential"},
                }
            ],
            "credential_key",
        ),
    ],
)
def test_studio_rejects_malformed_exit_actions(actions: list[dict], message: str):
    with pytest.raises(ValidationError, match=message):
        StudioWorkflowStage.model_validate(
            {
                "key": "verify",
                "name": "Verify",
                "exit_actions_enabled": True,
                "exit_actions": actions,
            }
        )


def test_studio_rejects_actions_when_resolution_is_disabled():
    with pytest.raises(ValidationError, match="exit_actions_enabled"):
        StudioWorkflowStage.model_validate(
            {
                "key": "verify",
                "name": "Verify",
                "exit_actions_enabled": False,
                "exit_actions": [EXIT_ACTIONS[3]],
            }
        )


@pytest.mark.parametrize(
    "requirement",
    [
        {
            "kind": "runtime_capability",
            "capability_id": "http_test_client",
            "authority_scope": "release:publish",
        },
        {
            "kind": "credential",
            "credential_key": "claude_profile",
            "decision_prompt": "Should never be accepted",
        },
        {
            "kind": "authority",
            "authority_scope": "release:publish",
            "capability_id": "http_test_client",
        },
        {
            "kind": "operator_judgment",
            "decision_prompt": "Is the residual risk acceptable?",
            "credential_key": "claude_profile",
        },
        {"kind": "invented_requirement", "capability_id": "http_test_client"},
    ],
)
def test_requirement_discriminator_rejects_cross_kind_and_unknown_fields(requirement: dict):
    """A second identifier must not be silently discarded by Pydantic.

    Ignoring an extra field would make the stored action look valid while two
    callers could resolve it under different requirement semantics.
    """
    action = {**EXIT_ACTIONS[0], "requirement": requirement}

    with pytest.raises(ValidationError):
        StudioWorkflowStage.model_validate(
            {
                "key": "verify",
                "name": "Verify",
                "exit_actions_enabled": True,
                "exit_actions": [action],
            }
        )


@pytest.mark.parametrize("key", ["", "   ", " leading", "trailing ", "a/b"])
def test_action_keys_are_stable_machine_identifiers(key: str):
    with pytest.raises(ValidationError, match="action key"):
        StudioWorkflowStage.model_validate(
            {
                "key": "verify",
                "name": "Verify",
                "exit_actions_enabled": True,
                "exit_actions": [{**EXIT_ACTIONS[3], "key": key}],
            }
        )


def test_approval_action_is_closed_and_includes_recheck():
    assert ApprovalAction.model_validate({"action": "recheck"}).action == "recheck"

    with pytest.raises(ValidationError):
        ApprovalAction.model_validate({"action": "definitely-not-valid"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason_code", "invented_reason"),
        ("resolution_mode", "approve_despite_missing_credential"),
    ],
)
def test_human_required_action_vocabularies_fail_closed(field: str, value: str):
    action = {
        "action_key": "read-provider-usage",
        "action_label": "Read provider usage",
        "action_description": "",
        "requirement": {
            "kind": "credential",
            "credential_key": "claude_profile",
        },
        "reason_code": "credential_status_unknown",
        "reason": "Credential status unavailable: claude_profile",
        "resolution_mode": "recheck",
        field: value,
    }

    with pytest.raises(ValidationError):
        ApprovalView.model_validate(
            {
                "id": "approval-1",
                "title": "Resolve Verify exit actions",
                "level": "medium",
                "workspace_slug": "loregarden",
                "stage_key": "verify",
                "stage_name": "Verify",
                "impact": "One action is unresolved.",
                "ticket_id": "ticket-1",
                "ticket_external_id": "ticket-1",
                "human_required_actions": [action],
                "allowed_actions": ["recheck", "reject"],
            }
        )


def test_approval_view_rejects_approve_for_a_recheck_only_requirement():
    action = {
        "action_key": "read-provider-usage",
        "action_label": "Read provider usage",
        "action_description": "",
        "requirement": {"kind": "credential", "credential_key": "claude_profile"},
        "reason_code": "credential_status_unknown",
        "reason": "Credential status unavailable: claude_profile",
        "resolution_mode": "recheck",
    }

    with pytest.raises(ValidationError, match="approve"):
        ApprovalView.model_validate(
            {
                "id": "approval-1",
                "title": "Resolve Verify exit actions",
                "level": "medium",
                "workspace_slug": "loregarden",
                "stage_key": "verify",
                "stage_name": "Verify",
                "impact": "One action is unresolved.",
                "ticket_id": "ticket-1",
                "ticket_external_id": "ticket-1",
                "human_required_actions": [action],
                "allowed_actions": ["approve", "recheck", "reject"],
            }
        )


def test_studio_create_update_view_publish_and_versions_preserve_actions(db_session: Session):
    service = StudioService(db_session)
    created = service.create_workflow(
        StudioWorkflowCreate(slug="exit-actions", name="Exit actions", stages=_stages())
    )
    assert _action_payload(created.stages[0]) == PERSISTED_EXIT_ACTIONS

    updated_actions = [
        *PERSISTED_EXIT_ACTIONS,
        {**EXIT_ACTIONS[3], "key": "accept-launch-risk"},
    ]
    updated = service.update_workflow(
        "exit-actions", StudioWorkflowUpdate(stages=_stages(updated_actions))
    )
    assert _action_payload(updated.stages[0]) == updated_actions

    published = service.publish_workflow("exit-actions")
    assert _action_payload(published.stages[0]) == updated_actions
    version = service.get_workflow_version("exit-actions", 1)
    assert _action_payload(version.snapshot.stages[0]) == updated_actions

    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-exit-actions")
    ).one()
    pinned = get_template_stages_at_version(db_session, template, 1)
    assert _action_payload(pinned[0]) == updated_actions

    service.update_workflow("exit-actions", StudioWorkflowUpdate(stages=_stages([EXIT_ACTIONS[3]])))
    service.publish_workflow("exit-actions")
    restored = service.restore_workflow_version("exit-actions", 1)
    assert _action_payload(restored.stages[0]) == updated_actions


def test_generated_workflow_parser_preserves_typed_exit_actions():
    generated = parse_workflow_generate_payload(
        json.dumps(
            {
                "name": "Generated exit actions",
                "slug": "generated-exit-actions",
                "stages": [
                    {
                        "key": "verify",
                        "name": "Verify",
                        "agent_id": "verifier",
                        "exit_actions_enabled": True,
                        "exit_actions": EXIT_ACTIONS,
                    }
                ],
            }
        ),
        agent_ids=["verifier"],
        skills=[],
    )

    assert generated is not None
    assert generated.stages[0].exit_actions_enabled is True
    assert _action_payload(generated.stages[0]) == EXIT_ACTIONS


def test_approval_view_serializes_only_structured_human_required_actions():
    action = {
        "action_key": "read-provider-usage",
        "action_label": "Read provider usage",
        "action_description": "",
        "requirement": {
            "kind": "credential",
            "credential_key": "claude_profile",
        },
        "reason_code": "credential_status_unknown",
        "reason": "Credential status unavailable: claude_profile",
        "resolution_mode": "recheck",
    }
    approval = ApprovalView.model_validate(
        {
            "id": "approval-1",
            "title": "Resolve Verify exit actions",
            "level": "medium",
            "workspace_slug": "loregarden",
            "stage_key": "verify",
            "stage_name": "Verify",
            "impact": "One action is unresolved.",
            "ticket_id": "ticket-1",
            "ticket_external_id": "ticket-1",
            "human_required_actions": [action],
            "allowed_actions": ["recheck", "reject"],
        }
    )

    payload = approval.model_dump(mode="json")
    assert payload["human_required_actions"] == [action]
    assert payload["allowed_actions"] == ["recheck", "reject"]
