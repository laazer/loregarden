"""The scenario's handoffs, in the document shape `handoff_store` writes.

The worked ticket used to carry handoffs shaped like nothing production holds
(``from_stage`` and a list of bare titles), so a surface reading them rendered
every one as unreadable. These are the real document: two agents, a checklist
of items with a status, evidence and certainty, and the met counter.

Status and certainty draw from their own generator, seeded per handoff, so the
scenario's shared generator advances exactly as it did before: the item names
and evidence are the same draws it always made.
"""

from __future__ import annotations

import random
from typing import TypeVar

from pydantic import BaseModel

from loregarden.models.domain import ClaimCertainty
from loregarden.services.handoff_feed import HandoffItemStatus


class HandoffCalibration(BaseModel):
    measured: str
    #: Checklist items by status, over every live handoff.
    statuses: dict[HandoffItemStatus, int]
    #: The same items by certainty; `None` is an item that omitted it.
    certainties: dict[ClaimCertainty | None, int]


#: Measured read-only on the live database, 2026-11-21: 1,397 handoffs on 204
#: tickets (1,129 in loregarden), a mean of 6.8 per ticket and at most 32.
HANDOFF_CALIBRATION = HandoffCalibration(
    measured="2026-11-21",
    statuses={
        HandoffItemStatus.COMPLETE: 4653,
        HandoffItemStatus.INCOMPLETE: 37,
        HandoffItemStatus.DEFERRED: 33,
    },
    certainties={
        ClaimCertainty.VERIFIED: 2736,
        ClaimCertainty.INFERRED: 1880,
        ClaimCertainty.USER_CONFIRMED: 34,
        None: 73,
    },
)


T = TypeVar("T")


def _weighted(rng: random.Random, weights: dict[T, int]) -> T:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def handoff_doc(
    *,
    seed: str,
    ext: str,
    from_agent: str,
    to_agent: str,
    item_names: list[str],
    evidence: str,
) -> dict:
    """A stored handoff document for `ext`, one checklist item per name."""
    rng = random.Random(seed)
    checklist = []
    for name in item_names:
        item = {
            "item_key": name.lower().replace(" ", "_")[:40],
            "item": name,
            "required": True,
            "status": _weighted(rng, HANDOFF_CALIBRATION.statuses).value,
            "evidence": evidence,
        }
        certainty = _weighted(rng, HANDOFF_CALIBRATION.certainties)
        if certainty is not None:
            item["certainty"] = certainty.value
        checklist.append(item)
    met = sum(1 for item in checklist if item["status"] == HandoffItemStatus.COMPLETE)
    return {
        "handoff": {
            "schema_version": "1.0",
            "ticket_id": ext,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "required_items_met": met,
            "total_required_items": len(checklist),
            "checklist": checklist,
        }
    }
