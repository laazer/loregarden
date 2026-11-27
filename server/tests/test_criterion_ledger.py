"""Every acceptance criterion of a ticket ends settled: met with proof, or delegated.

794 done tickets carried 4292 criteria and not one had ever been recorded as
met. The ledger is what a gate check reads to refuse a ticket whose criteria
are not all answered for.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from loregarden.models.domain import (
    Artifact,
    CriterionDisposition,
    Ticket,
    TicketState,
)
from loregarden.models.domain.criterion_tables import PersonJudgment
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.criterion_ledger import (
    GapKind,
    delegation_gaps,
    record_delegation,
    settle_met,
    settlements_of,
    unsettled,
    was_split_into,
)
from loregarden.services.evidence import ARTIFACT_KIND
from loregarden.services.seed import seed_database
from sqlmodel import Session
from tests.factories import make_workspace_ticket

CRITERIA = ["The no-layout arm reads width", "Every arm reads width", "Errors name the arm"]


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    seed_database(db_session)
    ticket = make_workspace_ticket(db_session, "cl-ticket")
    ticket.acceptance_criteria_json = serialize_criteria(CRITERIA)
    db_session.add(ticket)
    db_session.commit()
    return ticket


def _sibling(session: Session, name: str, criteria: list[str]) -> Ticket:
    other = make_workspace_ticket(session, name)
    other.acceptance_criteria_json = serialize_criteria(criteria)
    session.add(other)
    session.commit()
    return other


def _met(session: Session, ticket: Ticket, criterion: str, **extra) -> None:
    args = {"tests": ["tests/test_x.py::test_a"], "evidence_artifact_id": None, "reason": "r"}
    args.update(extra)
    settle_met(session, ticket, criterion, commit_sha="abc", settled_by="run:1", **args)


def test_a_ticket_starts_with_every_criterion_unsettled(db_session, ticket):
    assert unsettled(db_session, ticket) == CRITERIA


def test_met_by_tests_settles_the_criterion(db_session, ticket):
    _met(db_session, ticket, CRITERIA[1])
    assert unsettled(db_session, ticket) == [CRITERIA[0], CRITERIA[2]]
    row = settlements_of(db_session, ticket)[CRITERIA[1]]
    assert row.disposition is CriterionDisposition.MET
    assert json.loads(row.tests_json) == ["tests/test_x.py::test_a"]


def test_rewording_a_criterion_voids_its_settlement(db_session, ticket):
    _met(db_session, ticket, CRITERIA[0])
    ticket.acceptance_criteria_json = serialize_criteria(
        ["Every layout arm reads width", *CRITERIA[1:]]
    )
    db_session.add(ticket)
    db_session.commit()
    assert unsettled(db_session, ticket)[0] == "Every layout arm reads width"
    assert CRITERIA[0] not in settlements_of(db_session, ticket), (
        "the old wording's proof is not this one's"
    )


def test_met_needs_proof_a_reason_and_the_exact_text(db_session, ticket):
    with pytest.raises(ValueError, match="tests that prove it"):
        _met(db_session, ticket, CRITERIA[0], tests=[" "])
    with pytest.raises(ValueError, match="reason is required"):
        _met(db_session, ticket, CRITERIA[0], reason=" ")
    with pytest.raises(ValueError, match="worded exactly"):
        _met(db_session, ticket, "the no-layout arm reads width")


def test_met_by_evidence_must_name_this_tickets_evidence(db_session, ticket):
    other = _sibling(db_session, "cl-other", [])
    foreign = Artifact(ticket_id=other.id, kind=ARTIFACT_KIND, evidence_kind="real_surface")
    own = Artifact(ticket_id=ticket.id, kind=ARTIFACT_KIND, evidence_kind="real_surface")
    db_session.add_all([foreign, own])
    db_session.commit()
    with pytest.raises(ValueError, match="not an evidence artifact"):
        _met(db_session, ticket, CRITERIA[0], tests=[], evidence_artifact_id=foreign.id)
    _met(db_session, ticket, CRITERIA[0], tests=[], evidence_artifact_id=own.id)
    assert CRITERIA[0] not in unsettled(db_session, ticket)


def test_a_delegation_needs_a_target_that_carries_the_text(db_session, ticket):
    bare = _sibling(db_session, "cl-bare", [])
    with pytest.raises(ValueError, match="does not carry"):
        record_delegation(
            db_session, ticket, CRITERIA[2], target=bare, settled_by="operator", reason="r"
        )
    carrier = _sibling(db_session, "cl-carrier", [CRITERIA[2]])
    record_delegation(
        db_session, ticket, CRITERIA[2], target=carrier, settled_by="operator", reason="r"
    )
    db_session.commit()
    assert CRITERIA[2] not in unsettled(db_session, ticket)
    assert delegation_gaps(db_session, ticket) == []


@pytest.mark.parametrize(
    ("target_of", "reason", "refusal"),
    [
        (lambda ticket, carrier: ticket, "r", "its own ticket"),
        (lambda ticket, carrier: carrier, "  ", "reason is required"),
    ],
)
def test_a_delegation_to_itself_or_without_a_reason_is_refused(
    db_session, ticket, target_of, reason, refusal
):
    carrier = _sibling(db_session, "cl-carrier", [CRITERIA[2]])
    with pytest.raises(ValueError, match=refusal):
        record_delegation(
            db_session,
            ticket,
            CRITERIA[2],
            target=target_of(ticket, carrier),
            settled_by="operator",
            reason=reason,
        )
    assert CRITERIA[2] in unsettled(db_session, ticket)


def test_a_closed_target_is_refused_but_a_done_one_is_not(db_session, ticket):
    carrier = _sibling(db_session, "cl-carrier", [CRITERIA[2]])
    carrier.state = TicketState.WONT_DO
    with pytest.raises(ValueError, match="is closed wont_do"):
        record_delegation(
            db_session, ticket, CRITERIA[2], target=carrier, settled_by="operator", reason="r"
        )
    carrier.state = TicketState.DONE
    record_delegation(
        db_session, ticket, CRITERIA[2], target=carrier, settled_by="operator", reason="r"
    )


def test_a_delegation_replaces_the_proof_it_settled_with_before(db_session, ticket):
    own = Artifact(ticket_id=ticket.id, kind=ARTIFACT_KIND, evidence_kind="real_surface")
    db_session.add(own)
    db_session.commit()
    _met(db_session, ticket, CRITERIA[2], evidence_artifact_id=own.id)
    before = settlements_of(db_session, ticket)[CRITERIA[2]]
    before.judges = next(iter(PersonJudgment))
    before.person_ask = "Does it feel right?"
    before.settled_at = datetime(2026, 1, 1)
    db_session.add(before)
    db_session.commit()
    carrier = _sibling(db_session, "cl-carrier", [CRITERIA[2]])

    row = record_delegation(
        db_session,
        ticket,
        CRITERIA[2],
        target=carrier,
        settled_by="operator",
        reason="  the carrier owns the layout  ",
    )
    db_session.commit()

    assert row is settlements_of(db_session, ticket)[CRITERIA[2]]
    assert row.disposition is CriterionDisposition.DELEGATED
    assert (row.tests_json, row.evidence_artifact_id, row.commit_sha) == ("[]", None, "")
    assert (row.judges, row.person_ask) == (None, "")
    assert (row.settled_by, row.reason) == ("operator", "the carrier owns the layout")
    assert row.settled_at > datetime(2026, 1, 1)


def test_a_delegate_that_drops_or_loses_the_criterion_reopens_the_gap(db_session, ticket):
    carrier = _sibling(db_session, "cl-carrier", [CRITERIA[2]])
    record_delegation(
        db_session, ticket, CRITERIA[2], target=carrier, settled_by="operator", reason="r"
    )
    db_session.commit()

    carrier.state = TicketState.WONT_DO
    db_session.add(carrier)
    db_session.commit()
    assert [g.kind for g in delegation_gaps(db_session, ticket)] == [GapKind.DELEGATE_DROPPED]

    carrier.state = TicketState.IN_PROGRESS
    carrier.acceptance_criteria_json = serialize_criteria([])
    db_session.add(carrier)
    db_session.commit()
    assert [g.kind for g in delegation_gaps(db_session, ticket)] == [GapKind.DELEGATE_LOST_IT]


def test_a_split_is_remembered_on_the_ticket_it_went_to(db_session, ticket):
    carrier = _sibling(db_session, "cl-carrier", [CRITERIA[2]])
    record_delegation(
        db_session,
        ticket,
        CRITERIA[2],
        target=carrier,
        settled_by="control_plane",
        reason="r",
        by_split=True,
    )
    db_session.commit()
    assert was_split_into(db_session, carrier, CRITERIA[2])
    assert not was_split_into(db_session, carrier, CRITERIA[0])
