"""Producer-only clearing and audited waiver (lg-verdict-outcome-136).

Live derive stays honest in `stage_verdict`. The monotonic named set lives in
`unresolved_ledger`. persist never shrinks. clear (producer + live gone) and
waive (non-empty reason) are the only drops.

This file imports `unresolved_ledger` at module level on purpose: collection
fails until the module exists, which is the intended RED for test-design.
"""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    Artifact,
    InvariantReentry,
    ParallelAgentSpec,
    RunStatus,
    StageStatus,
    TicketState,
)
from loregarden.models.domain.enums import UnresolvedArtifactKind
from loregarden.services import unresolved_ledger as ledger
from loregarden.services.handoff_store import latest_handoff_doc
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.parallel_stage import (
    member_passed,
    member_result_from_run,
    reconcile_parallel_stage,
)
from loregarden.services.rework_feedback import MAX_REWORK_REROUTES, record_rework_feedback
from loregarden.services.rework_pause import ReworkPausePayload, file_rework_pause
from loregarden.services.stage_dispatch_prep import consume_dispatch_waiver
from loregarden.services.stage_report import StageReport
from loregarden.services.stage_verdict import UnresolvedItem, collect_signals, derive_verdict
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, col, select
from tests.test_stage_verdict import (
    _handoff,
    _item,
    _pass_report,
    _ticket,
)
from tests.test_stage_verdict_writers import (
    _agent_run,
    _fail_stdout,
    _incomplete_handoff,
    _pass_stdout,
    _running_ticket,
    _stage_status,
)

DURABLE = "handoff:test_suite_complete incomplete"
EPHEMERAL = "report:fail"
WAIVE_REASON = "accepted the remaining gap"

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "loregarden"


def _spec() -> ledger.LedgerActor:
    return ledger.LedgerActor(agent_id="spec", stage_key="spec")


def _designer() -> ledger.LedgerActor:
    return ledger.LedgerActor(agent_id="test_designer", stage_key="test-design")


def _human() -> ledger.LedgerActor:
    return ledger.LedgerActor(agent_id="human", stage_key="")


def _named(
    name: str,
    *,
    agent_id: str = "spec",
    stage_key: str = "spec",
) -> UnresolvedItem:
    return UnresolvedItem(
        name=name,
        producer_agent_id=agent_id,
        producer_stage_key=stage_key,
    )


def _live(
    session: Session,
    ticket,
    stage,
    *,
    agent_id: str = "spec",
    report: StageReport | None = None,
) -> tuple[UnresolvedItem, ...]:
    if report is None:
        report = _pass_report()
    return derive_verdict(
        collect_signals(
            session,
            ticket,
            report=report,
            tool_outcome=None,
            stage_def=stage,
            handoff_expected=True,
            agent_id=agent_id,
        )
    ).unresolved


def _newest_open(session: Session, ticket) -> object | None:
    rows = session.exec(
        select(Artifact)
        .where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == UnresolvedArtifactKind.OPEN,
        )
        .order_by(col(Artifact.created_at).desc())
    ).all()
    if not rows:
        return None
    return ledger.UnresolvedOpenPayload.model_validate_json(rows[0].content_json)


def _open_names(session: Session, ticket) -> list[str]:
    payload = _newest_open(session, ticket)
    if payload is None:
        return []
    return [item.name for item in payload.items]


def _kind_rows(session: Session, ticket, kind: UnresolvedArtifactKind) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == kind)
        ).all()
    )


def _visible_names(
    session: Session,
    ticket,
    live: tuple[UnresolvedItem, ...] | list[UnresolvedItem],
    *,
    current_stage_key: str,
    actor: ledger.LedgerActor | None = None,
) -> list[str]:
    return [
        item.name
        for item in ledger.visible_unresolved(
            session,
            ticket,
            live,
            current_stage_key=current_stage_key,
            actor=actor,
        )
    ]


# --------------------------------------------------------------------------- #
# Kinds + store shape
# --------------------------------------------------------------------------- #


def test_unresolved_artifact_kind_values_are_reserved_strings():
    assert UnresolvedArtifactKind.OPEN == "unresolved_open"
    assert UnresolvedArtifactKind.WAIVER == "unresolved_waiver"
    assert UnresolvedArtifactKind.CLEAR == "unresolved_clear"


def test_ledger_payload_models_exist():
    assert {f.name for f in fields(ledger.LedgerActor)} >= {"agent_id", "stage_key"}
    assert hasattr(ledger, "UnresolvedOpenPayload")
    assert hasattr(ledger, "UnresolvedWaiverPayload")
    assert hasattr(ledger, "UnresolvedClearPayload")


# --------------------------------------------------------------------------- #
# persist never shrinks; first persist copies producer
# --------------------------------------------------------------------------- #


