"""The corners of the compare-and-swap that the contract's own tests step over.

`test_ticket_revision_cas.py` proves the guard works: a matching token claims,
a stale one raises, two threads produce one winner. This module attacks the same
code from the directions that produce a guard which *passes that suite* and is
still wrong.

Each test here names the implementation it falsifies, because a test whose
mutant nobody can state is a test nobody can maintain:

| What it falsifies | Test |
|---|---|
| a claim whose WHERE lost `Ticket.id` | `test_the_claim_moves_only_the_named_ticket` |
| `if expected_revision:` anywhere on the path | `test_revision_zero_is_a_revision_like_any_other` |
| re-reading the current revision through the identity map | `test_the_conflict_reports_the_committed_revision_not_the_one_this_session_loaded` |
| a guard that runs after the applier | `test_a_conflict_leaves_every_field_exactly_as_it_was` |
| a claim that approves a row it did not find | `test_the_claim_on_a_missing_ticket_is_simply_false` |
| a `payload` that grew a key, or lost one | `test_the_payload_carries_exactly_four_keys` |
| any non-atomic claim, under wider contention than two | `test_four_threads_leave_exactly_one_winner_twice_running` |

The numbers the boundary tests use are not arbitrary: `revision` was measured on
the live database on 2026-10-20 at mean 826 and max 82,334, so 0, -1 and
2**63-1 are the values a caller can produce that no ticket will ever hold.
"""

from __future__ import annotations

import json
import threading

import pytest
from loregarden.models.domain import Ticket, TicketState, UpdateTicketRequest
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_revision_guard import (
    TicketRevisionConflict,
    claim_ticket_revision,
    guard_ticket_revision,
)
from sqlalchemy import event
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace

#: The largest integer SQLite stores. A caller can send it; no row holds it.
MAX_SQLITE_INT = 2**63 - 1


@pytest.fixture(name="two_tickets")
def two_tickets_fixture(isolated_db) -> tuple[str, str, int]:
    """Two tickets sharing one revision, as (claimed_id, bystander_id, revision).

    A guard tested against a single-row table cannot distinguish `WHERE id = ?
    AND revision = ?` from `WHERE revision = ?`: both update exactly one row and
    both return True. Two rows at the same revision is the cheapest fixture
    that can tell them apart.
    """
    with Session(isolated_db) as setup:
        workspace = make_workspace(setup, slug="proj")
        orch = OrchestrationService(setup)
        ids = []
        for title in ("the claimed ticket", "the bystander"):
            ticket = make_ticket(setup, workspace_id=workspace.id, title=title)
            orch.update_ticket_manual(ticket, UpdateTicketRequest(description="seeded"))
            setup.refresh(ticket)
            ids.append((ticket.id, ticket.revision))
    (claimed, revision), (bystander, other_revision) = ids
    assert revision == other_revision, "the fixture's premise is that both rows agree"
    return claimed, bystander, revision


@pytest.fixture(name="fresh_ticket")
def fresh_ticket_fixture(isolated_db) -> str:
    """A ticket nobody has edited, so `revision` is still 0.

    `Ticket.revision` defaults to 0 (`models/domain/tables.py`), which makes the
    only falsy revision a real, reachable state rather than a hypothetical.
    """
    with Session(isolated_db) as setup:
        workspace = make_workspace(setup, slug="proj")
        ticket = make_ticket(setup, workspace_id=workspace.id, title="never edited")
        assert ticket.revision == 0, "this fixture exists for the falsy revision"
        return ticket.id


def _stored(engine, ticket_id: str) -> Ticket:
    with Session(engine) as check:
        ticket = check.get(Ticket, ticket_id)
        check.expunge(ticket)
        return ticket


# ---- the row the claim is allowed to touch ----------------------------


def test_the_claim_moves_only_the_named_ticket(isolated_db, two_tickets) -> None:
    """A claim that matched on revision alone would move every quiet ticket.

    It would also still return True whenever exactly one row happened to sit at
    that revision, which on a one-row fixture is always.
    """
    claimed, bystander, revision = two_tickets

    with Session(isolated_db) as session:
        assert claim_ticket_revision(session, ticket_id=claimed, expected_revision=revision)
        session.commit()

    assert _stored(isolated_db, claimed).revision == revision + 1
    assert _stored(isolated_db, bystander).revision == revision, (
        "the claim moved a ticket nobody named"
    )


