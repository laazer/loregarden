import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_loader import stage_display_name
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    DispatchSurface,
    EventType,
    OrchestrationRun,
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
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.artifact_service import record_blocking_issue
from loregarden.services.compatibility_posture import apply_compatibility_posture
from loregarden.services.gate_checklist import expand_gate_checklist_for_ticket
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.run_completion import (
    complete_run_tail,
    release_execution_slot,
    settle_stage_after_failed_completion,
)
from loregarden.services.run_concurrency import find_active_orchestration_run
from loregarden.services.run_log_stream import bootstrap_run_log
from loregarden.services.scheduling import schedule_orchestration
from loregarden.services.stage_retry_budget import (
    DispatchOrigin,
    blocked_on_stage_retry_budget,
    clear_stage_dispatches,
    clear_stage_retry_block,
    commit_standalone_stage_dispatch,
    evaluate_standalone_stage_dispatch,
)
from loregarden.services.studio_routing import (
    find_terminal_stage,
    is_agentless_stage,
    is_terminal_stage,
    resolve_stage_execution,
)
from loregarden.services.ticket_rollup import has_children, reconcile_ancestors, reconcile_parent
from loregarden.services.ticket_state_service import choose
from loregarden.services.ticket_tags import serialize_tags
from loregarden.services.triage_question_log import (
    record_home_chat_question_exchange,
    record_triage_question_exchange,
)
from loregarden.services.workflow_service import resolve_ticket_stages, resolve_workspace_stages
from loregarden.services.workflow_state import (
    build_stage_views,
    initial_stages_json,
    next_executable_stage,
    parse_stage_map,
    parse_stage_notes,
    reconcile_workflow_state,
    serialize_stage_map,
    set_stage_status,
    settle_unreached_stages,
)
from loregarden.services.worktree_lifecycle import release_ticket_worktree
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def _run_code() -> str:
    return f"run_{secrets.token_hex(3)}"


@dataclass(frozen=True)
class _RunTarget:
    """The stage a run is about to start on, once every precondition has held."""

    instance: WorkflowInstance
    stages: list[WorkflowStageDef]
    target_key: str
    stage_def: WorkflowStageDef
    stage_map: dict[str, StageStatus]


def _resolve_run_agent(
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    *,
    agent_id: str | None,
    skill_name: str | None,
) -> tuple[str, str | None]:
    """Which agent (and skill) runs this stage, or why nothing can.

    Explicit ``agent_id``/``skill_name`` win over what the stage resolves to —
    that is how a driver fans a parallel stage out one member at a time.
    """
    resolved_agent_id, resolved_skill = resolve_stage_execution(ticket, stage_def)
    chosen_agent = agent_id or resolved_agent_id
    chosen_skill = skill_name or resolved_skill or stage_def.skill_name
    if is_agentless_stage(stage_def):
        raise ValueError(
            f"Stage '{stage_def.key}' is a human approval gate — it does not run an agent CLI."
        )
    if not chosen_agent:
        # Two different faults used to share the gate message above, which
        # sent every reader looking for a gate that was not there. A stage
        # that *should* run an agent but resolved none is a routing defect —
        # most often a parallel stage started without naming which member
        # this run is (`agent_id`), since its agents live in
        # `parallel_agents` and only a driver can fan them out.
        raise ValueError(
            f"Stage '{stage_def.key}' resolved no agent to run it. A "
            f"'{stage_def.stage_type}' stage must either name an agent or be "
            "started per member with an explicit agent_id."
        )
    return chosen_agent, chosen_skill


def _blocking_issue_for_stage(
    session: Session, ticket: Ticket, stage_key: str, message: str
) -> str:
    return record_blocking_issue(session, ticket, run_id=None, stage_key=stage_key, message=message)


def _content_fields(ticket: Ticket) -> tuple[str, str, str, str]:
    """The stored form of every field a content edit can touch.

    Snapshotting once before and once after is what lets each field below be a
    plain assignment: no per-field compare-then-flag, and no way to add a field
    to the edit path but forget to make it bump the revision.
    """
    return (
        ticket.title,
        ticket.description,
        ticket.acceptance_criteria_json,
        ticket.tags_json,
    )


