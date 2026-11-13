"""How far through its workflow a ticket is, as stages settled out of stages total.

The sidebar's Running rows draw it as a ring, so a lane reads as "4 of 9" rather
than a dot that only says something is running. A rework sends a ticket back to
an earlier stage without resetting the ones after it, so ``rerun`` counts the
done stages past the active one: done once, they will run again. The client's
``willRerunStageKeys`` (``client/src/lib/stageDisplay.ts``) applies the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import StageStatus, Ticket
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session

#: A stage that has finished: done, or closed without running.
SETTLED = frozenset({StageStatus.DONE, StageStatus.WONT_DO})

#: The stage a run is on: everything done after it is from an earlier pass.
ACTIVE = frozenset({StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED})


@dataclass(frozen=True)
class StageProgress:
    done: int
    total: int
    #: Of ``done``, the stages after the active one, which the run will visit again.
    rerun: int


def stage_progress(session: Session, ticket: Ticket) -> StageProgress | None:
    """None when the ticket has no workflow instance yet, so there is nothing to count."""
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return None
    statuses = list(parse_stage_map(instance, stages).values())
    return StageProgress(
        done=sum(status in SETTLED for status in statuses),
        total=len(statuses),
        rerun=rerun_count(statuses),
    )


def rerun_count(statuses: list[StageStatus]) -> int:
    """Done stages after the first active one, in workflow order; 0 when none is active."""
    active = next((index for index, status in enumerate(statuses) if status in ACTIVE), None)
    if active is None:
        return 0
    return sum(status == StageStatus.DONE for status in statuses[active + 1 :])
