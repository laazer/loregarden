"""Gate eval episodes and scorecard, built from small hand-made event lists.

The eval's value is comparing repair behaviour across adapters and models, so the
rules that decide what counts as an attempt, a repair and an identical
resubmission are pinned here directly — plus the loader, against real
`GateEvaluated` rows, because a scorecard over a query that silently dropped rows
would still look plausible.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from loregarden.cli.gate_eval import render_table
from loregarden.models.domain import (
    CliAdapter,
    DomainEvent,
    EventType,
    GateFailureKind,
    GateFixTier,
    GateOutcome,
    TicketState,
)
from loregarden.services.gate_eval import (
    GateEvent,
    build_episodes,
    build_scorecard,
    gate_scorecard,
)
from loregarden.services.gate_producer import ProducerJoin
from sqlmodel import Session
from tests.factories import make_agent_run, make_orchestration_run, make_ticket, make_workspace

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
FORMAT = "Would reformat: src/a.py"
PY_ORG = "Python organization check failed:\nsrc/a.py:3: isinstance"
TIMEOUT = "Gate command timed out after 300s"

_seq = iter(range(10_000))


def ev(
    outcome: GateOutcome,
    message: str = "",
    *,
    minute: int,
    ticket: str = "t1",
    ws: str = "blobert",
    from_stage: str = "implement",
    to_stage: str = "script_review",
    fix_tier: GateFixTier | None = GateFixTier.NONE,
    state: TicketState = TicketState.DONE,
) -> GateEvent:
    return GateEvent(
        event_id=f"e{next(_seq):05d}",
        at=T0 + timedelta(minutes=minute),
        ticket_id=ticket,
        ticket_ref=ticket,
        workspace=ws,
        ticket_state=state,
        outcome=outcome,
        message=message,
        from_stage=from_stage,
        to_stage=to_stage,
        fix_tier=fix_tier,
    )


P, F, U = GateOutcome.PASSED, GateOutcome.FAILED, GateOutcome.UNAVAILABLE


def test_attempts_group_by_ticket_and_normalized_transition_in_time_order():
    events = [
        ev(P, minute=30, from_stage="implement", fix_tier=GateFixTier.AGENT),
        ev(F, PY_ORG, minute=0, from_stage="implementation"),
        ev(P, minute=5, ticket="t2", from_stage="test_design", to_stage="test_break"),
    ]
    episodes = build_episodes(events)
    assert [(e.ticket_id, e.transition, e.attempts) for e in episodes] == [
        ("t1", "implement→script_review", 2),
        ("t2", "test-design→test-break", 1),
    ]
    repaired = episodes[0]
    assert not repaired.first_pass
    assert repaired.eventually_passed
    assert repaired.attempts_to_pass == 2
    assert repaired.failure_kinds == [GateFailureKind.WORK]


def test_skipped_and_disabled_evaluations_are_not_attempts():
    events = [
        ev(GateOutcome.SKIPPED, minute=0),
        ev(GateOutcome.DISABLED, minute=1),
        ev(P, minute=2),
    ]
    [episode] = build_episodes(events)
    assert (episode.attempts, episode.first_pass) == (1, True)


def test_unavailable_is_a_failed_harness_attempt():
    [episode] = build_episodes([ev(U, "npx: not found", minute=0), ev(P, minute=5)])
    assert episode.failure_kinds == [GateFailureKind.HARNESS]
    assert episode.first_attempt_clean
    assert not episode.first_pass


def test_identical_work_failure_after_an_agent_fix_is_a_resubmission():
    events = [
        ev(F, PY_ORG, minute=0),
        ev(F, PY_ORG, minute=10, fix_tier=GateFixTier.AGENT),
    ]
    [episode] = build_episodes(events)
    assert episode.repeated_identical_failure
    assert not episode.eventually_passed


def test_identical_failure_after_only_a_mechanical_fixer_is_not_a_resubmission():
    events = [
        ev(F, PY_ORG, minute=0),
        ev(F, PY_ORG, minute=1, fix_tier=GateFixTier.MECHANICAL),
    ]
    [episode] = build_episodes(events)
    assert not episode.repeated_identical_failure


def test_identical_failure_without_a_recorded_fix_tier_counts():
    # Older events have no fix_tier and cannot say who re-ran the stage.
    events = [ev(F, PY_ORG, minute=0, fix_tier=None), ev(F, PY_ORG, minute=9, fix_tier=None)]
    assert build_episodes(events)[0].repeated_identical_failure


def test_identical_harness_failures_are_not_a_resubmission():
    events = [ev(F, TIMEOUT, minute=0), ev(F, TIMEOUT, minute=9, fix_tier=GateFixTier.AGENT)]
    assert not build_episodes(events)[0].repeated_identical_failure


def test_identical_failures_must_be_consecutive():
    events = [
        ev(F, PY_ORG, minute=0),
        ev(F, FORMAT, minute=5, fix_tier=GateFixTier.AGENT),
        ev(F, PY_ORG, minute=9, fix_tier=GateFixTier.AGENT),
    ]
    assert not build_episodes(events)[0].repeated_identical_failure


def test_scorecard_headlines():
    events = [
        # t1: clean first pass
        ev(P, minute=0, ticket="t1"),
        # t2: work failure, identical resubmission, then repaired on attempt 3
        ev(F, PY_ORG, minute=0, ticket="t2"),
        ev(F, PY_ORG, minute=5, ticket="t2", fix_tier=GateFixTier.AGENT),
        ev(P, minute=9, ticket="t2", fix_tier=GateFixTier.AGENT),
        # t3: harness, then pass
        ev(F, TIMEOUT, minute=0, ticket="t3"),
        ev(P, minute=5, ticket="t3"),
        # t4: work failure, never repaired
        ev(F, FORMAT, minute=0, ticket="t4", state=TicketState.BLOCKED),
        # t5: inherited at triage, in another workspace
        ev(F, FORMAT, minute=0, ticket="t5", ws="loregarden", from_stage="triage", to_stage="plan"),
    ]
    card = build_scorecard(build_episodes(events))
    blobert = card.by_workspace["blobert"]
    assert blobert.episodes == 4
    assert blobert.first_attempt_clean_rate == 0.5  # t1, t3
    assert blobert.first_pass_rate == 0.25
    assert blobert.harness_blocked_rate == 0.25
    assert blobert.repair_episodes == 2  # t2, t4
    assert blobert.repair_recovery_rate == 0.5
    assert blobert.identical_resubmission_rate == 0.5
    assert (blobert.attempts_to_pass_median, blobert.attempts_to_pass_max) == (2.0, 3)

    lore = card.by_workspace["loregarden"]
    assert lore.first_attempt_clean_rate == 1.0
    assert lore.repair_episodes == 0
    assert lore.repair_recovery_rate is None  # nothing to recover, not 0%
    assert list(card.by_transition["loregarden"]) == ["triage→plan"]

    assert [(n.ticket_ref, n.ticket_state) for n in card.never_passed] == [
        ("t4", TicketState.BLOCKED),
        ("t5", TicketState.DONE),
    ]
    assert card.harness_causes == {"timeout": 1}
    assert card.overall.failures_by_kind == {
        GateFailureKind.HARNESS: 1,
        GateFailureKind.WORK: 3,
        GateFailureKind.INHERITED: 1,
    }


def test_unknown_failures_are_listed_for_the_rules_to_grow():
    card = build_scorecard(build_episodes([ev(F, "brand new failure", minute=0)]))
    assert [u.message for u in card.unknown_failures] == ["brand new failure"]
    assert card.overall.first_attempt_clean_rate == 1.0


def test_scorecard_is_deterministic_under_input_order():
    events = [
        ev(F, PY_ORG, minute=0, ticket="a"),
        ev(P, minute=3, ticket="a", fix_tier=GateFixTier.AGENT),
        ev(P, minute=1, ticket="b"),
    ]
    forward = build_scorecard(build_episodes(events)).model_dump_json()
    backward = build_scorecard(build_episodes(list(reversed(events)))).model_dump_json()
    assert forward == backward


# --- loader over real rows ---------------------------------------------------


def _gate_row(session: Session, ticket_id: str, workspace_id: str, at: datetime, **payload):
    base = {"from_stage": "implement", "to_stage": "script_review", "message": ""}
    session.add(
        DomainEvent(
            type=EventType.GATE_EVALUATED,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            payload_json=json.dumps({**base, **payload}),
            created_at=at,
        )
    )
    session.commit()


def test_loader_joins_producers_and_counts_unreadable_rows(db_session: Session):
    ws = make_workspace(db_session, slug="eval-ws")
    ticket = make_ticket(
        db_session, workspace_id=ws.id, external_id="EVAL-1", state=TicketState.DONE
    )
    orch = make_orchestration_run(db_session, workspace_id=ws.id, ticket_id=ticket.id)
    make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        orchestration_run_id=orch.id,
        stage_key="implement",
        command="/usr/local/bin/cursor-agent --print",
        model="gpt-5",
        finished_at=T0 - timedelta(minutes=1),
    )
    _gate_row(
        db_session,
        ticket.id,
        ws.id,
        T0,
        outcome="failed",
        message=PY_ORG,
        orchestration_run_id=orch.id,
        fix_tier="none",
    )
    _gate_row(
        db_session,
        ticket.id,
        ws.id,
        T0 + timedelta(minutes=5),
        outcome="passed",
        orchestration_run_id=orch.id,
        fix_tier="agent",
        agent_run_id="recorded-run",
        agent_id="backend_implementer",
        adapter="claude",
        model="claude-opus-5-5",
    )
    _gate_row(db_session, ticket.id, ws.id, T0, outcome="not-an-outcome")

    card, [episode] = gate_scorecard(db_session, workspace_slug="eval-ws")

    assert card.unreadable_events == 1
    assert (episode.ticket_ref, episode.attempts, episode.attempts_to_pass) == ("EVAL-1", 2, 2)
    assert episode.producer is not None
    assert (episode.producer.adapter, episode.producer.model, episode.producer.join) == (
        CliAdapter.CURSOR,
        "gpt-5",
        ProducerJoin.ORCHESTRATION_RUN,
    )
    assert list(card.by_producer) == ["cursor/gpt-5"]
    assert card.producer_coverage.by_join == {"orchestration_run": 1}


def test_endpoint_serves_the_scorecard(client: TestClient, db_session: Session):
    ws = make_workspace(db_session, slug="eval-api")
    ticket = make_ticket(db_session, workspace_id=ws.id, external_id="EVAL-API")
    _gate_row(db_session, ticket.id, ws.id, T0, outcome="failed", message=FORMAT)

    response = client.get(
        "/api/parallel/gate-scorecard", params={"workspace": "eval-api", "episodes": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scorecard"]["overall"]["repair_episodes"] == 1
    assert [e["ticket_ref"] for e in body["episodes"]] == ["EVAL-API"]


def test_table_names_every_episode_that_never_passed():
    card = build_scorecard(build_episodes([ev(F, FORMAT, minute=0, ticket="STUCK-9")]))
    assert "STUCK-9" in render_table(card)
