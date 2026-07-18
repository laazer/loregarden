import json
import secrets
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_loader import expand_gate_checklist, stage_display_name
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    EventType,
    RunStatus,
    StageStatus,
    StudioAgent,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowStageView,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.artifact_service import record_blocking_issue
from loregarden.services.compatibility_posture import apply_compatibility_posture
from loregarden.services.run_log_stream import bootstrap_run_log, finalize_run_log_artifact
from loregarden.services.stage_report import (
    StageReport,
    parse_stage_report,
    stage_report_artifact_content,
)
from loregarden.services.studio_routing import find_terminal_stage, is_terminal_stage
from loregarden.services.triage_question_log import record_triage_question_exchange
from loregarden.services.workflow_service import resolve_ticket_stages, resolve_workspace_stages
from loregarden.services.workflow_state import (
    build_stage_views,
    initial_stages_json,
    parse_stage_map,
    reconcile_workflow_state,
    serialize_stage_map,
    set_stage_status,
    settle_unreached_stages,
)
from sqlmodel import Session, select


def _run_code() -> str:
    return f"run_{secrets.token_hex(3)}"


def _stage_report_artifact(stage_key: str, report: StageReport) -> dict:
    """Build a `context`-kind artifact payload from a parsed stage report."""
    return {
        "kind": "context",
        "title": f"Stage report — {stage_key}",
        "content": stage_report_artifact_content(stage_key, report),
    }


def _blocking_issue(session: Session, ticket: Ticket, run: AgentRun, message: str) -> str:
    return record_blocking_issue(
        session, ticket, run_id=run.id, stage_key=run.stage_key, message=message
    )


def _blocking_issue_for_stage(
    session: Session, ticket: Ticket, stage_key: str, message: str
) -> str:
    return record_blocking_issue(session, ticket, run_id=None, stage_key=stage_key, message=message)


def _apply_operator_edits(ticket: Ticket, body: UpdateTicketRequest) -> None:
    """Apply the fields a human edits directly (title, description, posture).

    Module-level rather than another branch inside update_ticket_manual, which is
    already well past its statement budget.
    """
    content_updated = False

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise ValueError("Title cannot be empty")
        if ticket.title != title:
            ticket.title = title
            content_updated = True

    if body.description is not None and ticket.description != body.description:
        ticket.description = body.description
        content_updated = True

    if body.compatibility_posture is not None:
        apply_compatibility_posture(ticket, body.compatibility_posture)

    if content_updated:
        ticket.revision += 1
        ticket.last_updated_by = "human"


def _build_gate_impact(ticket: Ticket, stage_name: str) -> str:
    lines = [f"Stage '{stage_name}' requires human sign-off before completion."]
    lines.append(f"What's being tested: {ticket.title}")
    if ticket.description.strip():
        lines.append(ticket.description.strip())
    try:
        criteria = json.loads(ticket.acceptance_criteria_json or "[]")
    except json.JSONDecodeError:
        criteria = []
    if criteria:
        lines.append("Acceptance criteria:")
        lines.extend(f"- {item}" for item in criteria)
    return "\n".join(lines)


class OrchestrationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_workspace(self, slug: str) -> Workspace | None:
        return self.session.exec(select(Workspace).where(Workspace.slug == slug)).first()

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        return self.session.get(Ticket, ticket_id)

    def get_template_for_ticket(self, ticket: Ticket) -> WorkflowTemplate | None:
        template, _ = resolve_ticket_stages(self.session, ticket)
        return template

    def get_workflow_instance(self, ticket_id: str) -> WorkflowInstance | None:
        return self.session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
        ).first()

    def _resolve_stages(self, ticket: Ticket) -> tuple[WorkflowInstance | None, list]:
        instance = self.get_workflow_instance(ticket.id)
        _, stages = resolve_ticket_stages(self.session, ticket)
        return instance, stages

    def _resolve_transitions(self, ticket: Ticket) -> list[dict[str, str]]:
        template = self.get_template_for_ticket(ticket)
        if not template:
            return []
        return StateMachine.parse_transitions(template.transitions_json)

    def ensure_workflow_instance(
        self, ticket: Ticket, *, commit: bool = True
    ) -> tuple[WorkflowInstance | None, bool]:
        """Attach a workflow instance to feature/task/bug tickets when missing."""
        if ticket.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
            return self.get_workflow_instance(ticket.id), False
        if ticket.workflow_disabled:
            return None, False

        ws = self.session.get(Workspace, ticket.workspace_id)
        if not ws:
            return None, False
        template, stages = resolve_ticket_stages(self.session, ticket)
        if not template or not stages:
            template, stages = resolve_workspace_stages(self.session, ws)
        if not template or not stages:
            return None, False

        instance = self.get_workflow_instance(ticket.id)
        changed = False
        if not instance:
            if not ticket.workflow_stage_key:
                first_stage = min(stages, key=lambda s: s.order)
                ticket.workflow_stage_key = first_stage.key
                ticket.workflow_stage_status = StageStatus.PENDING
                ticket.next_agent = first_stage.agent_id
                changed = True
            instance = WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template.id,
                template_version=template.version,
                current_stage_key=ticket.workflow_stage_key,
                stages_json=initial_stages_json(stages),
            )
            self.session.add(instance)
            changed = True
        elif instance.template_id != template.id:
            instance.template_id = template.id
            instance.template_version = template.version
            changed = True

        if changed:
            reconcile_workflow_state(ticket, instance, stages, persist=False)
            self.session.add(ticket)
            self.session.add(instance)
            if commit:
                self.session.commit()
        return instance, changed

    def reconcile_ticket(self, ticket: Ticket, *, commit: bool = True) -> Ticket:
        instance, ensured = self.ensure_workflow_instance(ticket, commit=False)
        _, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            if commit and ensured:
                self.session.commit()
            return ticket
        before = (
            ticket.state,
            ticket.workflow_stage_key,
            ticket.workflow_stage_status,
            instance.stages_json,
        )
        reconcile_workflow_state(ticket, instance, stages)
        after = (
            ticket.state,
            ticket.workflow_stage_key,
            ticket.workflow_stage_status,
            instance.stages_json,
        )
        if commit and (before != after or ensured):
            self.session.add(ticket)
            self.session.add(instance)
            self.session.commit()
        return ticket

    def build_stage_views(self, ticket: Ticket) -> list[WorkflowStageView]:
        instance, _ = self.ensure_workflow_instance(ticket, commit=True)
        _, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            return []
        views = build_stage_views(ticket, instance, stages)
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        return views

    def start_ticket(self, ticket: Ticket) -> Ticket:
        result = StateMachine.can_transition_ticket(ticket.state, TicketState.IN_PROGRESS)
        if not result.ok:
            raise ValueError(result.message)
        ticket.state = TicketState.IN_PROGRESS
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.TICKET_STATE_CHANGED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"state": ticket.state.value},
        )
        return ticket

    def update_ticket_manual(self, ticket: Ticket, body: UpdateTicketRequest) -> Ticket:
        _apply_operator_edits(ticket, body)

        if body.workflow_template_slug is not None:
            from loregarden.services.workflow_service import WorkflowService

            wf = WorkflowService(self.session)
            if not body.workflow_template_slug.strip():
                wf.clear_ticket_workflow(ticket)
            else:
                wf.set_ticket_workflow_template(ticket, body.workflow_template_slug)
            self.session.refresh(ticket)

        instance, stages = self._resolve_stages(ticket)
        if body.auto_state is True:
            ticket.state_locked = False
        elif body.auto_state is False or body.state is not None:
            ticket.state_locked = True

        if body.state is not None:
            ticket.state = body.state
            ticket.revision += 1
            ticket.last_updated_by = "human"
            if body.state == TicketState.WONT_DO:
                ticket.state_locked = True

        if body.branch is not None:
            ticket.branch = body.branch.strip()
            ticket.revision += 1
            ticket.last_updated_by = "human"

        if body.stage_updates and instance and stages:
            stage_map = parse_stage_map(instance, stages)
            for key, status in body.stage_updates.items():
                if key not in stage_map:
                    raise ValueError(f"Unknown stage key: {key}")
                stage_map[key] = status
            instance.stages_json = serialize_stage_map(stage_map, stages)
            if body.auto_state is True or not ticket.state_locked:
                reconcile_workflow_state(ticket, instance, stages, persist=False)
        elif body.stage_key and body.stage_status and instance and stages:
            set_stage_status(ticket, instance, stages, body.stage_key, body.stage_status)
        elif instance and stages:
            stage_map = parse_stage_map(instance, stages)
            if body.workflow_stage_key:
                if body.workflow_stage_key not in stage_map:
                    raise ValueError(f"Unknown stage key: {body.workflow_stage_key}")
                ticket.workflow_stage_key = body.workflow_stage_key
            if body.workflow_stage_status:
                ticket.workflow_stage_status = body.workflow_stage_status
            if body.workflow_stage_key or body.workflow_stage_status:
                key = ticket.workflow_stage_key
                if key in stage_map:
                    stage_map[key] = ticket.workflow_stage_status
                    instance.stages_json = serialize_stage_map(stage_map, stages)
                instance.current_stage_key = ticket.workflow_stage_key
            if body.auto_state is True or not ticket.state_locked:
                reconcile_workflow_state(ticket, instance, stages, persist=False)
        elif body.auto_state is True and instance and stages:
            reconcile_workflow_state(ticket, instance, stages, persist=False)

        ticket.updated_at = datetime.now(timezone.utc)
        if instance:
            self.session.add(instance)
        self.session.add(ticket)
        self.session.commit()

        if body.state is not None:
            event_bus.publish(
                self.session,
                EventType.TICKET_STATE_CHANGED,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                payload={"state": ticket.state.value, "manual": True},
            )
        return ticket

    def advance_stage(self, ticket: Ticket) -> Ticket:
        if ticket.state == TicketState.WONT_DO:
            raise ValueError("Cannot advance a won't-do ticket")
        self.ensure_workflow_instance(ticket, commit=True)
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        current = ticket.workflow_stage_key
        if ticket.workflow_stage_status in (StageStatus.RUNNING, StageStatus.AWAITING):
            raise ValueError("Current stage must complete before advancing")

        if current and ticket.workflow_stage_status not in (
            StageStatus.DONE,
            StageStatus.WONT_DO,
        ):
            set_stage_status(ticket, instance, stages, current, StageStatus.DONE)

        if ticket.workflow_stage_status in (StageStatus.DONE, StageStatus.WONT_DO):
            ticket.blocking_issues = ""

        transitions = self._resolve_transitions(ticket)
        route = StateMachine.resolve_next_stage_key(stages, transitions, current, outcome="pass")
        if not route:
            reconcile_workflow_state(ticket, instance, stages)
            self.session.add(ticket)
            self.session.add(instance)
            self.session.commit()
            event_bus.publish(
                self.session,
                EventType.STAGE_COMPLETED,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                payload={"stage_key": current, "final": True},
            )
            return ticket

        from loregarden.services.workflow_routing import apply_stage_route

        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key=current,
            outcome="pass",
        )
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.STAGE_STARTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"stage_key": ticket.workflow_stage_key},
        )
        return ticket

    def route_workflow_stage(
        self,
        ticket: Ticket,
        *,
        from_stage_key: str,
        outcome: str = "reject",
        next_stage_key: str = "",
        next_agent: str = "",
        blocking_issues: str = "",
    ) -> Ticket:
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")
        if from_stage_key not in {stage.key for stage in stages}:
            raise ValueError(f"Unknown stage key: {from_stage_key}")

        from loregarden.services.workflow_routing import apply_stage_route

        transitions = self._resolve_transitions(ticket)
        short_blocking_issues = _blocking_issue_for_stage(
            self.session, ticket, from_stage_key, blocking_issues
        )
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key=from_stage_key,
            outcome=outcome,
            next_stage_key=next_stage_key,
            next_agent=next_agent,
            blocking_issues=short_blocking_issues,
        )
        ticket.revision += 1
        ticket.last_updated_by = "human"
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(ticket)
        return ticket

    def finalize_workflow(self, ticket: Ticket) -> Ticket:
        """Mark the terminal done stage complete and close out the ticket."""
        self.ensure_workflow_instance(ticket, commit=True)
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        done_def = find_terminal_stage(stages)
        if not done_def:
            raise ValueError("Workflow has no done stage")

        if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
            raise ValueError(f"Ticket is already {ticket.state.value}")

        if ticket.workflow_stage_status in (StageStatus.RUNNING, StageStatus.AWAITING):
            raise ValueError("Current stage must complete before finishing the ticket")

        current = ticket.workflow_stage_key
        if (
            current
            and current != done_def.key
            and ticket.workflow_stage_status
            not in (
                StageStatus.DONE,
                StageStatus.WONT_DO,
            )
        ):
            raise ValueError("Advance to the Done stage before completing the ticket")

        settle_unreached_stages(ticket, instance, stages, terminal_key=done_def.key)
        ticket.workflow_stage_key = done_def.key
        set_stage_status(ticket, instance, stages, done_def.key, StageStatus.DONE)
        ticket.blocking_issues = ""
        reconcile_workflow_state(ticket, instance, stages)
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.STAGE_COMPLETED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"stage_key": "done", "final": True},
        )
        return ticket

    def enter_human_gate(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
    ) -> Ticket:
        """Open a human approval gate for agentless workflow stages (e.g. approval)."""
        from loregarden.services.studio_routing import is_agentless_stage, resolve_stage_execution

        self.ensure_workflow_instance(ticket, commit=True)
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        target_key = stage_key or ticket.workflow_stage_key
        if not target_key:
            raise ValueError("No stage key for human gate")

        stage_def = next((s for s in stages if s.key == target_key), None)
        if not stage_def:
            raise ValueError(f"Unknown stage key: {target_key}")

        if is_terminal_stage(stage_def):
            self.finalize_workflow(ticket)
            return ticket

        agent_id, _ = resolve_stage_execution(ticket, stage_def)
        if agent_id or not is_agentless_stage(stage_def):
            raise ValueError(f"Stage '{target_key}' is not a human approval gate")

        if ticket.workflow_stage_status in (StageStatus.RUNNING, StageStatus.AWAITING):
            if not (
                ticket.workflow_stage_status == StageStatus.AWAITING
                and target_key == ticket.workflow_stage_key
            ):
                raise ValueError("Current stage must complete before starting another")

        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(target_key) == StageStatus.WONT_DO:
            raise ValueError(f"Stage '{target_key}' is marked won't do")

        # Opening the gate is a fresh attempt — drop any stale blocking message
        # left over from a prior failure (see the matching comment in
        # start_run for why this must be unconditional, not just BLOCKED/DONE).
        ticket.blocking_issues = ""

        if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
            raise ValueError(f"Cannot open human gate for ticket in state: {ticket.state.value}")

        if ticket.state == TicketState.BACKLOG:
            self.start_ticket(ticket)
            self.session.refresh(ticket)
            instance = self.get_workflow_instance(ticket.id) or instance

        ticket.workflow_stage_key = target_key
        set_stage_status(ticket, instance, stages, target_key, StageStatus.AWAITING)
        ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

        template = self.get_template_for_ticket(ticket)
        stage_name = stage_display_name(template, target_key) if template else target_key
        existing = self.session.exec(
            select(Approval).where(
                Approval.ticket_id == ticket.id,
                Approval.stage_key == target_key,
                Approval.status == ApprovalStatus.PENDING,
                Approval.kind == ApprovalKind.WORKFLOW_GATE,
            )
        ).first()
        if not existing:
            self._create_workflow_gate_approval(ticket, target_key, stage_name, stage_def=stage_def)

        event_bus.publish(
            self.session,
            EventType.STAGE_STARTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"stage_key": target_key, "human_gate": True},
        )
        return ticket

    def _reject_if_triage_active(self, ticket: Ticket) -> None:
        from loregarden.services.run_concurrency import find_active_run
        from loregarden.services.triage_service import TRIAGE_AGENT_ID

        if find_active_run(self.session, ticket.id, only_agent_id=TRIAGE_AGENT_ID):
            raise ValueError(
                "Triage is currently running for this ticket — wait for it to finish before starting a stage run."
            )

    def start_run(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        orchestration_run_id: str | None = None,
        agent_id: str | None = None,
        skill_name: str | None = None,
        auto_approve: bool = False,
        timeout_override_seconds: int | None = None,
    ) -> AgentRun:
        template = self.get_template_for_ticket(ticket)
        if not template:
            raise ValueError("No workflow template for ticket workspace")

        self.ensure_workflow_instance(ticket, commit=True)
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        target_key = stage_key or ticket.workflow_stage_key
        if not target_key:
            target_key = StateMachine.next_stage_key(stages, "")
            if not target_key:
                raise ValueError("Workflow has no stages")

        if ticket.workflow_stage_status in (StageStatus.RUNNING, StageStatus.AWAITING):
            if not (
                ticket.workflow_stage_status == StageStatus.RUNNING
                and target_key == ticket.workflow_stage_key
            ):
                raise ValueError("Current stage must complete before advancing")

        self._reject_if_triage_active(ticket)

        stage_def = next((s for s in stages if s.key == target_key), None)
        if not stage_def:
            raise ValueError(f"Unknown stage key: {target_key}")

        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(target_key) == StageStatus.WONT_DO:
            raise ValueError(f"Stage '{target_key}' is marked won't do")

        # Starting a stage is a fresh attempt — drop any stale blocking message
        # left over from a prior failure. Without this, a stage that was left
        # PENDING (not BLOCKED) after an earlier failure elsewhere carries its
        # old blocking_issues text forward; the moment this run marks it
        # RUNNING, reconcile_workflow_state sees non-empty blocking_issues and
        # misreports the ticket as BLOCKED before anything has actually failed
        # in this attempt.
        ticket.blocking_issues = ""

        if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
            raise ValueError(f"Cannot start run for ticket in state: {ticket.state.value}")

        if ticket.state == TicketState.BACKLOG:
            self.start_ticket(ticket)
            self.session.refresh(ticket)
            instance = self.get_workflow_instance(ticket.id) or instance

        from loregarden.services.studio_routing import is_agentless_stage, resolve_stage_execution

        resolved_agent_id, resolved_skill = resolve_stage_execution(ticket, stage_def)
        chosen_agent = agent_id or resolved_agent_id
        chosen_skill = skill_name or resolved_skill or stage_def.skill_name
        if not chosen_agent or is_agentless_stage(stage_def):
            raise ValueError(
                f"Stage '{target_key}' is a human approval gate — it does not run an agent CLI."
            )

        ticket.workflow_stage_key = target_key
        if stage_map.get(target_key) != StageStatus.RUNNING:
            set_stage_status(ticket, instance, stages, target_key, StageStatus.RUNNING)
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

        # Pin the agent-definition version this run executes under, for
        # reproducibility (which definition produced this run's behavior).
        agent_row = self.session.exec(
            select(StudioAgent).where(StudioAgent.slug == chosen_agent)
        ).first()

        run = AgentRun(
            run_code=_run_code(),
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            orchestration_run_id=orchestration_run_id,
            agent_id=chosen_agent,
            agent_version=agent_row.version if agent_row else None,
            skill_name=chosen_skill,
            stage_key=target_key,
            status=RunStatus.RUNNING,
            auto_approve=auto_approve,
            timeout_override_seconds=timeout_override_seconds,
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        event_bus.publish(
            self.session,
            EventType.AGENT_RUN_STARTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            run_id=run.id,
            payload={"agent_id": chosen_agent, "stage_key": target_key},
        )
        bootstrap_run_log(run)
        return run

    def complete_run(
        self,
        run: AgentRun,
        *,
        status: RunStatus,
        stdout: str = "",
        stderr: str = "",
        artifacts: list[dict] | None = None,
        advance_workflow: bool = True,
    ) -> AgentRun:
        stored = self.session.get(AgentRun, run.id)
        if not stored:
            return run
        run = stored
        run.status = status
        run.stdout = stdout
        run.stderr = stderr
        run.finished_at = datetime.now(timezone.utc)
        self.session.add(run)
        self.session.commit()

        ticket = self.get_ticket(run.ticket_id)
        if not ticket:
            return run

        report = parse_stage_report(stdout)
        if advance_workflow:
            self._advance_stage_after_run(ticket, run, report, status, stderr)

        self._persist_run_artifacts(ticket, run, status, stderr, report, artifacts)

        event_bus.publish(
            self.session,
            EventType.AGENT_RUN_COMPLETED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            run_id=run.id,
            payload={"status": status.value},
        )
        if advance_workflow:
            workspace = self.session.get(Workspace, ticket.workspace_id)
            if workspace:
                from loregarden.services.artifact_service import refresh_execution_artifacts

                refresh_execution_artifacts(
                    self.session,
                    ticket=ticket,
                    run=run,
                    workspace=workspace,
                )
        finalize_run_log_artifact(run, status=status, stderr=stderr)
        return run

    def _advance_stage_after_run(
        self,
        ticket: Ticket,
        run: AgentRun,
        report,
        status: RunStatus,
        stderr: str,
    ) -> None:
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            return

        if report and report.status == "blocked":
            # Distinct from fail/needs_rework: the agent isn't reporting bad work to
            # redo upstream, it's reporting it cannot proceed at all (e.g. needs a
            # human decision) — reroute-for-rework would just waste a cycle, so this
            # halts the ticket directly instead.
            fallback = "Agent reported this stage as blocked"
            message = report.reroute_context or stderr[:2000] or fallback
            ticket.blocking_issues = _blocking_issue(self.session, ticket, run, message)
            set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
        elif report and report.status in ("fail", "needs_rework"):
            from loregarden.services.workflow_routing import apply_stage_route

            transitions = self._resolve_transitions(ticket)
            try:
                apply_stage_route(
                    ticket,
                    instance,
                    stages,
                    transitions,
                    from_key=run.stage_key,
                    outcome="reject",
                    next_stage_key=report.reroute_to_stage or "",
                    blocking_issues=_blocking_issue(
                        self.session, ticket, run, report.reroute_context or stderr[:2000]
                    ),
                )
            except ValueError:
                # No reject transition, no agent-specified target, and no
                # preceding stage to fall back to (already first-in-order).
                ticket.blocking_issues = _blocking_issue(
                    self.session,
                    ticket,
                    run,
                    report.reroute_context or stderr[:2000] or "Agent run failed",
                )
                set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
        elif status == RunStatus.SUCCEEDED:
            stage_status = StageStatus.DONE
            stage_def = next((s for s in stages if s.key == run.stage_key), None)
            if stage_def and stage_def.gate_required:
                stage_status = StageStatus.AWAITING
                template = self.get_template_for_ticket(ticket)
                if template:
                    stage_name = stage_display_name(template, run.stage_key)
                    self._create_workflow_gate_approval(
                        ticket, run.stage_key, stage_name, stage_def=stage_def
                    )
            set_stage_status(ticket, instance, stages, run.stage_key, stage_status)
            ticket.blocking_issues = ""
        else:
            ticket.blocking_issues = _blocking_issue(
                self.session, ticket, run, stderr[:2000] or "Agent run failed"
            )
            set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

    def _persist_run_artifacts(
        self,
        ticket: Ticket,
        run: AgentRun,
        status: RunStatus,
        stderr: str,
        report,
        artifacts: list[dict] | None,
    ) -> None:
        artifacts = list(artifacts or [])
        if status != RunStatus.SUCCEEDED:
            artifacts.append(
                {
                    "kind": "error",
                    "title": f"Run {run.run_code} failed",
                    "content": {
                        "message": stderr[:4000] or ticket.blocking_issues or "Agent run failed",
                        "run_code": run.run_code,
                        "agent_id": run.agent_id,
                        "stage_key": run.stage_key,
                        "command": run.command or "",
                    },
                }
            )
        if report:
            artifacts.append(_stage_report_artifact(run.stage_key, report))

        for item in artifacts:
            if item.get("kind") == "log":
                existing = self.session.exec(
                    select(Artifact).where(
                        Artifact.run_id == run.id,
                        Artifact.kind == "log",
                    )
                ).first()
                if existing:
                    continue
            artifact = Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind=item.get("kind", "log"),
                title=item.get("title", ""),
                content_json=json.dumps(item.get("content", {})),
            )
            self.session.add(artifact)
            self.session.commit()
            event_bus.publish(
                self.session,
                EventType.ARTIFACT_CREATED,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                run_id=run.id,
                artifact_id=artifact.id,
                payload={"kind": artifact.kind},
            )

    def finalize_stage(
        self,
        ticket: Ticket,
        stage_key: str,
        *,
        status: StageStatus,
        blocking_message: str = "",
    ) -> None:
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            return
        set_stage_status(ticket, instance, stages, stage_key, status)
        if status == StageStatus.BLOCKED and blocking_message:
            ticket.blocking_issues = blocking_message[:2000]
        elif status == StageStatus.DONE:
            ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

    def _create_workflow_gate_approval(
        self,
        ticket: Ticket,
        stage_key: str,
        stage_name: str,
        *,
        stage_def: WorkflowStageDef | None = None,
    ) -> Approval:
        checklist = expand_gate_checklist(ticket, list(stage_def.checklist) if stage_def else [])
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.WORKFLOW_GATE,
            title=f"Approve {ticket.title}",
            level="high" if ticket.priority == 1 else "medium",
            stage_key=stage_key,
            impact=_build_gate_impact(ticket, stage_name),
            checklist_json=json.dumps(checklist),
            status=ApprovalStatus.PENDING,
        )
        self.session.add(approval)
        self.session.commit()
        event_bus.publish(
            self.session,
            EventType.APPROVAL_REQUESTED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"approval_id": approval.id},
        )
        return approval

    async def create_parallel_run(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        max_concurrent: int = 3,
    ) -> dict:
        """
        Create a run with parallel execution support.

        Checks queue and either:
        - Starts immediately if slot available
        - Queues run if no slots available

        Args:
            ticket: Ticket to run
            stage_key: Stage to start (optional)
            max_concurrent: Max concurrent runs (default 3)

        Returns:
            {
                "status": "started" | "queued",
                "run": AgentRun (if started),
                "position": int (if queued),
                "message": str
            }
        """
        from loregarden.services.parallel_queue import ParallelQueueService
        from loregarden.services.worktree_service import WorktreeService

        try:
            queue_service = ParallelQueueService(self.session, max_concurrent=max_concurrent)
            worktree_service = WorktreeService(self.session, repo_path=".")

            # Get queue stats
            queue_stats = queue_service.get_queue_stats(ticket.workspace_id)

            # Check available slots
            if queue_stats.get("available_slots", 0) > 0:
                # Create worktree and run immediately
                return await self._create_run_in_worktree(
                    ticket=ticket,
                    stage_key=stage_key,
                    worktree_service=worktree_service,
                    queue_service=queue_service,
                )
            else:
                # Queue the run
                queue_result = await queue_service.queue_run(
                    workspace_id=ticket.workspace_id,
                    ticket_id=ticket.id,
                    run_id="",  # Placeholder, will be created on promotion
                )
                return {
                    "status": "queued",
                    "position": queue_result.get("position"),
                    "queue_length": queue_result.get("queue_length"),
                    "message": queue_result.get("message"),
                }

        except Exception as e:
            import logging

            logging.error(f"Error creating parallel run: {e}", exc_info=True)
            raise

    async def _create_run_in_worktree(
        self,
        ticket: Ticket,
        stage_key: str | None,
        worktree_service,
        queue_service,
    ) -> dict:
        """
        Create a run in an isolated worktree.

        Args:
            ticket: Ticket to run
            stage_key: Stage key (optional)
            worktree_service: WorktreeService instance
            queue_service: ParallelQueueService instance

        Returns:
            {
                "status": "started",
                "run": AgentRun,
                "worktree_id": str,
                "message": str
            }
        """
        try:
            # Create agent run (without worktree yet)
            run = self.start_run(ticket, stage_key=stage_key)

            # Create worktree for this run
            worktree = worktree_service.create_worktree(
                workspace_id=ticket.workspace_id,
                agent_run_id=run.id,
                parent_branch="main",
            )

            if worktree:
                # Link worktree to run
                run.worktree_id = worktree.id
                self.session.add(run)
                self.session.commit()

            # Queue the run in available slot
            queue_result = await queue_service.queue_run(
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                run_id=run.id,
            )

            return {
                "status": "started",
                "run": run,
                "worktree_id": worktree.id if worktree else None,
                "slot_number": queue_result.get("slot_number"),
                "message": f"Started in {worktree.worktree_path if worktree else 'main repo'}",
            }

        except Exception as e:
            import logging

            logging.error(f"Error creating run in worktree: {e}", exc_info=True)
            raise

    async def on_parallel_run_complete(
        self,
        run: AgentRun,
        auto_merge: bool = False,
    ) -> dict:
        """
        Called when a parallel run completes.

        Handles:
        - Merging worktree changes
        - Freeing slot
        - Promoting next run from queue

        Args:
            run: Completed AgentRun
            auto_merge: Whether to auto-merge if conflicts (default False)

        Returns:
            {
                "status": "merged" | "failed" | "conflicts",
                "next_run": AgentRun (if promoted),
                "message": str
            }
        """
        from loregarden.services.parallel_queue import ParallelQueueService
        from loregarden.services.worktree_service import WorktreeService

        try:
            # Merge worktree if exists
            if run.worktree_id:
                worktree_service = WorktreeService(self.session, repo_path=".")
                worktree = worktree_service.get_worktree(run.worktree_id)

                if worktree:
                    merge_success = worktree_service.merge_worktree(
                        worktree,
                        target_branch="main",
                        auto_resolve=auto_merge,
                    )

                    if not merge_success:
                        return {
                            "status": "conflicts",
                            "worktree_id": worktree.id,
                            "conflict_files": worktree.conflict_files,
                            "message": f"Merge conflicts in {len(worktree.conflict_files)} files",
                        }

            # Free slot and promote from queue
            queue_service = ParallelQueueService(self.session, max_concurrent=3)
            promotion = await queue_service.on_run_complete(
                workspace_id=run.workspace_id,
                run_id=run.id,
            )

            if promotion and promotion.get("status") == "promoted":
                return {
                    "status": "merged",
                    "next_run": promotion.get("next_run"),
                    "message": promotion.get("message"),
                }
            else:
                return {
                    "status": "merged",
                    "message": "Run completed, slot freed, queue empty",
                }

        except Exception as e:
            import logging

            logging.error(f"Error on parallel run complete: {e}", exc_info=True)
            raise


