"""Resolve a sibling-overlap park: what its approval carries, and what "wait" does.

Kept apart from `sibling_overlap_park`, which raises the park, because the
approval resolver imports this and `stage_parking` imports the resolver's
module chain; one module holding both closed an import cycle.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import Approval, ApprovalKind, Ticket
from loregarden.models.domain.path_claim_enums import StageParkReason
from loregarden.services.ticket_dependencies import TicketDependencyService
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

logger = logging.getLogger(__name__)


class SiblingOverlapPark(BaseModel):
    """What an overlap park's approval carries in `tool_input_json`."""

    model_config = ConfigDict(frozen=True)

    sibling_ids: list[str]
    #: For the inbox card, which names the siblings on its Wait button.
    sibling_external_ids: list[str]


def is_sibling_overlap_park(approval: Approval) -> bool:
    return (
        approval.kind is ApprovalKind.STAGE_PARK
        and approval.tool_name == StageParkReason.SIBLING_OVERLAP
    )


def wait_for_siblings(session: Session, ticket: Ticket, approval: Approval) -> None:
    """Link ``ticket`` to wait for every sibling the park named.

    Raises `DependencyCycleError` when a sibling has come to wait for this
    ticket since the park was raised; the caller blocks the stage with that
    reason rather than leaving it to wait on a loop.
    """
    park = SiblingOverlapPark.model_validate_json(approval.tool_input_json)
    dependencies = TicketDependencyService(session)
    for sibling_id in park.sibling_ids:
        dependencies.add_dependency(ticket.id, sibling_id, created_by=f"approval:{approval.id}")
    logger.info(
        "%s now waits for %s (sibling overlap park %s)",
        ticket.external_id,
        ", ".join(park.sibling_external_ids),
        approval.id,
    )
