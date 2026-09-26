"""Chat replies are bounded only against runaway output, and a cut is never silent."""

import logging
from unittest import mock

from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket, TriageMessage
from loregarden.services import agent_turn_runner, triage_run_service
from loregarden.services.agent_turn_runner import AgentTurnResult
from loregarden.services.cli_agent_runner import cap_reply
from loregarden.services.triage_run_service import TriageTurnExecutor, start_triage_run
from sqlmodel import Session, col, select

# Longer than the old 8000-character cap that silently cut triage replies.
LONG_REPLY = "".join(f"line {i:05d} of a long triage answer\n" for i in range(1000))


def _last_assistant_reply(session: Session, ticket_id: str) -> str:
    message = session.exec(
        select(TriageMessage)
        .where(TriageMessage.ticket_id == ticket_id, TriageMessage.role == "assistant")
        .order_by(col(TriageMessage.created_at).desc())
    ).first()
    assert message is not None
    return message.content


def test_cap_reply_leaves_a_reply_under_the_cap_untouched(caplog):
    with caplog.at_level(logging.WARNING):
        assert cap_reply("short answer", 100, label="Baxter") == "short answer"
    assert caplog.records == []


def test_cap_reply_marks_the_cut_and_logs_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        capped = cap_reply("x" * 150, 100, label="Baxter")

    assert capped.startswith("x" * 100)
    assert not capped.startswith("x" * 101)
    assert "truncated" in capped[100:].lower()
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_long_stub_triage_reply_is_stored_whole(
    client: TestClient, db_session: Session, monkeypatch
):
    assert len(LONG_REPLY) > 8000
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", LONG_REPLY)
    ticket = db_session.exec(select(Ticket)).first()
    assert ticket is not None

    _, run = start_triage_run(db_session, ticket, "explain everything")
    TriageTurnExecutor(db_session).execute(run, ticket)

    assert _last_assistant_reply(db_session, ticket.id) == LONG_REPLY


def test_long_agent_triage_reply_is_stored_whole(
    client: TestClient, db_session: Session, monkeypatch
):
    def fake_turn(request):
        return AgentTurnResult(
            reply=LONG_REPLY, strategy="advisory_oneshot", adapter="local", run_id=request.run_id
        )

    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    ticket = db_session.exec(select(Ticket)).first()
    assert ticket is not None

    with (
        mock.patch.object(triage_run_service, "resolve_chat_adapter", return_value="local"),
        mock.patch.object(agent_turn_runner, "run_agent_turn", fake_turn),
        mock.patch.object(triage_run_service, "run_agent_turn", fake_turn),
    ):
        _, run = start_triage_run(db_session, ticket, "explain everything")
        TriageTurnExecutor(db_session).execute(run, ticket)

    assert _last_assistant_reply(db_session, ticket.id) == LONG_REPLY
