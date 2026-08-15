"""Two claimants, one free slot, one winner.

Every claim in `parallel_queue` used to be select-then-mutate: read the rows
where `is_available`, pick one, set it False, commit. Nothing stood between the
read and the write, so two claimants arriving together both saw the same row as
free and both wrote it — the second write simply won. One slot, two occupants,
and a pool that had quietly admitted past `max_concurrent`.

The admission site even asserted the opposite in a comment: "Claimed before the
caller starts anything, so two requests arriving together cannot both read the
slot as free." That was the intent; it was not what the code did.

The claim is now a conditional UPDATE, so the database picks the winner in one
statement and the loser sees `rowcount == 0`.
"""

import threading

import pytest
from loregarden.models.domain import AgentSlot, Ticket, Workspace
from loregarden.services.parallel_queue import ParallelQueueService, claim_free_slot
from loregarden.services.queue_admission import QueueAdmissionService
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _one_free_slot(session: Session) -> None:
    """Exactly one slot, available. The contended resource, made scarce."""
    for slot in session.exec(select(AgentSlot)).all():
        session.delete(slot)
    session.add(AgentSlot(slot_number=1, is_available=True))
    session.commit()


def _ticket(session: Session, workspace, code: str) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace.id, title=code)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


# ---- the race, made deterministic --------------------------------------


def test_two_claimants_on_one_slot_produce_one_winner(isolated_db, session):
    """Both read the slot as free before either writes — the exact interleaving.

    Sequenced by hand rather than by threads: the losing claimant must have
    already decided the slot is free, which is what select-then-mutate could
    not survive and what a conditional UPDATE does.
    """
    _one_free_slot(session)

    with Session(isolated_db) as first, Session(isolated_db) as second:
        # Both observe a free slot. This is the state the old code acted on.
        assert first.exec(select(AgentSlot).where(AgentSlot.is_available == True)).first()  # noqa: E712
        assert second.exec(select(AgentSlot).where(AgentSlot.is_available == True)).first()  # noqa: E712

        won = claim_free_slot(first)
        lost = claim_free_slot(second)

    assert won is not None
    assert lost is None, "both claimants took the same slot"

    session.expire_all()
    slots = session.exec(select(AgentSlot)).all()
    assert [s.is_available for s in slots] == [False]


def test_concurrent_claimants_never_exceed_the_pool(isolated_db, session):
    """Eight threads, three slots. Real concurrency, not an interleaving."""
    for slot in session.exec(select(AgentSlot)).all():
        session.delete(slot)
    for number in (1, 2, 3):
        session.add(AgentSlot(slot_number=number, is_available=True))
    session.commit()

    won: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def claim() -> None:
        start.wait()
        with Session(isolated_db) as own:
            slot = claim_free_slot(own)
        if slot is not None:
            with lock:
                won.append(slot.slot_number)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(won) == 3, f"pool of 3 handed out {len(won)} slots"
    assert sorted(won) == [1, 2, 3], "the same slot went to two claimants"


# ---- the behaviour the claim has to keep -------------------------------


def test_a_preferred_slot_is_honoured_when_free(session, workspace):
    _one_free_slot(session)
    session.add(AgentSlot(slot_number=2, is_available=True))
    session.commit()

    assert claim_free_slot(session, preferred=2).slot_number == 2


def test_a_preferred_slot_that_filled_falls_back(session, workspace):
    """A preference, not a demand — the ask was to run the ticket."""
    _one_free_slot(session)
    session.add(AgentSlot(slot_number=2, is_available=False))
    session.commit()

    assert claim_free_slot(session, preferred=2).slot_number == 1


def test_no_preference_takes_the_lowest_free_slot(session):
    for slot in session.exec(select(AgentSlot)).all():
        session.delete(slot)
    for number in (1, 2, 3):
        session.add(AgentSlot(slot_number=number, is_available=(number != 1)))
    session.commit()

    assert claim_free_slot(session).slot_number == 2


def test_a_full_pool_yields_nothing(session):
    for slot in session.exec(select(AgentSlot)).all():
        session.delete(slot)
    session.add(AgentSlot(slot_number=1, is_available=False))
    session.commit()

    assert claim_free_slot(session) is None


# ---- the callers still behave ------------------------------------------


