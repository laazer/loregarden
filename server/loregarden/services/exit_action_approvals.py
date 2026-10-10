"""Exit-action gate resolution helpers for ApprovalService.

Three things a person can do to a workflow gate, and what each may complete:

- **approve** a gate whose actions are all approvable. Operator judgment is
  satisfied by the click itself. Granting authority is not: it clears the
  requirement, and the stage has to run the action.
- **recheck** — observe the runtime again. What became executable is handed
  back to the stage.
- **reject** — the ordinary path, unchanged here.

Clearing a requirement (a successful recheck, or a grant) closes the gate,
records the decision on it (`exit_action_ledger`), moves the stage out of
AWAITING and schedules a continuation. It never leaves an empty PENDING gate,
and it never marks the action done: only the continuation's passing report
attests it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalResolutionAction,
    ApprovalStatus,
    CliAdapter,
    EventType,
    ExitActionContinuation,
    ExitActionRequirementKind,
    ExitActionResolution,
    ExitActionResolutionMode,
    HumanRequiredExitAction,
    OrchestrationDriver,
    RuntimeExitActionSnapshot,
    StageStatus,
    Ticket,
    WorkflowExitAction,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.exit_action_dispatch import driver_for_run, observe_runtime_snapshot
from loregarden.services.exit_action_ledger import (
    read_gate_ledger,
    read_gate_resolution,
    stored_run_snapshot,
    write_gate_ledger,
)
from loregarden.services.exit_actions import allowed_resolution_actions, resolve_exit_actions
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, col, select


def _require_approve_allowed(resolution: ExitActionResolution) -> None:
    """Approve is accepted only when the gate's own actions permit it.

    ``allowed_actions`` has been recomputed from ``human_required_actions`` by
    `read_gate_resolution`; an empty gate permits nothing, because approving it
    would attest work nobody named.
    """
    if not resolution.human_required_actions:
        raise ValueError(
            "This gate records no unresolved exit actions, so approving it would attest "
            "nothing. Reject it, or re-run the stage to raise a gate from its exit actions."
        )
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


def _authority_actions(resolution: ExitActionResolution) -> list[HumanRequiredExitAction]:
    return [
        action
        for action in resolution.human_required_actions
        if action.requirement.kind == ExitActionRequirementKind.AUTHORITY
        and action.resolution_mode == ExitActionResolutionMode.APPROVE
    ]


class ExitActionApprovalMixin:
    """Mixin: workflow-gate exit-action recheck / grant / approve guards."""

    session: Session

    def _resume_orchestration(self, ticket: Ticket, *, carry_driver: bool = False) -> None:
        """Provided by `ApprovalService`: resume with the parent run's flags."""
        raise NotImplementedError

    def _reject_unsupported_approve(self, approval: Approval) -> None:
        """Server-side enforcement that approve cannot stand in for execution.

        Reached by every ``resolve(approved=True)``. A gate holding authority is
        refused here too: granting it must schedule the stage to run the action
        (`approve_exit_action_gate`), and a plain approve would mark it done.
        """
        if approval.kind != ApprovalKind.WORKFLOW_GATE:
            return
        resolution = read_gate_resolution(approval)
        _require_approve_allowed(resolution)
        if _authority_actions(resolution):
            raise ValueError(
                "This gate asks for an authority grant. Granting it schedules the stage "
                "to run the action rather than completing it — approve it from the inbox."
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

    def _rewrite_gate(self, approval: Approval, remaining: list[HumanRequiredExitAction]) -> None:
        """Refresh a gate that stays pending with the reasons a recheck just observed."""
        approval.tool_input_json = ExitActionResolution(
            human_required_actions=remaining,
            allowed_actions=allowed_resolution_actions(remaining),
        ).model_dump_json()
        self.session.add(approval)
        self.session.commit()

    def _continuable_ticket(self, approval: Approval) -> Ticket:
        """The gate's ticket, refusing before anything changes if it cannot continue."""
        ticket = self.session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
        if ticket is None:
            raise ValueError(f"Gate {approval.id} has no ticket to continue")
        if workflow_instance_for(self.session, ticket.id) is None:
            raise ValueError(f"Ticket {ticket.external_id} has no workflow to continue")
        return ticket

    def _reopen_stage_for_continuation(self, ticket: Ticket, stage_key: str) -> None:
        """Move the gated stage out of AWAITING so the continuation dispatches it."""
        instance = workflow_instance_for(self.session, ticket.id)
        _, stages = resolve_ticket_stages(self.session, ticket)
        if instance is None or parse_stage_map(instance, stages).get(stage_key) != (
            StageStatus.AWAITING
        ):
            return
        set_stage_status(ticket, instance, stages, stage_key, StageStatus.PENDING)
        ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

    def _clear_gate(
        self,
        approval: Approval,
        *,
        newly_executable: list[str],
        granted_scopes: list[str] | None = None,
        approved_judgments: list[str] | None = None,
    ) -> ExitActionContinuation:
        """Close the gate on a cleared requirement and hand the stage its actions back."""
        ticket = self._continuable_ticket(approval)
        ledger = read_gate_ledger(approval)
        ledger.granted_authority_scopes = list(
            dict.fromkeys([*ledger.granted_authority_scopes, *(granted_scopes or [])])
        )
        ledger.approved_judgment_keys = list(
            dict.fromkeys([*ledger.approved_judgment_keys, *(approved_judgments or [])])
        )
        ledger.continuation_action_keys = list(newly_executable)
        write_gate_ledger(approval, ledger)
        approval.status = ApprovalStatus.APPROVED
        approval.resolved_at = datetime.now(timezone.utc)
        self.session.add(approval)
        self.session.commit()

        self._reopen_stage_for_continuation(ticket, approval.stage_key)
        event_bus.publish(
            self.session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=approval.workspace_id,
            ticket_id=approval.ticket_id,
            payload={
                "approval_id": approval.id,
                "approved": True,
                "continuation_action_keys": ledger.continuation_action_keys,
            },
        )
        self._resume_orchestration(ticket, carry_driver=True)
        return ExitActionContinuation(
            approval_id=approval.id,
            newly_assigned_action_keys=ledger.continuation_action_keys,
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

    @staticmethod
    def _adapter_for_gate(approval: Approval, run: AgentRun | None) -> CliAdapter:
        """The adapter the gated run used; LOCAL (which executes nothing) when unknown."""
        raw = approval.cli_adapter
        if not raw and run is not None:
            raw = str((stored_run_snapshot(run) or {}).get("adapter") or "")
        try:
            return CliAdapter(raw)
        except (
            ValueError
        ):  # silent-ok: unknown or absent adapter fails closed as LOCAL, which executes nothing
            return CliAdapter.LOCAL

    def _fresh_runtime_snapshot(self, approval: Approval) -> RuntimeExitActionSnapshot:
        """Probe the live environment — never trust a client-supplied snapshot."""
        run = self._gate_run(approval)
        ticket = self.session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
        return observe_runtime_snapshot(
            self.session,
            run_id=run.id if run is not None else approval.id,
            agent_id=(run.agent_id if run is not None else (ticket.next_agent if ticket else ""))
            or "",
            agent_version=run.agent_version if run is not None else None,
            driver=(
                driver_for_run(self.session, run)
                if run is not None
                else OrchestrationDriver.MANUAL_STAGE
            ),
            adapter=self._adapter_for_gate(approval, run),
            workspace=self.session.get(Workspace, approval.workspace_id),
            ticket_id=approval.ticket_id,
            stage_key=approval.stage_key,
        )

    def recheck(self, approval_id: str) -> ExitActionContinuation:
        """Re-evaluate a gate against a server-probed runtime snapshot.

        Nothing cleared: the gate stays pending with refreshed reasons. Anything
        cleared: the gate closes and the stage continues with those actions;
        whatever is still unresolved is raised again when that run completes.
        """
        approval = self._pending_gate(approval_id)
        resolution = read_gate_resolution(approval)
        if not resolution.human_required_actions:
            raise ValueError(
                "This gate records no exit actions to recheck. Reject it, or re-run the "
                "stage to raise a gate from its exit actions."
            )
        stage = WorkflowStageDef(
            key=approval.stage_key or "exit",
            name=approval.stage_key or "exit",
            exit_actions_enabled=True,
            exit_actions=[
                WorkflowExitAction(
                    key=action.action_key,
                    label=action.action_label,
                    description=action.action_description,
                    requirement=action.requirement,
                )
                for action in resolution.human_required_actions
            ],
        )
        rechecked = resolve_exit_actions(stage, self._fresh_runtime_snapshot(approval))
        if not rechecked.assigned_actions:
            self._rewrite_gate(approval, rechecked.human_required_actions)
            return ExitActionContinuation(approval_id=approval.id)
        return self._clear_gate(approval, newly_executable=rechecked.assigned_action_keys)

    def grant_authority(self, approval_id: str, *, authority_scope: str) -> ExitActionContinuation:
        """Grant one policy scope, which lets its actions run — nothing more."""
        approval = self._pending_gate(approval_id)
        resolution = read_gate_resolution(approval)
        _require_approve_allowed(resolution)
        granted = [
            action
            for action in _authority_actions(resolution)
            if action.requirement.authority_scope == authority_scope
        ]
        if not granted:
            raise ValueError(f"This gate has no grantable action requiring: {authority_scope}")
        return self._clear_gate(
            approval,
            newly_executable=[action.action_key for action in granted],
            granted_scopes=[authority_scope],
        )

    def approve_exit_action_gate(self, approval_id: str) -> ExitActionContinuation | None:
        """Approve a workflow gate that grants authority rather than completing the stage.

        Returns a continuation when grantable authority was present — the caller
        must not fall through to resolve()/stage-DONE. Returns None when the gate
        is pure operator judgment (or not a workflow gate), so the normal approve
        path runs. Raises when approve is not permitted on this gate.
        """
        approval = self.session.get(Approval, approval_id)
        if approval is None or approval.kind != ApprovalKind.WORKFLOW_GATE:
            return None
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("Approval already resolved")
        resolution = read_gate_resolution(approval)
        _require_approve_allowed(resolution)
        grantable = _authority_actions(resolution)
        if not grantable:
            return None
        return self._clear_gate(
            approval,
            newly_executable=[action.action_key for action in grantable],
            granted_scopes=list(
                dict.fromkeys(action.requirement.authority_scope for action in grantable)
            ),
            # The same click answers the operator judgment on this card.
            approved_judgments=[
                action.action_key
                for action in resolution.human_required_actions
                if action.requirement.kind == ExitActionRequirementKind.OPERATOR_JUDGMENT
            ],
        )
