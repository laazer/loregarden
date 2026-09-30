"""Runtime evaluation of workflow stage exit actions.

A stage author says *what has to happen* before the stage may be left; this
module answers *who can do it on this run*. Everything the responsible runtime
can execute is assigned to the agent, and only the remainder reaches a person.

The evaluation is fail-closed in both directions that matter: a requirement
whose status was never observed is unknown rather than satisfied, and clearing
a requirement never marks its action done — only a passing stage report does.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    AgentRun,
    ApprovalResolutionAction,
    AssignedExitAction,
    AuthorityStatus,
    CliAdapter,
    ExitActionReasonCode,
    ExitActionRequirementCatalog,
    ExitActionRequirementKind,
    ExitActionResolution,
    ExitActionResolutionMode,
    HumanRequiredExitAction,
    OrchestrationDriver,
    RuntimeAvailability,
    RuntimeExitActionSnapshot,
    StageReportStatus,
    WorkflowExitAction,
    WorkflowStageDef,
)

# The server owns these vocabularies so Studio picks identifiers rather than
# inventing them, and so an action can never name a requirement nothing here
# knows how to evaluate. Operator judgment has no catalog: its identifier is
# the decision prompt, which is authored prose by definition.
CAPABILITY_IDS: tuple[str, ...] = (
    "http_test_client",
    "git_push",
    "github_pull_request",
    "shell_command",
    "workspace_file_write",
)
CREDENTIAL_KEYS: tuple[str, ...] = (
    "claude_profile",
    "cursor_profile",
    "codex_profile",
    "github_token",
)
AUTHORITY_SCOPES: tuple[str, ...] = (
    "release:publish",
    "repo:push",
    "workspace:destructive_write",
    "ticket:supersede",
)

_CATALOG_BY_KIND: dict[ExitActionRequirementKind, tuple[str, ...]] = {
    ExitActionRequirementKind.RUNTIME_CAPABILITY: CAPABILITY_IDS,
    ExitActionRequirementKind.CREDENTIAL: CREDENTIAL_KEYS,
    ExitActionRequirementKind.AUTHORITY: AUTHORITY_SCOPES,
}


def requirement_catalog() -> ExitActionRequirementCatalog:
    """The typed vocabularies Studio authors exit actions against."""
    return ExitActionRequirementCatalog(
        requirement_kinds=list(ExitActionRequirementKind),
        capability_ids=list(CAPABILITY_IDS),
        credential_keys=list(CREDENTIAL_KEYS),
        authority_scopes=list(AUTHORITY_SCOPES),
    )


def requirement_identifier(action: WorkflowExitAction) -> str:
    """The single identifier (or prompt) this action's requirement is keyed by."""
    requirement = action.requirement
    if requirement.kind == ExitActionRequirementKind.RUNTIME_CAPABILITY:
        return requirement.capability_id
    if requirement.kind == ExitActionRequirementKind.CREDENTIAL:
        return requirement.credential_key
    if requirement.kind == ExitActionRequirementKind.AUTHORITY:
        return requirement.authority_scope
    return requirement.decision_prompt


def validate_against_catalog(actions: list[WorkflowExitAction]) -> None:
    """Reject well-shaped identifiers the server does not own.

    Shape validation lives on the models; this is the second half of AC-2 —
    a capability nothing can evaluate is not an authorable requirement.
    """
    for action in actions:
        catalog = _CATALOG_BY_KIND.get(action.requirement.kind)
        if catalog is None:
            continue
        identifier = requirement_identifier(action)
        if identifier not in catalog:
            raise ValueError(
                f"Exit action '{action.key}' names an unknown "
                f"{action.requirement.kind.value} identifier: {identifier}"
            )


def capture_runtime_snapshot(
    *,
    run: AgentRun,
    driver: OrchestrationDriver,
    adapter: CliAdapter,
    capability_statuses: dict[str, RuntimeAvailability] | None = None,
    credential_preflight: dict[str, RuntimeAvailability] | None = None,
    authority_statuses: dict[str, AuthorityStatus] | None = None,
    capability_data_fresh: bool = True,
) -> RuntimeExitActionSnapshot:
    """Record what this run's runtime can do, and persist it on the run.

    Every driver goes through here, so a built-in, manual, and external-MCP run
    are evaluated from the same shape of evidence. Only statuses are stored —
    never the credential material a preflight inspected.
    """
    snapshot = RuntimeExitActionSnapshot(
        run_id=run.id,
        agent_id=run.agent_id,
        agent_version=run.agent_version,
        adapter=adapter,
        driver=driver,
        capability_data_fresh=capability_data_fresh,
        capabilities=capability_statuses or {},
        credentials=credential_preflight or {},
        authority=authority_statuses or {},
    )
    run.runtime_exit_action_snapshot_json = json.dumps(snapshot.model_dump(mode="json"))
    return snapshot


def _unmet_capability(
    identifier: str, snapshot: RuntimeExitActionSnapshot
) -> tuple[ExitActionReasonCode, str, ExitActionResolutionMode] | None:
    # Stale external data is not weaker evidence, it is no evidence: a harness
    # that reported "available" an hour ago says nothing about this dispatch.
    status = snapshot.capabilities.get(identifier) if snapshot.capability_data_fresh else None
    if status == RuntimeAvailability.AVAILABLE:
        return None
    if status == RuntimeAvailability.UNAVAILABLE:
        return (
            ExitActionReasonCode.CAPABILITY_UNAVAILABLE,
            f"Capability unavailable on this runtime: {identifier}",
            ExitActionResolutionMode.RECHECK,
        )
    return (
        ExitActionReasonCode.CAPABILITY_STATUS_UNKNOWN,
        f"Capability status unavailable: {identifier}",
        ExitActionResolutionMode.RECHECK,
    )


