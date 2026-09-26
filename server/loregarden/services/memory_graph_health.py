"""The memory graph's shape, measured and tracked over time.

Borrowed from second-brain-os's metrics discipline, sharpened by what this
control plane can see that a notes vault cannot. The advice carried over whole:
a single reading says almost nothing, direction over time is the information,
and page counts are not a health signal — they rise whether the graph is
improving or not. So every figure here is a *share of live learnings*, and a
snapshot is only interesting against the one before it.

What is measured, per workspace graph:

- ``unlinked``    no relation to any other node — recall can reach it, the
                  digest never can.
- ``never_surfaced`` never injected into a single run's briefing (178). A
                  learning no run has seen is not memory, it is storage.
- ``surfaced_unscored`` injected, but no run it went into has concluded with
                  a rung, so its confidence is still the prior.
- ``stale``       not updated in `STALE_AFTER_DAYS`. Some staleness is
                  correct; a rising share while learnings keep arriving means
                  new material lands in new nodes instead of updating old ones.
- ``contested``   holds an unresolved `contradicts` edge — neither side
                  superseded.
- ``superseded`` / ``discredited`` for context: history and withdrawals.

The pattern worth naming in words (and `compare` does): unlinked share rising
while the learning count rises means writers have stopped linking.

Snapshots are append-only rows in the control-plane DB (`memory_health_snapshots`),
because the figures join the shard with `learning_applications`, which lives there.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from loregarden.models.domain import (
    LearningApplication,
    MemoryHealthSnapshot,
    MemoryRelationType,
    utcnow,
)
from loregarden.services.memory_store import AgentMemoryService
from pydantic import BaseModel
from sqlmodel import Session, col, select

STALE_AFTER_DAYS = 90
#: A share must move by at least this many points to count as having moved.
_NOTEWORTHY_POINTS = 2.0
#: Shares where a rise is bad news, in the order `watch` prefers them.
_ADVERSE = ("unlinked", "never_surfaced", "contested", "stale", "surfaced_unscored")


class GraphFigures(BaseModel):
    """Counts for one workspace graph at one moment."""

    learnings: int
    unlinked: int
    never_surfaced: int
    surfaced_unscored: int
    stale: int
    contested: int
    superseded: int
    discredited: int

    def share(self, name: str) -> float:
        """Percentage of live learnings. 0 for an empty graph — `learnings`
        says it is empty, so the share is never read on its own."""
        count = self.model_dump()[name]
        return round(100.0 * count / self.learnings, 1) if self.learnings else 0.0


class GraphHealthReading(BaseModel):
    workspace_slug: str
    measured_at: datetime
    figures: GraphFigures
    shares: dict[str, float]


class Movement(BaseModel):
    metric: str
    was: float
    now: float


class GraphHealthReport(BaseModel):
    current: GraphHealthReading
    previous: GraphHealthReading | None
    moved: list[Movement]
    notes: list[str]
    #: The one share to fix, or None. One, deliberately: a report with five
    #: action items gets none of them done.
    watch: str | None


def _census(conn: sqlite3.Connection, workspace_slug: str, stale_before: str) -> dict:
    """Shard-side figures in one connection. Node ids come back for the join."""
    scope = "workspace_slug = ?" if workspace_slug else "1 = 1"
    params = (workspace_slug,) if workspace_slug else ()
    live = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT id, updated_at FROM memory_nodes "  # noqa: S608 - fixed clause
            f"WHERE {scope} AND COALESCE(discredited, 0) = 0",
            params,
        )
    }
    (discredited,) = conn.execute(
        f"SELECT COUNT(*) FROM memory_nodes WHERE {scope} AND COALESCE(discredited, 0) = 1",  # noqa: S608
        params,
    ).fetchone()
    edges = conn.execute(
        "SELECT source_id, target_id, relation_type FROM memory_relations"
    ).fetchall()
    linked: set[str] = set()
    superseded: set[str] = set()
    contradictions: list[tuple[str, str]] = []
    for source, target, kind in edges:
        if source not in live or target not in live:
            continue
        linked.update((source, target))
        if kind == MemoryRelationType.SUPERSEDES:
            superseded.add(target)
        elif kind == MemoryRelationType.CONTRADICTS:
            contradictions.append((source, target))
    contested = {
        node for pair in contradictions if not superseded.intersection(pair) for node in pair
    }
    return {
        "live": live,
        "discredited": discredited,
        "unlinked": len(live.keys() - linked),
        "superseded": len(superseded),
        "contested": len(contested),
        "stale": sum(1 for updated in live.values() if updated < stale_before),
    }


def measure(
    session: Session,
    memory: AgentMemoryService,
    workspace_slug: str,
    *,
    now: datetime | None = None,
) -> GraphHealthReading:
    """Measure one workspace graph now. Raises if the graph is not configured."""
    moment = now or utcnow()
    stale_before = (moment - timedelta(days=STALE_AFTER_DAYS)).replace(microsecond=0).isoformat()
    graph = memory.require_graph(workspace_slug)
    with graph.connection() as conn:
        census = _census(conn, workspace_slug, stale_before)
    live_ids = list(census["live"])
    surfaced: set[str] = set()
    scored: set[str] = set()
    if live_ids:
        for node_id, outcome in session.exec(
            select(LearningApplication.node_id, LearningApplication.outcome).where(
                col(LearningApplication.node_id).in_(live_ids)
            )
        ).all():
            surfaced.add(node_id)
            if outcome is not None:
                scored.add(node_id)
    figures = GraphFigures(
        learnings=len(live_ids),
        unlinked=census["unlinked"],
        never_surfaced=len(live_ids) - len(surfaced),
        surfaced_unscored=len(surfaced - scored),
        stale=census["stale"],
        contested=census["contested"],
        superseded=census["superseded"],
        discredited=census["discredited"],
    )
    return _reading(workspace_slug, moment, figures)


def _reading(workspace_slug: str, moment: datetime, figures: GraphFigures) -> GraphHealthReading:
    return GraphHealthReading(
        workspace_slug=workspace_slug,
        measured_at=moment,
        figures=figures,
        shares={name: figures.share(name) for name in _ADVERSE},
    )


def record_snapshot(session: Session, reading: GraphHealthReading) -> MemoryHealthSnapshot:
    """Append a reading. Never overwrites: the history is the point."""
    row = MemoryHealthSnapshot(
        workspace_slug=reading.workspace_slug,
        taken_at=reading.measured_at,
        **reading.figures.model_dump(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def snapshots(
    session: Session, workspace_slug: str, *, limit: int = 24
) -> list[GraphHealthReading]:
    """Recorded readings for a workspace, newest first."""
    rows = session.exec(
        select(MemoryHealthSnapshot)
        .where(MemoryHealthSnapshot.workspace_slug == workspace_slug)
        .order_by(col(MemoryHealthSnapshot.taken_at).desc())
        .limit(limit)
    ).all()
    return [
        _reading(
            row.workspace_slug,
            row.taken_at,
            GraphFigures.model_validate(row.model_dump(include=set(GraphFigures.model_fields))),
        )
        for row in rows
    ]


def compare(current: GraphHealthReading, previous: GraphHealthReading | None) -> GraphHealthReport:
    """The current reading against the last recorded one, in words where it matters."""
    if previous is None:
        return GraphHealthReport(
            current=current,
            previous=None,
            moved=[],
            notes=["No earlier snapshot to compare against. Record one to start a trend."],
            watch=None,
        )
    moved = [
        Movement(metric=name, was=previous.shares[name], now=current.shares[name])
        for name in _ADVERSE
        if abs(current.shares[name] - previous.shares[name]) >= _NOTEWORTHY_POINTS
    ]
    notes: list[str] = []
    grew = current.figures.learnings > previous.figures.learnings
    unlinked_rose = current.shares["unlinked"] - previous.shares["unlinked"] >= _NOTEWORTHY_POINTS
    if grew and unlinked_rose:
        notes.append(
            f"Learnings grew from {previous.figures.learnings} to {current.figures.learnings} "
            f"while the unlinked share rose from {previous.shares['unlinked']}% to "
            f"{current.shares['unlinked']}%: new learnings are being written without relations."
        )
    if not moved:
        notes.append("Nothing moved by more than a couple of points since the last snapshot.")
    rising = [m for m in moved if m.now > m.was]
    watch = min(rising, key=lambda m: _ADVERSE.index(m.metric)).metric if rising else None
    return GraphHealthReport(
        current=current, previous=previous, moved=moved, notes=notes, watch=watch
    )
