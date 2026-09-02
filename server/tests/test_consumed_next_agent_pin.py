"""`next_agent` is a request about one dispatch, not a standing fact.

A reject writes it to send work back to a named agent. It was persisted, read by
nine places as though it described the ticket, and never cleared — so a hint set
at `verify` still steered three stages later. That is the stale-pin loop #164
documented, and the demotion to a tie-breaker only covered half of it.

Two things had to be true before clearing it could work at all, and the second
is not obvious:

* the readers must derive rather than read the stored value
  (lg-workflow-integrity-604), or clearing blanks the UI;
* the READ PATH must stop rewriting it. `GET /api/tickets/{id}` reaches
  `reconcile_workflow_state` through `reconcile_ticket`, which backfilled the
  stage's agent and committed. Clearing the pin at dispatch was undone by the
  next page load.
"""

from loregarden.models.domain import Ticket
from loregarden.services.orchestration import (
    OrchestrationService,
    _consume_next_agent_pin,
)
from sqlmodel import Session, select


def _any_staged_ticket(session: Session) -> Ticket:
    return session.exec(select(Ticket).where(Ticket.workflow_stage_key != "")).first()


def test_reading_a_ticket_no_longer_rewrites_its_routing(client, isolated_db):
    """The defect that made AC3 unimplementable.

    Proven before the fix by clearing the field and reading the ticket back:
    `after a READ -> 'backend_implementer'`. A fetch is not a routing decision.
    """
    with Session(isolated_db) as session:
        ticket = _any_staged_ticket(session)
        ticket.next_agent = ""
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

        OrchestrationService(session).reconcile_ticket(ticket)
        session.refresh(ticket)

        assert ticket.next_agent == "", (
            "reading a ticket rewrote its routing; clearing the pin at dispatch "
            "would be undone by the next GET"
        )


def test_the_pin_is_cleared_by_the_dispatch_it_asked_for():
    ticket = Ticket(external_id="x", workspace_id="ws", title="t", next_agent="backend_implementer")

    _consume_next_agent_pin(ticket, "backend_implementer")

    assert ticket.next_agent == ""


def test_a_dispatch_somewhere_else_leaves_the_request_standing():
    """The request was for a specific agent. Another agent running does not
    satisfy it, so the hint survives to steer the dispatch it meant."""
    ticket = Ticket(external_id="x", workspace_id="ws", title="t", next_agent="backend_implementer")

    _consume_next_agent_pin(ticket, "frontend_implementer")

    assert ticket.next_agent == "backend_implementer"


def test_an_empty_pin_is_not_disturbed_by_a_dispatch():
    ticket = Ticket(external_id="x", workspace_id="ws", title="t", next_agent="")

    _consume_next_agent_pin(ticket, "backend_implementer")

    assert ticket.next_agent == ""
