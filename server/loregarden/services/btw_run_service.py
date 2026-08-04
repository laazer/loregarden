"""Background execution for aside (btw) answer turns.

Mirrors ``triage_run_service`` in shape but not in substance: an aside turn holds
no ``AgentRun`` row at all. Giving it one would put a second run on the ticket
while a stage is already going — ``find_active_run`` would see it, ``triage_run_status``
would publish it as the chat's active turn, and the composer would go busy every
time somebody asked a question. The ``BtwExchange`` row is the turn's lifecycle,
which is also what makes an interrupted one settleable at startup instead of
leaving a card that says "asking…" forever.
"""

from __future__ import annotations

import logging
import os
import threading

from loregarden.db.session import engine
from loregarden.models.domain import BtwStatus, Ticket
from loregarden.models.domain.tables import BtwExchange
from loregarden.services.btw_service import answer_text, record_answer, record_failure
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.triage_service import TRIAGE_AGENT_NAME
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

INTERRUPTED_ASIDE_MESSAGE = (
    f"{TRIAGE_AGENT_NAME} was interrupted by a server restart and never answered this. Ask again."
)


def execute_btw_exchange_background(exchange_id: str) -> None:
    """Answer one aside in a fresh session, settling the row whatever happens."""
    try:
        with Session(engine) as session:
            exchange = session.get(BtwExchange, exchange_id)
            if not exchange:
                logger.error("Background aside not found: %s", exchange_id)
                return
            ticket = session.get(Ticket, exchange.ticket_id)
            if not ticket:
                record_failure(session, exchange, "Ticket not found")
                return
            try:
                reply = answer_text(session, exchange, ticket)
            except Exception as exc:  # noqa: BLE001 - reported on the row, not raised
                logger.warning("Aside turn failed: %s", exchange_id, exc_info=True)
                record_failure(session, exchange, format_agent_unavailable(TRIAGE_AGENT_NAME, exc))
                return
            if not reply.strip():
                record_failure(session, exchange, f"{TRIAGE_AGENT_NAME} returned an empty answer")
                return
            record_answer(session, exchange, reply)
    except Exception:
        logger.exception("Background aside failed: %s", exchange_id)
        try:
            with Session(engine) as session:
                exchange = session.get(BtwExchange, exchange_id)
                if exchange and exchange.status == BtwStatus.PENDING:
                    record_failure(session, exchange, "Aside failed unexpectedly")
        except Exception:
            logger.exception("Could not mark aside %s as failed", exchange_id)


def fail_interrupted_asides(
    session: Session, *, message: str = INTERRUPTED_ASIDE_MESSAGE
) -> list[BtwExchange]:
    """Settle asides orphaned by a restart.

    Nothing else would: an aside's turn is a bare thread with no run row behind
    it, so a restart mid-turn leaves a row that is pending forever and a card
    that never resolves.
    """
    orphaned = session.exec(
        select(BtwExchange).where(BtwExchange.status == BtwStatus.PENDING)
    ).all()
    for exchange in orphaned:
        record_failure(session, exchange, message)
    return list(orphaned)


def schedule_btw_exchange(exchange_id: str) -> None:
    """Queue the answer turn without blocking the API request thread."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_btw_exchange_background(exchange_id)
        return
    thread = threading.Thread(
        target=execute_btw_exchange_background,
        args=(exchange_id,),
        name=f"loregarden-btw-{exchange_id[:8]}",
        daemon=True,
    )
    thread.start()