class ApprovalService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        answers: dict[str, str | list[str]] | None = None,
        response_text: str = "",
        always_allow: bool = False,
        allow_for_ticket: bool = False,
        allow_for_stage: bool = False,
        route_to_stage_key: str = "",
    ) -> Approval:
        from loregarden.agents.executors.permission_bridge import (
            build_ask_user_question_input,
            parse_stored_tool_input,
            validate_question_answers,
        )
        from loregarden.services.permission_allowlist import (
            add_ticket_allow_rule,
            add_workspace_allow_rule,
        )

        approval = self.session.get(Approval, approval_id)
        if not approval:
            raise ValueError("Approval not found")
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("Approval already resolved")

        rework_route_key = route_to_stage_key.strip()
        if rework_route_key:
            self._validate_rework_route(approval, rework_route_key)

        if approval.kind == ApprovalKind.CLI_QUESTION and approved:
            tool_input = json.loads(approval.tool_input_json or "{}")
            validate_question_answers(tool_input, answers, response=response_text)
            updated_input = build_ask_user_question_input(
                tool_input,
                answers=answers or {},
                response=response_text,
            )
            approval.response_json = json.dumps({"updated_input": updated_input})
            # The answer reaches the agent as a tool result; mirror it into the chat so the
            # operator's transcript shows the exchange rather than jumping over it.
            record_triage_question_exchange(
                self.session,
                approval,
                tool_input,
                answers=answers,
                response=response_text,
            )
        elif approval.kind == ApprovalKind.CLI_PERMISSION and approved:
            tool_input = parse_stored_tool_input(approval.tool_input_json)
            approval.response_json = json.dumps({"updated_input": tool_input})
            if always_allow:
                add_workspace_allow_rule(
                    self.session,
                    approval.workspace_id,
                    approval.tool_name,
                    tool_input,
                )
            if allow_for_ticket:
                add_ticket_allow_rule(
                    self.session,
                    approval.ticket_id,
                    approval.tool_name,
                    tool_input,
                )
            if allow_for_stage and approval.stage_key:
                add_ticket_allow_rule(
                    self.session,
                    approval.ticket_id,
                    approval.tool_name,
                    tool_input,
                    stage_key=approval.stage_key,
                )

        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval.resolved_at = datetime.now(timezone.utc)
        self.session.add(approval)
        self.session.commit()

        ticket = self.session.get(Ticket, approval.ticket_id)
        if ticket and approval.kind == ApprovalKind.WORKFLOW_GATE:
            self._apply_gate_resolution(
                ticket,
                approval,
                approved=approved,
                rework_route_key=rework_route_key,
                response_text=response_text,
            )

        event_bus.publish(
            self.session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=approval.workspace_id,
            ticket_id=approval.ticket_id,
            payload={"approval_id": approval.id, "approved": approved},
        )
        return approval

    def _validate_rework_route(self, approval: Approval, rework_route_key: str) -> None:
        """Validate an explicit stage override up front so a bad target can't
        leave the approval resolved with the ticket never rerouted. Used both
        for approve-with-rework (send a passing gate back for formalization)
        and reject-with-explicit-target (override the template's default
        reject route/previous-stage fallback with an operator's choice)."""
        if approval.kind != ApprovalKind.WORKFLOW_GATE:
            raise ValueError("route_to_stage_key only applies to workflow-gate sign-offs")
        gate_ticket = self.session.get(Ticket, approval.ticket_id)
        _, gate_stages = (
            self.orchestration._resolve_stages(gate_ticket) if gate_ticket else (None, [])
        )
        if not gate_stages or rework_route_key not in {s.key for s in gate_stages}:
            raise ValueError(f"Unknown rework stage key: {rework_route_key}")
        if not StateMachine.is_upstream_route(gate_stages, approval.stage_key, rework_route_key):
            raise ValueError(
                f"Rework stage '{rework_route_key}' must come before "
                f"gate stage '{approval.stage_key}'"
            )

    def _apply_gate_resolution(
        self,
        ticket: Ticket,
        approval: Approval,
        *,
        approved: bool,
        rework_route_key: str,
        response_text: str,
    ) -> None:
        instance, stages = self.orchestration._resolve_stages(ticket)
        if not instance or not stages or not approval.stage_key:
            return

        from loregarden.services.workflow_routing import apply_stage_route

        if approved and rework_route_key:
            note = response_text.strip() or (
                "Formalize the prototype changes made during this verification "
                "with production-quality implementation and tests."
            )
            transitions = self.orchestration._resolve_transitions(ticket)
            apply_stage_route(
                ticket,
                instance,
                stages,
                transitions,
                from_key=approval.stage_key,
                outcome="reject",
                next_stage_key=rework_route_key,
                blocking_issues=f"'{approval.stage_key}' gate approved with rework: {note}",
            )
        elif approved:
            set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.DONE)
        else:
            reject_message = response_text.strip() or "Human rejected approval"
            transitions = self.orchestration._resolve_transitions(ticket)
            try:
                apply_stage_route(
                    ticket,
                    instance,
                    stages,
                    transitions,
                    from_key=approval.stage_key,
                    outcome="reject",
                    next_stage_key=rework_route_key,
                    blocking_issues=reject_message,
                )
            except ValueError:
                # No reject transition and no preceding stage to fall back to
                # (already first-in-order) — hard-block in place.
                ticket.blocking_issues = reject_message
                set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.BLOCKED)

        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

        if approved and rework_route_key:
            self._resume_orchestration(ticket)

    def _resume_orchestration(self, ticket: Ticket) -> None:
        """Auto-continue after an approve-with-rework reroute — otherwise the
        ticket would sit at the target stage waiting for the operator to
        separately click Run/Agents Assemble, which defeats the point of
        routing it back in the same action."""
        from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
        from loregarden.services.run_service import schedule_orchestration

        if OrchestrationCallbackService(self.session).get_active_orchestration_run(ticket.id):
            return
        schedule_orchestration(ticket.id)

    def list_pending(self) -> list[Approval]:
        return list(
            self.session.exec(
                select(Approval).where(Approval.status == ApprovalStatus.PENDING)
            ).all()
        )
