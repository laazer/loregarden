"""Each acceptance criterion of a ticket, settled met or delegated (`criterion_tables`).

The writers are here, and so is the question the gate check asks: which of
this ticket's current criteria are not answered for? A settlement names the
criterion by its text, so only settlements whose text the ticket still carries
count. A reworded criterion is unsettled until it is proven again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    Artifact,
    CriterionDisposition,
    CriterionSettlement,
    Ticket,
    TicketState,
    utcnow,
)
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.evidence import ARTIFACT_KIND
from pydantic import TypeAdapter
from sqlmodel import Session, col, select

_TEST_IDS = TypeAdapter(list[str])


class GapKind(StrEnum):
    """Why a criterion is not answered for."""

    #: No settlement carries its current text.
    UNSETTLED = "unsettled"
    #: Settled met by tests, and a rerun at HEAD did not pass them.
    TESTS_FAILED = "tests_failed"
    #: Settled met by evidence captured at another commit.
    EVIDENCE_STALE = "evidence_stale"
    #: Delegated to a ticket that no longer carries its text.
    DELEGATE_LOST_IT = "delegate_lost_it"
    #: Delegated to a ticket that was closed without doing it.
    DELEGATE_DROPPED = "delegate_dropped"


@dataclass(frozen=True)
class CriterionGap:
    criterion: str
    kind: GapKind
    detail: str = ""

    def line(self) -> str:
        detail = f" — {self.detail}" if self.detail else ""
        return f"- [{self.kind.value}] {self.criterion}{detail}"


def test_ids(settlement: CriterionSettlement) -> list[str]:
    return _TEST_IDS.validate_json(settlement.tests_json or "[]")


def settlements_of(session: Session, ticket: Ticket) -> dict[str, CriterionSettlement]:
    """The ticket's settlements whose criterion it still carries, by criterion text."""
    current = set(load_criteria(ticket.acceptance_criteria_json))
    rows = session.exec(
        select(CriterionSettlement).where(CriterionSettlement.ticket_id == ticket.id)
    ).all()
    return {row.criterion: row for row in rows if row.criterion in current}


def unsettled(session: Session, ticket: Ticket) -> list[str]:
    """The ticket's current criteria with no settlement, in the ticket's order."""
    settled = settlements_of(session, ticket)
    return [c for c in load_criteria(ticket.acceptance_criteria_json) if c not in settled]


def _current_criterion(ticket: Ticket, criterion: str) -> str:
    text = criterion.strip()
    if text not in load_criteria(ticket.acceptance_criteria_json):
        raise ValueError(
            f"{ticket.external_id} has no acceptance criterion worded exactly "
            f"{text!r}. Settle a criterion by its exact current text."
        )
    return text


def _upsert(session: Session, ticket: Ticket, criterion: str) -> CriterionSettlement:
    row = session.exec(
        select(CriterionSettlement).where(
            CriterionSettlement.ticket_id == ticket.id,
            CriterionSettlement.criterion == criterion,
        )
    ).first()
    return row or CriterionSettlement(
        ticket_id=ticket.id, criterion=criterion, disposition=CriterionDisposition.MET
    )


def settle_met(
    session: Session,
    ticket: Ticket,
    criterion: str,
    *,
    tests: list[str],
    evidence_artifact_id: str | None,
    commit_sha: str,
    settled_by: str,
    reason: str,
) -> CriterionSettlement:
    """Record ``criterion`` met by ``tests`` or by an evidence artifact. Commits.

    Tests are the floor: the gate check reruns them at HEAD. Evidence without a
    test is for a criterion only a real surface can show (an HTTP response, a
    screenshot), and it must name an evidence artifact of this ticket.
    """
    text = _current_criterion(ticket, criterion)
    tests = [t.strip() for t in tests if t.strip()]
    if not tests and not evidence_artifact_id:
        raise ValueError("met needs the tests that prove it, or an evidence artifact")
    if evidence_artifact_id:
        artifact = session.get(Artifact, evidence_artifact_id)
        if artifact is None or artifact.ticket_id != ticket.id or artifact.kind != ARTIFACT_KIND:
            raise ValueError(f"{evidence_artifact_id} is not an evidence artifact of this ticket")
    if not reason.strip():
        raise ValueError("A reason is required: say how the tests or evidence prove it.")
    row = _upsert(session, ticket, text)
    row.disposition = CriterionDisposition.MET
    row.tests_json = json.dumps(tests)
    row.evidence_artifact_id = evidence_artifact_id
    row.commit_sha = commit_sha
    row.delegated_to_ticket_id = None
    row.by_split = False
    row.settled_by = settled_by
    row.settled_at = utcnow()
    row.reason = reason.strip()
    session.add(row)
    session.commit()
    return row


def record_delegation(
    session: Session,
    ticket: Ticket,
    criterion: str,
    *,
    target: Ticket,
    settled_by: str,
    reason: str,
    by_split: bool = False,
) -> CriterionSettlement:
    """Record ``criterion`` handed to ``target``. Does not commit.

    The caller has already given ``target`` the criterion's exact text; this
    refuses a target that does not carry it, because a delegation the target
    does not know about is a criterion nobody owns.
    """
    text = _current_criterion(ticket, criterion)
    if target.id == ticket.id:
        raise ValueError("A criterion cannot be delegated to its own ticket")
    if text not in load_criteria(target.acceptance_criteria_json):
        raise ValueError(f"{target.external_id} does not carry {text!r} as a criterion")
    if target.state in StateMachine.TERMINAL_TICKET_STATES and target.state is not TicketState.DONE:
        raise ValueError(f"{target.external_id} is closed {target.state.value}; it will not do it")
    if not reason.strip():
        raise ValueError("A reason is required: say why this is another ticket's work.")
    row = _upsert(session, ticket, text)
    row.disposition = CriterionDisposition.DELEGATED
    row.tests_json = "[]"
    row.evidence_artifact_id = None
    row.commit_sha = ""
    row.delegated_to_ticket_id = target.id
    row.by_split = by_split
    row.settled_by = settled_by
    row.settled_at = utcnow()
    row.reason = reason.strip()
    session.add(row)
    return row


def was_split_into(session: Session, ticket: Ticket, criterion: str) -> bool:
    """Whether ``criterion`` reached ``ticket`` by a split: a second failure is a person's."""
    return (
        session.exec(
            select(CriterionSettlement.id).where(
                CriterionSettlement.delegated_to_ticket_id == ticket.id,
                CriterionSettlement.criterion == criterion,
                col(CriterionSettlement.by_split).is_(True),
            )
        ).first()
        is not None
    )


def delegation_gaps(session: Session, ticket: Ticket) -> list[CriterionGap]:
    """Delegated criteria whose target lost the text or was closed without doing it."""
    gaps: list[CriterionGap] = []
    for text, row in settlements_of(session, ticket).items():
        if row.disposition is not CriterionDisposition.DELEGATED:
            continue
        target = (
            session.get(Ticket, row.delegated_to_ticket_id) if row.delegated_to_ticket_id else None
        )
        if target is None or text not in load_criteria(target.acceptance_criteria_json):
            name = target.external_id if target else "a missing ticket"
            gaps.append(
                CriterionGap(text, GapKind.DELEGATE_LOST_IT, f"{name} no longer carries it")
            )
        elif (
            target.state in StateMachine.TERMINAL_TICKET_STATES
            and target.state is not TicketState.DONE
        ):
            gaps.append(
                CriterionGap(
                    text,
                    GapKind.DELEGATE_DROPPED,
                    f"{target.external_id} closed {target.state.value}",
                )
            )
    return gaps
