"""A finished survey pass leaves its carrier ticket done.

A carrier exists to run one pass. `finish_pass` is the deliverable: once it
lands, the carrier has nothing left to do but finish its `survey` stage and its
terminal `done`. Only the carrier's own run completing does that, so when the
run dies after the agent finished the pass (the lease swept it, the server
restarted, a transition gate could not run), the carrier is left in progress or
blocked on work that is already recorded. Six carriers sat like that on
2026-11-03 until they were finished by hand, and `record_reads` refuses a
re-run because the pass is no longer DISPATCHED, so a requeue cannot help.

The repair does what a clean completion would have: it marks `survey` done,
moves the cursor to the terminal stage, and hands the ticket to the terminal
sweep's own finish. A carrier with a run or orchestration still live is left
alone, because a finished pass legitimately waits a few minutes for its run to
end (`feeder.running_passes`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import AgentRun, OrchestrationRun, Ticket, TicketState
from loregarden.models.domain.survey_enums import SurveyPassStatus
from loregarden.models.domain.survey_tables import SurveyPass
from loregarden.services.orchestration import LIVE_ORCHESTRATION_STATUSES, OrchestrationService
from loregarden.services.run_lease import IN_FLIGHT
from loregarden.services.terminal_stage_sweep import finish_parked_terminal_tickets
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Decided already: DONE and WONT_DO by the workflow, PARKED by a person.
_SETTLED = (TicketState.DONE, TicketState.WONT_DO, TicketState.PARKED)


@dataclass(frozen=True)
class OpenCarrier:
    survey_pass: SurveyPass
    carrier: Ticket


def carriers_behind_finished_passes(session: Session) -> list[OpenCarrier]:
    """Carriers still open, with nothing live behind them, whose pass finished."""
    live_runs = select(AgentRun.ticket_id).where(col(AgentRun.status).in_(IN_FLIGHT))
    live_orchestrations = select(OrchestrationRun.ticket_id).where(
        col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES)
    )
    rows = session.exec(
        select(SurveyPass, Ticket)
        .join(Ticket, col(Ticket.id) == SurveyPass.carrier_ticket_id)
        .where(SurveyPass.status == SurveyPassStatus.FINISHED)
        .where(col(Ticket.state).not_in(_SETTLED))
        .where(col(Ticket.state_locked).is_(False))
        .where(col(Ticket.id).not_in(live_runs))
        .where(col(Ticket.id).not_in(live_orchestrations))
    ).all()
    return [OpenCarrier(survey_pass, carrier) for survey_pass, carrier in rows]


def finished_at(open_carrier: OpenCarrier) -> datetime | None:
    return open_carrier.survey_pass.finished_at


def _move_to_terminal(session: Session, carrier: Ticket) -> None:
    """Mark `survey` done and put the cursor on the terminal stage, as a clean run would."""
    _, stages = resolve_ticket_stages(session, carrier)
    terminal = find_terminal_stage(stages) if stages else None
    if terminal is None or carrier.workflow_stage_key == terminal.key:
        return
    # A block on a stage whose work is recorded has nothing left to say;
    # `advance_stage` clears the text once the stage is done, the kind is ours.
    carrier.block_kind = None
    session.add(carrier)
    OrchestrationService(session).advance_stage(carrier)


def finish_carriers(session: Session, carrier_ids: Sequence[str]) -> int:
    """Finish each still-open carrier. Returns how many now read DONE.

    One that will not finish (a landing that blocks, an unresolved item) is
    logged and left for the next pass, where it counts as found but not
    repaired, which is how the registry shows it.
    """
    wanted = set(carrier_ids)
    finished = 0
    for item in carriers_behind_finished_passes(session):
        carrier = item.carrier
        if carrier.id not in wanted:
            continue
        try:
            _move_to_terminal(session, carrier)
        except ValueError:
            logger.warning(
                "Could not finish survey carrier %s (pass %s finished at %s)",
                carrier.external_id,
                item.survey_pass.id,
                item.survey_pass.finished_at,
                exc_info=True,
            )
            session.rollback()
            continue
        finish_parked_terminal_tickets(session, ticket_id=carrier.id)
        session.refresh(carrier)
        if carrier.state is TicketState.DONE:
            finished += 1
        else:
            logger.warning(
                "Survey carrier %s reached its terminal stage but reads %s",
                carrier.external_id,
                carrier.state.value,
            )
    return finished