def _apply_content_edits(ticket: Ticket, body: UpdateTicketRequest) -> bool:
    """The fields whose change earns a revision bump. Returns whether any did."""
    before = _content_fields(ticket)

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise ValueError("Title cannot be empty")
        ticket.title = title

    if body.description is not None:
        ticket.description = body.description

    if body.acceptance_criteria is not None:
        ticket.acceptance_criteria_json = serialize_criteria(body.acceptance_criteria)

    if body.tags is not None:
        ticket.tags_json = serialize_tags(body.tags)

    return _content_fields(ticket) != before


def _apply_operator_edits(ticket: Ticket, body: UpdateTicketRequest) -> None:
    """Apply the fields a human edits directly (title, description, criteria, posture).

    Module-level rather than another branch inside update_ticket_manual, which is
    already well past its statement budget.
    """
    content_updated = _apply_content_edits(ticket, body)

    if body.priority is not None:
        if body.priority < 1 or body.priority > 3:
            raise ValueError("Priority must be between 1 and 3")
        if ticket.priority != body.priority:
            ticket.priority = body.priority
            content_updated = True

    if body.compatibility_posture is not None:
        apply_compatibility_posture(ticket, body.compatibility_posture)

    if body.git_automation is not None:
        # serialize_override drops unknown keys and stores "" for an empty
        # override, so {} means "inherit the workspace policy again".
        ticket.git_automation_json = serialize_override(body.git_automation)

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