def test_a_claim_on_one_ticket_does_not_satisfy_the_other(isolated_db, two_tickets) -> None:
    """And the bystander is still claimable afterwards, at its own revision."""
    claimed, bystander, revision = two_tickets

    with Session(isolated_db) as session:
        assert claim_ticket_revision(session, ticket_id=claimed, expected_revision=revision)
        assert claim_ticket_revision(session, ticket_id=bystander, expected_revision=revision)
        session.commit()

    assert _stored(isolated_db, claimed).revision == revision + 1
    assert _stored(isolated_db, bystander).revision == revision + 1


def test_the_claim_on_a_missing_ticket_is_simply_false(isolated_db, two_tickets) -> None:
    """No row, no claim — and no exception from the claim itself.

    The id a caller sends is not validated against anything before it reaches
    here, so this is the shape of every typo'd `ticket_id` on the path.
    """
    _, _, revision = two_tickets

    with Session(isolated_db) as session:
        assert (
            claim_ticket_revision(session, ticket_id="no-such-ticket", expected_revision=revision)
            is False
        )
        session.commit()


def test_the_same_token_cannot_be_spent_twice(isolated_db, two_tickets) -> None:
    """The second claim of one revision fails inside the same transaction.

    A guard that cached its verdict, or that claimed against the value it read
    rather than the value in the row, would let a replayed request through — and
    a replay is exactly what the pane's automatic resubmit is.
    """
    claimed, _, revision = two_tickets

    with Session(isolated_db) as session:
        assert claim_ticket_revision(session, ticket_id=claimed, expected_revision=revision)
        assert (
            claim_ticket_revision(session, ticket_id=claimed, expected_revision=revision) is False
        )
        session.commit()

    assert _stored(isolated_db, claimed).revision == revision + 1, "the replay burned a revision"


def test_a_refused_claim_leaves_the_session_able_to_claim(isolated_db, two_tickets) -> None:
    """A mismatch is a verdict, not a broken transaction.

    `guard_ticket_revision` re-reads after a failed claim, and a re-read on a
    session whose transaction the failure had poisoned would raise instead of
    answering — turning a 409 into a 500.
    """
    claimed, _, revision = two_tickets

    with Session(isolated_db) as session:
        assert (
            claim_ticket_revision(session, ticket_id=claimed, expected_revision=revision + 1)
            is False
        )
        assert claim_ticket_revision(session, ticket_id=claimed, expected_revision=revision)
        session.commit()

    assert _stored(isolated_db, claimed).revision == revision + 1


# ---- the values a caller can actually send ----------------------------


def test_revision_zero_is_a_revision_like_any_other(isolated_db, fresh_ticket) -> None:
    """0 is the revision of every ticket nobody has edited yet.

    The whole path is gated on `is not None`, and the one value that makes
    `if expected_revision:` differ from it is the one a brand-new ticket holds.
    A guard skipped on a falsy token would silently stop guarding the newest
    tickets in the database.
    """
    with Session(isolated_db) as session:
        assert claim_ticket_revision(session, ticket_id=fresh_ticket, expected_revision=0) is True
        session.commit()

    assert _stored(isolated_db, fresh_ticket).revision == 1


