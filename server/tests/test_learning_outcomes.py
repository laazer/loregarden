"""The observed-outcome ladder and the confidence it feeds (lg-improved-memory-178)."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import timedelta
from functools import partial
from unittest.mock import patch

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
    MemoryBriefing,
    MemoryBriefingAssembly,
    OrchestratorDecision,
    RunStatus,
    TicketState,
    utcnow,
)
from loregarden.services import learning_outcomes
from loregarden.services.invariants import run_invariant
from loregarden.services.learning_confidence import (
    UNOBSERVED,
    LearningConfidence,
    Observation,
    score,
)
from loregarden.services.learning_ledger_invariants import (
    BRIEFINGS_COUNT_WHAT_THEY_DELIVERED,
    CONCLUDED_RUNS_GRADE_THEIR_LEARNINGS,
    LINKED_SINCE,
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
    expected = score(events)
    for seed in range(10):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert score(shuffled) == expected


def test_confidence_is_sample_size_aware():
    one = score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)])
    many = score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)] * 10)

    assert many.lower_bound > one.lower_bound
    # Corroboration gate: one save cannot mint a trusted lesson.
    assert not one.trusted
    assert many.trusted


def test_a_fresh_negative_outweighs_an_old_positive():
    mixed = score(
        [
            Observation(LearningOutcomeRung.CLEAN_PASS, T0 - timedelta(days=180)),
            Observation(LearningOutcomeRung.BLOCKED, T0),
        ]
    )
    assert mixed.mean < 0.5


def test_a_learning_recovers_by_holding_up_after_its_latest_failure():
    before = [Observation(LearningOutcomeRung.CLEAN_PASS, T0 - timedelta(days=d)) for d in (9, 8)]
    failure = [Observation(LearningOutcomeRung.BLOCKED, T0 - timedelta(days=5))]
    after = [Observation(LearningOutcomeRung.CLEAN_PASS, T0 - timedelta(days=d)) for d in (3, 2)]

    assert score(before + failure + after).mean > score(after + failure).mean > 0.5
    # Successes after the failure count in full; those before it at half weight.
    assert score(before + failure + after).alpha == 1.0 + 0.5 * 2 + 2


def test_evidence_does_not_decay_with_age():
    """Time-based decay is rejected: a fact is invalidated by events, not by a clock."""
    old = score([Observation(LearningOutcomeRung.CLEAN_PASS, T0 - timedelta(days=1000))])
    fresh = score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)])
    assert (old.alpha, old.beta) == (fresh.alpha, fresh.beta)


def test_no_observations_is_the_prior_and_says_so():
    assert score([]) == UNOBSERVED
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


def _grade(session) -> int:
    return run_invariant(session, CONCLUDED_RUNS_GRADE_THEIR_LEARNINGS).repaired


def test_the_pass_grades_a_run_its_stage_moved_past_and_confidence_reads_it(world):
    session, workspace, ticket = world
    first = _run(session, workspace, ticket, started=T0)
    _record(session, first, ticket, ["n-a", "n-b"])
    second = _run(
        session,
        workspace,
        ticket,
        started=T0 + timedelta(hours=1),
        code="R2",
        status=RunStatus.RUNNING,
    )
    _record(session, second, ticket, ["n-a"])

    assert _grade(session) == 1

    by_run = {(r.run_id, r.node_id): r for r in _applications(session)}
    assert by_run[first.id, "n-a"].outcome is LearningOutcomeRung.CLEAN_PASS
    assert by_run[first.id, "n-b"].outcome is LearningOutcomeRung.CLEAN_PASS
    assert by_run[second.id, "n-a"].settled_at is None
    confidence = learning_outcomes.confidence_for(session, ["n-a", "n-unseen"])
    assert confidence["n-a"].observations == 1
    assert confidence["n-unseen"] is UNOBSERVED
    assert _grade(session) == 0  # and there is nothing left to find


def test_runs_that_cannot_be_graded_never_hold_back_those_that_can(world):
    """The oldest rows were all open, filled the batch, and nothing was graded for 51 days.

    Half of these reach grading and come back pending (the block names another
    stage), so it is the bound itself that must not count them.
    """
    session, workspace, ticket = world
    for index in range(6):
        stuck = make_ticket(
            session,
            workspace_id=workspace.id,
            ticket_id=f"t-stuck-{index}",
            state=TicketState.BLOCKED,
        )
        _record(
            session, _run(session, workspace, stuck, started=T0, code=f"S{index}"), stuck, ["n"]
        )
        if index % 2:
            _event(
                session,
                stuck,
                EventType.ORCHESTRATOR_DECISION,
                T0 + timedelta(minutes=1),
                stage_key="review",
                decision=OrchestratorDecision.CLASSIFIED_BLOCK.value,
            )
    ready = _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="READY")
    _record(session, ready, ticket, ["n"])
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    found = [run.id for run, _ in learning_outcomes.gradable_runs(session, limit=1)]

    assert found == [ready.id]
    assert _grade(session) == 1


def test_a_backlog_larger_than_one_pass_drains_over_several(world):
    session, workspace, ticket = world
    for index in range(5):
        run = _run(
            session, workspace, ticket, started=T0 + timedelta(hours=index), code=f"B{index}"
        )
        _record(session, run, ticket, ["n"])
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    two_a_pass = partial(learning_outcomes.gradable_runs, limit=2)
    with patch("loregarden.services.learning_ledger_invariants.gradable_runs", two_a_pass):
        assert [_grade(session) for _ in range(4)] == [2, 2, 1, 0]


@pytest.mark.parametrize(
    "conclude",
    ["cancelled", "never_started", "ticket_done", "ticket_wont_do", "reran", "blocked"],
)
def test_every_way_a_run_concludes_is_found(world, conclude):
    """The candidate query must cover every case `derive_settlement` settles."""
    session, workspace, ticket = world
    run = _run(session, workspace, ticket, started=T0)
    _record(session, run, ticket, ["n"])
    assert list(learning_outcomes.gradable_runs(session)) == []
    if conclude == "cancelled":
        run.status = RunStatus.CANCELLED
    elif conclude == "never_started":
        run.started_at = None
    elif conclude == "ticket_done":
        ticket.state = TicketState.DONE
    elif conclude == "ticket_wont_do":
        ticket.state = TicketState.WONT_DO
    elif conclude == "reran":
        _run(session, workspace, ticket, started=T0 + timedelta(hours=1), code="R2")
    else:
        _event(
            session,
            ticket,
            EventType.ORCHESTRATOR_DECISION,
            T0 + timedelta(minutes=1),
            decision=OrchestratorDecision.CLASSIFIED_BLOCK.value,
        )
    session.add(run)
    session.add(ticket)
    session.commit()

    assert [found.id for found, _ in learning_outcomes.gradable_runs(session)] == [run.id]


# ---------------------------------------------------------------------------
# Counts — a briefing claims the learnings its run was linked to, no more.
# ---------------------------------------------------------------------------


def _claiming(session, run, ticket, *, claimed, linked):
    return record_briefing(
        session,
        run,
        ticket,
        replace(
            InheritedWisdom.not_attempted(),
            learnings_injected=claimed,
            learning_node_ids=tuple(linked),
        ),
        skipped=False,
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )


def _claimed(session, briefing_id) -> int:
    session.expire_all()
    return session.get(MemoryBriefing, briefing_id).learnings_injected


def test_an_overcounted_briefing_is_corrected_to_what_was_linked(world):
    session, workspace, ticket = world
    over = _claiming(
        session, _run(session, workspace, ticket, started=T0), ticket, claimed=5, linked=["n-a"]
    )
    honest_run = _run(session, workspace, ticket, started=T0, code="R2")
    honest = _claiming(session, honest_run, ticket, claimed=2, linked=["n-a", "n-b"])
    # The second assembly of one run links nothing new and is still honest.
    again = _claiming(session, honest_run, ticket, claimed=2, linked=["n-a", "n-b"])

    check = run_invariant(session, BRIEFINGS_COUNT_WHAT_THEY_DELIVERED)

    assert (check.found, check.repaired) == (1, 1)
    assert [_claimed(session, b) for b in (over, honest, again)] == [1, 2, 2]


def test_a_briefing_from_before_linkage_is_left_alone(world):
    session, workspace, ticket = world
    briefing = _claiming(
        session, _run(session, workspace, ticket, started=T0), ticket, claimed=5, linked=[]
    )
    row = session.get(MemoryBriefing, briefing)
    row.created_at = LINKED_SINCE - timedelta(days=1)
    session.add(row)
    session.commit()

    assert run_invariant(session, BRIEFINGS_COUNT_WHAT_THEY_DELIVERED).found == 0
    assert _claimed(session, briefing) == 5


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
    return score([Observation(LearningOutcomeRung.CLEAN_PASS, T0)] * 8)


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
