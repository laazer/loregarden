"""Orchestration callback operations — shared by REST API, MCP, and builtin driver."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_service import record_blocking_issue
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_discovery import looks_like_ticket_uuid
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, select


def _orch_code() -> str:
    return f"orch_{secrets.token_hex(3)}"


class OrchestrationCallbackService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orch = OrchestrationService(session)

    def resolve_ticket(
        self,
        *,
        ticket_id: str | None = None,
        external_id: str | None = None,
        workspace_slug: str | None = None,
    ) -> Ticket:
        if ticket_id:
            ticket = self.session.get(Ticket, ticket_id)
            if ticket:
                return ticket
            if looks_like_ticket_uuid(ticket_id):
                raise ValueError("Ticket not found")
            external_id = external_id or ticket_id

        if external_id:
            if workspace_slug:
                ws = self.session.exec(
                    select(Workspace).where(Workspace.slug == workspace_slug)
                ).first()
                if ws:
                    ticket = self.session.exec(
                        select(Ticket).where(
                            Ticket.workspace_id == ws.id,
                            Ticket.external_id == external_id,
                        )
                    ).first()
                    if ticket:
                        return ticket
            ticket = self.session.exec(
                select(Ticket).where(Ticket.external_id == external_id)
            ).first()
            if ticket:
                return ticket

        raise ValueError("Ticket not found")

    def get_active_orchestration_run(self, ticket_id: str) -> OrchestrationRun | None:
        return self.session.exec(
            select(OrchestrationRun)
            .where(OrchestrationRun.ticket_id == ticket_id)
            .where(OrchestrationRun.status == OrchestrationRunStatus.RUNNING)
            .order_by(OrchestrationRun.created_at.desc())
        ).first()

    def start_orchestration_run(
        self,
        ticket: Ticket,
        *,
        driver,
        profile_slug: str,
        auto_approve: bool = False,
        stop_at_stage_key: str = "",
    ) -> OrchestrationRun:
        active = self.get_active_orchestration_run(ticket.id)
        if active:
            raise ValueError(f"Orchestration already running: {active.run_code}")

        if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
            now = datetime.now(timezone.utc)
            run = OrchestrationRun(
                run_code=_orch_code(),
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                driver=driver,
                profile_slug=profile_slug,
                status=OrchestrationRunStatus.SUCCEEDED,
                current_stage_key=ticket.workflow_stage_key,
                error_message="Nothing to orchestrate",
                started_at=now,
                finished_at=now,
            )
            self.session.add(run)
            self.session.commit()
            self.session.refresh(run)
            return run

        if ticket.state == TicketState.BACKLOG:
            self.orch.start_ticket(ticket)
            self.session.refresh(ticket)

        run = OrchestrationRun(
            run_code=_orch_code(),
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            driver=driver,
            profile_slug=profile_slug,
            status=OrchestrationRunStatus.RUNNING,
            current_stage_key=ticket.workflow_stage_key,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        event_bus.publish(
            self.session,
            EventType.ORCHESTRATION_RUN_STARTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"run_code": run.run_code, "driver": driver.value, "profile": profile_slug},
        )
        return run

    def start_stage(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        stage_key: str,
        agent_id: str = "",
    ) -> Ticket:
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        if stage_key not in parse_stage_map(instance, stages):
            raise ValueError(f"Unknown stage key: {stage_key}")

        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(stage_key) == StageStatus.WONT_DO:
            raise ValueError(f"Stage '{stage_key}' is marked won't do")

        set_stage_status(ticket, instance, stages, stage_key, StageStatus.RUNNING)
        if agent_id:
            ticket.next_agent = agent_id
        ticket.last_updated_by = agent_id or "orchestrator"
        ticket.revision += 1
        orch_run.current_stage_key = stage_key
        self.session.add(ticket)
        self.session.add(instance)
        self.session.add(orch_run)
        self.session.commit()

        event_bus.publish(
            self.session,
            EventType.STAGE_STARTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"stage_key": stage_key, "orchestration_run_id": orch_run.id},
        )
        return ticket

    def complete_stage(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        stage_key: str,
        next_agent: str = "",
        next_stage_key: str = "",
        outcome: str = "pass",
        blocking_issues: str = "",
        advance: bool = True,
    ) -> Ticket:
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        ticket.revision += 1
        ticket.last_updated_by = "orchestrator"

        short_blocking_issues = record_blocking_issue(
            self.session,
            ticket,
            run_id=orch_run.id,
            stage_key=stage_key,
            message=blocking_issues,
        )

        if advance:
            transitions = self.orch._resolve_transitions(ticket)
            apply_stage_route(
                ticket,
                instance,
                stages,
                transitions,
                from_key=stage_key,
                outcome=outcome,
                next_stage_key=next_stage_key,
                next_agent=next_agent,
                blocking_issues=short_blocking_issues,
                orch_run=orch_run,
                # Live call: the ValueError reaches the agent as a tool error.
                strict=True,
            )
        else:
            set_stage_status(ticket, instance, stages, stage_key, StageStatus.DONE)
            # See workflow_routing.apply_stage_route: next_agent is only a
            # trusted override on rework (reject), not a normal-pass hint.
            if next_agent and outcome == "reject":
                ticket.next_agent = next_agent
                ticket.next_status = "Proceed"
            ticket.blocking_issues = short_blocking_issues

        self.session.add(ticket)
        self.session.add(instance)
        self.session.add(orch_run)
        self.session.commit()

        event_bus.publish(
            self.session,
            EventType.STAGE_COMPLETED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={
                "stage_key": stage_key,
                "orchestration_run_id": orch_run.id,
                "outcome": outcome,
                "workflow_stage_key": ticket.workflow_stage_key,
            },
        )
        self.orch.reconcile_ticket(ticket)
        self.session.refresh(ticket)
        return ticket

    def skip_stage(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        stage_key: str,
        reason: str = "",
    ) -> Ticket:
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")
        set_stage_status(ticket, instance, stages, stage_key, StageStatus.WONT_DO)
        if reason:
            ticket.blocking_issues = reason[:2000]
        ticket.revision += 1
        orch_run.current_stage_key = stage_key
        self.session.add(ticket)
        self.session.add(instance)
        self.session.add(orch_run)
        self.session.commit()
        self.orch.reconcile_ticket(ticket)
        self.session.refresh(ticket)
        return ticket

    def block_ticket(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        stage_key: str = "",
        message: str,
    ) -> Ticket:
        instance, stages = self.orch._resolve_stages(ticket)
        key = stage_key or ticket.workflow_stage_key
        if instance and stages and key:
            set_stage_status(ticket, instance, stages, key, StageStatus.BLOCKED)
            self.session.add(instance)
        ticket.state = TicketState.BLOCKED
        ticket.blocking_issues = record_blocking_issue(
            self.session,
            ticket,
            run_id=orch_run.id,
            stage_key=key or "",
            message=message,
        )
        ticket.next_status = "Blocked"
        ticket.revision += 1
        ticket.last_updated_by = "orchestrator"
        orch_run.status = OrchestrationRunStatus.BLOCKED
        orch_run.error_message = message[:2000]
        orch_run.finished_at = datetime.now(timezone.utc)
        if key:
            orch_run.current_stage_key = key
        self.session.add(ticket)
        self.session.add(orch_run)
        self.session.commit()
        return ticket

    def attach_artifact(
        self,
        ticket: Ticket,
        *,
        kind: str,
        title: str,
        content: dict,
        run_id: str | None = None,
    ) -> Artifact:
        artifact = Artifact(
            ticket_id=ticket.id,
            run_id=run_id,
            kind=kind,
            title=title,
            content_json=json.dumps(content),
        )
        self.session.add(artifact)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.ARTIFACT_CREATED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            run_id=run_id,
            artifact_id=artifact.id,
            payload={"kind": kind},
        )
        return artifact

    def request_approval(
        self,
        ticket: Ticket,
        *,
        stage_key: str,
        title: str = "",
        impact: str = "",
        level: str = "medium",
    ) -> Approval:
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.WORKFLOW_GATE,
            title=title or f"Approve {ticket.title}",
            level=level,
            stage_key=stage_key,
            impact=impact or f"Stage '{stage_key}' requires human sign-off.",
            status=ApprovalStatus.PENDING,
        )
        instance, stages = self.orch._resolve_stages(ticket)
        if instance and stages:
            set_stage_status(ticket, instance, stages, stage_key, StageStatus.AWAITING)
            self.session.add(instance)
            self.session.add(ticket)
        self.session.add(approval)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.APPROVAL_REQUESTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"approval_id": approval.id, "stage_key": stage_key},
        )
        return approval

    def complete_orchestration(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        status: OrchestrationRunStatus,
        message: str = "",
    ) -> OrchestrationRun:
        orch_run.status = status
        orch_run.error_message = message[:2000]
        orch_run.finished_at = datetime.now(timezone.utc)
        if status == OrchestrationRunStatus.SUCCEEDED and ticket.state not in (
            TicketState.DONE,
            TicketState.WONT_DO,
        ):
            instance, stages = self.orch._resolve_stages(ticket)
            if instance and stages:
                self.orch.reconcile_ticket(ticket)
                self.session.refresh(ticket)
        self.session.add(orch_run)
        self.session.add(ticket)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.ORCHESTRATION_RUN_COMPLETED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"run_code": orch_run.run_code, "status": status.value},
        )
        return orch_run