def _consume_scope_reroute_pin(ticket: Ticket, chosen_agent: str) -> None:
    """Clear the scope-denial reroute pin once its dispatch is committed.

    The pin exists to steer this one dispatch to the sibling implementer; clearing
    it here means a *fresh* denial in this run sets a new pin, but a satisfied one
    can never linger into a later stage as a stale hint.
    """
    if ticket.scope_reroute_agent and ticket.scope_reroute_agent == chosen_agent:
        ticket.scope_reroute_agent = ""


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

    def next_executable_stage_key(self, ticket: Ticket) -> str | None:
        """The stage a driver should run next, or None if nothing may run.

        Public because every driver needs it, not just the builtin one: an
        external harness asking for its next stage has no other way to resolve
        the ticket's stage map. See ``next_executable_stage`` for the rules.
        """
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            return None
        return next_executable_stage(stages, parse_stage_map(instance, stages))

    def stage_definition(self, ticket: Ticket, stage_key: str) -> WorkflowStageDef | None:
        """This ticket's definition of ``stage_key``, or None if it has none.

        Public for the same reason as ``next_executable_stage_key``: a driver
        outside this process has to know what kind of stage it is being handed
        before it can run it (see ``services.parallel_stage``).
        """
        _, stages = self._resolve_stages(ticket)
        return next((s for s in stages if s.key == stage_key), None) if stages else None

    def stage_status(self, ticket: Ticket, stage_key: str) -> StageStatus | None:
        """The recorded status of ``stage_key`` on this ticket's instance."""
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            return None
        return parse_stage_map(instance, stages).get(stage_key)

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
            self._reconcile_workflow(ticket, instance, stages)
            self.session.add(ticket)
            self.session.add(instance)
            if commit:
                self.session.commit()
        return instance, changed

    def _owns_state(self, ticket: Ticket) -> bool:
        """Whether this ticket's own workflow may write its state.

        A parent's state belongs to `ticket_rollup`, which reads its children.
        The workflow answer for a parent is `backlog` in perpetuity — nothing
        runs a parent's stages, so they never leave `triage/pending` — and it
        used to overwrite the rollup on any read.
        """
        return not has_children(self.session, ticket.id)

    def _reconcile_workflow(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
    ) -> dict[str, StageStatus]:
        """Every workflow reconcile in this service, with the owner decided once.

        A seam rather than a keyword at each call site: the rule is easy to
        state and was previously enforced nowhere, so a new call site should
        inherit it instead of remembering it.
        """
        return reconcile_workflow_state(
            ticket, instance, stages, persist=False, owns_state=self._owns_state(ticket)
        )

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
        self._reconcile_workflow(ticket, instance, stages)
        # A read repairs a parent rather than merely declining to corrupt it.
        # The push-on-child-change hook and the startup sweep both leave windows
        # — a child created rather than moved, a server that has not restarted —
        # and this is the call every ticket read already passes through.
        if not self._owns_state(ticket):
            reconcile_parent(self.session, ticket)
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
            if before[0] != after[0]:
                # This ticket's state moved, so every parent above it is now a
                # summary of something that changed. Hooked here because this is
                # the widest point an orchestrated state change passes through;
                # the startup sweep covers whatever still slips past it.
                reconcile_ancestors(self.session, ticket)
        return ticket

    def build_stage_views(self, ticket: Ticket) -> list[WorkflowStageView]:
        instance, _ = self.ensure_workflow_instance(ticket, commit=True)
        _, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            return []
        views = build_stage_views(ticket, instance, stages, owns_state=self._owns_state(ticket))
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        return views

    def start_ticket(self, ticket: Ticket) -> Ticket:
        choose(self.session, ticket, TicketState.IN_PROGRESS, actor="human")
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)
        self.session.commit()
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
            # The one writer that took any value from the API and wrote it
            # unchecked. `choose` validates it as a move somebody decided on.
            choose(self.session, ticket, body.state, actor="human", emit=False)
            if body.state == TicketState.WONT_DO:
                ticket.state_locked = True

        if body.branch is not None:
            ticket.branch = body.branch.strip()
            ticket.revision += 1
            ticket.last_updated_by = "human"

        if body.stage_updates and instance and stages:
            self._apply_manual_stage_updates(
                ticket,
                instance,
                stages,
                body.stage_updates,
                auto_state=body.auto_state is True or not ticket.state_locked,
            )
        elif body.stage_key and body.stage_status and instance and stages:
            set_stage_status(ticket, instance, stages, body.stage_key, body.stage_status)
            if body.stage_status == StageStatus.PENDING:
                self.refresh_stage_retry_budget(ticket, body.stage_key)
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
                    instance.stages_json = serialize_stage_map(
                        stage_map, stages, notes=parse_stage_notes(instance)
                    )
                instance.current_stage_key = ticket.workflow_stage_key
            if body.auto_state is True or not ticket.state_locked:
                self._reconcile_workflow(ticket, instance, stages)
        elif body.auto_state is True and instance and stages:
            self._reconcile_workflow(ticket, instance, stages)

        ticket.updated_at = datetime.now(timezone.utc)
        if instance:
            self.session.add(instance)
        self.session.add(ticket)
        self.session.commit()

        if body.state is not None:
            self._settle_manual_state_change(ticket)
        return ticket

    def _settle_manual_state_change(self, ticket: Ticket) -> None:
        """What follows a human (or MCP) setting a ticket's state by hand."""
        # Abandoning or finishing a ticket retires its tree too; otherwise the
        # directory and its branch checkout outlive every reason they existed.
        release_ticket_worktree(self.session, ticket)
        # Closing the last open child finishes its parent, and a human closing
        # it by hand is no different from an agent doing so.
        reconcile_ancestors(self.session, ticket)
        event_bus.publish(
            self.session,
            EventType.TICKET_STATE_CHANGED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"state": ticket.state.value, "manual": True},
        )

    def refresh_stage_retry_budget(
        self, ticket: Ticket, stage_key: str, *, clear_dispatches: bool = True
    ) -> None:
        """Give a continued / human-reset stage a full dispatch budget again.

        The circuit breaker persists ``stage_dispatch`` artifacts across runs.
        Continuing a failed or blocked stage without wiping that counter makes
        the next start re-block immediately on the same exhausted budget.

        ``clear_dispatches=False`` clears the stale blocking state without
        touching the counter, for a caller that has to unblock a ticket the
        breaker did not block.
        """
        if not stage_key:
            return
        if clear_dispatches:
            clear_stage_dispatches(self.session, ticket.id, stage_key)
        if blocked_on_stage_retry_budget(self.session, ticket, stage_key):
            ticket.blocking_issues = ""
            clear_stage_retry_block(self.session, ticket.id, stage_key)
        if ticket.state == TicketState.BLOCKED and not ticket.state_locked:
            choose(self.session, ticket, TicketState.IN_PROGRESS, actor="human", emit=False)
            ticket.next_status = ""

    def _apply_manual_stage_updates(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        stage_updates: dict[str, StageStatus],
        *,
        auto_state: bool,
    ) -> None:
        stage_map = parse_stage_map(instance, stages)
        for key, status in stage_updates.items():
            if key not in stage_map:
                raise ValueError(f"Unknown stage key: {key}")
            stage_map[key] = status
            if status == StageStatus.PENDING:
                self.refresh_stage_retry_budget(ticket, key)
        instance.stages_json = serialize_stage_map(
            stage_map, stages, notes=parse_stage_notes(instance)
        )
        if auto_state:
            self._reconcile_workflow(ticket, instance, stages)

    def _stage_start_clears_budget(
        self,
        ticket: Ticket,
        target_key: str,
        stage_map: dict[str, StageStatus],
    ) -> bool:
        """Whether `_prepare_stage_start` is about to hand this stage a fresh
        dispatch budget — a re-entry of a stage *this breaker* blocked.

        Read separately from the write so `start_run` can settle the retry
        budget before `_prepare_stage_start` mutates anything: the dispatch that
        clears the block is the first of the new budget, not a refusal.

        The same answer for an orchestrated start as for a standalone one. It
        was once unconditional for the orchestrated path, on the premise that
        reaching BLOCKED is a human act — but `loregarden_block_ticket` is
        granted to every agent and is not denied under orchestration, so an
        agent at its budget can block itself (`callbacks.block_ticket` sets
        ticket *and* stage BLOCKED) and restart orchestration to wipe the
        counter that was bounding it. Only the breaker's own block earns a
        reset, whoever asks for the start.
        """
        if ticket.state != TicketState.BLOCKED and stage_map.get(target_key) != StageStatus.BLOCKED:
            return False
        return blocked_on_stage_retry_budget(self.session, ticket, target_key)

    def _prepare_stage_start(
        self,
        ticket: Ticket,
        target_key: str,
        stage_map: dict[str, StageStatus],
        *,
        clear_budget: bool,
    ) -> None:
        """Clear stale blocking text and restore budget when re-entering a blocked stage.

        ``clear_budget`` is decided by the caller, *before* this runs: the
        blocking text is one of the inputs to that decision and the first thing
        this method erases.
        """
        # Starting a stage is a fresh attempt — drop any stale blocking message
        # left over from a prior failure. Without this, a stage that was left
        # PENDING (not BLOCKED) after an earlier failure elsewhere carries its
        # old blocking_issues text forward; the moment this run marks it
        # RUNNING, reconcile_workflow_state sees non-empty blocking_issues and
        # misreports the ticket as BLOCKED before anything has actually failed
        # in this attempt.
        ticket.blocking_issues = ""
        # Human Continue / Re-run of a blocked stage must restore a full
        # dispatch budget. Do not clear on ordinary in-loop starts — parallel
        # members and self-reroutes would wipe the counter the breaker just
        # recorded for this pass.
        if ticket.state == TicketState.BLOCKED or stage_map.get(target_key) == StageStatus.BLOCKED:
            # Only the breaker's own block earns a fresh counter, on every path.
            # A stage blocked for another reason — an interrupted run, a failing
            # gate, an agent's own `loregarden_block_ticket` — is still unblocked
            # here, but keeps its dispatch count: wiping it would make the
            # counter unable to accumulate across exactly the re-runs it exists
            # to bound. See `_stage_start_clears_budget` for why the orchestrated
            # path no longer resets unconditionally.
            self.refresh_stage_retry_budget(ticket, target_key, clear_dispatches=clear_budget)

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
            self._reconcile_workflow(ticket, instance, stages)
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

    def finalize_workflow(self, ticket: Ticket, *, force: bool = False) -> Ticket:
        """Mark the terminal done stage complete and close out the ticket.

        ``force`` skips only the "advance to the Done stage first" precondition —
        used to finish an aggregator parent that intentionally never ran its own
        stages (settle_unreached_stages below marks those still-PENDING stages
        WONT_DO). The terminal-state and RUNNING/AWAITING guards still apply.
        """
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
            not force
            and current
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
        self._reconcile_workflow(ticket, instance, stages)
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

    def _reject_if_ticket_busy(self, ticket: Ticket, target_key: str) -> None:
        """Refuse to start when something is already in flight on this ticket.

        Re-entering the stage that is already RUNNING is the one exception —
        that is a parallel member or a re-dispatch of the same stage.
        """
        if ticket.workflow_stage_status in (StageStatus.RUNNING, StageStatus.AWAITING):
            if not (
                ticket.workflow_stage_status == StageStatus.RUNNING
                and target_key == ticket.workflow_stage_key
            ):
                raise ValueError("Current stage must complete before advancing")

        self._reject_if_triage_active(ticket)

    def _resolve_run_target(self, ticket: Ticket, stage_key: str | None) -> _RunTarget:
        """Resolve the stage a run would start on, refusing if it may not start.

        The checks run in this order deliberately: an earlier guard shadows a
        later one, so the caller sees the first reason the run cannot start.
        """
        self.ensure_workflow_instance(ticket, commit=True)
        instance, stages = self._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        target_key = stage_key or ticket.workflow_stage_key
        if not target_key:
            target_key = StateMachine.next_stage_key(stages, "")
            if not target_key:
                raise ValueError("Workflow has no stages")

        self._reject_if_ticket_busy(ticket, target_key)

        stage_def = next((s for s in stages if s.key == target_key), None)
        if not stage_def:
            raise ValueError(f"Unknown stage key: {target_key}")

        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(target_key) == StageStatus.WONT_DO:
            raise ValueError(f"Stage '{target_key}' is marked won't do")

        return _RunTarget(
            instance=instance,
            stages=stages,
            target_key=target_key,
            stage_def=stage_def,
            stage_map=stage_map,
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
        force: bool = False,
        dispatch_surface: DispatchSurface = DispatchSurface.HTTP,
    ) -> AgentRun:
        template = self.get_template_for_ticket(ticket)
        if not template:
            raise ValueError("No workflow template for ticket workspace")

        target = self._resolve_run_target(ticket, stage_key)
        instance = target.instance
        target_key = target.target_key

        # Both refusals are settled before a single write. `_prepare_stage_start`
        # clears blocking text and can flip the ticket to IN_PROGRESS, and a
        # guard that raised after it returned a 409 having already erased the
        # operator's blocking diagnosis and left the ticket started.
        if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
            raise ValueError(f"Cannot start run for ticket in state: {ticket.state.value}")

        # Read before `_prepare_stage_start`, which erases the blocking text this
        # decision is partly read from.
        budget_reset_pending = self._stage_start_clears_budget(ticket, target_key, target.stage_map)

        # A dispatch with no orchestration run behind it was counted by nobody:
        # the orchestrator loop records its own pass through
        # `enforce_stage_retry_budget` before it ever calls here, so recording
        # again for an orchestrated run would cost every pass two attempts and
        # halve the budget.
        decision = None
        if not orchestration_run_id:
            decision = evaluate_standalone_stage_dispatch(
                self.session,
                ticket,
                target_key,
                stage_already_running=target.stage_map.get(target_key) == StageStatus.RUNNING,
                force=force,
                # `_prepare_stage_start` below may hand this stage a fresh
                # budget. When it will, this dispatch is the first of that new
                # budget rather than one refused against the old one.
                budget_reset_pending=budget_reset_pending,
            )

        self._prepare_stage_start(
            ticket,
            target_key,
            target.stage_map,
            clear_budget=budget_reset_pending,
        )

        # Resolved here rather than after the commit block for two reasons.
        # The attribution below needs the agent that will actually run, and
        # `ticket.next_agent` is not it — the pin is empty for most of a
        # ticket's life, so an audit row that named it recorded "" for the
        # agent whose forced dispatch it exists to attribute. And
        # `_resolve_run_agent` raises on a stage that resolves no agent: doing
        # that after the commit charged the stage a dispatch it never spent.
        #
        # Safe to move because `_prepare_stage_start` above does not touch
        # `next_agent`, so nothing between the old and new position changes what
        # this resolves to.
        chosen_agent, chosen_skill = _resolve_run_agent(
            ticket, target.stage_def, agent_id=agent_id, skill_name=skill_name
        )

        # After the reset, so a cleared counter is refilled by this dispatch.
        if decision is not None:
            commit_standalone_stage_dispatch(
                self.session,
                ticket.id,
                target_key,
                decision,
                origin=DispatchOrigin(
                    surface=dispatch_surface,
                    agent_id=chosen_agent,
                ),
            )

        if ticket.state == TicketState.BACKLOG:
            self.start_ticket(ticket)
            self.session.refresh(ticket)
            instance = self.get_workflow_instance(ticket.id) or instance

        _consume_scope_reroute_pin(ticket, chosen_agent)

        ticket.workflow_stage_key = target_key
        if target.stage_map.get(target_key) != StageStatus.RUNNING:
            set_stage_status(ticket, instance, target.stages, target_key, StageStatus.RUNNING)
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

        # The run's terminal status is durable as of the commit above; everything
        # below needs the ticket. That split is what stranded a stage once: the run
        # reached FAILED, loading the ticket then raised, and the stage stayed
        # RUNNING with nothing alive behind it — invisible to the reaper, which only
        # looks at in-flight runs. So a failure here is logged and left for
        # `settle_stranded_stages` rather than escaping into the executor. It is not
        # recoverable in-process: a ticket whose row will not load cannot be settled
        # through the ORM at all.
        try:
            return complete_run_tail(
                self,
                run,
                status=status,
                stdout=stdout,
                stderr=stderr,
                artifacts=artifacts,
                advance_workflow=advance_workflow,
            )
        except Exception as exc:
            self.session.rollback()
            logger.exception(
                "Run %s recorded as %s but completing it raised; attempting to settle "
                "its stage directly",
                run.id,
                status.value,
            )
            settle_stage_after_failed_completion(self, run, exc)
            return run
        finally:
            # The run is terminal either way, so the slot it held is free either
            # way. In the tail it was reachable only when nothing above it
            # failed, and every leaked slot took a lane off the board for good.
            release_execution_slot(self, run)

    def auto_resolve_gate_approval(self, approval: Approval, run: AgentRun) -> None:
        """Pre-resolve a gate approval raised under auto_approve.

        Lives here rather than in ``run_completion`` only because ``ApprovalService``
        is defined in this module; importing it there would close a cycle.
        """
        ApprovalService(self.session).auto_resolve(
            approval.id, orchestration_run_id=run.orchestration_run_id or ""
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
        checklist = expand_gate_checklist_for_ticket(
            self.session, ticket, list(stage_def.checklist) if stage_def else []
        )
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
            record_home_chat_question_exchange(
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
            # Ticket/stage allow rules need a work item; Home chat approvals are
            # workspace-scoped and can only persist always_allow.
            if allow_for_ticket and approval.ticket_id:
                add_ticket_allow_rule(
                    self.session,
                    approval.ticket_id,
                    approval.tool_name,
                    tool_input,
                )
            if allow_for_stage and approval.ticket_id and approval.stage_key:
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

        ticket = self.session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
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

    def auto_resolve(self, approval_id: str, *, orchestration_run_id: str) -> Approval:
        """Resolve a WORKFLOW_GATE approval on behalf of an auto_approve run.

        Unlike `resolve()`, this never schedules a background resume — the
        caller (BuiltinOrchestrator) is already mid-loop and continues the
        same `execute()` pass synchronously. The row is still created/kept
        (never skipped) and marked `resolved_by="automation"` with the
        resolving run id, so an auto-approved gate stays distinguishable from
        a human sign-off in the approvals table.
        """
        approval = self.session.get(Approval, approval_id)
        if not approval:
            raise ValueError("Approval not found")
        if approval.status != ApprovalStatus.PENDING:
            return approval
        if approval.kind != ApprovalKind.WORKFLOW_GATE:
            raise ValueError("auto_resolve only applies to workflow-gate approvals")

        approval.status = ApprovalStatus.APPROVED
        approval.resolved_at = datetime.now(timezone.utc)
        approval.resolved_by = "automation"
        approval.resolving_orchestration_run_id = orchestration_run_id
        self.session.add(approval)
        self.session.commit()

        ticket = self.session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
        if ticket:
            self._apply_gate_resolution(
                ticket,
                approval,
                approved=True,
                rework_route_key="",
                response_text="",
                resume=False,
            )

        event_bus.publish(
            self.session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=approval.workspace_id,
            ticket_id=approval.ticket_id,
            payload={"approval_id": approval.id, "approved": True, "automated": True},
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
        resume: bool = True,
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

        if approved and resume:
            self._resume_orchestration(ticket)

    def _resume_orchestration(self, ticket: Ticket) -> None:
        """Carry on after an approval, rather than waiting to be told.

        Approving a gate leaves the ticket pointing at a stage ready to run, so
        asking the operator to then press Run is a second decision carrying no
        information beyond the first. Both approval shapes resume: a plain
        approval and an approve-with-rework reroute.

        A rejection deliberately does not. It means more work is needed, and the
        operator may want to add guidance — or steer the stage once it starts —
        before anything spends tokens on the rework.

        `auto_approve` is carried over from the run that reached the gate.
        Resuming without it would silently downgrade an unattended run into one
        that stops at the next tool prompt.
        """
        if find_active_orchestration_run(self.session, ticket.id):
            return

        previous = self.session.exec(
            select(OrchestrationRun)
            .where(OrchestrationRun.ticket_id == ticket.id)
            .order_by(OrchestrationRun.created_at.desc())
        ).first()
        schedule_orchestration(ticket.id, auto_approve=bool(previous and previous.auto_approve))

    def list_pending(self) -> list[Approval]:
        return list(
            self.session.exec(
                select(Approval).where(Approval.status == ApprovalStatus.PENDING)
            ).all()
        )
