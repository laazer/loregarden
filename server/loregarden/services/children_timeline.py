"""A parent ticket's timeline: its direct children, each as a row of stage visits.

Most parents never run a workflow of their own (161 of 171 on the live database
when this was written); their history *is* their children's. The single-ticket
ledger (`run_ledger`) already turns a ticket's runs into stage visits, so this
builds that same ledger for every child in one pass and adds what a parent's
reader needs beside it: where each child is now, and which open siblings (or
other tickets) it is still waiting on.

Grandchildren are not flattened in. A child that is itself a parent reports its
``child_count``, and the reader expands it by asking for that child's timeline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from loregarden.core.timestamps import as_utc
from loregarden.core.workflow_loader import stage_display_name
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    Ticket,
    TicketDependency,
    TicketState,
    WorkflowTemplate,
    WorkItemType,
)
from loregarden.models.domain.block_kinds import BlockKind
from loregarden.services.hierarchy_service import collect_ticket_scope_ids
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_ledger import LedgerVisit, build_ledger
from loregarden.services.ticket_state_service import RESOLVED_STATES
from loregarden.services.workflow_service import stage_resolution_memo
from pydantic import BaseModel
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, func, select


class ChildTimelineSegment(BaseModel):
    """One stage visit of one child: a single segment of its bar."""

    stage_key: str
    stage_name: str
    visit_number: int
    status: RunStatus
    attempts: int
    started_at: datetime | None
    #: None while the visit is still running; the bar runs to "now".
    finished_at: datetime | None


class ChildTimelineDependency(BaseModel):
    """An unresolved ticket a child waits for."""

    id: str
    external_id: str
    title: str
    state: TicketState


class ChildTimelineRow(BaseModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    work_item_type: WorkItemType
    workflow_stage_key: str
    workflow_stage_name: str
    workflow_stage_status: StageStatus
    block_kind: BlockKind | None
    #: This child's own children; above zero means the row can expand.
    child_count: int
    total_runs: int
    segments: list[ChildTimelineSegment]
    waiting_on: list[ChildTimelineDependency]


class ChildrenTimeline(BaseModel):
    parent_id: str
    children: list[ChildTimelineRow]
    #: Earliest run start and latest run end across every descendant, not only
    #: the direct children: an expanded row's children are laid on this same
    #: axis, so it must already cover their work. Both None when nothing ran.
    window_start: datetime | None
    window_end: datetime | None


#: Only the columns the ledger reads: stdout/stderr make a full row expensive,
#: and the largest parent here has over 200 child runs.
_RUN_COLUMNS = (
    AgentRun.id,
    AgentRun.run_code,
    AgentRun.ticket_id,
    AgentRun.agent_id,
    AgentRun.skill_name,
    AgentRun.stage_key,
    AgentRun.status,
    AgentRun.started_at,
    AgentRun.finished_at,
    AgentRun.created_at,
)


def _segment(visit: LedgerVisit, stage_name: str) -> ChildTimelineSegment:
    # Stored naive (SQLite drops the zone); tag them as the UTC they are.
    starts = [as_utc(a.started_at) for a in visit.attempts if a.started_at]
    status = RunStatus(visit.status)
    ends = [as_utc(a.finished_at) for a in visit.attempts if a.finished_at]
    return ChildTimelineSegment(
        stage_key=visit.stage_key,
        stage_name=stage_name,
        visit_number=visit.visit_number,
        status=status,
        attempts=len(visit.attempts),
        started_at=min(starts) if starts else None,
        finished_at=None if status == RunStatus.RUNNING or not ends else max(ends),
    )


def _runs_by_child(session: Session, child_ids: list[str]) -> dict[str, list[AgentRun]]:
    runs = session.exec(
        select(AgentRun)
        .where(col(AgentRun.ticket_id).in_(child_ids))
        .options(load_only(*_RUN_COLUMNS))
    ).all()
    grouped: dict[str, list[AgentRun]] = defaultdict(list)
    for run in runs:
        grouped[run.ticket_id].append(run)
    return grouped


def _open_dependencies(
    session: Session, child_ids: list[str]
) -> dict[str, list[ChildTimelineDependency]]:
    rows = session.exec(
        select(TicketDependency.ticket_id, Ticket)
        .join(Ticket, col(Ticket.id) == TicketDependency.depends_on_ticket_id)
        .where(col(TicketDependency.ticket_id).in_(child_ids))
        .where(col(Ticket.state).not_in(RESOLVED_STATES))
        .order_by(col(Ticket.ticket_number))
    ).all()
    waiting: dict[str, list[ChildTimelineDependency]] = defaultdict(list)
    for child_id, dep in rows:
        waiting[child_id].append(
            ChildTimelineDependency(
                id=dep.id, external_id=dep.external_id, title=dep.title, state=dep.state
            )
        )
    return waiting


def _grandchild_counts(session: Session, child_ids: list[str]) -> dict[str, int]:
    rows = session.exec(
        select(Ticket.parent_ticket_id, func.count())
        .where(col(Ticket.parent_ticket_id).in_(child_ids))
        .group_by(col(Ticket.parent_ticket_id))
    ).all()
    return {parent_id: count for parent_id, count in rows if parent_id}


def children_timeline(session: Session, parent: Ticket) -> ChildrenTimeline:
    children = list(
        session.exec(
            select(Ticket)
            .where(Ticket.parent_ticket_id == parent.id)
            .order_by(col(Ticket.ticket_number), col(Ticket.external_id))
        ).all()
    )
    child_ids = [c.id for c in children]
    runs = _runs_by_child(session, child_ids)
    waiting = _open_dependencies(session, child_ids)
    grandchildren = _grandchild_counts(session, child_ids)
    orch = OrchestrationService(session)

    rows: list[ChildTimelineRow] = []
    with stage_resolution_memo():
        for child in children:
            template = orch.get_template_for_ticket(child)
            visits = build_ledger(runs.get(child.id, []))
            rows.append(
                ChildTimelineRow(
                    id=child.id,
                    external_id=child.external_id,
                    title=child.title,
                    state=child.state,
                    work_item_type=child.work_item_type,
                    workflow_stage_key=child.workflow_stage_key,
                    workflow_stage_name=_stage_name(template, child.workflow_stage_key),
                    workflow_stage_status=child.workflow_stage_status,
                    block_kind=child.block_kind,
                    child_count=grandchildren.get(child.id, 0),
                    total_runs=sum(len(v.attempts) for v in visits),
                    segments=[_segment(v, _stage_name(template, v.stage_key)) for v in visits],
                    waiting_on=waiting.get(child.id, []),
                )
            )

    window_start, window_end = _descendant_window(session, parent.id)
    # Children with history first, in the order their work began; the rest keep ticket order.
    started = sorted((r for r in rows if _first_start(r)), key=_first_start)
    not_started = [r for r in rows if not _first_start(r)]
    return ChildrenTimeline(
        parent_id=parent.id,
        children=started + not_started,
        window_start=window_start,
        window_end=window_end,
    )


def _descendant_window(session: Session, parent_id: str) -> tuple[datetime | None, datetime | None]:
    descendants = [tid for tid in collect_ticket_scope_ids(session, parent_id) if tid != parent_id]
    if not descendants:
        return None, None
    start, end = session.exec(
        select(
            func.min(AgentRun.started_at),
            func.max(func.coalesce(AgentRun.finished_at, AgentRun.started_at)),
        ).where(col(AgentRun.ticket_id).in_(descendants))
    ).one()
    return (as_utc(start) if start else None, as_utc(end) if end else None)


def _first_start(row: ChildTimelineRow) -> datetime | None:
    starts = [s.started_at for s in row.segments if s.started_at]
    return min(starts) if starts else None


def _stage_name(template: WorkflowTemplate | None, stage_key: str) -> str:
    if not stage_key:
        return ""
    return stage_display_name(template, stage_key) if template else stage_key
