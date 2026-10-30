"""One compare-and-swap on `tickets.revision`, and the two edits that race for it.

`tickets.revision` was incremented by fourteen writers, served to the client as
`TicketDetail.revision`, and accepted back by no endpoint — so every concurrent
edit was last-write-wins with no error and no trace. The guard this module
covers is the compare-and-swap that makes the field mean something, opt-in, at
the two edges a caller drives.

**Which test does the work.** `test_two_threads_same_expected_revision_one_wins`
is the only test here that falsifies a check-then-act implementation: a
sequential two-session test never interleaves the read and the write, so it
passes against `SELECT revision; if revision == expected: UPDATE`.
`test_the_claim_is_one_statement_and_reads_nothing_first` catches the same
mutant structurally, by counting the statements the claim emits. Both are kept:
the threaded one proves atomicity under contention, the counting one says why,
and `tests/test_docker_lease_concurrency.py` recorded what happens when only
the weaker test exists.

**The arithmetic test is load-bearing.** The CAS owns one bump and the in-body
bumps in `services/ticket_manual_edit.py` are left alone, which is only safe
because `revision` stays clean in memory on the path that matters — SQLAlchemy
flushes dirty attributes, so a value nobody assigned is never written back over
the claim. `test_revision_arithmetic_returned_value_is_reusable` and
`test_the_claim_does_not_expire_revision_on_a_loaded_ticket` are what go red if
anyone "fixes" the guard to assign, refresh or expire `ticket.revision`.

Modelled on `tests/test_docker_lease_concurrency.py`, and on `conftest`'s
`isolated_db` for the same reason: a real pool rather than StaticPool, so two
sessions hold two connections and two transactions, under WAL with a 30s busy
timeout.
"""

from __future__ import annotations

import json
import threading

import pytest
from loregarden.models.domain import Ticket, UpdateTicketRequest
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_revision_guard import (
    TicketRevisionConflict,
    claim_ticket_revision,
    guard_ticket_revision,
)
from sqlalchemy import event
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace


@pytest.fixture(name="guarded_ticket")
def guarded_ticket_fixture(isolated_db) -> tuple[str, int]:
    """One ticket with criteria to tick and a settled revision, as (id, revision).

    Built in a fixture rather than in each test body so a broken fixture is an
    ERROR rather than a failure that reads like the behaviour under test.
    """
    with Session(isolated_db) as setup:
        workspace = make_workspace(setup, slug="proj")
        ticket = make_ticket(setup, workspace_id=workspace.id, title="original title")
        OrchestrationService(setup).update_ticket_manual(
            ticket,
            UpdateTicketRequest(
                description="original description",
                acceptance_criteria=["first", "second"],
            ),
        )
        setup.refresh(ticket)
        return ticket.id, ticket.revision


def _guarded_edit(
    engine,
    ticket_id: str,
    expected_revision: int,
    body: UpdateTicketRequest,
    *,
    before_claim=None,
) -> int:
    """What the two caller-driven edges do: load the row, claim it, then apply.

    The load comes first deliberately — that is the ordering in `api/tickets.py`
    and in the MCP handler, and it is the ordering under which a guard that
    expired or re-read `revision` would write a stale value back over the claim.
    Returns the committed revision the caller is handed.

    `before_claim` runs after the load and before the guard, which is the only
    point where the concurrency test can arm a rendezvous that lands on the
    guard's own first statement rather than on the load's.
    """
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if before_claim is not None:
            before_claim()
        guard_ticket_revision(session, ticket_id=ticket_id, expected_revision=expected_revision)
        OrchestrationService(session).update_ticket_manual(ticket, body)
        session.refresh(ticket)
        return ticket.revision


def _stored(engine, ticket_id: str) -> Ticket:
    with Session(engine) as check:
        ticket = check.get(Ticket, ticket_id)
        check.expunge(ticket)
        return ticket


# ---- the claim itself --------------------------------------------------


def test_a_matching_expected_revision_claims_the_row(isolated_db, guarded_ticket) -> None:
    """True, and the committed row sits one above what the caller read."""
    ticket_id, revision = guarded_ticket

    with Session(isolated_db) as session:
        assert (
            claim_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision) is True
        )
        session.commit()

    assert _stored(isolated_db, ticket_id).revision == revision + 1


def test_the_claim_commits_nothing_of_its_own(isolated_db, guarded_ticket) -> None:
    """The caller's transaction owns the commit, so a later failure takes the
    claim with it rather than burning a revision on an edit that never landed."""
    ticket_id, revision = guarded_ticket

    with Session(isolated_db) as session:
        assert claim_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision)
        # A peer, in its own transaction, still sees the unclaimed row.
        assert _stored(isolated_db, ticket_id).revision == revision
        session.rollback()

    assert _stored(isolated_db, ticket_id).revision == revision


