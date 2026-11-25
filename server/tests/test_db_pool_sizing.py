"""The database pool must never make a request wait for a connection.

It was SQLAlchemy's default, five connections with ten overflow, and one slow
handler starved every endpoint. Then it was twenty with forty, sized against the
forty request threads. That was the wrong quantity: a request holds its
connection after its endpoint has returned, while it waits for a thread to
validate its response. Connections held are bounded by requests in flight, so
any ceiling can deadlock against the threads (``Settings.db_pool_size``).
"""

from __future__ import annotations

import asyncio
import threading
from collections import Counter
from collections.abc import Iterator

import httpx
import pytest
from fastapi import Depends, FastAPI
from loregarden.config import settings
from loregarden.db import session as db_session
from loregarden.db.session import create_app_engine, get_session
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

#: Well past the hundred or so (40 threads + 60 connections) where 20 + 40 locks up.
CONCURRENT_REQUESTS = 300
#: Only reached on failure: how long a deadlocked request takes to become a 500.
POOL_TIMEOUT_SECONDS = 1.0


class _Reading(BaseModel):
    value: int


def _read(session: Session = Depends(get_session)) -> dict[str, int]:
    return {"value": session.execute(text("select 1")).scalar_one()}


@pytest.fixture
def app_engine(tmp_path, monkeypatch) -> Iterator[Engine]:
    """The app's own engine, on a scratch file, as ``get_session`` will use it."""
    engine = create_app_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    # SQLAlchemy exposes no public setter for a built pool's checkout timeout.
    engine.pool._timeout = POOL_TIMEOUT_SECONDS
    monkeypatch.setattr(db_session, "engine", engine)
    yield engine
    engine.dispose()


async def test_a_burst_of_requests_does_not_deadlock_the_pool_against_the_threads(app_engine):
    """FastAPI gives an endpoint with a response model two turns on the threadpool.

    Every request arrives before any finishes, so all of them queue for the first
    turn ahead of anyone's second. No timing is involved: with a finite pool, the
    finished requests hold every connection and wait behind threads that are
    waiting for one.
    """
    app = FastAPI()
    app.get("/reading", response_model=_Reading)(_read)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *(client.get("/reading") for _ in range(CONCURRENT_REQUESTS))
        )

    assert Counter(r.status_code for r in responses) == {200: CONCURRENT_REQUESTS}


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
