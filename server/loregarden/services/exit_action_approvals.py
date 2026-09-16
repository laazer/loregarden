"""Exit-action gate resolution helpers for ApprovalService."""

from __future__ import annotations

import json

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalResolutionAction,
    ApprovalStatus,
    ExitActionContinuation,
    ExitActionRequirementKind,
    ExitActionResolution,
    ExitActionResolutionMode,
    WorkflowExitAction,
    WorkflowStageDef,
)
from loregarden.services.exit_actions import (
    allowed_resolution_actions,
    resolve_exit_actions,
)


class ExitActionApprovalMixin:
    """Mixin: workflow-gate exit-action recheck / grant / approve guards."""

    def _gate_resolution(self, approval: Approval) -> ExitActionResolution:
        """The typed exit-action payload a workflow gate was opened with."""
        if approval.kind != ApprovalKind.WORKFLOW_GATE:
            return ExitActionResolution()
        try:
            payload = json.loads(approval.tool_input_json or "{}")
        except json.JSONDecodeError:  # silent-ok: malformed gate payload yields empty resolution
            return ExitActionResolution()
        return ExitActionResolution.model_validate(payload)

    def _reject_unsupported_approve(self, approval: Approval) -> None:
        """Server-side enforcement that approve cannot stand in for execution."""
        resolution = self._gate_resolution(approval)
        if not resolution.human_required_actions:
            return
        if ApprovalResolutionAction.APPROVE in resolution.allowed_actions:
            return
        blocking = ", ".join(
            action.action_key
            for action in resolution.human_required_actions
            if action.resolution_mode == ExitActionResolutionMode.RECHECK
        )
        raise ValueError(
            "This gate cannot be approved: it requires a successful recheck of "
            f"{blocking}. Approving would not supply what the action needs."
        )

    def _pending_gate(self, approval_id: str) -> Approval:
        approval = self.session.get(Approval, approval_id)
        if not approval:
            raise ValueError("Approval not found")
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("Approval already resolved")
        if approval.kind != ApprovalKind.WORKFLOW_GATE:
            raise ValueError("Exit-action resolution only applies to workflow-gate approvals")
        return approval

    def _rewrite_gate(self, approval: Approval, remaining) -> None:
        approval.tool_input_json = json.dumps(
            ExitActionResolution(
                human_required_actions=remaining,
                allowed_actions=allowed_resolution_actions(remaining),
            ).model_dump(mode="json")
        )
        self.session.add(approval)
        self.session.commit()

    def _schedule_exit_action_continuation(self, approval: Approval, newly_assigned: list[str]):
        # Lazy import: run_service → cli → permission_bridge → orchestration.
        from loregarden.services import run_service

        if newly_assigned and approval.ticket_id:
            run_service.schedule_orchestration(
                approval.ticket_id,
                stop_at_stage_key=approval.stage_key or None,
                assigned_exit_action_keys=newly_assigned,
            )
        return ExitActionContinuation(
            approval_id=approval.id,
            newly_assigned_action_keys=newly_assigned,
            completed_action_keys=[],
        )

    def recheck(self, approval_id: str, *, runtime_snapshot):
        """Re-evaluate a gate's unresolved actions against a fresh snapshot."""
        approval = self._pending_gate(approval_id)
        resolution = self._gate_resolution(approval)
        actions = [
            WorkflowExitAction(
                key=action.action_key,
                label=action.action_label,
                description=action.action_description,
                requirement=action.requirement,
            )
            for action in resolution.human_required_actions
        ]
        stage = WorkflowStageDef(
            key=approval.stage_key or "exit",
            name=approval.stage_key or "exit",
            exit_actions_enabled=True,
            exit_actions=actions,
        )
        rechecked = resolve_exit_actions(stage, runtime_snapshot)
        self._rewrite_gate(approval, rechecked.human_required_actions)
        return self._schedule_exit_action_continuation(approval, rechecked.assigned_action_keys)

    def grant_authority(self, approval_id: str, *, authority_scope: str):
        """Grant one policy scope, which lets its actions run — nothing more."""
        approval = self._pending_gate(approval_id)
        resolution = self._gate_resolution(approval)
        granted = [
            action
            for action in resolution.human_required_actions
            if action.requirement.kind == ExitActionRequirementKind.AUTHORITY
            and action.requirement.authority_scope == authority_scope
        ]
        if not granted:
            raise ValueError(f"This gate has no action requiring authority: {authority_scope}")
        granted_keys = [action.action_key for action in granted]
        self._rewrite_gate(
            approval,
            [
                action
                for action in resolution.human_required_actions
                if action.action_key not in granted_keys
            ],
        )
        return self._schedule_exit_action_continuation(approval, granted_keys)
