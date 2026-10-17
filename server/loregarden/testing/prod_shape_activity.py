"""The scenario's recent activity: domain events, at the live two-week volume.

The workspace dashboard's "since you were away" digests `domain_events`, and
the scenario had none, so every render of it was the empty state. These are
calibrated from the live log (read-only) over the 14 days to 2026-10-17, per
workspace: how many tickets something happened to, and how many of each
digested kind. Stamped relative to build time, so a "last 14 days" read
always finds them.

Split from `prod_shape` (near its size cap); `build_prod_shape` calls it.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import timedelta

from pydantic import BaseModel
from sqlmodel import Session, select

from loregarden.models.domain import (
    ActivityHighlightKind,
    DomainEvent,
    EventType,
    GateOutcome,
    RunStatus,
    StageCompletionOutcome,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.models.domain.enums import utcnow


class ActivityShape(BaseModel):
    #: Tickets with at least one digested event in the window.
    tickets: int
    kinds: dict[ActivityHighlightKind, int]


#: Measured 2026-10-17 from `domain_events`, the 14 days before (read-only).
ACTIVITY_CALIBRATION: dict[str, ActivityShape] = {
    "loregarden": ActivityShape(
        tickets=405,
        kinds={
            ActivityHighlightKind.CREATED: 361,
            ActivityHighlightKind.MOVED: 168,
            ActivityHighlightKind.LANDED: 50,
            ActivityHighlightKind.LANDING_FAILED: 10,
            ActivityHighlightKind.RUN_FAILED: 54,
            ActivityHighlightKind.GATE_FAILED: 6,
            ActivityHighlightKind.APPROVAL_REQUESTED: 147,
        },
    ),
    "blobert": ActivityShape(
        tickets=13,
        kinds={
            ActivityHighlightKind.CREATED: 5,
            ActivityHighlightKind.MOVED: 4,
            ActivityHighlightKind.LANDING_FAILED: 2,
            ActivityHighlightKind.RUN_FAILED: 7,
            ActivityHighlightKind.APPROVAL_REQUESTED: 16,
        },
    ),
}
WINDOW = timedelta(days=14)
_STAGES = ["review", "implement", "spec", "plan", "script_review"]


def _event(kind: ActivityHighlightKind, rng: random.Random) -> tuple[EventType, dict]:
    stage = rng.choice(_STAGES)
    match kind:
        case ActivityHighlightKind.CREATED:
            return EventType.TICKET_CREATED, {}
        case ActivityHighlightKind.MOVED:
            state = rng.choice([TicketState.IN_PROGRESS, TicketState.DONE, TicketState.BLOCKED])
            return EventType.TICKET_STATE_CHANGED, {"state": state.value}
        case ActivityHighlightKind.LANDED:
            return EventType.TICKET_LANDED, {"ok": True, "target": "main"}
        case ActivityHighlightKind.LANDING_FAILED:
            return EventType.TICKET_LANDED, {"ok": False, "target": "main"}
        case ActivityHighlightKind.STAGE_REJECTED:
            return EventType.STAGE_COMPLETED, {
                "outcome": StageCompletionOutcome.REJECT.value,
                "stage_key": stage,
            }
        case ActivityHighlightKind.RUN_FAILED:
            return EventType.AGENT_RUN_COMPLETED, {"status": RunStatus.FAILED.value}
        case ActivityHighlightKind.GATE_FAILED:
            return EventType.GATE_EVALUATED, {
                "outcome": GateOutcome.FAILED.value,
                "stage_key": stage,
            }
        case ActivityHighlightKind.APPROVAL_REQUESTED:
            return EventType.APPROVAL_REQUESTED, {"stage_key": stage}


def add_activity(session: Session, rng: random.Random, event_id: Callable[[str, int], str]) -> int:
    """Write the calibrated events and return how many the scenario holds.

    Idempotent by id (`event_id`, the scenario's `prod_shape_id`), so a second
    build writes nothing and still reports the same total.
    """
    now = utcnow()
    total = 0
    for slug, shape in ACTIVITY_CALIBRATION.items():
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        total += sum(shape.kinds.values())
        if session.get(DomainEvent, event_id(f"{slug}/activity", 0)) is not None:
            continue
        ids = sorted(
            session.exec(select(Ticket.id).where(Ticket.workspace_id == workspace.id)).all()
        )
        chosen = rng.sample(ids, min(shape.tickets, len(ids)))
        # Distinct, spread offsets: the digest orders tickets by their newest event.
        step = WINDOW / (len(chosen) + 1)
        latest = {ticket_id: now - step * (n + 1) for n, ticket_id in enumerate(chosen)}
        slot = 0
        for kind, count in shape.kinds.items():
            for _ in range(count):
                ticket_id = chosen[slot % len(chosen)]
                slot += 1
                event_type, payload = _event(kind, rng)
                session.add(
                    DomainEvent(
                        id=event_id(f"{slug}/activity", slot - 1),
                        type=event_type,
                        workspace_id=workspace.id,
                        ticket_id=ticket_id,
                        payload_json=json.dumps(payload),
                        created_at=latest[ticket_id] - timedelta(seconds=slot),
                    )
                )
    session.commit()
    return total
