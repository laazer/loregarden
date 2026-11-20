"""Invariant: an operator override queued behind a stage run is applied once nothing runs.

Kept apart from `operator_questions` for the reason `closed_ticket_answers` is:
the repair resumes the orchestration, and `orchestration` imports
`operator_questions`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import InvariantReentry, Ticket
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.operator_questions import apply_queued_overrides, queued_overrides
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_resume import resume_orchestration
from loregarden.services.run_concurrency import find_active_stage_run
from loregarden.services.ticket_state_service import RESOLVED_STATES
from sqlmodel import Session


def _stranded(session: Session) -> list[tuple[Ticket, list[str]]]:
    """Open tickets holding queued overrides with no stage run left to wait for."""
    by_ticket: dict[str, list[str]] = {}
    for approval in queued_overrides(session):
        if approval.ticket_id is not None:
            by_ticket.setdefault(approval.ticket_id, []).append(approval.id)
    stranded: list[tuple[Ticket, list[str]]] = []
    for ticket_id, approval_ids in by_ticket.items():
        ticket = session.get(Ticket, ticket_id)
        if ticket is None or find_active_stage_run(session, ticket_id) is not None:
            continue
        stranded.append((ticket, approval_ids))
    return stranded


def queued_overrides_with_nothing_running(session: Session) -> list[Violation]:
    """Each queued override whose ticket has no stage run in flight.

    Closed tickets are included: the override can no longer apply, and only the
    operator can say whether it still matters. `entered_at` is the answer's
    resolution; the run it waited on ended some time after.
    """
    violations: list[Violation] = []
    for ticket, approval_ids in _stranded(session):
        for approval_id in approval_ids:
            violations.append(
                Violation(
                    subject_id=approval_id,
                    ticket_id=ticket.id,
                    entered_at=None,
                    detail=(
                        f"{ticket.external_id} has an operator override queued behind a "
                        f"stage run, and no stage is running ({ticket.state.value})"
                    ),
                )
            )
    return violations


def apply_stranded_overrides(session: Session, violations: Sequence[Violation]) -> int:
    """Apply them on open tickets, re-checked here; a closed ticket stays reported."""
    wanted = {violation.subject_id for violation in violations}
    applied = 0
    orch = OrchestrationService(session)
    for ticket, approval_ids in _stranded(session):
        hits = wanted.intersection(approval_ids)
        if not hits or ticket.state in RESOLVED_STATES:
            continue
        if apply_queued_overrides(session, orch, ticket):
            applied += len(hits)
            resume_orchestration(session, ticket)
    return applied


QUEUED_OVERRIDES_APPLY = Invariant(
    name="queued_overrides_apply",
    ticket="lg-conversation-that-2752",
    statement=(
        "no operator override stays queued behind a stage run once no stage runs on its ticket"
    ),
    detect=queued_overrides_with_nothing_running,
    repair=apply_stranded_overrides,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 20),
    expected_because=(
        "run completion applies a queued override, but a stage run also ends outside "
        "completion (failed at restart, reaped, cancelled, ended by a stage ask), and "
        "those writers do not settle the ticket's workflow"
    ),
)
