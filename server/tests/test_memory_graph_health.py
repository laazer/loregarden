"""Graph health: shares of live learnings, snapshots, and what moved."""

from __future__ import annotations

from datetime import timedelta

import pytest
from loregarden.models.domain import (
    LearningApplication,
    LearningOutcomeRung,
    MemoryRelationType,
    RunStatus,
    utcnow,
)
from loregarden.services import memory_graph_health as health
from loregarden.services.memory_store import AgentMemoryService
from tests.factories import make_agent_run, make_workspace
from tests.memory_helpers import frozen_clock

WS = "lg"


@pytest.fixture
def memory() -> AgentMemoryService:
    return AgentMemoryService.from_settings()


def _node(memory, title, **kwargs):
    return memory.upsert_memory(title=title, body="b", workspace_slug=WS, **kwargs)["graph"]["id"]


def _surface(session, node_id, *, outcome=None, code="R1"):
    workspace = make_workspace(session, slug="health-ws")
    run = make_agent_run(
        session, workspace_id=workspace.id, run_code=code, status=RunStatus.SUCCEEDED
    )
    session.add(
        LearningApplication(
            run_id=run.id,
            node_id=node_id,
            workspace_id=workspace.id,
            outcome=outcome,
            settled_at=utcnow() if outcome else None,
        )
    )
    session.commit()


def test_every_figure_is_measured_from_the_graph_and_the_ledger(db_session, memory):
    with frozen_clock("2020-01-01T00:00:00+00:00"):
        stale = _node(memory, "Stale and alone")
    a, b, c = _node(memory, "A"), _node(memory, "B"), _node(memory, "C")
    old = _node(memory, "Old")
    withdrawn = _node(memory, "Withdrawn")
    memory.create_relation(
        source_id=a, target_id=b, relation_type=MemoryRelationType.CONTRADICTS, workspace_slug=WS
    )
    memory.create_relation(
        source_id=c, target_id=old, relation_type=MemoryRelationType.SUPERSEDES, workspace_slug=WS
    )
    memory.set_discredited(
        node_id=withdrawn, workspace_slug=WS, discredited=True, reason="r", writer="t"
    )
    _surface(db_session, a, outcome=LearningOutcomeRung.CLEAN_PASS, code="R1")
    _surface(db_session, b, code="R2")

    reading = health.measure(db_session, memory, WS)

    assert reading.figures.model_dump() == {
        "learnings": 5,
        "unlinked": 1,  # stale
        "never_surfaced": 3,  # stale, c, old
        "surfaced_unscored": 1,  # b
        "stale": 1,
        "contested": 2,  # a and b
        "superseded": 1,  # old
        "discredited": 1,
    }
    assert reading.shares["unlinked"] == 20.0
    assert stale  # the node exists; it is the unlinked, stale one


def test_an_empty_graph_reads_as_zero_learnings_not_as_perfect_health(db_session, memory):
    reading = health.measure(db_session, memory, WS)
    assert reading.figures.learnings == 0
    assert set(reading.shares.values()) == {0.0}


def test_without_an_earlier_snapshot_the_report_says_so(db_session, memory):
    _node(memory, "A")
    report = health.compare(health.measure(db_session, memory, WS), None)
    assert report.previous is None and report.watch is None
    assert "No earlier snapshot" in report.notes[0]


def test_snapshots_are_appended_and_read_newest_first(db_session, memory):
    _node(memory, "A")
    first = health.measure(db_session, memory, WS, now=utcnow() - timedelta(days=30))
    second = health.measure(db_session, memory, WS)
    health.record_snapshot(db_session, first)
    health.record_snapshot(db_session, second)

    history = health.snapshots(db_session, WS)
    assert len(history) == 2
    assert history[0].measured_at > history[1].measured_at


def test_growth_with_a_rising_unlinked_share_is_named_and_watched(db_session, memory):
    a, b = _node(memory, "A"), _node(memory, "B")
    memory.create_relation(source_id=a, target_id=b, workspace_slug=WS)
    before = health.measure(db_session, memory, WS)
    _node(memory, "C")
    _node(memory, "D")
    after = health.measure(db_session, memory, WS)

    report = health.compare(after, before)

    assert report.watch == "unlinked"
    assert any("written without relations" in note for note in report.notes)
    moved = {m.metric: (m.was, m.now) for m in report.moved}
    assert moved["unlinked"] == (0.0, 50.0)
