"""Exit-action gate resolution helpers for ApprovalService."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalResolutionAction,
    ApprovalStatus,
    CliAdapter,
    ExitActionContinuation,
    ExitActionRequirementKind,
    ExitActionResolution,
    ExitActionResolutionMode,
    OrchestrationDriver,
    RuntimeExitActionSnapshot,
    Ticket,
    WorkflowExitAction,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.exit_action_dispatch import driver_for_run
from loregarden.services.exit_action_probe import (
    probe_authority,
    probe_capabilities,
    probe_credentials,
)
from loregarden.services.exit_actions import (
    allowed_resolution_actions,
    capture_runtime_snapshot,
    resolve_exit_actions,
)
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.scheduling import schedule_orchestration
from sqlmodel import col, select


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
        if newly_assigned and approval.ticket_id:
            schedule_orchestration(
                approval.ticket_id,
                stop_at_stage_key=approval.stage_key or None,
                assigned_exit_action_keys=newly_assigned,
            )
        return ExitActionContinuation(
            approval_id=approval.id,
            newly_assigned_action_keys=newly_assigned,
            completed_action_keys=[],
        )

    def _gate_run(self, approval: Approval) -> AgentRun | None:
        """The run whose runtime the gate was opened against, when one exists."""
        if approval.run_id:
            run = self.session.get(AgentRun, approval.run_id)
            if run is not None:
                return run
        if not approval.ticket_id:
            return None
        query = select(AgentRun).where(AgentRun.ticket_id == approval.ticket_id)
        if approval.stage_key:
            query = query.where(AgentRun.stage_key == approval.stage_key)
        return self.session.exec(query.order_by(col(AgentRun.created_at).desc())).first()

    def _adapter_for_gate(self, approval: Approval, run: AgentRun | None) -> CliAdapter:
        raw = (approval.cli_adapter or "").strip()
        if not raw and run and run.runtime_exit_action_snapshot_json:
            try:
                prior = json.loads(run.runtime_exit_action_snapshot_json)
            except (
                json.JSONDecodeError
            ):  # silent-ok: corrupt prior snapshot; fall through to default
                prior = {}
            raw = str(prior.get("adapter") or "").strip()
        if not raw:
            return CliAdapter.LOCAL
        try:
            return CliAdapter(raw)
        except ValueError:  # silent-ok: unknown adapter string; fail closed as non-executing
            return CliAdapter.LOCAL

    def _fresh_runtime_snapshot(self, approval: Approval) -> RuntimeExitActionSnapshot:
        """Probe the live environment — never trust a client-supplied snapshot."""
        run = self._gate_run(approval)
        workspace = self.session.get(Workspace, approval.workspace_id)
        adapter = self._adapter_for_gate(approval, run)
        profile = resolve_orchestration_profile(workspace) if workspace is not None else None
        capabilities = probe_capabilities(adapter=adapter)
        credentials = probe_credentials()
        authority = probe_authority(profile=profile)
        if run is not None:
            snapshot = capture_runtime_snapshot(
                run=run,
                driver=driver_for_run(self.session, run),
                adapter=adapter,
                capability_statuses=capabilities,
                credential_preflight=credentials,
                authority_statuses=authority,
            )
            self.session.add(run)
            self.session.commit()
            return snapshot
        ticket = self.session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
        return RuntimeExitActionSnapshot(
            run_id=approval.id,
            agent_id=(ticket.next_agent if ticket is not None else "") or "",
            adapter=adapter,
            driver=OrchestrationDriver.MANUAL_STAGE,
            capabilities=capabilities,
            credentials=credentials,
            authority=authority,
        )

    def recheck(self, approval_id: str) -> ExitActionContinuation:
        """Re-evaluate a gate against a server-probed runtime snapshot."""
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
        snapshot = self._fresh_runtime_snapshot(approval)
        rechecked = resolve_exit_actions(stage, snapshot)
        self._rewrite_gate(approval, rechecked.human_required_actions)
        return self._schedule_exit_action_continuation(approval, rechecked.assigned_action_keys)

    def grant_authority(self, approval_id: str, *, authority_scope: str) -> ExitActionContinuation:
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

    def approve_exit_action_gate(self, approval_id: str) -> ExitActionContinuation | None:
        """Approve a workflow gate that grants authority rather than completing the stage.

        Returns a continuation when grantable authority was present — the caller must
        not fall through to resolve()/stage-DONE. Returns None when the gate is pure
        operator judgment (or not a workflow gate), so the normal approve path runs.
        """
        approval = self.session.get(Approval, approval_id)
        if approval is None or approval.kind != ApprovalKind.WORKFLOW_GATE:
            return None
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("Approval already resolved")
        self._reject_unsupported_approve(approval)
        resolution = self._gate_resolution(approval)
        grantable = [
            action
            for action in resolution.human_required_actions
            if action.requirement.kind == ExitActionRequirementKind.AUTHORITY
            and action.resolution_mode == ExitActionResolutionMode.APPROVE
        ]
        if not grantable:
            return None

        granted_keys = [action.action_key for action in grantable]
        # Operator judgment on the same card is satisfied by this approve click;
        # everything else that was approve-eligible is authority we just granted.
        remaining = [
            action
            for action in resolution.human_required_actions
            if action.action_key not in granted_keys
            and action.requirement.kind != ExitActionRequirementKind.OPERATOR_JUDGMENT
        ]
        self._rewrite_gate(approval, remaining)
        if not remaining:
            approval.status = ApprovalStatus.APPROVED
            approval.resolved_at = datetime.now(timezone.utc)
            self.session.add(approval)
            self.session.commit()
        return self._schedule_exit_action_continuation(approval, granted_keys)