def test_persist_unions_new_names_and_never_shrinks(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    first = _named(DURABLE)
    second = _named("handoff:test_all_runnable incomplete")
    ledger.persist_unresolved(db_session, ticket, [first, second], _spec())
    assert set(_open_names(db_session, ticket)) == {DURABLE, second.name}
    ledger.persist_unresolved(db_session, ticket, [first], _spec())
    assert set(_open_names(db_session, ticket)) == {DURABLE, second.name}


def test_first_persist_copies_producer_later_persist_does_not_overwrite(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    ledger.persist_unresolved(
        db_session,
        ticket,
        [_named(DURABLE, agent_id="test_designer", stage_key="test-design")],
        _designer(),
    )
    payload = _newest_open(db_session, ticket)
    item = next(entry for entry in payload.items if entry.name == DURABLE)
    assert item.producer_agent_id == "spec"
    assert item.producer_stage_key == "spec"


def test_first_seen_run_id_is_not_overwritten(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    remaining = ledger.settle_unresolved(
        db_session,
        ticket,
        [_named(DURABLE)],
        _spec(),
        current_stage_key="spec",
        run_id="run-1",
    )
    assert DURABLE in [item.name for item in remaining]
    ledger.settle_unresolved(
        db_session,
        ticket,
        [_named(DURABLE)],
        _spec(),
        current_stage_key="spec",
        run_id="run-2",
    )
    item = next(entry for entry in _newest_open(db_session, ticket).items if entry.name == DURABLE)
    assert item.first_seen_run_id == "run-1"


def test_persist_marks_handoff_and_evidence_durable_report_ephemeral(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(
        db_session,
        ticket,
        [
            _named(DURABLE),
            _named("evidence:real_surface missing"),
            _named(EPHEMERAL),
        ],
        _spec(),
    )
    by_name = {item.name: item for item in _newest_open(db_session, ticket).items}
    assert by_name[DURABLE].durable is True
    assert by_name["evidence:real_surface missing"].durable is True
    assert by_name[EPHEMERAL].durable is False


# --------------------------------------------------------------------------- #
# visible: persisted ∪ live, no writes; durable vs ephemeral
# --------------------------------------------------------------------------- #


def test_visible_unions_persisted_and_live_and_does_not_write(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    before = len(_kind_rows(db_session, ticket, UnresolvedArtifactKind.OPEN))
    live = [_named("handoff:test_all_runnable incomplete")]
    names = _visible_names(db_session, ticket, live, current_stage_key="spec", actor=_spec())
    assert DURABLE in names
    assert "handoff:test_all_runnable incomplete" in names
    assert len(_kind_rows(db_session, ticket, UnresolvedArtifactKind.OPEN)) == before


def test_durable_names_carry_across_stages_ephemeral_do_not(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(
        db_session,
        ticket,
        [_named(DURABLE), _named(EPHEMERAL)],
        _spec(),
    )
    at_spec = _visible_names(db_session, ticket, (), current_stage_key="spec", actor=_spec())
    at_implement = _visible_names(
        db_session, ticket, (), current_stage_key="implement", actor=_spec()
    )
    assert DURABLE in at_spec and EPHEMERAL in at_spec
    assert DURABLE in at_implement
    assert EPHEMERAL not in at_implement


def test_ephemeral_names_are_isolated_to_producing_parallel_member(db_session, tmp_path):
    reviewer = ledger.LedgerActor(agent_id="architecture_reviewer", stage_key="review")
    sibling = ledger.LedgerActor(agent_id="static_qa", stage_key="review")
    ticket, stage, _ = _ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id="architecture_reviewer"),
            ParallelAgentSpec(agent_id="static_qa"),
        ],
    )
    ledger.persist_unresolved(
        db_session,
        ticket,
        [
            _named(DURABLE, agent_id="architecture_reviewer", stage_key="review"),
            _named(EPHEMERAL, agent_id="architecture_reviewer", stage_key="review"),
        ],
        reviewer,
    )
    for_reviewer = _visible_names(
        db_session, ticket, (), current_stage_key="review", actor=reviewer
    )
    for_sibling = _visible_names(db_session, ticket, (), current_stage_key="review", actor=sibling)
    assert EPHEMERAL in for_reviewer
    assert EPHEMERAL not in for_sibling
    assert DURABLE in for_reviewer and DURABLE in for_sibling


# --------------------------------------------------------------------------- #
# clear: producer + live gone only
# --------------------------------------------------------------------------- #


def test_clear_drops_only_when_actor_is_producer_and_live_gone(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    dropped = ledger.clear(db_session, ticket, DURABLE, _spec(), live=())
    assert dropped is True
    assert DURABLE not in _open_names(db_session, ticket)
    clears = _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR)
    assert len(clears) == 1
    payload = ledger.UnresolvedClearPayload.model_validate_json(clears[0].content_json)
    assert payload.name == DURABLE
    assert payload.actor.agent_id == "spec"


def test_later_agent_cannot_clear_even_when_live_gone(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.clear(db_session, ticket, DURABLE, _designer(), live=()) is False
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []


def test_producer_cannot_clear_while_live_still_emits(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    live = [_named(DURABLE)]
    ledger.persist_unresolved(db_session, ticket, live, _spec())
    assert ledger.clear(db_session, ticket, DURABLE, _spec(), live=live) is False
    assert DURABLE in _open_names(db_session, ticket)


def test_human_cannot_clear(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.clear(db_session, ticket, DURABLE, _human(), live=()) is False
    assert DURABLE in _open_names(db_session, ticket)


def test_empty_producer_stage_key_matches_agent_id_only(db_session, tmp_path):
    """Handoff producer_stage_key is empty; the same agent may clear from a later stage."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    item = _named(DURABLE, agent_id="test_designer", stage_key="")
    ledger.persist_unresolved(db_session, ticket, [item], _designer())
    later = ledger.LedgerActor(agent_id="test_designer", stage_key="implement")
    assert ledger.clear(db_session, ticket, DURABLE, later, live=()) is True
    assert DURABLE not in _open_names(db_session, ticket)


def test_later_complete_handoff_from_a_different_agent_does_not_drop_recorded_name(
    db_session, tmp_path
):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")], from_agent="spec")
    live = _live(db_session, ticket, stage, agent_id="spec")
    ledger.persist_unresolved(db_session, ticket, live, _spec())
    assert DURABLE in _open_names(db_session, ticket)
    _handoff(
        db_session,
        ticket,
        [_item(status="complete", certainty="inferred")],
        from_agent="test_designer",
    )
    later_live = _live(db_session, ticket, stage, agent_id="test_designer")
    remaining = ledger.settle_unresolved(
        db_session,
        ticket,
        later_live,
        _designer(),
        current_stage_key="spec",
    )
    assert DURABLE in [item.name for item in remaining]
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []


def test_same_producer_completing_their_own_item_clears_it(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")], from_agent="spec")
    ledger.persist_unresolved(
        db_session, ticket, _live(db_session, ticket, stage, agent_id="spec"), _spec()
    )
    _handoff(
        db_session, ticket, [_item(status="complete", certainty="inferred")], from_agent="spec"
    )
    remaining = ledger.settle_unresolved(
        db_session,
        ticket,
        _live(db_session, ticket, stage, agent_id="spec"),
        _spec(),
        current_stage_key="spec",
    )
    assert DURABLE not in [item.name for item in remaining]
    assert DURABLE not in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR)


def test_missing_latest_handoff_doc_does_not_drop_recorded_names(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")], from_agent="spec")
    ledger.persist_unresolved(
        db_session, ticket, _live(db_session, ticket, stage, agent_id="spec"), _spec()
    )
    for row in db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "handoff")
    ).all():
        db_session.delete(row)
    db_session.commit()
    assert latest_handoff_doc(db_session, ticket.id) is None
    live = _live(db_session, ticket, stage, agent_id="spec")
    assert DURABLE not in [item.name for item in live]
    remaining = ledger.settle_unresolved(
        db_session, ticket, live, _designer(), current_stage_key="spec"
    )
    assert DURABLE in [item.name for item in remaining]
    assert DURABLE in _open_names(db_session, ticket)


def test_malformed_latest_envelope_does_not_drop_recorded_names(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")], from_agent="spec")
    ledger.persist_unresolved(
        db_session, ticket, _live(db_session, ticket, stage, agent_id="spec"), _spec()
    )
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind="handoff",
            title="handoff",
            content_json=json.dumps({"handoff": {"from_agent": 1, "checklist": "nope"}}),
        )
    )
    db_session.commit()
    live = _live(db_session, ticket, stage, agent_id="spec")
    assert DURABLE not in [item.name for item in live]
    remaining = ledger.settle_unresolved(
        db_session, ticket, live, _designer(), current_stage_key="spec"
    )
    assert DURABLE in [item.name for item in remaining]
    assert DURABLE in _open_names(db_session, ticket)


# --------------------------------------------------------------------------- #
# waive: non-empty reason, audited, producer-owned or human
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reason", ["", "   ", "\t", "\n", "\r\n", " \t\n "])
def test_waive_refuses_empty_and_whitespace_reason(db_session, tmp_path, reason):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.waive(db_session, ticket, DURABLE, reason, _spec(), run_id="r1") is False
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []


def test_waive_writes_audit_then_drops(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.waive(db_session, ticket, DURABLE, WAIVE_REASON, _spec(), run_id="r1") is True
    assert DURABLE not in _open_names(db_session, ticket)
    rows = _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)
    assert len(rows) == 1
    payload = ledger.UnresolvedWaiverPayload.model_validate_json(rows[0].content_json)
    assert payload.name == DURABLE
    assert payload.reason == WAIVE_REASON
    assert payload.actor.agent_id == "spec"
    assert payload.run_id == "r1"
    assert payload.when


def test_stage_report_waivers_from_later_agent_refused_producer_succeeds(db_session, tmp_path):
    from loregarden.services.stage_report import WaiverItem

    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    refused = ledger.settle_unresolved(
        db_session,
        ticket,
        [_named(DURABLE)],
        _designer(),
        waivers=[WaiverItem(name=DURABLE, reason=WAIVE_REASON)],
        current_stage_key="spec",
    )
    assert DURABLE in [item.name for item in refused]
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []
    remaining = ledger.settle_unresolved(
        db_session,
        ticket,
        [_named(DURABLE)],
        _spec(),
        waivers=[WaiverItem(name=DURABLE, reason=WAIVE_REASON)],
        current_stage_key="spec",
        run_id="r-producer",
    )
    assert DURABLE not in [item.name for item in remaining]
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)


def test_human_waive_actor_is_literal_human(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.waive(db_session, ticket, DURABLE, WAIVE_REASON, _human(), run_id="") is True
    payload = ledger.UnresolvedWaiverPayload.model_validate_json(
        _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)[0].content_json
    )
    assert payload.actor.agent_id == "human"


# --------------------------------------------------------------------------- #
# settle: persist, producer-waive, producer-clear live-gone; PASS + remaining → NEEDS_REWORK
# --------------------------------------------------------------------------- #


def test_settle_does_not_drop_any_other_way(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    remaining = ledger.settle_unresolved(
        db_session, ticket, (), _designer(), current_stage_key="spec"
    )
    assert DURABLE in [item.name for item in remaining]
    assert DURABLE in _open_names(db_session, ticket)


def test_live_pass_with_carried_other_agent_name_is_needs_rework_not_done(db_session, tmp_path):
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    _handoff(
        db_session,
        ticket,
        [_item(status="complete", certainty="inferred")],
        from_agent="test_designer",
    )
    run = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id="test_designer",
        stage_key=stage.key,
        status=RunStatus.RUNNING,
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_pass_stdout()
    )
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE
    assert DURABLE in _open_names(db_session, ticket)


def test_claimed_fail_first_persists_live_names(db_session, tmp_path):
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_fail_stdout()
    )
    assert DURABLE in _open_names(db_session, ticket)


def test_attach_artifact_refuses_reserved_unresolved_kinds(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    svc = OrchestrationCallbackService(db_session)
    before = db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    for kind in (
        UnresolvedArtifactKind.OPEN,
        UnresolvedArtifactKind.WAIVER,
        UnresolvedArtifactKind.CLEAR,
    ):
        with pytest.raises(ValueError, match="unresolved_"):
            svc.attach_artifact(ticket, kind=kind, title="forged", content={"name": DURABLE})
    after = db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    assert len(after) == len(before)


# --------------------------------------------------------------------------- #
# Writers: parallel sibling, Advance refuse
# --------------------------------------------------------------------------- #


def test_parallel_sibling_report_fail_does_not_fail_a_passer(db_session, tmp_path):
    spec_a = ParallelAgentSpec(agent_id="architecture_reviewer")
    spec_b = ParallelAgentSpec(agent_id="static_qa")
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id=spec_a.agent_id,
        stage_type="parallel",
        parallel_agents=[spec_a, spec_b],
    )
    run_a = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id=spec_a.agent_id,
        stage_key=stage.key,
        stdout=_fail_stdout(),
        status=RunStatus.SUCCEEDED,
    )
    run_b = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id=spec_b.agent_id,
        stage_key=stage.key,
        stdout=_pass_stdout(),
        status=RunStatus.SUCCEEDED,
    )
    member_result_from_run(run_a)
    assert EPHEMERAL in _open_names(db_session, ticket)
    assert member_passed(run_b) is True
    ticket.workflow_stage_key = "implement"
    db_session.add(ticket)
    db_session.commit()
    later = _visible_names(
        db_session,
        ticket,
        (),
        current_stage_key="implement",
        actor=ledger.LedgerActor(agent_id=spec_b.agent_id, stage_key="implement"),
    )
    assert EPHEMERAL not in later


def test_advance_stage_refuses_done_and_finish_while_names_remain(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.PENDING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    with pytest.raises(ValueError) as exc:
        OrchestrationService(db_session).advance_stage(ticket)
    message = str(exc.value)
    assert DURABLE in message
    assert "producer-clear" in message.lower()
    assert "Rework paused" in message
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE

    done = next(item for item in stages if item.key == "done")
    ticket.workflow_stage_key = done.key
    ticket.workflow_stage_status = StageStatus.PENDING
    db_session.add(ticket)
    db_session.commit()
    with pytest.raises(ValueError, match=DURABLE):
        OrchestrationService(db_session).advance_stage(ticket)


def test_advance_stage_succeeds_after_human_waiver(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.waive(db_session, ticket, DURABLE, WAIVE_REASON, _human()) is True
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.PENDING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    OrchestrationService(db_session).advance_stage(ticket)
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == "implement"


# --------------------------------------------------------------------------- #
# Bounded retry / REWORK_PAUSE
# --------------------------------------------------------------------------- #


def test_file_rework_pause_stamps_unresolved_names_and_lists_them(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    pause = file_rework_pause(
        db_session,
        ticket,
        stage_key="spec",
        target_stage="implement",
        message="Rework loop: 'implement' has been rerouted 3×.",
    )
    payload = ReworkPausePayload.model_validate_json(pause.tool_input_json)
    assert DURABLE in payload.unresolved_names
    assert DURABLE in pause.impact
    checklist = json.loads(pause.checklist_json)
    joined = "\n".join(checklist)
    assert "written reason" in joined.lower() or "waive" in joined.lower()
    assert "Retry without waiving" in joined
    assert "Reject to send the work back" in joined


def test_missing_unresolved_names_on_payload_defaults_to_empty():
    payload = ReworkPausePayload.model_validate({"target_stage": "implement"})
    assert payload.unresolved_names == []
    empty = ReworkPausePayload()
    assert empty.unresolved_names == []


def test_rework_pause_nonempty_response_waives_currently_persisted_names(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = "Rework loop paused"
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(pause.id, approved=True, response_text=WAIVE_REASON)
    db_session.refresh(ticket)
    assert DURABLE not in _open_names(db_session, ticket)
    waiver = ledger.UnresolvedWaiverPayload.model_validate_json(
        _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)[0].content_json
    )
    assert waiver.actor.agent_id == "human"
    assert waiver.reason == WAIVE_REASON
    assert _stage_status(db_session, ticket, stage.key) is StageStatus.DONE


def test_rework_pause_empty_approve_keeps_names_resets_budget_sets_pending(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    for index in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="implement",
            from_stage=stage.key,
            context=f"round {index}",
        )
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = "Rework loop paused"
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(pause.id, approved=True, response_text="")
    db_session.refresh(ticket)
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []
    assert _stage_status(db_session, ticket, stage.key) is StageStatus.PENDING
    assert ticket.workflow_stage_status is not StageStatus.DONE
    from loregarden.services.rework_feedback import rework_reroute_count

    assert rework_reroute_count(db_session, ticket, "implement") == 0


def test_rework_pause_routing_on_approve_does_not_waive(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(
        pause.id, approved=True, response_text="", route_to_stage_key="plan"
    )
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []


def test_after_max_rework_reroutes_pause_message_lists_surviving_names(db_session, tmp_path):
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    _incomplete_handoff(db_session, ticket)
    live = _live(db_session, ticket, stage, agent_id="spec")
    ledger.persist_unresolved(db_session, ticket, live, _spec())
    for index in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="plan",
            from_stage=stage.key,
            context=f"round {index}",
        )
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_fail_stdout()
    )
    pause = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.REWORK_PAUSE
        )
    ).first()
    assert pause is not None
    payload = ReworkPausePayload.model_validate_json(pause.tool_input_json)
    assert DURABLE in payload.unresolved_names
    assert DURABLE in pause.impact
    assert DURABLE in _open_names(db_session, ticket)


def test_loregarden_requeue_ticket_does_not_clear_names(db_session, tmp_path):
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = "stuck"
    db_session.add(ticket)
    db_session.commit()
    payload = json.loads(
        execute_tool(
            db_session,
            "loregarden_requeue_ticket",
            normalize_tool_arguments(
                "loregarden_requeue_ticket",
                {"ticket_id": ticket.id, "reason": "unstick without waiving"},
            ),
        )
    )
    assert "error" not in payload
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []


# --------------------------------------------------------------------------- #
# Prompt reads the ledger
# --------------------------------------------------------------------------- #


def test_prompt_shows_persisted_durable_names_after_different_agent_complete_handoff(
    db_session, tmp_path
):
    from loregarden.agents.stage_context import build_orchestration_context

    ticket, stage, stages = _ticket(db_session, tmp_path)
    _handoff(db_session, ticket, [_item(status="incomplete")], from_agent="spec")
    ledger.persist_unresolved(
        db_session, ticket, _live(db_session, ticket, stage, agent_id="spec"), _spec()
    )
    _handoff(
        db_session,
        ticket,
        [_item(status="complete", certainty="inferred")],
        from_agent="test_designer",
    )
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="implement",
        stage_key="implement",
    )
    implement = next(item for item in stages if item.key == "implement")
    text = build_orchestration_context(
        ticket=ticket, run=run, stage_def=implement, stages=stages, session=db_session
    )
    assert "## Unresolved items" in text
    assert f"`{DURABLE}`" in text


# --------------------------------------------------------------------------- #
# Invalid OPEN JSON; unaudited shrink detector
# --------------------------------------------------------------------------- #


def test_invalid_open_json_is_empty_current_set_and_logs_warning(db_session, tmp_path, caplog):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.OPEN,
            title="Unresolved open",
            content_json="{not json",
        )
    )
    db_session.commit()
    live = [_named(DURABLE)]
    with caplog.at_level(logging.WARNING):
        names = _visible_names(db_session, ticket, live, current_stage_key="spec", actor=_spec())
    assert DURABLE in names
    assert caplog.records
    assert any(record.exc_info for record in caplog.records)


def test_detector_finds_unaudited_open_shrink_and_repair_restores_previous(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    first = ledger.UnresolvedOpenPayload(
        items=[
            ledger.UnresolvedOpenItem(
                name=DURABLE,
                producer_agent_id="spec",
                producer_stage_key="spec",
                durable=True,
                first_seen_run_id="r1",
            ),
            ledger.UnresolvedOpenItem(
                name="handoff:test_all_runnable incomplete",
                producer_agent_id="spec",
                producer_stage_key="spec",
                durable=True,
                first_seen_run_id="r1",
            ),
        ]
    )
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.OPEN,
            title="Unresolved open",
            content_json=first.model_dump_json(),
        )
    )
    db_session.commit()
    shrunk = ledger.UnresolvedOpenPayload(
        items=[
            ledger.UnresolvedOpenItem(
                name=DURABLE,
                producer_agent_id="spec",
                producer_stage_key="spec",
                durable=True,
                first_seen_run_id="r1",
            )
        ]
    )
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.OPEN,
            title="Unresolved open",
            content_json=shrunk.model_dump_json(),
        )
    )
    db_session.commit()
    invariant = next(
        item for item in INVARIANTS if item.name == "unresolved_open_shrink_is_audited"
    )
    assert invariant.reentry is InvariantReentry.DEFECT
    found = list(invariant.detect(db_session))
    assert found
    assert any(violation.ticket_id == ticket.id for violation in found)
    repaired = invariant.repair(db_session, found)
    assert repaired >= 1
    assert set(_open_names(db_session, ticket)) == {
        DURABLE,
        "handoff:test_all_runnable incomplete",
    }
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []


def test_detector_finds_nothing_on_a_ticket_with_no_open_rows(db_session, tmp_path):
    _ticket(db_session, tmp_path)
    invariant = next(
        item for item in INVARIANTS if item.name == "unresolved_open_shrink_is_audited"
    )
    assert list(invariant.detect(db_session)) == []


def test_no_gate_producer_import_in_ledger_or_stage_verdict():
    for relative in ("services/unresolved_ledger.py", "services/stage_verdict.py"):
        tree = ast.parse((_SOURCE_ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "gate_producer" not in alias.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "gate_producer" not in node.module


# --------------------------------------------------------------------------- #
# Adversarial / mutation pins (test-break)
# --------------------------------------------------------------------------- #


def test_persist_empty_live_does_not_wipe_open(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    ledger.persist_unresolved(db_session, ticket, (), _spec())
    assert _open_names(db_session, ticket) == [DURABLE]


def test_duplicate_live_names_persist_once(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE), _named(DURABLE)], _spec())
    assert _open_names(db_session, ticket).count(DURABLE) == 1


def test_visible_unresolved_is_sorted_by_name(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    later = "handoff:z_item incomplete"
    earlier = "handoff:a_item incomplete"
    ledger.persist_unresolved(db_session, ticket, [_named(later)], _spec())
    names = _visible_names(
        db_session, ticket, [_named(earlier)], current_stage_key="spec", actor=_spec()
    )
    assert names == sorted(names)
    assert names == [earlier, later]


def test_clear_of_a_name_not_in_open_is_refused(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.clear(db_session, ticket, "handoff:ghost incomplete", _spec(), live=()) is False
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []


def test_clear_leaves_other_open_names_unchanged(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    other = "handoff:test_all_runnable incomplete"
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE), _named(other)], _spec())
    assert ledger.clear(db_session, ticket, DURABLE, _spec(), live=()) is True
    assert DURABLE not in _open_names(db_session, ticket)
    assert other in _open_names(db_session, ticket)


def test_nonempty_producer_stage_key_requires_both_identity_fields(db_session, tmp_path):
    """R4.3. Evidence/report producer match is agent_id AND stage_key, not agent_id alone."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    name = "evidence:real_surface missing"
    ledger.persist_unresolved(
        db_session, ticket, [_named(name, agent_id="spec", stage_key="spec")], _spec()
    )
    later_stage = ledger.LedgerActor(agent_id="spec", stage_key="implement")
    assert ledger.clear(db_session, ticket, name, later_stage, live=()) is False
    assert name in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []


def test_waive_of_a_name_not_in_open_is_refused(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert (
        ledger.waive(db_session, ticket, "handoff:ghost incomplete", WAIVE_REASON, _spec()) is False
    )
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []


def test_waive_stores_the_stripped_reason(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    assert ledger.waive(db_session, ticket, DURABLE, f"  {WAIVE_REASON}  ", _spec()) is True
    payload = ledger.UnresolvedWaiverPayload.model_validate_json(
        _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)[0].content_json
    )
    assert payload.reason == WAIVE_REASON


@pytest.mark.parametrize(
    "ephemeral",
    [
        "report:fail",
        "report:needs_rework",
        "report:blocked",
        "tool:reject",
        "ux_answers missing",
        "fix_coverage missing",
        "operation_criteria pending",
    ],
)
def test_every_ephemeral_prefix_stays_on_producing_stage(db_session, tmp_path, ephemeral):
    """R3.2. An implementer that only special-cases `report:` would leak the rest."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE), _named(ephemeral)], _spec())
    at_later = _visible_names(db_session, ticket, (), current_stage_key="implement", actor=_spec())
    assert DURABLE in at_later
    assert ephemeral not in at_later


def test_pydantic_invalid_open_json_is_empty_current_set(db_session, tmp_path, caplog):
    """R2.2. Valid JSON that fails the payload model is the same as corrupt JSON."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.OPEN,
            title="Unresolved open",
            content_json=json.dumps({"items": "nope"}),
        )
    )
    db_session.commit()
    live = [_named(DURABLE)]
    with caplog.at_level(logging.WARNING):
        names = _visible_names(db_session, ticket, live, current_stage_key="spec", actor=_spec())
    assert DURABLE in names
    assert caplog.records
    assert any(record.exc_info for record in caplog.records)


def test_forged_invalid_waiver_row_does_not_drop_open_names(db_session, tmp_path):
    """R5.5 defense in depth: a reserved-kind row whose payload fails Pydantic is ignored."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.WAIVER,
            title="forged",
            content_json="{not json",
        )
    )
    db_session.commit()
    names = _visible_names(db_session, ticket, (), current_stage_key="spec", actor=_spec())
    assert DURABLE in names
    assert DURABLE in _open_names(db_session, ticket)


def test_stray_clear_row_does_not_drop_open_names(db_session, tmp_path):
    """Visible reads the OPEN snapshot. A CLEAR without a matching shrink is not a drop."""
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.CLEAR,
            title="forged clear",
            content_json=json.dumps({"name": DURABLE, "actor": "spec", "run_id": "forged"}),
        )
    )
    db_session.commit()
    names = _visible_names(db_session, ticket, (), current_stage_key="spec", actor=_spec())
    assert DURABLE in names
    assert DURABLE in _open_names(db_session, ticket)


def test_mcp_attach_artifact_refuses_reserved_unresolved_kinds(db_session, tmp_path):
    ticket, orch, stage, _ = _running_ticket(db_session, tmp_path)
    run = _agent_run(
        db_session, ticket, orch, agent_id="spec", stage_key=stage.key, status=RunStatus.RUNNING
    )
    before = db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    for kind in (
        UnresolvedArtifactKind.OPEN,
        UnresolvedArtifactKind.WAIVER,
        UnresolvedArtifactKind.CLEAR,
    ):
        with pytest.raises(ValueError, match="unresolved_"):
            execute_tool(
                db_session,
                "loregarden_attach_artifact",
                normalize_tool_arguments(
                    "loregarden_attach_artifact",
                    {
                        "run_id": run.run_code,
                        "kind": kind,
                        "title": "forged",
                        "content_json": json.dumps({"name": DURABLE, "reason": WAIVE_REASON}),
                    },
                ),
            )
    after = db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    assert len(after) == len(before)


def test_ledger_does_not_define_a_second_rework_cap():
    source = (_SOURCE_ROOT / "services/unresolved_ledger.py").read_text(encoding="utf-8")
    assert "MAX_REWORK" not in source


def test_dispatch_waiver_does_not_clear_or_waive_names(db_session, tmp_path):
    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    ticket.dispatch_waiver_stage_key = stage.key
    ticket.dispatch_waiver_approval_id = "appr_dispatch"
    db_session.add(ticket)
    db_session.commit()
    assert consume_dispatch_waiver(ticket, stage.key) == "appr_dispatch"
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.CLEAR) == []
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []


def test_one_human_reason_waives_every_currently_persisted_name(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    other = "handoff:test_all_runnable incomplete"
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE), _named(other)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(pause.id, approved=True, response_text=WAIVE_REASON)
    assert _open_names(db_session, ticket) == []
    waivers = [
        ledger.UnresolvedWaiverPayload.model_validate_json(row.content_json)
        for row in _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)
    ]
    assert {item.name for item in waivers} == {DURABLE, other}
    assert {item.reason for item in waivers} == {WAIVE_REASON}
    assert {item.actor.agent_id for item in waivers} == {"human"}


def test_human_waive_uses_currently_persisted_open_not_the_pause_snapshot(db_session, tmp_path):
    """R7.4. A producer-clear between pause and click is honoured; only remaining names waive."""
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    other = "handoff:test_all_runnable incomplete"
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE), _named(other)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    payload = ReworkPausePayload.model_validate_json(pause.tool_input_json)
    assert set(payload.unresolved_names) >= {DURABLE, other}
    assert ledger.clear(db_session, ticket, DURABLE, _spec(), live=()) is True
    ApprovalService(db_session).resolve(pause.id, approved=True, response_text=WAIVE_REASON)
    waived = [
        ledger.UnresolvedWaiverPayload.model_validate_json(row.content_json).name
        for row in _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER)
    ]
    assert other in waived
    assert DURABLE not in waived
    assert _open_names(db_session, ticket) == []


def test_rework_pause_whitespace_approve_is_retry_not_waive(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(pause.id, approved=True, response_text=" \t\n ")
    db_session.refresh(ticket)
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []
    assert _stage_status(db_session, ticket, stage.key) is StageStatus.PENDING


def test_rework_pause_with_no_surviving_names_is_one_click_done(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    payload = ReworkPausePayload.model_validate_json(pause.tool_input_json)
    assert payload.unresolved_names == []
    ApprovalService(db_session).resolve(pause.id, approved=True, response_text="")
    db_session.refresh(ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []
    assert _stage_status(db_session, ticket, stage.key) is StageStatus.DONE


def test_rework_pause_reject_keeps_names_and_resets_budget(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    for index in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="implement",
            from_stage=stage.key,
            context=f"round {index}",
        )
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.BLOCKED)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key=stage.key, target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(pause.id, approved=False, response_text="send it back")
    db_session.refresh(ticket)
    assert DURABLE in _open_names(db_session, ticket)
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []
    from loregarden.services.rework_feedback import rework_reroute_count

    assert rework_reroute_count(db_session, ticket, "implement") == 0


def test_durable_name_from_a_failing_parallel_sibling_carries_forward(db_session, tmp_path):
    spec_a = ParallelAgentSpec(agent_id="architecture_reviewer")
    spec_b = ParallelAgentSpec(agent_id="static_qa")
    reviewer = ledger.LedgerActor(agent_id=spec_a.agent_id, stage_key="review")
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id=spec_a.agent_id,
        stage_type="parallel",
        parallel_agents=[spec_a, spec_b],
    )
    ledger.persist_unresolved(
        db_session,
        ticket,
        [
            _named(DURABLE, agent_id=spec_a.agent_id, stage_key="review"),
            _named(EPHEMERAL, agent_id=spec_a.agent_id, stage_key="review"),
        ],
        reviewer,
    )
    later = _visible_names(
        db_session,
        ticket,
        (),
        current_stage_key="implement",
        actor=ledger.LedgerActor(agent_id=spec_b.agent_id, stage_key="implement"),
    )
    assert DURABLE in later
    assert EPHEMERAL not in later


def test_reconcile_parallel_stage_does_not_finalize_done_when_a_member_failed(db_session, tmp_path):
    spec_a = ParallelAgentSpec(agent_id="architecture_reviewer")
    spec_b = ParallelAgentSpec(agent_id="static_qa")
    ticket, orch, stage, _ = _running_ticket(
        db_session,
        tmp_path,
        stage_key="review",
        agent_id=spec_a.agent_id,
        stage_type="parallel",
        parallel_agents=[spec_a, spec_b],
    )
    run_a = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id=spec_a.agent_id,
        stage_key=stage.key,
        stdout=_fail_stdout(),
        status=RunStatus.SUCCEEDED,
    )
    run_b = _agent_run(
        db_session,
        ticket,
        orch,
        agent_id=spec_b.agent_id,
        stage_key=stage.key,
        stdout=_pass_stdout(),
        status=RunStatus.SUCCEEDED,
    )
    results = [member_result_from_run(run_a), member_result_from_run(run_b)]
    with patch.object(OrchestrationService, "finalize_stage") as finalize:
        reconcile_parallel_stage(db_session, ticket, orch, stage.key, results)
    done_calls = [
        call
        for call in finalize.call_args_list
        if call.kwargs.get("status") is StageStatus.DONE
        or (call.args and StageStatus.DONE in call.args)
    ]
    assert done_calls == []
    assert _stage_status(db_session, ticket, stage.key) is not StageStatus.DONE


def test_advance_stage_message_names_every_remaining_item(db_session, tmp_path):
    ticket, orch, stage, stages = _running_ticket(db_session, tmp_path)
    other = "evidence:real_surface missing"
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE), _named(other)], _spec())
    instance, resolved = OrchestrationService(db_session)._resolve_stages(ticket)
    set_stage_status(ticket, instance, resolved or stages, stage.key, StageStatus.PENDING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    with pytest.raises(ValueError) as exc:
        OrchestrationService(db_session).advance_stage(ticket)
    message = str(exc.value)
    assert DURABLE in message
    assert other in message
    assert "producer-clear" in message.lower()
    assert "Rework paused" in message


def test_settle_waiver_for_a_name_not_in_open_writes_no_row(db_session, tmp_path):
    from loregarden.services.stage_report import WaiverItem

    ticket, stage, _ = _ticket(db_session, tmp_path)
    ledger.persist_unresolved(db_session, ticket, [_named(DURABLE)], _spec())
    remaining = ledger.settle_unresolved(
        db_session,
        ticket,
        [_named(DURABLE)],
        _spec(),
        waivers=[WaiverItem(name="handoff:ghost incomplete", reason=WAIVE_REASON)],
        current_stage_key="spec",
    )
    assert DURABLE in [item.name for item in remaining]
    assert _kind_rows(db_session, ticket, UnresolvedArtifactKind.WAIVER) == []
