"""What a chat's wait card is given to show (lg-code-knowledge-1055).

The card on 1055's triage chat read "6h 59m left" on a 2h ceiling and nothing
else: the deadline went out as naive UTC, which a browser parses as its own
wall clock, and the waiter (a `kill -0` loop) writes no output until it exits,
so the landing it waited on — 17% through its suite — was invisible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    ChatSurface,
    DockerFootprint,
    DockerHolderKind,
    DockerLease,
    DockerLeaseStatus,
    LandingPhase,
    TicketState,
    utcnow,
)
from loregarden.models.domain.chat_wait_tables import ChatWait
from loregarden.services import chat_wait
from loregarden.services.chat_wait import last_log_line, wait_payload
from loregarden.services.land_verification import LAND_VERIFY_TRANSITION
from loregarden.services.landing_status import (
    TicketLandingStatus,
    landing_summary,
)
from loregarden.services.triage_service import triage_snapshot
from loregarden.testing.factories import make_ticket, make_workspace
from sqlmodel import Session
from tests.worktree_helpers import at_terminal_stage, make_run


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _wait(log_path: str, **fields) -> ChatWait:
    registered = datetime(2026, 11, 17, 22, 2, 47)  # naive, as SQLite hands it back
    return ChatWait(
        surface=ChatSurface.TICKET_TRIAGE,
        thread_key="t",
        origin_run_id="r",
        pid=1,
        pid_identity="",
        command="wait for land_ticket",
        log_path=log_path,
        registered_at=registered,
        deadline=registered + timedelta(hours=2),
        **fields,
    )


def test_the_stamps_carry_their_utc_offset(tmp_path):
    payload = wait_payload(_wait(str(tmp_path / "none.log")))

    deadline = datetime.fromisoformat(payload["deadline"])
    assert deadline.utcoffset() == timedelta(0), (
        f"{payload['deadline']!r} has no offset, so a browser reads it as local time"
    )
    assert deadline == datetime(2026, 11, 18, 0, 2, 47, tzinfo=timezone.utc)
    assert datetime.fromisoformat(payload["registered_at"]).utcoffset() == timedelta(0)


def test_the_last_line_is_the_last_one_written_without_its_escapes(tmp_path):
    log = tmp_path / "push.log"
    log.write_text(
        "capacity: waiting — 2 ahead\n\x1b[38;2;0;0;0m│\x1b[m pre-push server-tests [5/5]\n\n  \n"
    )

    assert last_log_line(str(log)) == "│ pre-push server-tests [5/5]"


def test_a_redrawn_progress_line_shows_its_latest_draw(tmp_path):
    log = tmp_path / "bar.log"
    log.write_text("[1/5] 20%\r[2/5] 40%\r[3/5] 60%")

    assert last_log_line(str(log)) == "[3/5] 60%"


def test_only_the_tail_of_a_long_log_is_read_and_the_line_is_capped(tmp_path):
    log = tmp_path / "long.log"
    log.write_text("x" * 1_000_000 + "\n" + "y" * 1000 + "\n")

    line = last_log_line(str(log))

    assert set(line) == {"y"}
    assert len(line) == chat_wait._LOG_LINE_CHARS


def test_a_log_not_written_yet_has_no_line(tmp_path):
    assert last_log_line(str(tmp_path / "absent.log")) == ""
    assert wait_payload(_wait(str(tmp_path / "absent.log")))["log_line"] == ""


def test_an_unreadable_log_says_so_rather_than_reading_as_empty(tmp_path):
    directory = tmp_path / "a-directory"
    directory.mkdir()

    line = last_log_line(str(directory))

    assert line.startswith("(log unreadable"), line


@pytest.mark.parametrize(
    ("phase", "fields", "expected"),
    [
        (LandingPhase.NOT_LANDING, {}, ""),
        (LandingPhase.MERGING, {}, "Landing on integration/x: merging"),
        (
            LandingPhase.WAITING_FOR_CAPACITY,
            {"ahead_count": 2},
            "Landing on integration/x: waiting for capacity, 2 ahead",
        ),
        (LandingPhase.VERIFYING, {}, "Landing on integration/x: verifying"),
        (
            LandingPhase.VERIFYING,
            {"progress": "pytest (5/5) · 2220/13321 (16%)"},
            "Landing on integration/x: verifying · pytest (5/5) · 2220/13321 (16%)",
        ),
    ],
)
def test_every_landing_phase_has_its_one_line(phase, fields, expected):
    status = TicketLandingStatus(phase=phase, target="integration/x", **fields)

    assert landing_summary(status) == expected


def test_a_ticket_chat_waiting_while_its_ticket_lands_shows_the_landing(session, tmp_path):
    workspace = make_workspace(session, slug="wait-card")
    ticket = make_ticket(
        session, workspace_id=workspace.id, external_id="lg-wait-1", state=TicketState.IN_PROGRESS
    )
    at_terminal_stage(session, ticket)
    session.refresh(ticket)
    origin = make_run(session, workspace, ticket, "run_wait1", agent_id="triage")
    log = tmp_path / "land-wait.log"
    log.write_text("")
    session.add(
        ChatWait(
            surface=ChatSurface.TICKET_TRIAGE,
            thread_key=ticket.id,
            origin_run_id=origin.id,
            pid=1,
            pid_identity="",
            command="wait for land_ticket lg-wait-1",
            log_path=str(log),
            deadline=utcnow() + timedelta(hours=2),
        )
    )
    session.add(
        DockerLease(
            holder_kind=DockerHolderKind.AD_HOC,
            holder_label=f"gates {LAND_VERIFY_TRANSITION} 0123456789ab · wait-card · lg-wait-1",
            ticket_id=ticket.id,
            status=DockerLeaseStatus.HELD,
            footprint=DockerFootprint.HEAVY,
            cpus=4.0,
            memory_mb=8192,
            position=0,
            granted_at=utcnow(),
            progress_step="pytest (5/5)",
            progress_done=2220,
            progress_total=13321,
        )
    )
    session.commit()

    wait = triage_snapshot(session, ticket)["wait"]

    assert wait is not None
    landing = wait["landing"]
    assert landing is not None, "the ticket is landing; the card must say so"
    assert "verifying · pytest (5/5)" in landing["summary"]
    assert (landing["done"], landing["total"]) == (2220, 13321)
    assert wait["log_line"] == ""


def test_a_ticket_chat_with_no_landing_sends_none(session, tmp_path):
    workspace = make_workspace(session, slug="wait-card-idle")
    ticket = make_ticket(session, workspace_id=workspace.id, external_id="lg-wait-2")
    origin = make_run(session, workspace, ticket, "run_wait2", agent_id="triage")
    session.add(
        ChatWait(
            surface=ChatSurface.TICKET_TRIAGE,
            thread_key=ticket.id,
            origin_run_id=origin.id,
            pid=1,
            pid_identity="",
            command="git push",
            log_path=str(tmp_path / "push.log"),
            deadline=utcnow() + timedelta(hours=2),
        )
    )
    session.commit()

    assert triage_snapshot(session, ticket)["wait"]["landing"] is None