def test_a_mismatched_expected_revision_changes_nothing(isolated_db, guarded_ticket) -> None:
    ticket_id, revision = guarded_ticket

    with Session(isolated_db) as session:
        assert (
            claim_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision + 7)
            is False
        )
        session.commit()

    assert _stored(isolated_db, ticket_id).revision == revision


def test_the_claim_is_one_statement_and_reads_nothing_first(isolated_db, guarded_ticket) -> None:
    """Exactly one statement, and it is the UPDATE.

    A `SELECT revision` before it is the whole defect in miniature: between the
    read and the write another writer can move the row, and the guard then
    approves an edit against a revision that no longer exists. Counted rather
    than reviewed, because this is the shape the mutation control attacks.
    """
    ticket_id, revision = guarded_ticket
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        statements.append(statement)

    with Session(isolated_db) as session:
        event.listen(isolated_db, "before_cursor_execute", record)
        try:
            assert claim_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision)
        finally:
            event.remove(isolated_db, "before_cursor_execute", record)
        session.commit()

    assert len(statements) == 1, f"the claim emitted {len(statements)} statements: {statements}"
    assert statements[0].lstrip().upper().startswith("UPDATE")


def test_the_claim_does_not_expire_revision_on_a_loaded_ticket(isolated_db, guarded_ticket) -> None:
    """A loaded `Ticket.revision` stays clean and stays readable without SQL.

    `synchronize_session=False` makes the in-session value look stale, and the
    tempting repair — assign it, or `session.refresh` — is what breaks the
    arithmetic below. This pins the absence of that repair at its mechanism:
    after the claim, reading `revision` off the loaded instance emits no
    statement, because nothing expired it.
    """
    ticket_id, revision = guarded_ticket
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        statements.append(statement)

    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        assert claim_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision)
        event.listen(isolated_db, "before_cursor_execute", record)
        try:
            in_memory = ticket.revision
        finally:
            event.remove(isolated_db, "before_cursor_execute", record)
        session.commit()

    assert statements == [], f"reading revision re-queried the row: {statements}"
    assert in_memory == revision, "the guard made the loaded revision dirty or re-read it"


# ---- the conflict type ------------------------------------------------


def test_the_conflict_carries_both_numbers_in_one_json_line() -> None:
    """One payload shape, two carriers: HTTP uses `.payload`, MCP uses `str()`.

    `mcp/protocol.py` renders a raised exception as `{text: str(exc)}` and
    offers no other channel, so prose there would drop `current_revision` for
    every programmatic caller on that transport.
    """
    exc = TicketRevisionConflict(expected_revision=4, current_revision=11)

    assert exc.expected_revision == 4
    assert exc.current_revision == 11
    assert exc.payload["error"] == "revision_conflict"
    assert exc.payload["expected_revision"] == 4
    assert exc.payload["current_revision"] == 11
    assert isinstance(exc.payload["current_revision"], int)
    assert "4" in exc.payload["message"] and "11" in exc.payload["message"]

    rendered = str(exc)
    assert "\n" not in rendered, "the MCP text channel is one line"
    assert json.loads(rendered) == exc.payload


def test_the_conflict_is_not_a_value_error() -> None:
    """`api/tickets.py` maps `ValueError` to 400. A conflict that could be
    caught by that arm would answer 400 with prose instead of 409 with a
    number, depending only on which `except` came first."""
    exc = TicketRevisionConflict(expected_revision=1, current_revision=2)

    assert isinstance(exc, Exception)
    assert not isinstance(exc, ValueError)


# ---- the guard under contention ---------------------------------------


