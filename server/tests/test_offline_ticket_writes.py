"""The offline write path: `loregarden mcp call` against the database, no server.

Rebuilds the 2026-10-12 incident on real rows. Two workspaces each hold a ticket
numbered 137; a write addressed as "137" once matched both and rewrote the wrong
one's ``updated_at`` in naive local time. Every write here goes through the CLI
entry point an agent falls back to, and every check re-reads by the row's own id.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from loregarden.cli import errors
from loregarden.cli.main import main
from loregarden.models.domain import Ticket
from loregarden.models.domain.enums import utcnow
from loregarden.services.ticket_ids import (
    AmbiguousTicketReference,
    assign_external_id,
    resolve_one,
)
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace

#: How SQLAlchemy's SQLite DateTime stores an ORM write: naive, microseconds, no offset.
STORED_SHAPE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}$")


def _numbered_ticket(session: Session, slug: str, prefix: str, *, number: int) -> Ticket:
    """A ticket whose number and id the real issuing code assigned."""
    workspace = make_workspace(session, slug=slug)
    workspace.ticket_prefix = prefix
    workspace.last_ticket_number = number - 1
    session.add(workspace)
    ticket = make_ticket(session, workspace_id=workspace.id, title=f"{slug} work")
    assign_external_id(session, ticket, workspace)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="twins")
def twins_fixture(db_session: Session) -> tuple[Ticket, Ticket]:
    """Two real tickets, both numbered 137, issued by the real numbering code."""
    first = _numbered_ticket(db_session, "verdicts", "lgx", number=137)
    second = _numbered_ticket(db_session, "absorber", "blx", number=137)
    assert (first.ticket_number, second.ticket_number) == (137, 137)
    assert first.external_id != second.external_id
    return first, second


@pytest.fixture(name="eastern_clock")
def eastern_clock_fixture() -> Iterator[None]:
    """Run with a local clock 4 hours off UTC, so a naive local stamp is visible."""
    with patch.dict(os.environ, {"TZ": "America/New_York"}):
        time.tzset()
        yield
    time.tzset()


def _raw_row(session: Session, ticket_id: str) -> tuple[str, str]:
    """``(description, updated_at)`` exactly as stored, read by primary key."""
    row = session.execute(
        text("SELECT description, updated_at FROM tickets WHERE id = :id"), {"id": ticket_id}
    ).one()
    return row[0], row[1]


def _cli(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["mcp", "call", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestAmbiguousReferencesAreRefused:
    def test_a_bare_number_with_no_workspace_names_both_candidates(self, db_session, twins):
        first, second = twins
        with pytest.raises(AmbiguousTicketReference) as refused:
            resolve_one(db_session, "137")
        assert sorted(refused.value.candidates) == sorted([first.external_id, second.external_id])

    def test_a_bare_number_is_refused_even_with_one_holder(self, db_session):
        """Unique today is not unique: the next workspace to reach the number would
        silently change what the same reference writes to."""
        only = _numbered_ticket(db_session, "loner", "lon", number=137)
        with pytest.raises(AmbiguousTicketReference) as refused:
            resolve_one(db_session, "137")
        assert only.external_id in refused.value.candidates

    def test_a_bare_external_id_names_exactly_one(self, db_session, twins):
        first, _second = twins
        assert resolve_one(db_session, first.external_id).id == first.id

    def test_a_number_inside_its_workspace_is_unambiguous(self, db_session, twins):
        _first, second = twins
        assert resolve_one(db_session, "137", workspace_id=second.workspace_id).id == second.id

    def test_a_legacy_id_two_workspaces_share_is_refused(self, db_session, twins):
        """`legacy_external_id` is free text and repeats across workspaces in the live
        database (`monitor-escalations` is held twice); `resolve` takes the first."""
        for ticket in twins:
            ticket.legacy_external_id = "monitor-escalations"
            db_session.add(ticket)
        db_session.commit()
        with pytest.raises(AmbiguousTicketReference) as refused:
            resolve_one(db_session, "monitor-escalations")
        assert len(refused.value.candidates) == 2


class TestTheCliWritePath:
    def test_a_write_to_137_is_refused_and_touches_neither_row(self, db_session, twins, capsys):
        before = {ticket.id: _raw_row(db_session, ticket.id) for ticket in twins}

        code, _out, err = _cli(capsys, "loregarden_update_ticket", "ticket_id=137", "description=x")

        assert code == errors.EXIT_ERROR
        assert "AmbiguousTicketReference" in err
        for ticket in twins:
            assert ticket.external_id in err
        db_session.expire_all()
        assert {ticket.id: _raw_row(db_session, ticket.id) for ticket in twins} == before

    def test_a_write_by_external_id_lands_on_one_row_in_utc(
        self, db_session, twins, capsys, eastern_clock
    ):
        target, bystander = twins
        bystander_before = _raw_row(db_session, bystander.id)

        code, out, err = _cli(
            capsys,
            "loregarden_update_ticket",
            f"ticket_id={target.external_id}",
            "description=prior art appended",
        )

        assert code == errors.EXIT_OK, err
        assert json.loads(out)["ticket_id"] == target.id
        db_session.expire_all()
        description, stamped = _raw_row(db_session, target.id)
        assert description == "prior art appended"
        assert STORED_SHAPE.match(stamped), stamped
        # UTC, not the 4-hours-off local clock the fixture installed.
        written = datetime.fromisoformat(stamped).replace(tzinfo=timezone.utc)
        assert abs(datetime.now(timezone.utc) - written) < timedelta(minutes=1)
        assert _raw_row(db_session, bystander.id) == bystander_before


class TestRowcountGuard:
    def test_an_update_whose_row_vanished_raises_instead_of_reporting_success(
        self, db_session, twins
    ):
        """The ORM updates by primary key and checks the matched count: a write that
        reaches zero rows is a StaleDataError, never a quiet no-op."""
        target, _bystander = twins
        with db_session.bind.begin() as other:
            other.execute(
                text("UPDATE tickets SET id = :moved WHERE id = :id"),
                {"moved": f"{target.id}-moved", "id": target.id},
            )
        target.title = "edited"
        db_session.add(target)
        with pytest.raises(StaleDataError, match="expected to update 1 row"):
            db_session.commit()


class TestTimestampConvention:
    def test_utcnow_is_aware_utc(self, eastern_clock):
        stamp = utcnow()
        assert stamp.utcoffset() == timedelta(0)
        assert abs(datetime.now(timezone.utc) - stamp) < timedelta(seconds=5)

    def test_an_orm_stamp_is_stored_naive_utc(self, db_session, twins, eastern_clock):
        target, _bystander = twins
        stamp = utcnow()
        target.updated_at = stamp
        db_session.add(target)
        db_session.commit()
        _description, stored = _raw_row(db_session, target.id)
        assert stored == stamp.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
