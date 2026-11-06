"""The survey's states in the invariant registry's shape: detectors and repairs.

`invariant_registry` declares each invariant (the PR fix-coverage check reads the
declarations there); the survey-specific halves live beside the survey code.
"""

from __future__ import annotations

from collections.abc import Sequence

from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.invariants import Violation
from loregarden.services.reference_survey.carrier_settlement import (
    carriers_behind_finished_passes,
    finish_carriers,
)
from loregarden.services.reference_survey.inbox import cleared_cards, close_cards
from loregarden.services.reference_survey.map_repair import remap_from_clone, unsplit_maps
from sqlmodel import Session


def cleared_card_violations(session: Session) -> list[Violation]:
    """`entered_at` is the later clone or dispatch that cleared the card's cause."""
    return [
        Violation(
            subject_id=item.card.id,
            ticket_id=None,
            entered_at=item.cleared_at,
            detail=f"{item.card.title}: {item.because}",
        )
        for item in cleared_cards(session)
    ]


def close_cleared_card_violations(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    closed = 0
    for item in cleared_cards(session):
        if item.card.id in wanted:
            closed += close_cards(
                session, [item.card], reason=f"Closed by the survey: {item.because}."
            )
    return closed


def open_carrier_violations(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.carrier.id,
            ticket_id=item.carrier.id,
            entered_at=item.survey_pass.finished_at,
            detail=f"pass {item.survey_pass.ordinal} finished; its carrier reads "
            f"{item.carrier.state.value} at '{item.carrier.workflow_stage_key}'",
        )
        for item in carriers_behind_finished_passes(session)
    ]


def finish_carrier_violations(session: Session, violations: Sequence[Violation]) -> int:
    return finish_carriers(session, [violation.subject_id for violation in violations])


def unsplit_map_violations(session: Session) -> list[Violation]:
    """`entered_at` is the source's last fetch, which every map is taken in."""
    return [
        Violation(
            subject_id=item.source.id,
            ticket_id=None,
            entered_at=item.source.fetched_at,
            detail=f"{item.source.name}'s map at {item.source.mapped_commit[:12]} keeps "
            f"{', '.join(item.leaves)} whole, over the pass budget",
        )
        for item in unsplit_maps(session)
    ]


def remap_unsplit_violations(session: Session, violations: Sequence[Violation]) -> int:
    sources = (session.get(SurveySource, violation.subject_id) for violation in violations)
    return sum(remap_from_clone(session, source) for source in sources if source is not None)
