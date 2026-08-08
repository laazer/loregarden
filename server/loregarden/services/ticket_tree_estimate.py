"""How much work is left in a ticket and everything under it.

A lane does not run a stage, it runs a *ticket* — and a ticket with children
runs them too. Costing a lane at one agent's median therefore answered a
question nobody asked: the board said "about four minutes" for a feature with
eleven stages and nine child tasks behind it.

What is measurable here, and what this module measures:

* **Which stages are actually left.** The workflow instance records each
  stage's status, so a ticket eight stages in is not costed as a fresh one.
* **What each remaining stage costs**, from that stage's own history
  (`run_duration_stats`), with the observed rework multiplier applied to
  stages that have not started — because history says a stage runs about 1.4
  times, not once.
* **The shape of the subtree.** Children can run in different lanes, so the
  sum of their work is not the time to finish them. Both bounds are reported:
  ``work_seconds`` (everything still to do) and ``critical_path_seconds`` (the
  longest chain that cannot be shortened by adding lanes). A projection over a
  given number of lanes is ``max(critical_path, work / lanes)``.

Everything is ``None`` when there is no history to draw on. A subtree where
some tickets are estimable and some are not reports the estimate *and* the
count it could not price, so the dashboard can say "at least this much" rather
than passing off a partial sum as a total.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.run_duration_stats import DurationStats, load_duration_stats
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, col, select

#: A ticket in one of these is not going to cost anything more.
TERMINAL_STATES = frozenset({TicketState.DONE, TicketState.WONT_DO})

#: Stage statuses meaning "this one is behind us".
RESOLVED_STAGES = frozenset({StageStatus.DONE, StageStatus.WONT_DO})

#: Stage statuses meaning "started, not finished" — costed by what is left of
#: the run on it rather than by a full attempt.
IN_FLIGHT_STAGES = frozenset({StageStatus.RUNNING, StageStatus.AWAITING})

_LIVE_RUN_STATUSES = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)


@dataclass(frozen=True)
class TreeEstimate:
    """What is left for a ticket and its descendants.

    ``work_seconds`` and ``critical_path_seconds`` are None only when nothing
    in the subtree could be priced at all. When some of it could,
    ``unknown_tickets`` says how many were left out, and the numbers are a
    floor rather than a total.
    """

    ticket_id: str
    #: This ticket's own remaining stages, excluding descendants.
    own_seconds: float | None
    #: Every remaining stage in the subtree, summed. Total work, not elapsed.
    work_seconds: float | None
    #: The longest chain of work that more lanes cannot shorten.
    critical_path_seconds: float | None
    #: Unfinished tickets in the subtree, including this one.
    ticket_count: int
    #: How many of those could not be priced.
    unknown_tickets: int
    #: Remaining stages across the subtree, for the same reason as above.
    stage_count: int

    def projected_seconds(self, lanes: int) -> float | None:
        """Wall-clock to finish the subtree given this many parallel lanes.

        Neither bound alone is honest: the sum pretends one lane, the critical
        path pretends infinitely many.
        """
        if self.work_seconds is None or self.critical_path_seconds is None:
            return None
        return max(self.critical_path_seconds, self.work_seconds / max(1, lanes))

    def as_payload(self, lanes: int) -> dict[str, object]:
        return {
            "work_seconds": self.work_seconds,
            "critical_path_seconds": self.critical_path_seconds,
            "projected_seconds": self.projected_seconds(lanes),
            "ticket_count": self.ticket_count,
            "unknown_tickets": self.unknown_tickets,
            "stage_count": self.stage_count,
        }


_EMPTY = TreeEstimate(
    ticket_id="",
    own_seconds=0.0,
    work_seconds=0.0,
    critical_path_seconds=0.0,
    ticket_count=0,
    unknown_tickets=0,
    stage_count=0,
)


class TicketTreeEstimator:
    """Prices ticket subtrees against one shared read of history.

    Built once per snapshot and reused across cards: the queue websocket asks
    about several tickets every few seconds, and each of those walks a subtree
    whose children, workflow instances and live runs would otherwise be a
    query apiece.
    """

    def __init__(
        self,
        session: Session,
        stats: DurationStats | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.stats = stats if stats is not None else load_duration_stats(session)
        self.now = now or datetime.now(timezone.utc)
        self._children: dict[str, list[Ticket]] = {}
        self._instances: dict[str, WorkflowInstance] = {}
        self._stage_cache: dict[tuple, list[WorkflowStageDef]] = {}
        self._live_runs: dict[str, AgentRun] = {}
        self._loaded_for: set[str] = set()
        self._memo: dict[str, TreeEstimate] = {}

    # ---- public ---------------------------------------------------------

    def estimate(self, ticket_id: str) -> TreeEstimate:
        ticket = self.session.get(Ticket, ticket_id)
        if not ticket:
            return _EMPTY
        self._load_subtree(ticket)
        return self._estimate_ticket(ticket, seen=set())

    # ---- batch loading --------------------------------------------------

    def _load_subtree(self, root: Ticket) -> None:
        """Breadth-first, one query per level rather than per ticket."""
        if root.id in self._loaded_for:
            return
        self._loaded_for.add(root.id)

        frontier = [root.id]
        collected: set[str] = {root.id}
        while frontier:
            rows = self.session.exec(
                select(Ticket).where(col(Ticket.parent_ticket_id).in_(frontier))
            ).all()
            by_parent: dict[str, list[Ticket]] = defaultdict(list)
            for child in rows:
                by_parent[child.parent_ticket_id or ""].append(child)
            for parent_id in frontier:
                self._children.setdefault(parent_id, by_parent.get(parent_id, []))
            frontier = [child.id for child in rows if child.id not in collected]
            collected.update(frontier)

        self._load_details(sorted(collected))

    def _load_details(self, ticket_ids: list[str]) -> None:
        missing = [ticket_id for ticket_id in ticket_ids if ticket_id not in self._instances]
        if not missing:
            return
        for instance in self.session.exec(
            select(WorkflowInstance).where(col(WorkflowInstance.ticket_id).in_(missing))
        ).all():
            self._instances[instance.ticket_id] = instance
        for run in self.session.exec(
            select(AgentRun).where(
                col(AgentRun.ticket_id).in_(missing),
                col(AgentRun.status).in_(_LIVE_RUN_STATUSES),
            )
        ).all():
            if run.ticket_id:
                self._live_runs[run.ticket_id] = run

    # ---- estimating -----------------------------------------------------

    def _estimate_ticket(self, ticket: Ticket, seen: set[str]) -> TreeEstimate:
        if ticket.id in self._memo:
            return self._memo[ticket.id]
        if ticket.id in seen:
            # A parent cycle is a data bug, not something to recurse forever on.
            return _EMPTY
        seen = seen | {ticket.id}

        own, stage_count = self._own_remaining(ticket)

        work: float | None = own
        path: float | None = own
        ticket_count = 0 if ticket.state in TERMINAL_STATES else 1
        unknown = 1 if own is None and ticket.state not in TERMINAL_STATES else 0

        deepest_child = 0.0
        for child in self._children.get(ticket.id, []):
            child_estimate = self._estimate_ticket(child, seen)
            ticket_count += child_estimate.ticket_count
            unknown += child_estimate.unknown_tickets
            stage_count += child_estimate.stage_count
            if child_estimate.work_seconds is not None:
                work = (work or 0.0) + child_estimate.work_seconds
            if child_estimate.critical_path_seconds is not None:
                deepest_child = max(deepest_child, child_estimate.critical_path_seconds)

        if deepest_child:
            path = (path or 0.0) + deepest_child

        estimate = TreeEstimate(
            ticket_id=ticket.id,
            own_seconds=own,
            work_seconds=work,
            critical_path_seconds=path,
            ticket_count=ticket_count,
            unknown_tickets=unknown,
            stage_count=stage_count,
        )
        self._memo[ticket.id] = estimate
        return estimate

    def _own_remaining(self, ticket: Ticket) -> tuple[float | None, int]:
        """Seconds left in this ticket's own pipeline, and how many stages."""
        if ticket.state in TERMINAL_STATES:
            return 0.0, 0
        if not self.stats.has_history():
            return None, 0

        stages = self._stages_for(ticket)
        if not stages:
            # No workflow to walk: a parent that only aggregates children costs
            # nothing itself, and a ticket whose workflow is disabled will not
            # run stages either.
            return 0.0, 0

        instance = self._instances.get(ticket.id)
        stage_map = (
            parse_stage_map(instance, stages)
            if instance
            else {stage.key: StageStatus.PENDING for stage in stages}
        )

        total = 0.0
        remaining = 0
        for stage in sorted(stages, key=lambda item: item.order):
            status = stage_map.get(stage.key, StageStatus.PENDING)
            if status in RESOLVED_STAGES:
                continue
            cost = self._stage_cost(stage)
            if cost is None:
                continue
            remaining += 1
            if status in IN_FLIGHT_STAGES:
                total += max(0.0, cost - self._elapsed_on(ticket))
            else:
                total += cost * self.stats.attempts_per_stage

        return total, remaining

    def _stage_cost(self, stage: WorkflowStageDef) -> float | None:
        """One attempt at a stage, or None when it costs no agent time.

        A gate or a terminal marker with no agent behind it and no history of
        its own is not unknown — it is free, and pricing it at the workspace
        median would inflate every pipeline by a stage that never ran one.
        """
        if stage.key in self.stats.by_stage:
            return self.stats.by_stage[stage.key]
        if not stage.agent_id:
            return None
        return self.stats.stage_seconds(stage.key, stage.agent_id)

    def _elapsed_on(self, ticket: Ticket) -> float:
        run = self._live_runs.get(ticket.id)
        if not run or not run.started_at:
            return 0.0
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0.0, (self.now - started).total_seconds())

    def _stages_for(self, ticket: Ticket) -> list[WorkflowStageDef]:
        """Stage definitions, cached across every ticket sharing a template.

        `resolve_ticket_stages` reads a workspace override off disk, so calling
        it per ticket would put a file read in the middle of a websocket tick.
        """
        instance = self._instances.get(ticket.id)
        key = (
            ticket.workspace_id,
            ticket.workflow_disabled,
            instance.template_id if instance else "",
            instance.template_version if instance else None,
        )
        if key not in self._stage_cache:
            _, stages = resolve_ticket_stages(self.session, ticket)
            self._stage_cache[key] = stages
        return self._stage_cache[key]
