"""The observed-outcome ladder and the confidence it feeds (lg-improved-memory-178)."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import timedelta

import pytest
from loregarden.agents.inherited_wisdom import InheritedWisdom, build_inherited_wisdom
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    DomainEvent,
    EventType,
    GateFixTier,
    GateOutcome,
    LearningApplication,
    LearningOutcomeRung,
    MemoryBriefingAssembly,
    OrchestratorDecision,
    RunStatus,
    TicketState,
    utcnow,
)
from loregarden.services import learning_outcomes
from loregarden.services.learning_confidence import (
    UNOBSERVED,
    LearningConfidence,
    Observation,
    score,
)
from loregarden.services.memory_briefing_telemetry import record_briefing
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.rework_feedback import (
    REWORK_FEEDBACK_KIND,
    rework_feedback_artifact_title,
)
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_ticket, make_workspace
from tests.memory_helpers import briefing_ticket

T0 = utcnow() - timedelta(days=2)
STAGE = "implement"


# ---------------------------------------------------------------------------
# Scoring — a Beta posterior, order-independent and sample-size aware.
# ---------------------------------------------------------------------------


def _observations(seed: int, count: int) -> list[Observation]:
    rng = random.Random(seed)
    return [
        Observation(
            rung=rng.choice(list(LearningOutcomeRung)),
            observed_at=T0 - timedelta(days=rng.uniform(0, 400)),
        )
        for _ in range(count)
    ]


def test_shuffled_event_order_yields_identical_confidence():
    events = _observations(seed=7, count=200)
    expected = score(events, as_of=T0)
    for seed in range(10):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert score(shuffled, as_of=T0) == expected


def test_confidence_is_sample_size_aware():
    one = score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)], as_of=T0)
    many = score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)] * 10, as_of=T0)

    assert many.lower_bound > one.lower_bound
    # Corroboration gate: one save cannot mint a trusted lesson.
    assert not one.trusted
    assert many.trusted


def test_a_fresh_negative_outweighs_an_old_positive():
    mixed = score(
        [
            Observation(LearningOutcomeRung.CLEAN_PASS, T0 - timedelta(days=180)),
            Observation(LearningOutcomeRung.BLOCKED, T0),
        ],
        as_of=T0,
    )
    assert mixed.mean < 0.5


def test_no_observations_is_the_prior_and_says_so():
    assert score([], as_of=T0) == UNOBSERVED
    assert UNOBSERVED.observations == 0


# ---------------------------------------------------------------------------
# Ladder — derived from recorded signals, in the run's own stage window.
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db_session: Session):
    workspace = make_workspace(db_session, slug="ladder")
    ticket = make_ticket(
        db_session, workspace_id=workspace.id, ticket_id="t-ladder", state=TicketState.IN_PROGRESS
    )
    return db_session, workspace, ticket


def _run(session, workspace, ticket, *, started, status=RunStatus.SUCCEEDED, code="R1"):
    return make_agent_run(
        session,
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_code=code,
        stage_key=STAGE,
        status=status,
        started_at=started,
        finished_at=started + timedelta(minutes=5),
    )


def _event(session, ticket, event_type, at, **payload):
    session.add(
        DomainEvent(
            type=event_type,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload_json=json.dumps({"stage_key": STAGE, **payload}),
            created_at=at,
        )
    )
    session.commit()


def _settle(session, run) -> learning_outcomes.Settlement:
    session.refresh(run)
    return learning_outcomes.derive_settlement(session, run)


def test_an_open_window_is_pending_not_a_rung(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)

    assert _settle(session, run) == learning_outcomes.Settlement(settled=False)


def test_a_run_whose_stage_moved_on_cleanly_is_a_clean_pass(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="R2")

    assert _settle(session, run).rung is LearningOutcomeRung.CLEAN_PASS


def test_a_mechanical_gate_fix_is_passed_after_autofix(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    _event(
        session,
        ticket,
        EventType.GATE_EVALUATED,
        T0 + timedelta(minutes=10),
        outcome=GateOutcome.PASSED.value,
        fix_tier=GateFixTier.MECHANICAL.value,
    )
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert _settle(session, run).rung is LearningOutcomeRung.PASSED_AFTER_AUTOFIX


def test_rework_sent_back_to_this_stage_is_rerouted(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=REWORK_FEEDBACK_KIND,
            title=rework_feedback_artifact_title(STAGE),
            created_at=T0 + timedelta(minutes=30),
        )
    )
    session.commit()
    _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="R2")

    assert _settle(session, run).rung is LearningOutcomeRung.REROUTED


def test_a_block_settles_immediately_and_outranks_everything(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    _event(
        session,
        ticket,
        EventType.GATE_EVALUATED,
        T0 + timedelta(minutes=10),
        outcome=GateOutcome.PASSED.value,
        fix_tier=GateFixTier.MECHANICAL.value,
    )
    _event(
        session,
        ticket,
        EventType.ORCHESTRATOR_DECISION,
        T0 + timedelta(minutes=11),
        decision=OrchestratorDecision.CLASSIFIED_BLOCK.value,
    )

    assert _settle(session, run) == learning_outcomes.Settlement(
        settled=True, rung=LearningOutcomeRung.BLOCKED
    )


def test_a_signal_after_the_stage_reran_belongs_to_the_rerun(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="R2")
    _event(
        session,
        ticket,
        EventType.ORCHESTRATOR_DECISION,
        T0 + timedelta(hours=2),
        decision=OrchestratorDecision.CLASSIFIED_BLOCK.value,
    )

    assert _settle(session, run).rung is LearningOutcomeRung.CLEAN_PASS


def test_a_gate_failure_for_another_stage_is_not_this_runs(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.ERROR,
            title="Transition gate failed — review",
            created_at=T0 + timedelta(minutes=30),
        )
    )
    session.commit()
    _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="R2")

    assert _settle(session, run).rung is LearningOutcomeRung.CLEAN_PASS


def test_a_cancelled_run_settles_with_no_rung(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0, status=RunStatus.CANCELLED)

    assert _settle(session, run) == learning_outcomes.Settlement(settled=True, rung=None)


def test_a_run_still_going_is_pending(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0, status=RunStatus.RUNNING)

    assert not _settle(session, run).settled


# ---------------------------------------------------------------------------
# Linkage — record_briefing links injected learnings; settlement grades them.
# ---------------------------------------------------------------------------


def _record(session, run, ticket, node_ids):
    return record_briefing(
        session,
        run,
        ticket,
        replace(InheritedWisdom.not_attempted(), learning_node_ids=tuple(node_ids)),
        skipped=False,
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )


def _applications(session) -> list[LearningApplication]:
    session.expire_all()
    return list(session.exec(select(LearningApplication)).all())


def test_each_surfaced_learning_is_linked_to_the_real_run_once(world):
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)

    briefing_id = _record(session, run, ticket, ["n-a", "n-b"])
    _record(session, run, ticket, ["n-a", "n-b"])  # second assembly, same run

    rows = _applications(session)
    assert sorted((r.run_id, r.node_id) for r in rows) == [(run.id, "n-a"), (run.id, "n-b")]
    assert {r.briefing_id for r in rows} == {briefing_id}
    assert all(r.settled_at is None and r.outcome is None for r in rows)


def test_a_later_briefing_settles_the_earlier_run_and_confidence_reads_it(world):
    session, workspace, ticket = world
    first = _run(session, workspace, ticket, started=T0)
    _record(session, first, ticket, ["n-a"])
    second = _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="R2")
    second.status = RunStatus.RUNNING
    session.add(second)
    session.commit()

    _record(session, second, ticket, ["n-a"])

    by_run = {r.run_id: r for r in _applications(session)}
    assert by_run[first.id].outcome is LearningOutcomeRung.CLEAN_PASS
    assert by_run[second.id].settled_at is None
    confidence = learning_outcomes.confidence_for(session, ["n-a", "n-unseen"])
    assert confidence["n-a"].observations == 1
    assert confidence["n-unseen"] is UNOBSERVED


# ---------------------------------------------------------------------------
# Briefing — confidence reaches the text: ordering and annotation.
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_memory(tmp_path) -> tuple[AgentMemoryService, dict[str, str]]:
    memory = AgentMemoryService(graph_sqlite_base=tmp_path / "memory.sqlite")
    ids = {}
    for key, title in (
        ("weak", "Rate limiting weak lesson"),
        ("strong", "Rate limiting strong lesson"),
    ):
        node = memory.upsert_memory(
            title=title, body="Rate limiting on the public API.", workspace_slug="lg"
        )["graph"]
        ids[key] = node["id"]
    return memory, ids


def _trusted() -> LearningConfidence:
    return score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)] * 8, as_of=T0)


def test_briefing_orders_by_confidence_and_annotates_it(graph_memory):
    memory, ids = graph_memory

    result = build_inherited_wisdom(
        briefing_ticket(),
        "lg",
        memory=memory,
        confidence_lookup=lambda node_ids: {ids["strong"]: _trusted()},
    )

    assert result.learning_node_ids[0] == ids["strong"]
    assert set(result.learning_node_ids) == set(ids.values())
    assert result.text.index("strong lesson") < result.text.index("weak lesson")
    assert "over 8 observed runs, trusted" in result.text
    assert "no observed outcomes yet" in result.text


def test_a_failed_confidence_lookup_degrades_visibly(graph_memory):
    memory, _ = graph_memory

    def broken(_ids):
        raise RuntimeError("db gone")

    result = build_inherited_wisdom(
        briefing_ticket(), "lg", memory=memory, confidence_lookup=broken
    )

    assert result.confidence_unavailable is True
    assert "Rate limiting" in result.text
    assert "observed" not in result.text
