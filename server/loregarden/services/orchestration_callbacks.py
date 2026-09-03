"""Orchestration callback operations — shared by REST API, MCP, and builtin driver."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    DispatchSurface,
    EventType,
    ExternalHarness,
    HumanActionTier,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    StageVerdictChannel,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_service import record_blocking_issue
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.prepared_action import (
    PreparedAction,
    assess_handover,
)
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.run_concurrency import find_active_orchestration_run, find_active_run
from loregarden.services.run_interruption import ORPHAN_OF_TERMINAL_ORCH_MESSAGE
from loregarden.services.run_lease import SUPERVISED
from loregarden.services.stage_retry_budget import (
    DispatchOrigin,
    guard_standalone_stage_dispatch,
)
from loregarden.services.studio_routing import is_prunable_stage, prunable_stage_keys
from loregarden.services.ticket_discovery import looks_like_ticket_uuid
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_state_service import choose
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from loregarden.services.worktree_lifecycle import release_ticket_worktree
from sqlmodel import Session, col, select


def _orch_code() -> str:
    return f"orch_{secrets.token_hex(3)}"


#: Phrases a stage report uses when it is asking for a person rather than
#: reporting a fault. Deliberately a small, literal list: this only decides
#: whether a block also lands in the inbox as an action, and a false negative
#: leaves today's behaviour exactly as it was.
_HUMAN_WORK_MARKERS = (
    "a human",
    "an operator",
    "human/operator",
    "manually",
    "by hand",
    "needs a person",
    "requires a person",
)


def _looks_like_human_work(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _HUMAN_WORK_MARKERS)


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
                    ticket = resolve_external_id(self.session, external_id, workspace_id=ws.id)
                    if ticket:
                        return ticket
            # Unscoped: the workspace prefix in a structured id names the workspace,
            # so this is only ambiguous for the pre-restructure ids, which is what
            # it was before.
            ticket = resolve_external_id(self.session, external_id)
            if ticket:
                return ticket

        raise ValueError("Ticket not found")

    def get_active_orchestration_run(self, ticket_id: str) -> OrchestrationRun | None:
        """The run in flight for this ticket. Delegates to `run_concurrency`."""
        return find_active_orchestration_run(self.session, ticket_id)

    def claim_orchestration_run(
        self,
        ticket: Ticket,
        *,
        driver=None,
        profile_slug: str = "",
        auto_approve: bool = False,
        stop_at_stage_key: str = "",
        timeout_override_seconds: int | None = None,
    ) -> OrchestrationRun:
        """Reserve this ticket's orchestration before anything executes it.

        `schedule_orchestration` hands the work to a background thread, and the
        run row is created *there* — so a caller that dispatches and then reads
        the row back to bind to it is racing that thread, and loses. Two things
        went wrong that way: an admission reservation never bound, leaving a
        slot claimed but naming nothing that any release could find; and a lane
        read no run, concluded its dispatch had been refused, and left its entry
        queued while the orchestration ran outside any lane.

        Claiming here closes the window — the caller has an id before a thread
        exists — and `start_orchestration_run` adopts this row rather than
        opening a second one.
        """
        active = self.get_active_orchestration_run(ticket.id)
        if active:
            return active

        if driver is None or not profile_slug:
            # Resolved here so both callers stay one line; an explicit driver
            # (an API or MCP override) still wins over the workspace default.
            from loregarden.services.orchestration_profile import resolve_orchestration_profile

            workspace = self.session.get(Workspace, ticket.workspace_id)
            if not workspace:
                raise ValueError("Workspace not found")
            profile = resolve_orchestration_profile(workspace)
            driver = driver or profile.driver
            profile_slug = profile_slug or profile.slug

        run = OrchestrationRun(
            run_code=_orch_code(),
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            driver=driver,
            profile_slug=profile_slug,
            status=OrchestrationRunStatus.QUEUED,
            current_stage_key=ticket.workflow_stage_key,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
            timeout_override_seconds=timeout_override_seconds,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def touch_lease(self, run: OrchestrationRun) -> None:
        """Renew a run's lease. Called by every write that names it.

        The lease is what lets `_occupant_is_live` stop trusting `status`, which
        only the run's own owner ever moves — so a harness that walked away held
        its lane permanently rather than until restart. Renewing it here means
        the work itself vouches for the run: a slow-but-live session keeps its
        lane with no human in the loop, and an abandoned one stops being
        indistinguishable from a busy one.
        """
        run.last_seen_at = datetime.now(timezone.utc)
        self.session.add(run)

    def _finish_orchestration_run(
        self,
        run: OrchestrationRun,
        *,
        status: OrchestrationRunStatus,
        message: str = "",
    ) -> None:
        """Put an orchestration run into a terminal status and give its lane back.

        The single exit for every terminal status. A lane is held for the life of
        an orchestration, so reaching a terminal status and releasing the lane are
        one transition, not two things a caller is trusted to remember — and the
        one caller that forgot (`block_ticket`) left the lane entry reading ACTIVE
        for as long as the row existed. Nothing selects a terminal orchestration
        again, so that residue is permanent, and `ticket_activity` reports the
        ticket as running from then on however finished it is.

        Callers mutate the ticket first and let the commit here carry it; the
        run's own terminal fields are set here so no caller can set a status
        without the release. In-flight child agent runs are failed in the same
        turn: leaving them ``RUNNING`` is how a finished ticket kept a Running
        badge on the home board after its lane was already empty.
        """
        run.status = status
        run.error_message = message[:2000]
        run.finished_at = datetime.now(timezone.utc)
        self.session.add(run)
        self.session.commit()
        self._fail_in_flight_children(run)
        self._release_execution_lane(run)

    def _fail_in_flight_children(self, orch_run: OrchestrationRun) -> None:
        """Fail leftover agent runs so they stop reading as live work."""
        children = self.session.exec(
            select(AgentRun)
            .where(AgentRun.orchestration_run_id == orch_run.id)
            .where(col(AgentRun.status).in_(list(SUPERVISED)))
        ).all()
        for child in children:
            self.orch.complete_run(
                child,
                status=RunStatus.FAILED,
                stderr=ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
                advance_workflow=False,
            )

    def abandon_claim(self, run: OrchestrationRun, *, message: str) -> None:
        """Fail a claim nothing will adopt, so it stops looking live.

        A claim is only a promise that work is about to start. When the dispatch
        it was made for refuses, leaving it QUEUED would block every later start
        of this ticket on an orchestration that never began.
        """
        self._finish_orchestration_run(run, status=OrchestrationRunStatus.FAILED, message=message)

    def start_orchestration_run(
        self,
        ticket: Ticket,
        *,
        driver,
        profile_slug: str,
        auto_approve: bool = False,
        stop_at_stage_key: str = "",
        timeout_override_seconds: int | None = None,
        external_harness: ExternalHarness | None = None,
    ) -> OrchestrationRun:
        # Starting work is what earns a ticket its workflow instance. This used
        # to happen on the read path — `GET /api/tickets` created one per row as
        # a side effect of serializing — so a ticket only had a cursor once
        # somebody had looked at it. Removing that write (606) put the
        # initialisation here, where a write belongs.
        self.orch.ensure_workflow_instance(ticket, commit=True)

        active = self.get_active_orchestration_run(ticket.id)
        if active and active.status == OrchestrationRunStatus.QUEUED:
            # The claim this execution was dispatched for. Adopt it, so whoever
            # bound a slot to that id follows the work it stands for.
            return self._adopt_claim(
                active,
                ticket,
                driver=driver,
                profile_slug=profile_slug,
                timeout_override_seconds=timeout_override_seconds,
                external_harness=external_harness,
            )
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
                external_harness=external_harness,
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
            external_harness=external_harness,
            current_stage_key=ticket.workflow_stage_key,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
            timeout_override_seconds=timeout_override_seconds,
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

    def _adopt_claim(
        self,
        run: OrchestrationRun,
        ticket: Ticket,
        *,
        driver,
        profile_slug: str,
        timeout_override_seconds: int | None = None,
        external_harness: ExternalHarness | None = None,
    ) -> OrchestrationRun:
        """Turn a claim into the running orchestration it stood for.

        `auto_approve`, `stop_at_stage_key` and the timeout stay the claim's
        when already set: they were answered by whoever queued the ticket.
        """
        if ticket.state == TicketState.BACKLOG:
            self.orch.start_ticket(ticket)
            self.session.refresh(ticket)

        if timeout_override_seconds is not None and run.timeout_override_seconds is None:
            run.timeout_override_seconds = timeout_override_seconds
        run.status = OrchestrationRunStatus.RUNNING
        run.driver = driver
        run.profile_slug = profile_slug
        run.external_harness = external_harness
        run.current_stage_key = ticket.workflow_stage_key
        run.started_at = datetime.now(timezone.utc)
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
        force: bool = False,
    ) -> Ticket:
        self.touch_lease(orch_run)
        # Same reason as start_orchestration_run: a stage started directly on an
        # untouched ticket is still the moment work begins, and nothing on the
        # read path sets this up any more (606).
        self.orch.ensure_workflow_instance(ticket, commit=True)
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        # Which channel carried the verdict, observed rather than inferred. The
        # alternative — deducing "the tool was used" from the stage having moved
        # without a sentinel — is the kind of reasoning that produced this
        # milestone's wrong adherence numbers (lg-workflow-integrity-95).
        active = find_active_run(self.session, ticket.id)
        if active is not None and active.stage_key == stage_key:
            active.verdict_channel = StageVerdictChannel.TOOL
            self.session.add(active)

        if stage_key not in parse_stage_map(instance, stages):
            raise ValueError(f"Unknown stage key: {stage_key}")

        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(stage_key) == StageStatus.WONT_DO:
            raise ValueError(f"Stage '{stage_key}' is marked won't do")

        # This path never reaches `start_run` and creates no AgentRun, so it is
        # counted by neither the orchestrator loop nor the run seam: it needs its
        # own check site. Before the first write, or a refusal would leave the
        # stage RUNNING with nothing behind it — and on this path there is no
        # AgentRun to make that visible.
        guard_standalone_stage_dispatch(
            self.session,
            ticket,
            stage_key,
            stage_already_running=stage_map.get(stage_key) == StageStatus.RUNNING,
            force=force,
            origin=DispatchOrigin(
                surface=DispatchSurface.MCP,
                orchestration_run_id=orch_run.id,
                agent_id=agent_id,
            ),
        )

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
        self.touch_lease(orch_run)
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        ticket.revision += 1
        ticket.last_updated_by = "orchestrator"

        short_blocking_issues = record_blocking_issue(
            self.session,
            ticket,
            run_id=None,
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
        """Declare a stage won't-do for this ticket, with the reason on record.

        Only a stage the template marks prunable may be skipped, and only before
        it has run — see `is_prunable_stage` for why the allowance is the
        template's to give. The reason is stored as that stage's note rather
        than on `ticket.blocking_issues`: the latter is read by
        `_derive_ticket_state`, so explaining a skip used to flip the ticket to
        BLOCKED whenever the caller's own stage was still RUNNING.
        """
        self.touch_lease(orch_run)
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            raise ValueError("Ticket has no workflow instance")

        stage_map = parse_stage_map(instance, stages)
        if stage_key not in stage_map:
            raise ValueError(f"Unknown stage key: {stage_key}")
        stage_def = next(s for s in stages if s.key == stage_key)

        if not is_prunable_stage(stage_def):
            offered = prunable_stage_keys(stages)
            hint = ", ".join(f"'{key}'" for key in offered) if offered else "(none)"
            raise ValueError(
                f"Stage '{stage_key}' is required by this workflow and cannot be skipped. "
                f"Skippable stages: {hint}"
            )

        current = stage_map[stage_key]
        if current == StageStatus.RUNNING:
            raise ValueError(
                f"Stage '{stage_key}' is running — report its outcome with complete_stage "
                "instead of skipping it"
            )
        if current == StageStatus.DONE:
            raise ValueError(f"Stage '{stage_key}' already ran and cannot be skipped")

        set_stage_status(
            ticket,
            instance,
            stages,
            stage_key,
            StageStatus.WONT_DO,
            note=reason[:500],
        )
        # A cursor left on the stage just pruned reads as "we are here" in the
        # workflow pane, on a stage nothing will ever run. Move it to the first
        # stage still open; reconcile settles the rest.
        if ticket.workflow_stage_key == stage_key:
            resolved = (StageStatus.DONE, StageStatus.WONT_DO)
            stage_map = parse_stage_map(instance, stages)
            for stage in sorted(stages, key=lambda s: s.order):
                if stage_map.get(stage.key) not in resolved:
                    ticket.workflow_stage_key = stage.key
                    ticket.workflow_stage_status = stage_map[stage.key]
                    break
        ticket.revision += 1
        orch_run.current_stage_key = stage_key
        self.session.add(ticket)
        self.session.add(instance)
        self.session.add(orch_run)
        self.session.commit()

        event_bus.publish(
            self.session,
            EventType.STAGE_SKIPPED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={
                "stage_key": stage_key,
                "orchestration_run_id": orch_run.id,
                "reason": reason,
            },
        )
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
        prepared_action: PreparedAction | None = None,
    ) -> Ticket:
        self.touch_lease(orch_run)
        instance, stages = self.orch._resolve_stages(ticket)
        key = stage_key or ticket.workflow_stage_key
        if instance and stages and key:
            set_stage_status(ticket, instance, stages, key, StageStatus.BLOCKED)
            self.session.add(instance)
        # Chosen, not derived: something decided this ticket cannot proceed.
        choose(self.session, ticket, TicketState.BLOCKED, actor="orchestrator", emit=False)
        ticket.blocking_issues = record_blocking_issue(
            self.session,
            ticket,
            run_id=None,
            stage_key=key or "",
            message=message,
        )
        ticket.next_status = "Blocked"
        if key:
            orch_run.current_stage_key = key
        self.session.add(ticket)
        self._finish_orchestration_run(
            orch_run, status=OrchestrationRunStatus.BLOCKED, message=message
        )
        self.record_human_action(
            ticket, stage_key=key or "", message=message, prepared_action=prepared_action
        )
        return ticket

    def record_human_action(
        self,
        ticket: Ticket,
        *,
        stage_key: str,
        message: str,
        prepared_action: PreparedAction | None,
    ) -> Approval | None:
        """Put a human-gated block in the inbox as an action, not a paragraph.

        Only for blocks that name human work. A block with no prepared action
        still reaches the inbox — otherwise the ladder would be enforced by
        making badly-described work invisible, which strands it — but it arrives
        carrying `assess_handover`'s findings, so what it failed to prepare is
        stated rather than left for a person to notice
        (lg-workflow-integrity-460).
        """
        if prepared_action is None and not _looks_like_human_work(message):
            return None

        assessment = assess_handover(message=message, action=prepared_action)
        action = prepared_action or PreparedAction(tier=HumanActionTier.MANUAL)
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.HUMAN_ACTION,
            title=f"Human action — {ticket.title}",
            level="medium",
            stage_key=stage_key,
            impact=message,
            tool_name=action.command,
            tool_input_json=action.model_dump_json(),
            checklist_json=json.dumps(assessment.findings),
            status=ApprovalStatus.PENDING,
        )
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

    def attach_artifact(
        self,
        ticket: Ticket,
        *,
        kind: str,
        title: str,
        content: dict,
        run_id: str | None = None,
        evidence_kind: str = "",
        commit_sha: str = "",
    ) -> Artifact:
        artifact = Artifact(
            ticket_id=ticket.id,
            run_id=run_id,
            kind=kind,
            title=title,
            content_json=json.dumps(content),
            evidence_kind=evidence_kind,
            commit_sha=commit_sha,
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

    def _release_execution_lane(self, orch_run) -> None:
        _release_execution_lane_impl(self.session, orch_run)

    def complete_orchestration(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        status: OrchestrationRunStatus,
        message: str = "",
    ) -> OrchestrationRun:
        if status == OrchestrationRunStatus.SUCCEEDED and ticket.state not in (
            TicketState.DONE,
            TicketState.WONT_DO,
        ):
            instance, stages = self.orch._resolve_stages(ticket)
            if instance and stages:
                self.orch.reconcile_ticket(ticket)
                self.session.refresh(ticket)
        self.session.add(ticket)
        self._finish_orchestration_run(orch_run, status=status, message=message)
        # A finished ticket's tree has nothing left to run in it, and leaving
        # it costs a directory and a branch checkout that blocks the next one.
        release_ticket_worktree(self.session, ticket)
        event_bus.publish(
            self.session,
            EventType.ORCHESTRATION_RUN_COMPLETED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload={"run_code": orch_run.run_code, "status": status.value},
        )
        return orch_run


logger = logging.getLogger(__name__)


def _release_execution_lane_impl(session, orch_run) -> None:
    """Give the queue lane back once the whole pipeline is done.

    A lane runs a ticket, not a stage, so it is held for the life of this
    orchestration and released here rather than when any single agent run
    finishes. Whatever was queued behind it in that lane starts next.

    Best-effort: the orchestration's terminal status is already committed, and
    a lane that fails to release is recoverable — a lost completion is not.
    """
    try:
        QueueLaneService(session).on_orchestration_complete(orch_run.id)
    except Exception:
        logger.warning(
            "Failed to release the execution lane for orchestration %s",
            orch_run.id,
            exc_info=True,
        )