def _unmet_credential(
    identifier: str, snapshot: RuntimeExitActionSnapshot
) -> tuple[ExitActionReasonCode, str, ExitActionResolutionMode] | None:
    status = snapshot.credentials.get(identifier)
    if status == RuntimeAvailability.AVAILABLE:
        return None
    if status == RuntimeAvailability.UNAVAILABLE:
        return (
            ExitActionReasonCode.CREDENTIAL_UNAVAILABLE,
            f"Credential unavailable: {identifier}",
            ExitActionResolutionMode.RECHECK,
        )
    return (
        ExitActionReasonCode.CREDENTIAL_STATUS_UNKNOWN,
        f"Credential status unavailable: {identifier}",
        ExitActionResolutionMode.RECHECK,
    )


def _unmet_authority(
    identifier: str, snapshot: RuntimeExitActionSnapshot
) -> tuple[ExitActionReasonCode, str, ExitActionResolutionMode] | None:
    status = snapshot.authority.get(identifier)
    if status == AuthorityStatus.GRANTED:
        return None
    if status == AuthorityStatus.GRANTABLE:
        # The only human-required runtime state approval genuinely resolves:
        # policy permits the grant and only a person may make it.
        return (
            ExitActionReasonCode.AUTHORITY_GRANT_REQUIRED,
            f"Authority grant required: {identifier}",
            ExitActionResolutionMode.APPROVE,
        )
    if status == AuthorityStatus.DENIED:
        return (
            ExitActionReasonCode.AUTHORITY_DENIED,
            f"Authority denied by policy: {identifier}",
            ExitActionResolutionMode.RECHECK,
        )
    return (
        ExitActionReasonCode.AUTHORITY_STATUS_UNKNOWN,
        f"Authority status unavailable: {identifier}",
        ExitActionResolutionMode.RECHECK,
    )


def _unmet_requirement(
    action: WorkflowExitAction, snapshot: RuntimeExitActionSnapshot
) -> tuple[ExitActionReasonCode, str, ExitActionResolutionMode] | None:
    """Why a person is needed for this action, or None when the agent can run it."""
    kind = action.requirement.kind
    identifier = requirement_identifier(action)
    if kind == ExitActionRequirementKind.RUNTIME_CAPABILITY:
        return _unmet_capability(identifier, snapshot)
    if kind == ExitActionRequirementKind.CREDENTIAL:
        return _unmet_credential(identifier, snapshot)
    if kind == ExitActionRequirementKind.AUTHORITY:
        return _unmet_authority(identifier, snapshot)
    # Operator judgment is never executable by a runtime: the decision is the
    # requirement, and its prompt is the reason a person is being asked.
    return (
        ExitActionReasonCode.OPERATOR_JUDGMENT_REQUIRED,
        action.requirement.decision_prompt,
        ExitActionResolutionMode.APPROVE,
    )


def allowed_resolution_actions(
    actions: list[HumanRequiredExitAction],
) -> list[ApprovalResolutionAction]:
    """What the server will accept for a gate holding ``actions``.

    Approve is withheld while any action needs a recheck: approving cannot
    supply a missing credential or capability, and offering it would let an
    operator wave through work that never ran.
    """
    if not actions:
        return []
    if any(action.resolution_mode == ExitActionResolutionMode.RECHECK for action in actions):
        return [ApprovalResolutionAction.RECHECK, ApprovalResolutionAction.REJECT]
    return [ApprovalResolutionAction.APPROVE, ApprovalResolutionAction.REJECT]


def resolve_exit_actions(
    stage: WorkflowStageDef | None,
    snapshot: RuntimeExitActionSnapshot | dict | None,
) -> ExitActionResolution:
    """Split a stage's authored exit actions against an observed runtime."""
    if stage is None or not stage.exit_actions_enabled or not stage.exit_actions:
        return ExitActionResolution()
    resolved_snapshot = (
        RuntimeExitActionSnapshot(run_id="", agent_id="")
        if snapshot is None
        else RuntimeExitActionSnapshot.model_validate(snapshot)
    )

    assigned: list[AssignedExitAction] = []
    human_required: list[HumanRequiredExitAction] = []
    for action in stage.exit_actions:
        unmet = _unmet_requirement(action, resolved_snapshot)
        if unmet is None:
            assigned.append(
                AssignedExitAction(
                    action_key=action.key,
                    action_label=action.label,
                    action_description=action.description,
                    requirement=action.requirement,
                )
            )
            continue
        reason_code, reason, mode = unmet
        human_required.append(
            HumanRequiredExitAction(
                action_key=action.key,
                action_label=action.label,
                action_description=action.description,
                requirement=action.requirement,
                reason_code=reason_code,
                reason=reason,
                resolution_mode=mode,
            )
        )
    return ExitActionResolution(
        assigned_actions=assigned,
        human_required_actions=human_required,
        allowed_actions=allowed_resolution_actions(human_required),
    )


def assign_actions_to_run(run: AgentRun, resolution: ExitActionResolution) -> None:
    """Record which actions this dispatch is expected to execute."""
    run.assigned_exit_action_keys_json = json.dumps(resolution.assigned_action_keys)


def attest_assigned_actions(run: AgentRun, *, report_status: StageReportStatus) -> None:
    """Only a passing report attests that assigned actions were carried out.

    A satisfied requirement means the agent *could* act, never that it did — so
    a failed or reworked stage completes nothing, and the assigned/completed
    split stays queryable instead of assumed.
    """
    assigned = json.loads(run.assigned_exit_action_keys_json or "[]")
    completed = assigned if report_status == StageReportStatus.PASS else []
    run.completed_exit_action_keys_json = json.dumps(completed)
