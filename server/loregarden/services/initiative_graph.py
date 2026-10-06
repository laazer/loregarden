"""An initiative as a dependency graph: what can start, what waits, and when it all lands.

The plan an agent drives is not a list of milestones run one after another. It
is tickets joined by "waits for" edges (`ticket_dependencies`), worked in
parallel lanes — one agent per lane — with milestones as phases. This module
turns that into answers:

* **status** per ticket — done, running, ready now, waiting on prerequisites,
  or waiting on a person (`needs-person` tag, parked, or blocked on a decision
  or human action). The autopilot dispatches only `READY`.
* **a schedule** — each lane takes one ticket at a time; a ticket starts when
  its prerequisites have finished and its lane is free. Lanes come from a
  ``<prefix>-lane-<name>`` tag (e.g. ``tcg-lane-model-render``); a ticket with
  none runs in a lane named after its workspace.
* **the critical path** — traced back from the last finish through whichever
  constraint set each start: a prerequisite, or the lane being busy.

Durations blend the two measurements `initiative_forecast` describes: the
workspace's pace (resolved work items per day, shared across its lanes) and the
agent run-time for the ticket's remaining stages. The longer wins. A ticket with
neither takes the median of the ones that have one, and is marked as such; with
no measurement anywhere, its finish is unknown and so is everything after it.

A prerequisite outside the initiative is scheduled too, in its own workspace's
lane, so a plan that waits on another repository's ticket says when.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from loregarden.models.domain import (
    BlockKind,
    ForecastBasis,
    NodeStatus,
    ScheduleTarget,
    Ticket,
    TicketActivity,
    TicketDependency,
    TicketState,
    WorkItemType,
    comparable_utc,
)
from loregarden.services.initiative_coverage import InitiativeCoverage, initiative_coverage
from loregarden.services.initiative_forecast import (
    Pace,
    duration_stats,
    group_by_workspace,
    measure_paces,
    plan_sequence,
)
from loregarden.services.initiative_service import load_targets, workspace_slugs
from loregarden.services.ticket_activity import classify_ticket_activity
from loregarden.services.ticket_state_service import RESOLVED_STATES
from loregarden.services.ticket_tags import load_tags
from loregarden.services.ticket_tree_estimate import TicketTreeEstimator
from sqlmodel import Session, col, select

#: A ticket a person has to act on. Autopilot never dispatches it.
NEEDS_PERSON_TAG = "needs-person"

#: ``tcg-lane-model-render`` -> ``model-render``; ``lane-ops`` -> ``ops``.
_LANE_TAG = re.compile(r"^(?:[a-z0-9]+-)?lane-(?P<lane>[a-z0-9][a-z0-9-]*)$")

_CONTAINERS = frozenset({WorkItemType.INITIATIVE, WorkItemType.MILESTONE})
_PERSON_BLOCKS = frozenset({BlockKind.DECISION, BlockKind.HUMAN_ACTION})
_DAY = 86_400.0
_TOLERANCE = timedelta(seconds=1)

#: Milestones with no plan row sort after every planned one.
_UNPLANNED_ORDER = 1_000_000


@dataclass
class PlanNode:
    ticket: Ticket
    lane: str
    #: The milestone (phase) it belongs to; None for an outside prerequisite.
    milestone_id: str | None
    deps: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.WAITING
    #: An agent run holds its lane (running, queued, or parked on an approval).
    holds_lane: bool = False
    duration_days: float | None = None
    basis: ForecastBasis = ForecastBasis.NONE
    #: True when the duration is the plan's median standing in for a measurement.
    assumed: bool = False
    start: datetime | None = None
    finish: datetime | None = None
    #: Prerequisites not yet done, for "what is it waiting on".
    waiting_on: list[str] = field(default_factory=list)
    #: Longest chain of work from here to the end of the plan, in days.
    rank: float = 0.0
    #: Dependency depth: 1 for a ticket with no open prerequisites.
    step: int = 1
    critical: bool = False
    external: bool = False
    #: Its milestone's position in the plan's order (phase); lower goes first.
    phase: int = _UNPLANNED_ORDER

    @property
    def id(self) -> str:
        return self.ticket.id


@dataclass
class PlanGraph:
    nodes: dict[str, PlanNode]
    #: Ticket ids in the critical chain, first to last.
    critical_path: list[str]
    #: Ids caught in a dependency cycle; they cannot be scheduled.
    cyclic: list[str]
    lanes: list[str]
    now: datetime

    def for_milestone(self, milestone_id: str) -> list[PlanNode]:
        return [n for n in self.nodes.values() if n.milestone_id == milestone_id]


def lane_for(ticket: Ticket, slug: str) -> str:
    for tag in load_tags(ticket.tags_json):
        match = _LANE_TAG.match(tag)
        if match:
            return match.group("lane")
    return slug or "unassigned"


def _status(ticket: Ticket, activity: TicketActivity) -> tuple[NodeStatus, bool]:
    """(status before dependencies are considered, whether it holds its lane)."""
    if ticket.state in RESOLVED_STATES:
        return NodeStatus.DONE, False
    if activity == TicketActivity.AWAITING:
        return NodeStatus.NEEDS_PERSON, True
    if activity in (TicketActivity.RUNNING, TicketActivity.QUEUED):
        return NodeStatus.RUNNING, True
    if (
        ticket.state == TicketState.PARKED
        or ticket.block_kind in _PERSON_BLOCKS
        or NEEDS_PERSON_TAG in load_tags(ticket.tags_json)
    ):
        return NodeStatus.NEEDS_PERSON, False
    if ticket.state == TicketState.BLOCKED:
        return NodeStatus.BLOCKED, False
    return NodeStatus.READY, False


def _blend(
    *, agent_days: float | None, pace: Pace | None, lanes: int
) -> tuple[float | None, ForecastBasis]:
    """A ticket's duration: the slower of pace and agent run-time, and which it was.

    Pace is items per day across the workspace's lanes, so one lane takes
    ``lanes / per_day`` days per item.
    """
    pace_days = lanes / pace.per_day if pace is not None and pace.per_day else None
    if (
        pace is not None
        and pace_days is not None
        and (agent_days is None or pace_days >= agent_days)
    ):
        return pace_days, pace.basis
    if agent_days is not None:
        return agent_days, ForecastBasis.AGENT_TIME
    return None, ForecastBasis.NONE


def _finished_by(node: PlanNode, clock: datetime) -> bool:
    return node.finish is not None and node.finish <= clock


def _priority(node: PlanNode) -> tuple[int, float, str]:
    """Which ticket a free lane takes: work already holding it, earlier phase, longer chain."""
    return (-1 if node.holds_lane else node.phase, -node.rank, node.ticket.external_id)


class PlanGraphBuilder:
    """Builds and schedules one initiative's graph against one read of history."""

    def __init__(self, session: Session, *, now: datetime) -> None:
        self.session = session
        self.now = now

    def build(
        self,
        coverage: InitiativeCoverage,
        slugs: dict[str, str],
        targets: dict[str, ScheduleTarget],
    ) -> tuple[PlanGraph, dict[str, Pace]]:
        milestones = coverage.roots
        nodes = self._nodes(coverage, slugs)
        self._edges(nodes, slugs)
        paces = self._paces(nodes)
        self._durations(nodes, paces)
        self._statuses(nodes)
        cyclic = self._ranks_and_steps(nodes)
        order = {m.id: i for i, m in enumerate(plan_sequence(milestones, targets))}
        for node in nodes.values():
            node.phase = order.get(node.milestone_id or "", _UNPLANNED_ORDER)
        self._schedule(nodes, order, cyclic)
        critical = self._critical_path(nodes)
        lanes = sorted({n.lane for n in nodes.values()})
        return PlanGraph(nodes, critical, sorted(cyclic), lanes, self.now), paces

    # ---- loading -------------------------------------------------------

    def _nodes(self, coverage: InitiativeCoverage, slugs: dict[str, str]) -> dict[str, PlanNode]:
        """Every work item under each phase; a phase with nothing under it is its own
        one item. A parent carries no work of its own (`ticket_rollup`), so a member
        feature with capabilities is planned as those capabilities."""
        nodes: dict[str, PlanNode] = {}
        for milestone in coverage.roots:
            items = [t for t in coverage.trees[milestone.id] if t.work_item_type not in _CONTAINERS]
            for ticket in items or [milestone]:
                nodes[ticket.id] = PlanNode(
                    ticket=ticket,
                    lane=lane_for(ticket, slugs.get(ticket.workspace_id or "", "")),
                    milestone_id=milestone.id,
                )
        return nodes

    def _edges(self, nodes: dict[str, PlanNode], slugs: dict[str, str]) -> None:
        edges = self.session.exec(
            select(TicketDependency).where(col(TicketDependency.ticket_id).in_(list(nodes)))
        ).all()
        outside = {e.depends_on_ticket_id for e in edges} - set(nodes)
        if outside:
            for ticket in self.session.exec(select(Ticket).where(col(Ticket.id).in_(outside))):
                slug = slugs.get(ticket.workspace_id or "") or ticket.workspace_id or ""
                nodes[ticket.id] = PlanNode(
                    ticket=ticket, lane=f"outside:{slug}", milestone_id=None, external=True
                )
        for edge in edges:
            if edge.depends_on_ticket_id in nodes:
                nodes[edge.ticket_id].deps.append(edge.depends_on_ticket_id)
                nodes[edge.depends_on_ticket_id].dependents.append(edge.ticket_id)

    def _paces(self, nodes: dict[str, PlanNode]) -> dict[str, Pace]:
        own = [n.ticket for n in nodes.values() if not n.external]
        return measure_paces(self.session, group_by_workspace(own), self.now)

    # ---- durations -----------------------------------------------------

    def _durations(self, nodes: dict[str, PlanNode], paces: dict[str, Pace]) -> None:
        estimator = TicketTreeEstimator(
            self.session, stats=duration_stats(self.session), now=self.now
        )
        open_nodes = [n for n in nodes.values() if n.ticket.state not in RESOLVED_STATES]
        lanes_per_workspace: dict[str, set[str]] = defaultdict(set)
        for node in open_nodes:
            lanes_per_workspace[node.ticket.workspace_id or ""].add(node.lane)
        estimator.prime([n.ticket for n in open_nodes])
        for node in open_nodes:
            workspace_id = node.ticket.workspace_id or ""
            seconds = estimator.estimate(node.id).projected_seconds(1)
            node.duration_days, node.basis = _blend(
                agent_days=seconds / _DAY if seconds else None,
                pace=paces.get(workspace_id),
                lanes=len(lanes_per_workspace[workspace_id]),
            )
        known = [n.duration_days for n in open_nodes if n.duration_days is not None]
        if not known:
            return
        fallback = median(known)
        for node in open_nodes:
            if node.duration_days is None:
                node.duration_days, node.assumed = fallback, True

    # ---- status, rank, depth -------------------------------------------

    def _statuses(self, nodes: dict[str, PlanNode]) -> None:
        activity = classify_ticket_activity(self.session, list(nodes))
        for node in nodes.values():
            node.status, node.holds_lane = _status(
                node.ticket, activity.get(node.id, TicketActivity.IDLE)
            )
        for node in nodes.values():
            node.waiting_on = [d for d in node.deps if nodes[d].status != NodeStatus.DONE]
            if node.status == NodeStatus.READY and node.waiting_on:
                node.status = NodeStatus.WAITING

    def _ranks_and_steps(self, nodes: dict[str, PlanNode]) -> set[str]:
        """Longest remaining chain (rank) and dependency depth (step); returns cycles."""
        indegree = {nid: len(n.deps) for nid, n in nodes.items()}
        queue = [nid for nid, d in indegree.items() if d == 0]
        topo: list[str] = []
        while queue:
            nid = queue.pop()
            topo.append(nid)
            for child in nodes[nid].dependents:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        cyclic = set(nodes) - set(topo)
        for nid in topo:
            node = nodes[nid]
            open_deps = [nodes[d] for d in node.deps if nodes[d].status != NodeStatus.DONE]
            node.step = 1 + max((d.step for d in open_deps), default=0)
        for nid in reversed(topo):
            node = nodes[nid]
            own = 0.0 if node.status == NodeStatus.DONE else (node.duration_days or 0.0)
            node.rank = own + max(
                (nodes[c].rank for c in node.dependents if c not in cyclic), default=0.0
            )
        return cyclic

    # ---- scheduling ----------------------------------------------------

    def _schedule(
        self, nodes: dict[str, PlanNode], order: dict[str, int], cyclic: set[str]
    ) -> None:
        """Simulate the lanes over time: one ticket per lane, best ticket first.

        Time-stepped, not greedy by discovery order: at each moment a free
        lane takes, among *its* tickets whose prerequisites have actually
        finished, the one with the highest priority — work already holding the
        lane, then earlier phase, then the longest chain behind it. Handing a
        lane to whichever ticket became eligible first instead let a phase-2
        ticket book a lane for next month and push the slice's first ticket
        behind it.
        """
        for node in nodes.values():
            if node.status == NodeStatus.DONE:
                resolved = node.ticket.resolved_at
                node.start = node.finish = comparable_utc(resolved) if resolved else self.now
        pending = self._priceable(nodes, cyclic)
        lane_free: dict[str, datetime] = defaultdict(lambda: self.now)
        clock: datetime | None = self.now
        while pending and clock is not None:
            if not self._start_free_lanes(nodes, pending, lane_free, clock):
                clock = self._next_event(nodes, pending, lane_free, clock)

    def _priceable(self, nodes: dict[str, PlanNode], cyclic: set[str]) -> set[str]:
        """Open tickets that can be dated: not cyclic, priced, and after priced work."""
        unknown: set[str] = set(cyclic)
        open_ids = {
            nid for nid, n in nodes.items() if n.status != NodeStatus.DONE and nid not in cyclic
        }
        for nid in self._topological(nodes, open_ids):
            node = nodes[nid]
            if node.duration_days is None or any(d in unknown for d in node.deps):
                unknown.add(nid)
        return open_ids - unknown

    @staticmethod
    def _start_free_lanes(
        nodes: dict[str, PlanNode],
        pending: set[str],
        lane_free: dict[str, datetime],
        clock: datetime,
    ) -> bool:
        """Start the best eligible ticket in every free lane. True if any started."""
        eligible: dict[str, list[PlanNode]] = defaultdict(list)
        for nid in pending:
            node = nodes[nid]
            if all(_finished_by(nodes[d], clock) for d in node.deps):
                eligible[node.lane].append(node)
        started = False
        for lane, candidates in eligible.items():
            if lane_free[lane] > clock:
                continue
            node = min(candidates, key=_priority)
            node.start = clock
            node.finish = clock + timedelta(days=node.duration_days or 0.0)
            lane_free[lane] = node.finish
            pending.discard(node.id)
            started = True
        return started

    @staticmethod
    def _next_event(
        nodes: dict[str, PlanNode],
        pending: set[str],
        lane_free: dict[str, datetime],
        clock: datetime,
    ) -> datetime | None:
        """The next moment a lane frees or a prerequisite finishes; None if never."""
        upcoming = [t for t in lane_free.values() if t > clock]
        upcoming += [
            f
            for nid in pending
            for f in (nodes[d].finish for d in nodes[nid].deps)
            if f is not None and f > clock
        ]
        return min(upcoming, default=None)

    @staticmethod
    def _topological(nodes: dict[str, PlanNode], ids: set[str]) -> list[str]:
        indegree = {nid: sum(1 for d in nodes[nid].deps if d in ids) for nid in ids}
        queue = sorted(nid for nid, n in indegree.items() if n == 0)
        out: list[str] = []
        while queue:
            nid = queue.pop()
            out.append(nid)
            for child in nodes[nid].dependents:
                if child in indegree:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        queue.append(child)
        return out

    def _critical_path(self, nodes: dict[str, PlanNode]) -> list[str]:
        open_nodes = [
            n for n in nodes.values() if n.status != NodeStatus.DONE and n.finish is not None
        ]
        if not open_nodes:
            return []
        lane_prev: dict[str, PlanNode] = {}
        by_lane: dict[str, list[PlanNode]] = defaultdict(list)
        for open_node in open_nodes:
            by_lane[open_node.lane].append(open_node)
        for lane_nodes in by_lane.values():
            lane_nodes.sort(key=lambda n: n.start or self.now)
            for before, after in zip(lane_nodes, lane_nodes[1:], strict=False):
                lane_prev[after.id] = before
        chain: list[str] = []
        node: PlanNode | None = max(open_nodes, key=lambda n: n.finish or self.now)
        while node is not None and node.id not in chain:
            node.critical = True
            chain.append(node.id)
            # Whatever finished last before this started is what held it back:
            # an open prerequisite, or the ticket ahead of it in its lane.
            candidates = [
                nodes[d]
                for d in node.deps
                if nodes[d].status != NodeStatus.DONE and nodes[d].finish is not None
            ]
            if node.id in lane_prev:
                candidates.append(lane_prev[node.id])
            gate = max(candidates, key=lambda n: n.finish or self.now, default=None)
            started = node.start or self.now
            held_back = (
                gate is not None and gate.finish is not None and gate.finish >= started - _TOLERANCE
            )
            node = gate if held_back else None
        return list(reversed(chain))


