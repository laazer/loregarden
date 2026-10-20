"""The scenario's ticket tree: nesting, waiting stages, and what blocked work waits on.

`prod_shape` hangs every ticket directly off a milestone, with no stage waiting
on anyone and every block unclassified, so a tree surface rendered over it saw
two levels and a "Needs me" view with nothing to sort. Live, capabilities sit
under features and tasks under capabilities (four levels below the roots), a
stage waits on a person on 15 tickets, and a quarter of the backlog is held by
an unmet prerequisite. Measured 2026-10-19 (read-only); counts that scale with
volume are scaled to the scenario by `scale`.

Split from `prod_shape` (near its size cap); `build_prod_shape` calls it.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from itertools import pairwise

from pydantic import BaseModel
from sqlmodel import Session

from loregarden.models.domain import (
    BlockKind,
    StageStatus,
    Ticket,
    TicketDependency,
    TicketState,
    WorkItemType,
)
from loregarden.services.ticket_state_service import RESOLVED_STATES


class TreeShape(BaseModel):
    measured: str
    #: Live ticket count when measured: the denominator `scale` uses.
    tickets: int
    #: Child type -> parent type -> live count. Features always hang off a milestone.
    parents: dict[WorkItemType, dict[WorkItemType, int]]
    #: In progress, with the current stage awaiting a person.
    awaiting: int
    #: The stage those wait in, by type (live: playtest features, script_review bugs).
    awaiting_stage: dict[WorkItemType, str]
    #: Backlog tickets, and those held by an unfinished prerequisite.
    backlog: int
    prerequisite_held: int


TREE_CALIBRATION = TreeShape(
    measured="2026-10-19",
    tickets=1676,
    parents={
        WorkItemType.CAPABILITY: {WorkItemType.FEATURE: 239},
        WorkItemType.TASK: {
            WorkItemType.CAPABILITY: 71,
            WorkItemType.FEATURE: 2,
            WorkItemType.MILESTONE: 1,
        },
        WorkItemType.BUG: {
            WorkItemType.MILESTONE: 188,
            WorkItemType.FEATURE: 111,
            WorkItemType.CAPABILITY: 5,
        },
    },
    awaiting=15,
    awaiting_stage={WorkItemType.FEATURE: "playtest", WorkItemType.BUG: "script_review"},
    backlog=1132,
    prerequisite_held=303,
)

#: Live blocks were harness 6, work 1, tickets 8 and unclassified 0. The
#: scenario's five blocked tickets keep that order of weight, as one rolled-up
#: chain (milestone -> feature -> capability), one of each agent kind, and one
#: decision: live had no person-owned block that day, but 2026-09-28 had four,
#: and a scenario without one never renders the "Needs me" person path.
_KINDS = {
    WorkItemType.MILESTONE: BlockKind.TICKETS,
    WorkItemType.FEATURE: BlockKind.TICKETS,
    WorkItemType.CAPABILITY: BlockKind.HARNESS,
    WorkItemType.BUG: BlockKind.WORK,
    WorkItemType.TASK: BlockKind.DECISION,
}


class TreeSummary(BaseModel):
    awaiting: int
    prerequisite_held: int
    blocked_kinds: dict[BlockKind, int]


def add_tree_shape(
    session: Session,
    tickets: list,
    rng: random.Random,
    make_id: Callable[[str, int | str], str],
    scale: float,
) -> TreeSummary:
    """Nest, park and hold the scenario's tickets in the live proportions. Idempotent.

    `tickets` are `build_prod_shape`'s rows: (slug, id, external_id, state, milestone id).
    """
    rows = [(slug, session.get(Ticket, tid), milestone) for slug, tid, _e, _s, milestone in tickets]
    _nest(rows, rng)
    blocked_kinds = _classify_blocks(session, rows)
    awaiting = _park(rows, rng, round(TREE_CALIBRATION.awaiting * scale))
    held = _hold(session, rows, rng, make_id)
    for _slug, ticket, _milestone in rows:
        session.add(ticket)
    session.commit()
    return TreeSummary(awaiting=awaiting, prerequisite_held=held, blocked_kinds=blocked_kinds)


def _open(ticket: Ticket) -> bool:
    return ticket.state not in RESOLVED_STATES


def _nest(rows: list, rng: random.Random) -> None:
    """Re-hang capabilities, tasks and bugs under a sibling in the same milestone."""
    by_milestone: dict[str, dict[WorkItemType, list[Ticket]]] = {}
    for _slug, ticket, milestone in rows:
        by_milestone.setdefault(milestone, {}).setdefault(ticket.work_item_type, []).append(ticket)
    for _slug, ticket, milestone in rows:
        split = TREE_CALIBRATION.parents.get(ticket.work_item_type)
        if split is None:
            continue
        [parent_type] = rng.choices(list(split), weights=list(split.values()))
        candidates = by_milestone[milestone].get(parent_type, [])
        if parent_type == WorkItemType.MILESTONE or not candidates:
            ticket.parent_ticket_id = milestone
            continue
        # An open child under a finished parent is a shape nobody reads twice.
        fitting = [c for c in candidates if _open(c) or not _open(ticket)] or candidates
        ticket.parent_ticket_id = rng.choice(fitting).id


def _classify_blocks(session: Session, rows: list) -> dict[BlockKind, int]:
    """Give each blocked ticket a kind, and chain the rolled-up ones under each other."""
    milestones = {milestone: slug for slug, _ticket, milestone in rows}
    candidates = [(slug, ticket) for slug, ticket, _m in rows] + [
        (slug, session.get(Ticket, milestone)) for milestone, slug in milestones.items()
    ]
    kinds: dict[BlockKind, int] = {}
    first: dict[str, dict[WorkItemType, Ticket]] = {}
    for slug, ticket in candidates:
        if ticket.state != TicketState.BLOCKED:
            continue
        ticket.block_kind = _KINDS[ticket.work_item_type]
        kinds[ticket.block_kind] = kinds.get(ticket.block_kind, 0) + 1
        session.add(ticket)
        first.setdefault(slug, {}).setdefault(ticket.work_item_type, ticket)
    for by_type in first.values():
        chain = [by_type.get(t) for t in _CHAIN]
        for parent, child in pairwise(chain):
            if parent is not None and child is not None:
                child.parent_ticket_id = parent.id
    return kinds


#: A blocked capability holds its feature, which holds its milestone.
_CHAIN = (WorkItemType.MILESTONE, WorkItemType.FEATURE, WorkItemType.CAPABILITY)


def _park(rows: list, rng: random.Random, count: int) -> int:
    """`count` in-progress tickets whose current stage awaits a person."""
    eligible = [
        ticket
        for _slug, ticket, _milestone in rows
        if ticket.state == TicketState.IN_PROGRESS
        and ticket.work_item_type in TREE_CALIBRATION.awaiting_stage
    ]
    for ticket in rng.sample(eligible, k=min(count, len(eligible))):
        ticket.workflow_stage_key = TREE_CALIBRATION.awaiting_stage[ticket.work_item_type]
        ticket.workflow_stage_status = StageStatus.AWAITING
    return min(count, len(eligible))


def _hold(
    session: Session, rows: list, rng: random.Random, make_id: Callable[[str, int | str], str]
) -> int:
    """Hold the live share of the backlog on an open prerequisite, never an ancestor.

    Every edge points at an earlier row in the same workspace, so the graph is acyclic.
    """
    share = TREE_CALIBRATION.prerequisite_held / TREE_CALIBRATION.backlog
    backlog = [i for i, (_s, t, _m) in enumerate(rows) if t.state == TicketState.BACKLOG]
    parents = {t.id: t.parent_ticket_id for _s, t, _m in rows}
    held = 0
    for n, index in enumerate(sorted(rng.sample(backlog, k=round(len(backlog) * share)))):
        slug, ticket, _milestone = rows[index]
        ancestors, cursor = set(), ticket.parent_ticket_id
        while cursor is not None:
            ancestors.add(cursor)
            cursor = parents.get(cursor)
        earlier = [
            t
            for s, t, _m in rows[:index]
            if s == slug and _open(t) and t.id not in ancestors and t.id != ticket.id
        ]
        if not earlier:
            continue
        prerequisite = rng.choice(earlier)
        edge_id = make_id("prerequisite", n)
        if session.get(TicketDependency, edge_id) is None:
            session.add(
                TicketDependency(
                    id=edge_id,
                    ticket_id=ticket.id,
                    depends_on_ticket_id=prerequisite.id,
                    created_by="prod-shape",
                )
            )
        held += 1
    return held
