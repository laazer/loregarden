"""`service_write` holds for a second service, not just the one that needed it first.

Ticket 637 moved this helper out of `reference_cache` because the distinction it
draws — has the caller opened a transaction, or only read — is not the cache's
question. It is faced by any service handed someone else's Session that wants its
own write to unwind independently, and 174's DevDocs fetcher chain is the next one
in line. Left where it was, that second caller would either import a private helper
out of a sibling service or copy the driver reach, and then there would be two
spellings of the pysqlite assumption to keep true.

So these tests stand in for that second caller. They use no `reference_cache` code
and reach no driver connection of their own: a service function written the way a
new one would be, and the helper as its only transaction machinery.
"""

import sqlite3

import pytest
from loregarden.db.session import service_write
from loregarden.models.domain.tables import ReferencePage
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

OURS = "https://second-service.example/row"
CALLERS = "https://second-service.example/callers-own-work"

#: Short enough that a held lock is a fast failure rather than a hang. The engines
#: under test use the 30s busy_timeout `db/session.py` sets, which would hang.
PROBE_BUSY_TIMEOUT_SECONDS = 0.25


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'second-service.db'}")
    SQLModel.metadata.create_all(engine, tables=[ReferencePage.__table__])
    return engine


def _page(url: str) -> ReferencePage:
    return ReferencePage(url=url, title="t", content_markdown="body", content_chars=4)


def a_second_service_writes(session: Session, url: str) -> None:
    """A new service, written the way one would be written today.

    It knows nothing about SQLite, transactions or connections; it asks for a
    Session to write through and resolves its row in that one.
    """
    with service_write(session) as writer:
        existing = writer.exec(select(ReferencePage).where(ReferencePage.url == url)).first()
        if existing is None:
            writer.add(_page(url))
        else:
            existing.hit_count += 1
        writer.flush()


def _durable(engine, url) -> bool:
    with Session(engine) as other:
        return other.exec(select(ReferencePage).where(ReferencePage.url == url)).first() is not None


def _second_connection_can_write(engine, url) -> bool:
    """The lock question, asked from a connection this service does not own."""
    con = sqlite3.connect(engine.url.database, timeout=PROBE_BUSY_TIMEOUT_SECONDS)
    try:
        con.execute(
            "insert into reference_pages (id,url,title,content_markdown,content_chars,"
            "etag,last_modified,kind,hit_count,fetched_at,created_at) values"
            " (?,?,'','',0,'','','doc',0,'2026-01-01','2026-01-01')",
            (url, url),
        )
        con.commit()
        return True
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc):
            raise
        return False
    finally:
        con.close()


def test_a_read_only_caller_gets_a_durable_write_and_keeps_no_lock(db):
    """The `get_session()` shape, for a service that is not the reference cache."""
    with Session(db) as caller:
        a_second_service_writes(caller, OURS)
        assert _second_connection_can_write(db, "https://probe.example/during"), (
            "the helper left a write lock on a read-only caller's connection"
        )
    assert _durable(db, OURS), "the write did not survive the caller's close"


def test_the_caller_owns_both_ends_when_it_was_already_writing(db):
    """The other shape: the caller's rollback must still discard our write with its own."""
    with Session(db) as caller:
        caller.add(_page(CALLERS))
        caller.flush()  # the caller's own transaction is now open at the driver
        a_second_service_writes(caller, OURS)
        assert caller.exec(select(ReferencePage).where(ReferencePage.url == OURS)).first()
        caller.rollback()

    assert not _durable(db, OURS), "the helper committed a transaction the caller owned"
    assert not _durable(db, CALLERS), "the caller's own work was committed for it"


def test_a_raising_body_unwinds_only_our_write(db):
    """A failure inside the block must not take the caller's pending work with it."""
    with Session(db) as caller:
        caller.add(_page(CALLERS))
        caller.flush()

        with pytest.raises(RuntimeError), service_write(caller) as writer:
            writer.add(_page(OURS))
            writer.flush()
            raise RuntimeError("the service failed after writing")

        assert caller.exec(select(ReferencePage).where(ReferencePage.url == CALLERS)).first(), (
            "the caller's pending work was rolled back by our failure"
        )
        caller.commit()

    assert _durable(db, CALLERS), "the caller could no longer commit its own work"
    assert not _durable(db, OURS), "a write that raised was kept"


def test_the_second_service_reaches_no_driver_connection(db):
    """AC2 in the form that can regress: the stand-in service must stay ignorant.

    A second caller that re-derives the driver reach — `connection.connection.
    driver_connection`, or its own `BEGIN` — is exactly what moving the helper
    was meant to prevent, and it would pass every other test in this file.
    """
    import inspect

    source = inspect.getsource(a_second_service_writes)
    for reach in ("driver_connection", "exec_driver_sql", "in_transaction", "sqlite3"):
        assert reach not in source, f"the stand-in service reached for {reach} itself"


def test_a_write_that_raises_a_database_error_does_not_end_the_callers_transaction(db):
    """The `OperationalError` shape, since a helper that swallowed it would look fine."""
    with Session(db) as caller:
        caller.add(_page(CALLERS))
        caller.flush()
        outer = caller.get_transaction()

        with pytest.raises(OperationalError), service_write(caller) as writer:
            writer.connection().exec_driver_sql("insert into no_such_table values (1)")

        assert caller.get_transaction() is outer, "the caller's transaction was ended"
