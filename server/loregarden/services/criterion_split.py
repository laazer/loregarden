"""Split criteria a ticket could not meet into a sibling, instead of stopping for a person.

A ticket that exhausts its rework budget on named criteria used to block, and
a person decided what to do with criteria nobody could meet in time. The
answer was almost always the same: make them their own ticket. So the control
plane does that itself. The criteria move to a new sibling under the same
parent, recorded ``DELEGATED`` with ``by_split``. The parent owns the sibling
and waits for it, and this ticket goes on with the rest.

A criterion is split at most once. One that fails again in the ticket it was
split into is the one case left that a person decides: two tickets in a row
could not meet it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from loregarden.models.domain import (
    CriterionDisposition,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    WorkItemType,
)
from loregarden.models.domain.work_item_types import VALID_HIERARCHY
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.criterion_ledger import (
    record_delegation,
    settlements_of,
    was_split_into,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.workflow_state import reconcile_workflow_state, set_stage_status
from sqlmodel import Session

logger = logging.getLogger(__name__)

SPLIT_BY = "control_plane"

#: Creates the split ticket. Installed by `criterion_split_creator` on import,
#: because creating a ticket goes through `TicketService`, which imports the
#: run-completion path that calls this module (the `operation_split` pattern).
#: Arguments: session, source ticket, parent id, type, title, description, criteria.
SplitTicketCreator = Callable[[Session, Ticket, str, WorkItemType, str, str, list[str]], Ticket]

_creator: SplitTicketCreator | None = None


def set_split_ticket_creator(creator: SplitTicketCreator | None) -> None:
    """Install the real creator. Called by `criterion_split_creator` on import."""
    global _creator  # noqa: PLW0603 — one process-wide wiring point, as in `operation_criteria`
    _creator = creator


class SplitRefused(ValueError):
    """The criteria cannot be split; the caller blocks for a person, saying why."""


def _placement(session: Session, ticket: Ticket) -> tuple[str, WorkItemType]:
    """Where the split ticket goes: beside ``ticket`` when it has a parent, under it when not."""
    parent = session.get(Ticket, ticket.parent_ticket_id) if ticket.parent_ticket_id else None
    if parent is not None and ticket.work_item_type in VALID_HIERARCHY[parent.work_item_type]:
        return parent.id, ticket.work_item_type
    children = VALID_HIERARCHY[ticket.work_item_type]
    if not children:
        raise SplitRefused(
            f"{ticket.external_id} is a {ticket.work_item_type.value} with no parent, and a "
            f"{ticket.work_item_type.value} holds no children, so there is nowhere to split to."
        )
    return ticket.id, children[0]


def splittable(session: Session, ticket: Ticket, criteria: list[str]) -> list[str]:
    """The criteria that can move: current, not already delegated, in the ticket's order.

    Raises `SplitRefused` when one of them already reached this ticket by a
    split: that is the second ticket to fail it.
    """
    named = {c.strip() for c in criteria}
    settled = settlements_of(session, ticket)
    moving = [
        c
        for c in load_criteria(ticket.acceptance_criteria_json)
        if c in named
        and (c not in settled or settled[c].disposition is not CriterionDisposition.DELEGATED)
    ]
    for criterion in moving:
        if was_split_into(session, ticket, criterion):
            raise SplitRefused(
                f"{criterion!r} was already split into {ticket.external_id} from another ticket "
                "that could not meet it, and this ticket could not meet it either."
            )
    return moving


def split_criteria(
    session: Session, ticket: Ticket, criteria: list[str], *, stage_key: str, why: str
) -> Ticket:
    """Move ``criteria`` to a new ticket and record each delegated there. Commits.

    Raises `SplitRefused` when nothing can move or the move is not allowed.
    """
    moving = splittable(session, ticket, criteria)
    if not moving:
        raise SplitRefused(
            f"None of the criteria named unmet is a current, undelegated criterion of "
            f"{ticket.external_id}: {criteria}"
        )
    parent_id, item_type = _placement(session, ticket)
    if _creator is None:
        raise RuntimeError(
            "No split-ticket creator is installed; "
            "import loregarden.services.criterion_split_creator in this entry point"
        )
    split = _creator(
        session,
        ticket,
        parent_id,
        item_type,
        f"{ticket.title} — criteria split off",
        (
            f"Split from {ticket.external_id} at its '{stage_key}' stage: {why}\n\n"
            "These criteria were not met within that ticket's rework budget. They are "
            "this ticket's whole scope, word for word, and the parent waits for them."
        ),
        moving,
    )
    for criterion in moving:
        record_delegation(
            session,
            ticket,
            criterion,
            target=split,
            settled_by=SPLIT_BY,
            reason=f"Split off at '{stage_key}': {why}",
            by_split=True,
        )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.SPLIT_CRITERIA,
        stage_key=stage_key,
        reason=(
            f"{len(moving)} criteria were not met within the rework budget and moved to "
            f"{split.external_id}; this ticket goes on with the rest."
        ),
        evidence={"split_ticket": split.external_id, "criteria": moving},
    )
    session.commit()
    return split


@dataclass(frozen=True)
class SplitResult:
    """What the rework cap's split did. ``refusal`` is for the pause to say, when it says anything."""

    split: bool
    refusal: str = ""


def split_instead_of_pausing(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    stage_key: str,
    unmet: list[str],
) -> SplitResult:
    """At the rework cap, split the named unmet criteria off and pass the stage.

    A rejecting stage that names no criterion has nothing to split: per the
    done rule its finding is new scope, and the pause goes ahead unchanged. A
    split that is refused, or whose ticket cannot be created, falls back to the
    pause, and the refusal is what the pause tells the person.
    """
    if not unmet:
        return SplitResult(split=False)
    try:
        split_criteria(
            session,
            ticket,
            unmet,
            stage_key=stage_key,
            why="they were named unmet past the ticket's rework budget.",
        )
    except ValueError as exc:  # SplitRefused, or the split ticket could not be created
        session.rollback()
        logger.warning("criteria split refused on %s: %s", ticket.external_id, exc)
        return SplitResult(split=False, refusal=f"Not split: {exc}")
    set_stage_status(
        ticket, instance, stages, stage_key, StageStatus.DONE, note="criteria split off"
    )
    reconcile_workflow_state(ticket, instance, stages, persist=False)
    session.add_all([ticket, instance])
    session.commit()
    return SplitResult(split=True)
