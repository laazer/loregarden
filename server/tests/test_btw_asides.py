"""Asides — questions asked while a run is working.

The behaviour worth pinning is the separation: an aside must be answerable
while the ticket is busy (which ordinary chat refuses), must not touch the run
unless escalated, and must never present the observer's reconstruction as the
working agent's own words.
"""

import json

import pytest
from loregarden.models.domain import AgentRun, BtwStatus, RunStatus, Ticket, TriageMessage
from loregarden.models.domain.tables import BtwExchange
from loregarden.services import btw_service
from loregarden.services.btw_run_service import (
    execute_btw_exchange_background,
    fail_interrupted_asides,
)
from loregarden.services.run_steering import pending_messages
from sqlmodel import Session, select


def _run(
    session: Session,
    ticket: Ticket,
    *,
    agent_id="planner",
    status=RunStatus.RUNNING,
    stage_key="plan",
    run_code="run_btw",
) -> AgentRun:
    run = AgentRun(
        run_code=run_code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=status,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _ticket(session: Session) -> Ticket:
    return session.exec(select(Ticket)).first()


def test_an_aside_is_accepted_while_a_run_is_working(db_session: Session):
    """The case ordinary chat refuses with a 409 is the one this channel is for."""
    ticket = _ticket(db_session)
    run = _run(db_session, ticket)

    exchange = btw_service.ask(db_session, ticket, "  why the subprocess path?  ")

    assert exchange.question == "why the subprocess path?"
    assert exchange.status == BtwStatus.PENDING
    assert exchange.observed_run_id == run.id


def test_asking_does_not_touch_the_run(db_session: Session):
    """The whole point: the observer answers, the run carries on unaware."""
    ticket = _ticket(db_session)
    run = _run(db_session, ticket)

    btw_service.ask(db_session, ticket, "what are you doing?")

    assert pending_messages(db_session, run.id) == []


def test_a_live_stage_run_wins_over_a_live_triage_turn(db_session: Session):
    """When both are going, the work is what the operator is watching."""
    ticket = _ticket(db_session)
    _run(db_session, ticket, agent_id="triage", stage_key="triage", run_code="run_tri")
    stage = _run(db_session, ticket, run_code="run_stage")

    assert btw_service.find_observed_run(db_session, ticket.id).id == stage.id


def test_an_idle_ticket_falls_back_to_its_last_run(db_session: Session):
    """ "What did you just do" is as much an aside as "what are you doing"."""
    ticket = _ticket(db_session)
    finished = _run(db_session, ticket, status=RunStatus.SUCCEEDED)

    exchange = btw_service.ask(db_session, ticket, "why did that fail?")

    assert exchange.observed_run_id == finished.id


def test_an_empty_question_is_refused(db_session: Session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="empty"):
        btw_service.ask(db_session, ticket, "   ")


def test_the_prompt_tells_the_observer_it_is_not_the_running_agent(db_session: Session):
    """An observer that thinks it is the worker will answer "why" as testimony."""
    ticket = _ticket(db_session)
    run = _run(db_session, ticket)

    prompt = btw_service.build_btw_prompt(db_session, ticket, run, "why?")

    assert "You are NOT" in prompt
    assert "reading its log from the outside" in prompt
    assert "mark the rest as inference" in prompt
    assert "still going right now" in prompt
    assert run.agent_id in prompt


def test_the_prompt_says_so_when_the_run_has_finished(db_session: Session):
    ticket = _ticket(db_session)
    run = _run(db_session, ticket, status=RunStatus.SUCCEEDED)

    prompt = btw_service.build_btw_prompt(db_session, ticket, run, "why?")

    assert "already finished" in prompt


def test_the_answer_is_mirrored_into_the_ticket_transcript(db_session: Session):
    ticket = _ticket(db_session)
    run = _run(db_session, ticket)
    exchange = btw_service.ask(db_session, ticket, "why the subprocess path?")

    btw_service.record_answer(db_session, exchange, "The log shows it shelling out to git.")

    assert exchange.status == BtwStatus.ANSWERED
    messages = db_session.exec(
        select(TriageMessage).where(TriageMessage.ticket_id == ticket.id)
    ).all()
    mirrored = [m for m in messages if "shelling out" in m.content]
    assert len(mirrored) == 1
    assert "btw" in mirrored[0].parts_json
    # Never attributed to the run it is about — that run did not say this.
    assert mirrored[0].run_id is None
    assert run.id in mirrored[0].parts_json


def test_the_mirrored_card_names_the_run_it_observed(db_session: Session):
    """The card renders its attribution from this — the one thing a reader
    must not be able to get wrong is whose answer it is."""
    ticket = _ticket(db_session)
    run = _run(db_session, ticket)
    exchange = btw_service.ask(db_session, ticket, "why?")

    message = btw_service.record_answer(db_session, exchange, "It looks like a retry.")

    part = json.loads(message.parts_json)[0]
    assert part["primitive"] == "btw"
    assert part["observed_agent_id"] == run.agent_id
    assert part["observed_stage_key"] == run.stage_key


def test_escalation_writes_the_question_into_the_running_agent(db_session: Session):
    ticket = _ticket(db_session)
    run = _run(db_session, ticket)
    exchange = btw_service.ask(db_session, ticket, "why the subprocess path?")

    btw_service.escalate(db_session, exchange)

    queued = pending_messages(db_session, run.id)
    assert len(queued) == 1
    assert "why the subprocess path?" in queued[0].content
    # The contract that keeps this from being a steer: answer, then carry on.
    assert "Do not change your plan" in queued[0].content
    assert exchange.escalated_at is not None


def test_escalation_is_refused_with_steering_own_reason(db_session: Session):
    """One reason string, so the API and the card cannot disagree."""
    ticket = _ticket(db_session)
    _run(db_session, ticket, agent_id="backend_implementer")
    exchange = btw_service.ask(db_session, ticket, "why?")

    with pytest.raises(ValueError, match="cannot receive input"):
        btw_service.escalate(db_session, exchange)
    assert "cannot receive input" in btw_service.escalation_refusal(db_session, exchange)


def test_escalation_is_refused_when_there_was_no_run(db_session: Session):
    ticket = _ticket(db_session)
    exchange = btw_service.ask(db_session, ticket, "what is this ticket about?")

    assert exchange.observed_run_id is None
    with pytest.raises(ValueError, match="no run to ask"):
        btw_service.escalate(db_session, exchange)


def test_a_stubbed_turn_answers_and_settles(db_session: Session, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BTW_STUB_RESPONSE", "It is running the gate.")
    ticket = _ticket(db_session)
    _run(db_session, ticket)
    exchange = btw_service.ask(db_session, ticket, "what is it doing?")

    execute_btw_exchange_background(exchange.id)

    db_session.refresh(exchange)
    assert exchange.status == BtwStatus.ANSWERED
    assert exchange.answer == "It is running the gate."


def test_an_empty_answer_fails_rather_than_landing_blank(db_session: Session, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BTW_STUB_RESPONSE", "   ")
    ticket = _ticket(db_session)
    _run(db_session, ticket)
    exchange = btw_service.ask(db_session, ticket, "what is it doing?")

    execute_btw_exchange_background(exchange.id)

    db_session.refresh(exchange)
    assert exchange.status == BtwStatus.FAILED
    assert "empty" in exchange.error


def test_a_restart_settles_asides_nothing_else_would_reach(db_session: Session):
    """An aside holds no run row, so no other reaper sees it."""
    ticket = _ticket(db_session)
    _run(db_session, ticket)
    exchange = btw_service.ask(db_session, ticket, "why?")

    settled = fail_interrupted_asides(db_session)

    assert [item.id for item in settled] == [exchange.id]
    db_session.refresh(exchange)
    assert exchange.status == BtwStatus.FAILED
    assert "interrupted" in exchange.error


def test_asking_over_the_api_while_a_run_is_active(client, db_session: Session):
    """No 409 here — that conflict is the reason the endpoint exists."""
    ticket = _ticket(db_session)
    _run(db_session, ticket)

    response = client.post(f"/api/tickets/{ticket.id}/btw", json={"content": "what now?"})

    assert response.status_code == 202
    body = response.json()
    assert body["question"] == "what now?"
    assert body["observed_run_active"] is True

    listing = client.get(f"/api/tickets/{ticket.id}/btw").json()
    assert [item["id"] for item in listing["exchanges"]] == [body["id"]]


def test_escalating_a_refused_run_over_the_api_is_a_conflict(client, db_session: Session):
    ticket = _ticket(db_session)
    _run(db_session, ticket, agent_id="backend_implementer")
    exchange = btw_service.ask(db_session, ticket, "why?")

    response = client.post(f"/api/tickets/{ticket.id}/btw/{exchange.id}/escalate")

    assert response.status_code == 409
    assert "cannot receive input" in response.json()["detail"]


def test_an_aside_belonging_to_another_ticket_is_not_found(client, db_session: Session):
    ticket = _ticket(db_session)
    exchange = BtwExchange(ticket_id="some-other-ticket", question="why?")
    db_session.add(exchange)
    db_session.commit()

    response = client.post(f"/api/tickets/{ticket.id}/btw/{exchange.id}/escalate")

    assert response.status_code == 404
