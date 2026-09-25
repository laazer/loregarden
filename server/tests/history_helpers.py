"""Reading a ticket's orchestrator decisions off the history rail.

Seven suites had grown their own private `_decisions`, and they had drifted into
three different return shapes — events, payload dicts, and decision strings —
which is why the duplication was not obvious enough for anyone to remove: they
did not look like the same function. One read underneath, three named
derivations on top, so a caller asks for the shape it actually uses.

The history rail is the only record that the orchestrator decided anything, so
these are the tests' one way of checking that it did. `ticket_history` already
defaults to `limit=100`; the suites that passed it explicitly were passing the
default.
"""

from __future__ import annotations

import json

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType
from sqlmodel import Session


def decision_events(session: Session, ticket_id: str) -> list:
    """The `OrchestratorDecision` events on this ticket, oldest first."""
    return [
        event
        for event in event_bus.ticket_history(session, ticket_id)
        if event.type == EventType.ORCHESTRATOR_DECISION
    ]


def decision_payloads(session: Session, ticket_id: str) -> list[dict]:
    """Their payloads — for a test that reads `reason`, `block_kind` or evidence."""
    return [json.loads(event.payload_json or "{}") for event in decision_events(session, ticket_id)]


def decision_kinds(session: Session, ticket_id: str) -> list[str]:
    """Just what was decided, in order — for `in`, `count` and equality.

    Subscripted rather than `.get`: a decision event without a `decision` is a
    publisher bug, and a helper that turned it into a `None` in the list would
    hide it behind an assertion about some other decision being absent.
    """
    return [payload["decision"] for payload in decision_payloads(session, ticket_id)]