def test_a_stale_zero_is_refused_rather_than_waved_through(isolated_db, two_tickets) -> None:
    """The negative half: 0 against an edited ticket is a conflict, not a pass."""
    claimed, _, revision = two_tickets
    assert revision > 0, "the fixture must have moved past the falsy revision"

    with Session(isolated_db) as session:
        with pytest.raises(TicketRevisionConflict) as caught:
            guard_ticket_revision(session, ticket_id=claimed, expected_revision=0)
        session.rollback()

    assert caught.value.expected_revision == 0
    assert caught.value.current_revision == revision
    assert _stored(isolated_db, claimed).revision == revision


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("negative", -1),
        ("far below any live revision", -82_334),
        ("the largest integer SQLite holds", MAX_SQLITE_INT),
        ("an order above the measured maximum", 1_000_000),
    ],
)
def test_an_impossible_token_is_refused_without_raising(
    isolated_db, two_tickets, label: str, token: int
) -> None:
    """A token no row can hold is a plain refusal, not a crash.

    Nothing validates the range of `expected_revision` — it is an `int | None`
    on the edge model and an `int` through the MCP coercer — so these are values
    the guard will be handed. A guard that compared types, or that let the
    database raise an overflow, would answer 500 where it owes a 409.
    """
    claimed, _, revision = two_tickets

    with Session(isolated_db) as session:
        assert (
            claim_ticket_revision(session, ticket_id=claimed, expected_revision=token) is False
        ), label
        with pytest.raises(TicketRevisionConflict) as caught:
            guard_ticket_revision(session, ticket_id=claimed, expected_revision=token)
        session.rollback()

    assert caught.value.expected_revision == token
    assert caught.value.current_revision == revision
    assert _stored(isolated_db, claimed).revision == revision


# ---- which number the conflict reports -------------------------------


def test_the_conflict_reports_the_committed_revision_not_the_one_this_session_loaded(
    isolated_db, two_tickets
) -> None:
    """The re-read must go to the database, not to the identity map.

    This is the shape of the real request: the edge loads the `Ticket` first, so
    by the time the guard runs there is already an instance in the session whose
    `revision` is the stale number the caller sent. A re-read through
    `session.get` hands that instance back, and the conflict then reports
    `expected == current` — a message that tells the pane the ticket did not
    move while refusing the write because it did.

    It also must not repair the staleness: `revision` stays clean on the loaded
    instance, which is what the arithmetic in the contract's own tests rests on.
    """
    claimed, _, revision = two_tickets

    with Session(isolated_db) as reader:
        loaded = reader.get(Ticket, claimed)
        assert loaded.revision == revision

        with Session(isolated_db) as writer:
            other = writer.get(Ticket, claimed)
            OrchestrationService(writer).update_ticket_manual(
                other, UpdateTicketRequest(title="moved by somebody else")
            )
            writer.refresh(other)
            moved_to = other.revision
        assert moved_to > revision

        with pytest.raises(TicketRevisionConflict) as caught:
            guard_ticket_revision(reader, ticket_id=claimed, expected_revision=revision)
        reader.rollback()

        assert caught.value.current_revision == moved_to, (
            "the conflict reported the revision this session had loaded, not the stored one"
        )
        assert caught.value.current_revision != caught.value.expected_revision


def test_the_payload_carries_exactly_four_keys() -> None:
    """Two transports read this dict; a key added here is a key a caller sees.

    HTTP sends it as the 409 `detail` and MCP sends `str()` of it as the only
    text the protocol has. Pinning the key set is how the two stay one
    vocabulary rather than two that agree today.
    """
    exc = TicketRevisionConflict(expected_revision=826, current_revision=82_334)

    assert set(exc.payload) == {"error", "expected_revision", "current_revision", "message"}
    assert json.loads(str(exc)) == exc.payload


@pytest.mark.parametrize(
    ("expected", "current"),
    [(0, 1), (-1, 0), (826, 82_334), (MAX_SQLITE_INT, 1)],
)
def test_the_payload_keeps_its_numbers_numbers(expected: int, current: int) -> None:
    """Through `json.dumps` and back, both stay ints.

    C1's whole point is that `current_revision` reaches the pane as a number it
    can send back, so a payload that stringified it — or that rendered a large
    value in scientific notation — would satisfy every prose assertion and still
    be unusable.
    """
    exc = TicketRevisionConflict(expected_revision=expected, current_revision=current)
    parsed = json.loads(str(exc))

    assert parsed["expected_revision"] == expected
    assert parsed["current_revision"] == current
    assert type(parsed["expected_revision"]) is int
    assert type(parsed["current_revision"]) is int


# ---- what a conflict must not have done ------------------------------


