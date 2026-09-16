"""Exit-action schema models — split from schemas.py for the size gate."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from loregarden.models.domain.enums import CliAdapter, OrchestrationDriver
from loregarden.models.domain.enums_exit_actions import (
    ApprovalResolutionAction,
    AuthorityStatus,
    ExitActionReasonCode,
    ExitActionRequirementKind,
    ExitActionResolutionMode,
    RuntimeAvailability,
)
from pydantic import ConfigDict, Discriminator, field_validator, model_serializer, model_validator
from sqlmodel import Field, SQLModel


class _ExitActionRequirement(SQLModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def reject_blank_identifier(cls, value: str, field_name: str) -> str:
        if not value.strip():
            raise ValueError(f"{field_name} must not be blank")
        return value


class RuntimeCapabilityRequirement(_ExitActionRequirement):
    kind: Literal[ExitActionRequirementKind.RUNTIME_CAPABILITY]
    capability_id: str

    @field_validator("capability_id")
    @classmethod
    def _validate_capability_id(cls, value: str) -> str:
        return cls.reject_blank_identifier(value, "capability_id")


class CredentialRequirement(_ExitActionRequirement):
    kind: Literal[ExitActionRequirementKind.CREDENTIAL]
    credential_key: str

    @field_validator("credential_key")
    @classmethod
    def _validate_credential_key(cls, value: str) -> str:
        return cls.reject_blank_identifier(value, "credential_key")


class AuthorityRequirement(_ExitActionRequirement):
    kind: Literal[ExitActionRequirementKind.AUTHORITY]
    authority_scope: str

    @field_validator("authority_scope")
    @classmethod
    def _validate_authority_scope(cls, value: str) -> str:
        return cls.reject_blank_identifier(value, "authority_scope")


class OperatorJudgmentRequirement(_ExitActionRequirement):
    kind: Literal[ExitActionRequirementKind.OPERATOR_JUDGMENT]
    decision_prompt: str

    @field_validator("decision_prompt")
    @classmethod
    def _validate_decision_prompt(cls, value: str) -> str:
        return cls.reject_blank_identifier(value, "decision_prompt")


ExitActionRequirement = Annotated[
    RuntimeCapabilityRequirement
    | CredentialRequirement
    | AuthorityRequirement
    | OperatorJudgmentRequirement,
    Discriminator("kind"),
]


class WorkflowExitAction(SQLModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    description: str = ""
    requirement: ExitActionRequirement

    @model_serializer(mode="wrap")
    def _serialize_optional_description(self, handler):
        payload = handler(self)
        if "description" not in self.model_fields_set:
            payload.pop("description", None)
        return payload

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):  # py-org: allow-isinstance
            return self.model_dump(mode="json") == other
        return super().__eq__(other)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("action key must be a lowercase kebab-case identifier")
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("action label must not be blank")
        return value


class ExitActionsStage(SQLModel):
    exit_actions_enabled: bool = False
    exit_actions: list[WorkflowExitAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_exit_actions(self):
        if self.exit_actions and not self.exit_actions_enabled:
            raise ValueError("exit_actions_enabled must be true when exit_actions are present")
        keys = [action.key for action in self.exit_actions]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate exit action key")
        return self


class HumanRequiredExitAction(SQLModel):
    action_key: str
    action_label: str
    action_description: str = ""
    requirement: ExitActionRequirement
    reason_code: ExitActionReasonCode
    reason: str
    resolution_mode: ExitActionResolutionMode


class RuntimeExitActionSnapshot(SQLModel):
    """What the control plane observed about a run's runtime before dispatch.

    Deliberately secret-free: it records *statuses*, never the credential
    material a preflight read. A missing key is unknown, which the resolver
    fails closed on — so an adapter that reports nothing can never make an
    action look executable.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    agent_version: int | None = None
    adapter: CliAdapter | str = ""
    driver: OrchestrationDriver | str = ""
    # External harnesses report capabilities out of band; a report that is no
    # longer current is unknown rather than still-true.
    capability_data_fresh: bool = True
    capabilities: dict[str, RuntimeAvailability] = Field(default_factory=dict)
    credentials: dict[str, RuntimeAvailability] = Field(default_factory=dict)
    authority: dict[str, AuthorityStatus] = Field(default_factory=dict)


class AssignedExitAction(SQLModel):
    """An authored action the dispatched agent is able to execute itself."""

    action_key: str
    action_label: str
    action_description: str = ""
    requirement: ExitActionRequirement


class ExitActionResolution(SQLModel):
    """How one stage's authored exit actions split against a runtime snapshot."""

    assigned_actions: list[AssignedExitAction] = Field(default_factory=list)
    human_required_actions: list[HumanRequiredExitAction] = Field(default_factory=list)
    allowed_actions: list[ApprovalResolutionAction] = Field(default_factory=list)

    @property
    def assigned_action_keys(self) -> list[str]:
        return [action.action_key for action in self.assigned_actions]

    @model_serializer(mode="wrap")
    def _serialize_with_assigned_keys(self, handler):
        payload = handler(self)
        payload["assigned_action_keys"] = self.assigned_action_keys
        return payload


class ExitActionContinuation(SQLModel):
    """Result of clearing a requirement on a pending workflow gate.

    ``completed_action_keys`` is always empty: clearing a requirement only lets
    the responsible stage run the action, and nothing but a passing continuation
    report may attest that it happened.
    """

    approval_id: str
    newly_assigned_action_keys: list[str] = Field(default_factory=list)
    completed_action_keys: list[str] = Field(default_factory=list)


class ExitActionRequirementCatalog(SQLModel):
    """Server-owned vocabularies Studio authors exit actions against."""

    requirement_kinds: list[ExitActionRequirementKind] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    credential_keys: list[str] = Field(default_factory=list)
    authority_scopes: list[str] = Field(default_factory=list)
