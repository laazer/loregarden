"""Keep ticket state, workflow pointer, and per-stage statuses consistent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.core.stage_groups import emptied_groups
from loregarden.models.domain import (
    ParallelAgentSpec,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowStageView,
)
from loregarden.services.ticket_state_service import derive


def initial_stages_json(stages: list[WorkflowStageDef]) -> str:
    ordered = sorted(stages, key=lambda s: s.order)
    return json.dumps([{"key": s.key, "status": StageStatus.PENDING.value} for s in ordered])


def stages_up_to_done_json(stages: list[WorkflowStageDef], completed_key: str) -> str:
    """Mark every stage up to and including completed_key as done; rest pending."""
    ordered = sorted(stages, key=lambda s: s.order)
    keys = [s.key for s in ordered]
    try:
        done_idx = keys.index(completed_key)
    except ValueError:
        done_idx = -1
    payload = []
    for i, stage in enumerate(ordered):
        status = StageStatus.DONE if i <= done_idx else StageStatus.PENDING
        payload.append({"key": stage.key, "status": status.value})
    return json.dumps(payload)


def parse_stage_map(
    instance: WorkflowInstance, stages: list[WorkflowStageDef]
) -> dict[str, StageStatus]:
    ordered = sorted(stages, key=lambda s: s.order)
    raw = json.loads(instance.stages_json or "[]")
    by_key = {item["key"]: StageStatus(item["status"]) for item in raw}
    return {s.key: by_key.get(s.key, StageStatus.PENDING) for s in ordered}


def parse_stage_notes(instance: WorkflowInstance) -> dict[str, str]:
    """Per-stage notes recorded alongside the statuses, keyed by stage key.

    Notes are why a stage ended up where it did — chiefly why it was pruned to
    WONT_DO. They live in the same rows as the statuses, so any writer that
    reserialises the map has to carry them forward or they are lost; that is why
    `serialize_stage_map` takes them as a required argument.
    """
    raw = json.loads(instance.stages_json or "[]")
    return {item["key"]: item.get("note", "") for item in raw if item.get("note")}


def serialize_stage_map(
    stage_map: dict[str, StageStatus],
    stages: list[WorkflowStageDef],
    *,
    notes: dict[str, str],
) -> str:
    """Render the stage map back to `stages_json`.

    `notes` is required rather than defaulted: it used to be absent entirely,
    so every status write silently erased the notes `build_stage_views` reads.
    Callers preserving existing notes pass `parse_stage_notes(instance)`.
    """
    ordered = sorted(stages, key=lambda s: s.order)
    payload: list[dict[str, str]] = []
    for stage in ordered:
        if stage.key not in stage_map:
            continue
        row = {"key": stage.key, "status": stage_map[stage.key].value}
        note = notes.get(stage.key, "")
        if note:
            row["note"] = note
        payload.append(row)
    return json.dumps(payload)


def next_executable_stage(
    stages: list[WorkflowStageDef], stage_map: dict[str, StageStatus]
) -> str | None:
    """Pick the next stage a driver should run: earliest-in-template-order wins.

    Every driver shares this one picker. Advancing a run settles the *stage
    instance* (``set_stage_status``) and deliberately leaves
    ``ticket.workflow_stage_key`` on the stage that just finished — choosing what
    runs next is the driver's job, not the completion path's. A driver that reads
    the cursor instead of calling this re-serves the finished stage forever.

    The cursor is not a shortcut here for a second reason: a stage can be re-run
    on its own (the workflow-lifecycle UI has a Run/Re-Run button per stage),
    which leaves an earlier stage PENDING while the cursor already points at a
    later one. Trusting the cursor in that state silently skips the earlier,
    still-unresolved stage. Always scanning in order means a cursor that is
    "ahead" self-heals on the next pass instead of compounding.

    Returns ``None`` when nothing may run — every stage resolved, or one is
    BLOCKED, which stops the workflow rather than being skipped past.
    """
    ordered = sorted(stages, key=lambda s: s.order)
    keys = [s.key for s in ordered]

    for status in (StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED):
        for key in keys:
            if stage_map.get(key) == status:
                if status == StageStatus.BLOCKED:
                    return None
                return key

    for key in keys:
        if stage_map.get(key) == StageStatus.PENDING:
            return key
    return None


def _stage_resolved(status: StageStatus) -> bool:
    return status in (StageStatus.DONE, StageStatus.WONT_DO)


def _cursor_stage(
    ticket: Ticket,
    stage_map: dict[str, StageStatus],
    stages: list[WorkflowStageDef],
) -> tuple[str, StageStatus]:
    ordered = sorted(stages, key=lambda s: s.order)
    keys = [s.key for s in ordered]

    for status in (StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED):
        for key in keys:
            if stage_map.get(key) == status:
                return key, status

    key = ticket.workflow_stage_key
    if key and key in stage_map:
        return key, stage_map[key]

    for key in keys:
        if stage_map.get(key) == StageStatus.PENDING:
            return key, StageStatus.PENDING

    last = keys[-1] if keys else ""
    return last, stage_map.get(last, StageStatus.DONE) if last else StageStatus.PENDING


def _derive_ticket_state(
    stage_map: dict[str, StageStatus],
    stages: list[WorkflowStageDef],
    *,
    blocking_issues: str,
    workflow_stage_key: str,
    workflow_stage_status: StageStatus,
) -> TicketState:
    statuses = list(stage_map.values())
    if any(s == StageStatus.BLOCKED for s in statuses):
        return TicketState.BLOCKED
    if blocking_issues and workflow_stage_status in (
        StageStatus.BLOCKED,
        StageStatus.RUNNING,
        StageStatus.AWAITING,
    ):
        return TicketState.BLOCKED

    ordered = sorted(stages, key=lambda s: s.order)
    required = [s for s in ordered if not s.optional]
    if (
        required
        and all(_stage_resolved(stage_map.get(s.key, StageStatus.PENDING)) for s in required)
        # An alternative group with every member pruned is unfinished work that
        # the `required` filter above cannot see: members must be `optional` to
        # be prunable at all, so they are never in that list. Without this the
        # ticket derives DONE having implemented nothing. IN_PROGRESS, not
        # BLOCKED — deriving state is a recomputation, not a decision, and the
        # refusal in `skip_stage` is where the decision belongs.
        and not emptied_groups(stages, stage_map)
    ):
        return TicketState.DONE

    if any(
        s
        in (
            StageStatus.RUNNING,
            StageStatus.AWAITING,
            StageStatus.BLOCKED,
            StageStatus.DONE,
            StageStatus.WONT_DO,
        )
        for s in statuses
    ):
        return TicketState.IN_PROGRESS

    return TicketState.BACKLOG


@dataclass(frozen=True)
class WorkflowDerivation:
    """What a ticket's workflow *would* say, computed without writing it.

    `reconcile_workflow_state` mutates the ticket and instance it is handed —
    `persist` only decides whether the caller commits, not whether anything
    changed in memory. That is fine for a writer and wrong for a reader: the
    ticket list reached it once per row, and the dirty objects were committed
    per row (lg-workflow-integrity-606).

    Splitting the derivation out lets a serializer ask the same question without
    answering it in the database.
    """

    stage_map: dict[str, StageStatus]
    current_key: str
    current_status: StageStatus
    ticket_state: TicketState


def derive_workflow(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
) -> WorkflowDerivation:
    """The reconciled view of this workflow. Pure — nothing is assigned."""
    stage_map = parse_stage_map(instance, stages)
    current_key, current_status = _cursor_stage(ticket, stage_map, stages)
    ticket_state = _derive_ticket_state(
        stage_map,
        stages,
        blocking_issues=ticket.blocking_issues,
        workflow_stage_key=current_key,
        workflow_stage_status=current_status,
    )
    return WorkflowDerivation(
        stage_map=stage_map,
        current_key=current_key,
        current_status=current_status,
        ticket_state=ticket_state,
    )


def reconcile_workflow_state(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    persist: bool = True,
    owns_state: bool = True,
) -> dict[str, StageStatus]:
    """Align stages_json, ticket workflow fields, and ticket.state.

    ``owns_state=False`` keeps the stage bookkeeping and skips the one write
    that a parent must not receive. A ticket with children carries no work of
    its own — nothing ever runs its stages, so they sit at ``triage/pending``
    and derive ``backlog`` forever. With both writers live, whichever ran last
    won: the rollup would summarise a finished subtree as `done`, and the next
    plain `GET /api/tickets/{id}` would derive `backlog` from those untouched
    stages and write it straight back. Callers holding a session pass
    ``owns_state=not has_children(...)``; the default suits a leaf, which is
    what this module was written for.
    """
    derived = derive_workflow(ticket, instance, stages)
    stage_map = derived.stage_map
    current_key, current_status = derived.current_key, derived.current_status
    ticket_state = derived.ticket_state

    ticket.workflow_stage_key = current_key
    ticket.workflow_stage_status = current_status
    # Sticky done: a workflow that no longer derives DONE (a reopened stage, a
    # template change) must not silently un-finish a ticket. `derive` owns the
    # state_locked / wont_do guards and the revision bookkeeping.
    sticky_done = ticket.state == TicketState.DONE and ticket_state != TicketState.DONE
    if owns_state and not sticky_done:
        derive(ticket, ticket_state, actor="workflow")
    ticket.updated_at = datetime.now(timezone.utc)

    instance.current_stage_key = current_key
    instance.stages_json = serialize_stage_map(stage_map, stages, notes=parse_stage_notes(instance))
    instance.updated_at = datetime.now(timezone.utc)

    # `next_agent` is deliberately NOT backfilled here. This function runs on the
    # READ path — `GET /api/tickets/{id}` reaches it through
    # `OrchestrationService.reconcile_ticket` — so writing routing here meant
    # fetching a ticket changed where it would go next. Cleared the pin and read
    # the ticket back, and the pin returned.
    #
    # The classify carve-out this replaces excluded classify stages only,
    # because backfilling there was read straight back by the route resolver as
    # a deliberate hint. That was the same defect on a narrower surface; readers
    # now derive the agent instead (`studio_routing.resolve_display_agent`), so
    # nothing needs the backfill at all.

    if persist:
        # Caller commits; we only mutate in-memory objects here.
        pass

    return stage_map


def set_stage_status(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    stage_key: str,
    status: StageStatus,
    *,
    note: str | None = None,
) -> dict[str, StageStatus]:
    """Set one stage's status, optionally recording why.

    `note=None` leaves any existing note alone; a string replaces it, and `""`
    clears it. The note is what the workflow pane shows next to a pruned stage,
    so a WONT_DO written without one reads as an unexplained gap.
    """
    stage_map = parse_stage_map(instance, stages)
    if stage_key not in stage_map:
        raise ValueError(f"Unknown stage key: {stage_key}")
    stage_map[stage_key] = status
    notes = parse_stage_notes(instance)
    if note is not None:
        if note:
            notes[stage_key] = note
        else:
            notes.pop(stage_key, None)
    instance.stages_json = serialize_stage_map(stage_map, stages, notes=notes)
    reconcile_workflow_state(ticket, instance, stages, persist=False)
    return stage_map


def _views_from(
    *,
    stage_map: dict[str, StageStatus],
    stages: list[WorkflowStageDef],
    note_by_key: dict[str, str],
) -> list[WorkflowStageView]:
    """One stage view per stage, in order. Shared so the reading and writing
    builders cannot drift into showing different things."""
    return [
        WorkflowStageView(
            key=stage.key,
            name=stage.name,
            status=stage_map[stage.key],
            order=stage.order,
            agent_id=stage.agent_id,
            skill_name=stage.skill_name,
            optional=stage.optional,
            note=note_by_key.get(stage.key, ""),
            stage_type=stage.stage_type or "agent",
            agents=_stage_agent_refs(stage),
            model=stage.model,
        )
        for stage in sorted(stages, key=lambda s: s.order)
    ]


def stage_views_readonly(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
) -> list[WorkflowStageView]:
    """The same views, computed without touching the ticket or the instance.

    Reads the derivation rather than applying it, so nothing is left dirty for
    a later commit to flush. `owns_state` is not a parameter because it only
    ever gated a *write* — there is none here.
    """
    derived = derive_workflow(ticket, instance, stages)
    return _views_from(
        stage_map=derived.stage_map,
        stages=stages,
        note_by_key=parse_stage_notes(instance),
    )


def build_stage_views(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    owns_state: bool = True,
) -> list[WorkflowStageView]:
    """Stage views, reconciling the ticket and instance in memory as it goes.

    For a caller that is going to persist anyway. A serializer wants
    `stage_views_readonly` instead — this one leaves the ticket and instance
    dirty, and on the list path that was committed once per row
    (lg-workflow-integrity-606).
    """
    reconcile_workflow_state(ticket, instance, stages, persist=False, owns_state=owns_state)
    stage_map = parse_stage_map(instance, stages)
    note_by_key = parse_stage_notes(instance)
    return _views_from(stage_map=stage_map, stages=stages, note_by_key=note_by_key)


def _stage_agent_refs(stage: WorkflowStageDef) -> list[ParallelAgentSpec]:
    refs: list[ParallelAgentSpec] = []
    if stage.stage_type == "parallel" and stage.parallel_agents:
        refs = list(stage.parallel_agents)
    elif stage.stage_type == "classify" and stage.classify_routes:
        refs = [
            ParallelAgentSpec(
                agent_id=route.agent_id, skill_name=route.skill_name or stage.skill_name
            )
            for route in stage.classify_routes
            if route.agent_id
        ]
    elif stage.agent_id:
        refs = [ParallelAgentSpec(agent_id=stage.agent_id, skill_name=stage.skill_name)]

    seen: set[str] = set()
    unique: list[ParallelAgentSpec] = []
    for ref in refs:
        if ref.agent_id in seen:
            continue
        seen.add(ref.agent_id)
        unique.append(ref)
    return unique


def settle_unreached_stages(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    terminal_key: str,
) -> list[str]:
    """Mark stages still PENDING at the terminal stage as WONT_DO; return their keys.

    Reaching the terminal stage settles the workflow: anything still PENDING is
    on a path this ticket never took. Left PENDING it would both hold the ticket
    short of DONE (_derive_ticket_state needs every non-optional stage resolved)
    and be picked up by next_executable_stage, running a branch that was never
    chosen. Template order cannot tell us what was reachable, so settle by
    arrival instead.
    """
    stage_map = parse_stage_map(instance, stages)
    unreached = [
        stage.key
        for stage in stages
        if stage.key != terminal_key
        and stage_map.get(stage.key, StageStatus.PENDING) == StageStatus.PENDING
    ]
    for key in unreached:
        set_stage_status(ticket, instance, stages, key, StageStatus.WONT_DO)
    return unreached