def test_a_conflict_leaves_every_field_exactly_as_it_was(isolated_db, two_tickets) -> None:
    """The guard runs before the applier, so a refused edit is not a partial one.

    A guard placed after `update_ticket_manual` would still answer 409 and still
    pass a test that only checks the title — the fields the edit also carried
    would already be committed. So this asserts the whole record, revision
    included: a refusal costs nothing.
    """
    claimed, _, revision = two_tickets
    before = _stored(isolated_db, claimed)

    with Session(isolated_db) as session:
        ticket = session.get(Ticket, claimed)
        with pytest.raises(TicketRevisionConflict):
            guard_ticket_revision(session, ticket_id=claimed, expected_revision=revision + 5)
            OrchestrationService(session).update_ticket_manual(
                ticket,
                UpdateTicketRequest(
                    title="a title that must not land",
                    description="a description that must not land",
                    state=TicketState.BLOCKED,
                    branch="feat/must-not-land",
                ),
            )
        session.rollback()

    after = _stored(isolated_db, claimed)
    assert after.title == before.title
    assert after.description == before.description
    assert after.state == before.state
    assert after.branch == before.branch
    assert after.revision == revision, "a refused edit burned a revision"


# ---- contention wider than two ---------------------------------------


@pytest.mark.parametrize("round_number", [1, 2])
def test_four_threads_leave_exactly_one_winner_twice_running(
    isolated_db, round_number: int
) -> None:
    """Four edits, one revision: one lands, three are told, and the row is one of them.

    Two threads can be satisfied by an implementation that is accidentally
    serialised by the scheduler; four, all held on the first statement their own
    guard issues, cannot. The property is "exactly one", not "at least one
    fails" — a guard that let two through would still raise three times if the
    fourth also lost.

    Parametrised over two rounds because a concurrency test that passes once
    has not been shown to be deterministic, and this suite's whole value is
    that it fails reliably when the guard is wrong.
    """
    with Session(isolated_db) as setup:
        workspace = make_workspace(setup, slug=f"proj-{round_number}")
        ticket = make_ticket(setup, workspace_id=workspace.id, title="contended")
        OrchestrationService(setup).update_ticket_manual(
            ticket, UpdateTicketRequest(description="seeded")
        )
        setup.refresh(ticket)
        ticket_id, revision = ticket.id, ticket.revision

    titles = [f"round {round_number} writer {n}" for n in range(4)]
    outcomes: list[tuple[str, BaseException | None]] = []
    lock = threading.Lock()
    arming = threading.local()
    at_the_claim = threading.Barrier(len(titles))

    def hold_until_everybody_is_here(conn, cursor, statement, parameters, ctx, many):  # noqa: ARG001
        if not getattr(arming, "armed", False):
            return
        arming.armed = False
        at_the_claim.wait(timeout=30)

    def edit(title: str) -> None:
        failure: BaseException | None = None
        try:
            with Session(isolated_db) as session:
                row = session.get(Ticket, ticket_id)
                arming.armed = True
                guard_ticket_revision(session, ticket_id=ticket_id, expected_revision=revision)
                OrchestrationService(session).update_ticket_manual(
                    row, UpdateTicketRequest(title=title)
                )
        except TicketRevisionConflict as exc:
            failure = exc
        finally:
            arming.armed = False
        with lock:
            outcomes.append((title, failure))

    threads = [threading.Thread(target=edit, args=(t,)) for t in titles]
    event.listen(isolated_db, "before_cursor_execute", hold_until_everybody_is_here)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=90)
    finally:
        event.remove(isolated_db, "before_cursor_execute", hold_until_everybody_is_here)

    assert not any(thread.is_alive() for thread in threads), "an edit deadlocked"
    assert len(outcomes) == len(titles), "an edit raised something other than a conflict"

    winners = [title for title, failure in outcomes if failure is None]
    losers = [failure for _, failure in outcomes if failure is not None]
    assert len(winners) == 1, f"more than one edit was allowed through: {outcomes}"
    assert len(losers) == len(titles) - 1
    assert all(exc.expected_revision == revision for exc in losers)
    assert all(exc.current_revision > revision for exc in losers)

    stored = _stored(isolated_db, ticket_id)
    assert stored.title == winners[0]
    assert stored.title in titles