@dataclass(frozen=True)
class PlanContext:
    """Everything one read of an initiative's plan is computed from."""

    initiative: Ticket
    #: The plan's phases: the initiative's children and members (its roots).
    milestones: list[Ticket]
    #: Which of `milestones` the initiative tracks by membership.
    member_ids: frozenset[str]
    slugs: dict[str, str]
    targets: dict[str, ScheduleTarget]
    graph: PlanGraph
    paces: dict[str, Pace]


def build_plan(session: Session, initiative: Ticket, *, now: datetime) -> PlanContext:
    coverage = initiative_coverage(session, initiative.id)
    milestones = coverage.roots
    targets = load_targets(session, [initiative.id, *(m.id for m in milestones)])
    # Outside prerequisites need their workspace's slug too; resolve after the
    # graph has found them, with the milestones' as the starting set.
    slugs = workspace_slugs(session, milestones)
    builder = PlanGraphBuilder(session, now=now)
    graph, paces = builder.build(coverage, slugs, targets)
    outside = [n.ticket for n in graph.nodes.values() if n.external]
    if outside:
        slugs = {**slugs, **workspace_slugs(session, outside)}
        for node in graph.nodes.values():
            if node.external:
                node.lane = f"outside:{slugs.get(node.ticket.workspace_id or '', '')}"
        graph.lanes = sorted({n.lane for n in graph.nodes.values()})
    return PlanContext(initiative, milestones, coverage.member_ids, slugs, targets, graph, paces)