def test_admission_claims_atomically_and_still_reports_its_lane(session, workspace):
    _one_free_slot(session)
    admission = QueueAdmissionService(session, max_concurrent=1)

    first = admission.reserve_orchestration(_ticket(session, workspace, "T-1"))
    second = admission.reserve_orchestration(_ticket(session, workspace, "T-2"))

    assert first.admitted is True
    assert first.slot_number == 1
    assert second.admitted is False


def test_promotion_does_not_strand_a_slot_when_there_is_nothing_to_run(session):
    """The claim commits, so taking one before finding work would leak a lane."""
    _one_free_slot(session)

    assert ParallelQueueService(session).promote_from_queue_sync() is None

    session.expire_all()
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is True


def test_two_claimants_with_spare_capacity_both_win(isolated_db, session):
    """Three slots, two claimants — nobody should be refused.

    The concurrency test above asserts a pool of three never hands out more
    than three, which a `claim_free_slot` that always failed would also satisfy.
    This asserts the other direction: with capacity to spare, a claimant that
    loses a race for one slot must go on to take another. CI caught exactly this
    — two tickets orchestrating concurrently, one refused — where a single-run
    local suite did not.
    """
    for slot in session.exec(select(AgentSlot)).all():
        session.delete(slot)
    for number in (1, 2, 3):
        session.add(AgentSlot(slot_number=number, is_available=True))
    session.commit()

    won: list[int | None] = []
    lock = threading.Lock()
    start = threading.Barrier(2)

    def claim() -> None:
        start.wait()
        with Session(isolated_db) as own:
            slot = claim_free_slot(own)
        with lock:
            won.append(slot.slot_number if slot else None)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert None not in won, "a claimant was refused while the pool had spare slots"
    assert len(set(won)) == 2, f"both claimants took the same slot: {won}"


# ---- an empty pool, initialised by two claimants at once ---------------


def _fresh_engine(tmp_path, name: str):
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_two_claimants_racing_an_empty_pool_are_both_admitted(tmp_path):
    """The case every other test here skipped by pre-creating slots.

    Production starts with no slots: `initialize_slots` builds them on first
    use. Two callers racing that both saw an empty pool and both inserted a
    full set, so the pool held two rows numbering themselves 1 — and a claim
    keyed on `slot_number` then matched both, saw rowcount 2, rejected every
    candidate and reported a full pool. Both claimants were refused with four
    slots free. 31 of 40 trials before the fix.
    """
    import threading

    from loregarden.db.migrations import apply_migrations
    from loregarden.services.queue_admission import QueueAdmissionService

    engine = _fresh_engine(tmp_path, "race.db")
    apply_migrations(engine)
    with Session(engine) as setup:
        ws = Workspace(slug="w", name="w", repo_path=".")
        setup.add(ws)
        setup.commit()
        setup.refresh(ws)
        ids = []
        for n in (1, 2):
            ticket = Ticket(external_id=f"T-{n}", workspace_id=ws.id, title=f"T-{n}")
            setup.add(ticket)
            setup.commit()
            setup.refresh(ticket)
            ids.append(ticket.id)

    admitted: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(2)

    def reserve(ticket_id: str) -> None:
        start.wait()
        with Session(engine) as own:
            ticket = own.get(Ticket, ticket_id)
            result = QueueAdmissionService(own, max_concurrent=3).reserve_orchestration(ticket)
            with lock:
                admitted.append(result.admitted)

    threads = [threading.Thread(target=reserve, args=(i,)) for i in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(admitted), f"a claimant was refused against a pool of three: {admitted}"

    with Session(engine) as check:
        slots = check.exec(select(AgentSlot)).all()
    assert len(slots) == 3, f"the pool built itself twice: {[s.slot_number for s in slots]}"


def test_the_pool_cannot_hold_two_slots_with_the_same_number(tmp_path):
    """Enforced by the schema as of 0083, not by whoever inserts next."""
    import pytest
    from loregarden.db.migrations import apply_migrations
    from sqlalchemy.exc import IntegrityError

    engine = _fresh_engine(tmp_path, "unique.db")
    apply_migrations(engine)
    with Session(engine) as session:
        session.add(AgentSlot(slot_number=1))
        session.commit()
        session.add(AgentSlot(slot_number=1))
        with pytest.raises(IntegrityError):
            session.commit()