def test_two_threads_same_expected_revision_one_wins(isolated_db, guarded_ticket) -> None:
    """The test this design exists for: two titles, one revision, one winner.

    Exactly one edit commits, exactly one raises `TicketRevisionConflict`, and —
    the assertion that matters — the loser's title is nowhere in the stored row.
    Today, with no guard, both commit and one simply vanishes.

    **The interleaving is forced, not hoped for.** A barrier at the top of each
    thread is not enough: measured against a check-then-act mutant of the claim,
    the winner ran its whole edit and committed before the loser's thread was
    scheduled at all, so the mutant survived and the test proved nothing. So the
    rendezvous sits where it has to — inside the driver, on the first statement
    each thread's guard issues. Both threads are held there until both have
    arrived, and only then does either reach the database. A single guarded
    UPDATE then has to be decided by the database, which is the property under
    test; a guard that reads first has both threads read the same stale value
    and both edits land.
    """
    ticket_id, revision = guarded_ticket
    outcomes: list[tuple[str, BaseException | None]] = []
    lock = threading.Lock()
    arming = threading.local()
    at_the_claim = threading.Barrier(2)

    def hold_until_both_threads_are_here(conn, cursor, statement, parameters, ctx, many):  # noqa: ARG001
        """Block the first statement a guarded edit issues, per thread."""
        if not getattr(arming, "armed", False):
            return
        arming.armed = False
        at_the_claim.wait(timeout=20)

    def arm() -> None:
        arming.armed = True

    def edit(title: str) -> None:
        failure: BaseException | None = None
        try:
            _guarded_edit(
                isolated_db,
                ticket_id,
                revision,
                UpdateTicketRequest(title=title),
                before_claim=arm,
            )
        except TicketRevisionConflict as exc:
            failure = exc
        finally:
            arming.armed = False
        with lock:
            outcomes.append((title, failure))

    threads = [threading.Thread(target=edit, args=(t,)) for t in ("alpha title", "beta title")]
    event.listen(isolated_db, "before_cursor_execute", hold_until_both_threads_are_here)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        event.remove(isolated_db, "before_cursor_execute", hold_until_both_threads_are_here)

    assert not any(thread.is_alive() for thread in threads), "an edit deadlocked"
    assert len(outcomes) == 2, "an edit raised something other than a conflict"

    winners = [title for title, failure in outcomes if failure is None]
    losers = [(title, failure) for title, failure in outcomes if failure is not None]
    assert len(winners) == 1, f"both edits were allowed through: {outcomes}"
    assert len(losers) == 1
    loser_title, conflict = losers[0]
    assert isinstance(conflict, TicketRevisionConflict)
    assert conflict.expected_revision == revision
    assert conflict.current_revision > revision

    stored = _stored(isolated_db, ticket_id)
    assert stored.title == winners[0]
    assert stored.title != loser_title, "the loser's write silently overwrote the winner's"


# ---- the arithmetic the contract rests on -----------------------------


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("content only", UpdateTicketRequest(description="a rewritten description")),
        ("checked criteria only", UpdateTicketRequest(checked_acceptance_criteria=["first"])),
        (
            "content and branch",
            UpdateTicketRequest(description="another description", branch="feat/guarded"),
        ),
    ],
)
def test_revision_arithmetic_returned_value_is_reusable(
    isolated_db, guarded_ticket, label: str, body: UpdateTicketRequest
) -> None:
    """Whatever the edit touches, the number the caller is handed is usable next.

    Three shapes, because they bump a different number of times: a content edit
    adds the in-body bump to the CAS's, a checks-only edit adds none, and
    content+branch adds two. The contract is not an exact arithmetic — it is
    that the value is strictly above what was sent, equals the stored row, and
    works as the next `expected_revision`. That is what a pane holding the reply
    needs, and it is what breaks if the guard assigns or refreshes `revision`.
    """
    ticket_id, revision = guarded_ticket

    returned = _guarded_edit(isolated_db, ticket_id, revision, body)

    assert returned > revision, f"{label}: the reply did not advance past what was sent"
    assert returned == _stored(isolated_db, ticket_id).revision, (
        f"{label}: the reply disagrees with the stored row"
    )

    # The reply is reusable: a caller that keeps editing never has to refetch.
    second = _guarded_edit(
        isolated_db, ticket_id, returned, UpdateTicketRequest(title=f"after {label}")
    )
    assert second > returned
    assert _stored(isolated_db, ticket_id).title == f"after {label}"


# ---- the absent token, and the stale one ------------------------------


def test_omitted_expected_revision_lets_both_writes_through(isolated_db, guarded_ticket) -> None:
    """Today's behaviour, asserted rather than assumed.

    This is the exemption the orchestrator relies on: with no token there is no
    claim, so a stage recording what happened can never fail on contention. It
    is also the bug, which is why it is pinned here rather than left implicit —
    the second write wins and the first leaves no trace.
    """
    ticket_id, _ = guarded_ticket

    for title in ("first unguarded", "second unguarded"):
        with Session(isolated_db) as session:
            ticket = session.get(Ticket, ticket_id)
            OrchestrationService(session).update_ticket_manual(
                ticket, UpdateTicketRequest(title=title)
            )

    stored = _stored(isolated_db, ticket_id)
    assert stored.title == "second unguarded"
    assert "first unguarded" not in stored.title


def test_stale_expected_revision_carries_current_revision(isolated_db, guarded_ticket) -> None:
    """A quiet ticket and a stale token: the conflict names the real number."""
    ticket_id, revision = guarded_ticket
    moved_to = _guarded_edit(
        isolated_db, ticket_id, revision, UpdateTicketRequest(title="the write that landed")
    )

    with Session(isolated_db) as session:
        with pytest.raises(TicketRevisionConflict) as caught:
            guard_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision)
        session.rollback()

    assert caught.value.expected_revision == revision
    assert caught.value.current_revision == moved_to
    assert _stored(isolated_db, ticket_id).title == "the write that landed"
