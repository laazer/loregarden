"""How many database connections the application can actually hold at once.

The pool was SQLAlchemy's default — five connections with ten overflow — which
nothing in the application ever chose. Fifteen has to cover every in-flight
request plus every ``/ws/queue`` snapshot, the reconciliation timer and run
streaming, so one slow handler holding a connection was enough to starve the
rest: every endpoint began failing with ``QueuePool limit of size 5 overflow 10
reached``, ``/health`` included.
"""

from __future__ import annotations

import threading

import anyio
import anyio.to_thread
from loregarden.config import settings
from loregarden.db.session import create_app_engine
from sqlmodel import Session


def _request_thread_limit() -> int:
    """How many sync request handlers AnyIO will run at once.

    FastAPI runs a sync endpoint in this threadpool and gives one request one
    thread, so this is the ceiling on handlers that can want a connection
    simultaneously. Read live rather than hardcoded: raising it without raising
    the pool is the mistake this test exists to catch.
    """

    async def read() -> int:
        return int(anyio.to_thread.current_default_thread_limiter().total_tokens)

    return anyio.run(read)


def test_the_pool_covers_every_request_thread():
    capacity = settings.db_pool_size + settings.db_max_overflow

    assert capacity >= _request_thread_limit(), (
        f"the pool holds {capacity} connections but {_request_thread_limit()} sync requests "
        "can run at once; the surplus requests will fail on a pool timeout rather than "
        "queue for a thread"
    )


def test_the_pool_leaves_room_for_the_background_readers():
    # Snapshots, the reconciliation timer and run streaming all open sessions
    # outside the request threadpool, so covering requests exactly is not enough.
    capacity = settings.db_pool_size + settings.db_max_overflow

    assert capacity > _request_thread_limit()


def test_the_engine_is_built_with_the_configured_pool(tmp_path):
    engine = create_app_engine(f"sqlite:///{tmp_path / 'pool.db'}")

    # Configuration that the engine silently ignores is worse than none: the
    # numbers would read as chosen while the defaults stayed in force.
    assert engine.pool.size() == settings.db_pool_size
    # SQLAlchemy exposes no public reader for the overflow ceiling.
    assert engine.pool._max_overflow == settings.db_max_overflow


def test_more_connections_than_the_old_ceiling_can_be_held_at_once(tmp_path):
    """The regression in the shape it actually took: connections held together.

    Fifteen was the old ceiling, and the sixteenth caller waited thirty seconds
    and then got an error rather than a connection.
    """
    engine = create_app_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    held: list[Session] = []
    failure: list[BaseException] = []

    def hold_one() -> None:
        try:
            session = Session(engine)
            session.connection()
            held.append(session)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            failure.append(exc)

    threads = [threading.Thread(target=hold_one) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    try:
        assert not failure, f"holding 16 connections at once failed: {failure[0]!r}"
        assert len(held) == 16
    finally:
        for session in held:
            session.close()
