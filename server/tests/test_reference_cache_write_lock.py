"""The reference cache never holds a database-wide write lock across a fetch.

Written from the spec for lg-improved-memory-638. That ticket is the *security*
half of the transaction defect 616 opened and 636 records the correctness half
of: `_cache_write` emits a `BEGIN` on the caller's connection and deliberately
never ends it, so the first write site to run escalates the caller's connection
to a SQLite RESERVED lock — and SQLite's write lock is per *database file*, not
per row. Any later network read on that same Session then happens with the whole
control plane unwritable, for exactly as long as the remote server takes to
answer. The server does not have to be hostile; slow is enough.

## What the tests measure, and why not a stopwatch

The ticket's own reproduction widens the window with a transport that sleeps for
three seconds and probes from outside. The sleep is not the observation — it is
scaffolding to make a wall-clock race observable. These tests probe from *inside*
the `MockTransport` handler instead, which is the same instant of the same call
stack with no timing to lose: the handler runs during `_fetch_with_redirects` by
construction, so a probe there asks "is the database writable *right now*, with
the fetch in flight" and answers deterministically. SQLite's locks are held by a
connection, not by a thread, so a second connection driven from the same thread
meets exactly the lock a second process would.

The probe is a *different* engine on the same file with a 250 ms busy timeout
(`_probe_engine`), because the test engine's own pool is built with a 30 s one —
a probe through it would hang for half a minute rather than report.

## The instruments

- **`_write_from_another_connection`** — the AC1 instrument. Returns `None` when
  a second connection committed a row, or the `OperationalError` when SQLite
  refused. Anything that is not a lock refusal is re-raised, so a schema or path
  mistake in the probe surfaces as itself instead of masquerading as the defect.
- **The caller's driver `in_transaction`** — the AC2 instrument, spelled out
  locally rather than imported from `reference_cache._driver_transaction_open`.
  Ticket 637 moves that helper into `db/session.py`; a test that imports it by
  its current private name would fail on a refactor that changes nothing about
  the behaviour pinned here.
- **A row the caller owns and has not committed** — the AC3 instrument, carried
  over from the 616 section of `test_fetch_reference_tool.py`. It separates all
  three outcomes with one sentinel: a commit makes it durable, a rollback
  destroys it, and a correctly nested write leaves it exactly as it was.

## Caller shapes, and why AC2 is scoped to one of them

`db/session.py`'s `get_session()` hands out a bare `Session(engine)` that has
only read. That caller holds no transaction of its own, so *any* lock alive when
`fetch_reference` returns is the cache's, and AC2 applies in full.

A caller that has flushed work of its own is holding a write lock because it
chose to. The cache may not end that transaction — that is 616 AC3, and the
whole point of the third acceptance criterion here is that a fix for the lock
must not be "commit the caller". So the caller-with-pending-work tests assert
the 616 invariants unchanged rather than demanding an unlocked database.

## What a partial fix loses, and how each test refuses it

- *Rolling back* the cache's own transaction releases the lock and discards the
  cache write. Every test below therefore asserts the fetched row is still
  there — through the caller before its commit, and durably after it.
- *Fixing only `_store`* leaves `_serve_hit` and `_revalidated` holding a lock,
  and every one of them is a real production path (a hit is the common case).
  The lock tests are parametrized over all three write sites for that reason.
- *Fixing only `fetch_reference`* leaves the DevDocs shape — a catalog fetch
  followed by an index fetch on one Session, which is the two-call shape the
  ticket names — still locking. Both entry points are parametrized.
"""

import sqlite3
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from loregarden.config import settings
from loregarden.models.domain.enums import (
    ReferenceCacheOutcome,
    ReferencePageKind,
    utcnow,
)
from loregarden.models.domain.tables import ReferencePage
from loregarden.services import reference_cache
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, create_engine, select

#: The URL a probe writes. Distinct from every fetched URL, so the probe can
#: never collide with the unique index the cache itself writes through.
PROBE_URL = "https://probe.example/second-connection-write"

#: The caller's own in-flight, uncommitted work — the 616 sentinel.
CALLER_URL = "https://caller.example/unrelated-pending-work"

#: How long the probe waits before reporting a refusal. Long enough that a busy
#: machine does not report a lock that is not there, short enough that a genuine
#: hold is a fast failure rather than a hang.
PROBE_BUSY_TIMEOUT_SECONDS = 0.25

HTML_PAGE = (
    b"<html><head><title>Array.prototype.map</title></head><body><main>"
    b"<h1>Array.prototype.map</h1>"
    b"<p>The map() method creates a new array populated with the results of "
    b"calling a provided function on every element in the calling array.</p>"
    b"<p>It does not mutate the array on which it is called, and it returns a "
    b"new array of the same length.</p>"
    b"</main></body></html>"
)
HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}
JSON_BODY = b'{"entries": [{"name": "Array.prototype.map", "path": "array/map"}]}'
JSON_HEADERS = {"content-type": "application/json"}

WRITE_SITES = ["miss", "hit", "revalidated"]
ENTRY_POINTS = ["fetch_reference", "fetch_cached_text"]


# --------------------------------------------------------------------------
# Hosts, without DNS in the way
# --------------------------------------------------------------------------

#: Public IP literals, so the SSRF guard judges them on the literal tier and no
#: resolver patch is needed at all. Every URL below is on one of these hosts.
FIRST_HOST = "https://93.184.216.34"
SECOND_HOST = "https://93.184.216.35"


def _url(host: str, path: str) -> str:
    return f"{host}/{path}"


# --------------------------------------------------------------------------
# The AC1 instrument: can a *different* connection write right now?
# --------------------------------------------------------------------------


def _probe_engine(isolated_db):
    """An engine on the same file whose connections give up quickly.

    `isolated_db` builds its pool with `timeout: 30`, which is right for the
    code under test and useless for a probe: a held lock would stall the test
    for thirty seconds instead of reporting. This engine is the same database
    and the same journal mode (WAL is a property of the file), differing only
    in how long it is willing to wait.
    """
    return create_engine(
        f"sqlite:///{isolated_db.url.database}",
        connect_args={
            "check_same_thread": False,
            "timeout": PROBE_BUSY_TIMEOUT_SECONDS,
        },
    )


def _write_from_another_connection(probe, suffix: str) -> OperationalError | None:
    """Try to commit a row on a second connection. None means it succeeded.

    A refusal comes back rather than raising so the caller can say *which*
    moment was locked. An `OperationalError` that is not a lock refusal is
    re-raised: a mistyped path or a missing table would otherwise be reported
    as the very defect this file exists to detect.
    """
    try:
        with Session(probe) as other:
            other.add(ReferencePage(url=f"{PROBE_URL}/{suffix}", title="probe"))
            other.commit()
    except OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise
        return exc
    return None


def _driver_transaction_open(session: Session) -> bool:
    """Whether pysqlite — not SQLAlchemy — has a transaction open on `session`.

    Spelled out here rather than imported from `reference_cache`: the module's
    own probe is private and 637 moves it to `db/session.py`. `session.connection()`
    is safe to call as an observation because pysqlite emits no `BEGIN` for it.
    """
    driver: sqlite3.Connection = session.connection().connection.driver_connection
    return driver.in_transaction


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


def _probing_transport(probe, response, *, suffix: str):
    """A transport that asks, mid-fetch, whether the database is writable.

    Returns `(observations, transport)`. `observations` gets one entry per HTTP
    request: `None` for "a second connection committed", or the refusal. An
    empty list means the network was never reached, which is itself a test
    failure — the assertion this file makes is about a moment that has to have
    happened.
    """
    observations: list[OperationalError | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        observations.append(_write_from_another_connection(probe, f"{suffix}-{len(observations)}"))
        return response(request)

    return observations, httpx.MockTransport(handle)


def _ok_html(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers=HTML_HEADERS, content=HTML_PAGE)


def _ok_json(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers=JSON_HEADERS, content=JSON_BODY)


def _not_modified(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(304, headers={"ETag": '"v1"'})


def _refuse(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("no HTTP request should have been made")


# --------------------------------------------------------------------------
# Entry points, parametrized so both are held to the same contract
# --------------------------------------------------------------------------


def _call(entry_point: str, session, url, *, transport):
    if entry_point == "fetch_reference":
        return reference_cache.fetch_reference(session, url, transport=transport)
    return reference_cache.fetch_cached_text(
        session,
        url,
        kind=ReferencePageKind.CATALOG,
        accept_types=("application/json",),
        transport=transport,
    )


def _body_for(entry_point: str):
    return _ok_html if entry_point == "fetch_reference" else _ok_json


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def _stale_age() -> int:
    return settings.reference_cache_ttl_seconds + 3600


def _seed_committed(engine, url, *, age_seconds=0, **fields):
    """Put a committed row in the database on a Session of its own.

    Never on the caller's Session: committing there emits the very `BEGIN`
    these tests exist to prove the cache does not leave behind, and a caller
    that has already written is a different caller shape (see the module
    docstring).
    """
    markdown = fields.pop("markdown", "# cached\n\nstored body text.")
    with Session(engine) as other:
        other.add(
            ReferencePage(
                url=url,
                title="Cached title",
                content_markdown=markdown,
                content_chars=len(markdown),
                fetched_at=utcnow() - timedelta(seconds=age_seconds),
                **fields,
            )
        )
        other.commit()


def _prime_write_site(site: str, isolated_db, entry_point: str):
    """`(url, transport)` for a first call that lands on one write site.

    Each of the three is a real production path that reaches `_cache_write`,
    and each takes the lock independently — a fix applied to one of them is
    exactly the partial fix these parametrizations refuse.
    """
    url = _url(FIRST_HOST, "first")
    if site == "hit":
        _seed_committed(isolated_db, url)
        return url, httpx.MockTransport(_refuse)
    if site == "revalidated":
        _seed_committed(isolated_db, url, age_seconds=_stale_age(), etag='"v1"')
        return url, httpx.MockTransport(_not_modified)
    return url, httpx.MockTransport(_body_for(entry_point))


def _pending_caller_work(session):
    """Work the caller has in flight in its own Session and has not committed."""
    row = ReferencePage(
        url=CALLER_URL, title="caller work", content_markdown="the caller's own row"
    )
    session.add(row)
    session.flush()
    return row


def _durable_urls(engine) -> set[str]:
    """The URLs a *different* connection can see — that is, what is committed."""
    with Session(engine) as other:
        return set(other.exec(select(ReferencePage.url)).all())


def _row(session, url):
    return session.exec(select(ReferencePage).where(ReferencePage.url == url)).first()


# --------------------------------------------------------------------------
# AC1 — no write lock is held across `_fetch_with_redirects`
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
@pytest.mark.parametrize("site", WRITE_SITES)
def test_a_second_fetch_on_one_session_does_not_lock_the_database(site, entry_point, isolated_db):
    """AC1, in the shape the ticket reproduces and production actually has.

    A DevDocs catalog fetch followed by an index fetch is two calls on one
    Session, and so is any tool that reads more than one page. The first call
    reaches a write site; the second then does its network read. Under the
    defect, the `BEGIN` the first call emitted is still open and unwritten-back,
    so the whole control-plane database is unwritable for the duration of the
    second call's read — a window a remote server chooses the length of.

    The probe runs inside the transport handler, which is inside
    `_fetch_with_redirects` by construction, so the moment measured is the
    moment the criterion names.
    """
    probe = _probe_engine(isolated_db)
    first_url, first_transport = _prime_write_site(site, isolated_db, entry_point)
    second_url = _url(SECOND_HOST, "second")
    observations, second_transport = _probing_transport(
        probe, _body_for(entry_point), suffix="second"
    )

    with Session(isolated_db) as caller:
        assert not caller.in_transaction(), "the caller must start the way get_session() hands it"
        first = _call(entry_point, caller, first_url, transport=first_transport)
        assert first.error == "", first
        second = _call(entry_point, caller, second_url, transport=second_transport)
        assert second.error == "", second

        assert observations, "the second fetch never reached the network — nothing was measured"
        assert observations == [None] * len(observations), (
            f"a write lock was held across the fetch after a {site} write: {observations}"
        )

        # A fix that releases the lock by discarding the cache's work is not a
        # fix; both rows have to be here, and still be here after the commit.
        assert _row(caller, first_url) is not None, "the first call's cache write was lost"
        assert _row(caller, second_url) is not None, "the second call's cache write was lost"
        caller.commit()

    durable = _durable_urls(isolated_db)
    assert first_url in durable and second_url in durable, (
        "the caller committed and the cache rows are not durable"
    )


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_the_very_first_fetch_holds_no_lock_across_its_own_read(entry_point, isolated_db):
    """The same criterion on a Session that has done nothing at all.

    This passes today — a bare SELECT emits no `BEGIN` on pysqlite — and that
    is the point: it pins the invariant against a fix that trades the trailing
    lock for a leading one by opening the transaction before the fetch instead
    of after it. Without it, "hold the lock for the whole call" reads as an
    improvement over "hold it forever".
    """
    probe = _probe_engine(isolated_db)
    url = _url(FIRST_HOST, "only")
    observations, transport = _probing_transport(probe, _body_for(entry_point), suffix="only")

    with Session(isolated_db) as caller:
        payload = _call(entry_point, caller, url, transport=transport)

    assert payload.error == "", payload
    assert observations == [None], f"the first fetch's own read was locked: {observations}"


def test_a_redirect_hop_does_not_lock_the_database_either(isolated_db):
    """Every hop of the redirect loop is a network read, not just the first.

    `_fetch_with_redirects` runs up to `_MAX_REDIRECTS` requests, and the
    criterion says "across `_fetch_with_redirects`" — the whole loop. A chain
    is the shape where a remote server gets to multiply the window it controls.
    """
    probe = _probe_engine(isolated_db)
    first_url, first_transport = _prime_write_site("miss", isolated_db, "fetch_reference")
    hop_target = _url(SECOND_HOST, "moved-here")

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/redirected"):
            return httpx.Response(302, headers={"Location": hop_target})
        return _ok_html(request)

    observations, transport = _probing_transport(probe, route, suffix="hop")

    with Session(isolated_db) as caller:
        primed = _call("fetch_reference", caller, first_url, transport=first_transport)
        assert primed.error == ""
        payload = reference_cache.fetch_reference(
            caller, _url(SECOND_HOST, "redirected"), transport=transport
        )

    assert payload.error == "", payload
    assert len(observations) == 2, f"the redirect loop did not run both hops: {observations}"
    assert observations == [None, None], f"a hop was fetched under a write lock: {observations}"


# --------------------------------------------------------------------------
# AC2 — the caller's Session is not left holding a transaction
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
@pytest.mark.parametrize("site", WRITE_SITES)
def test_a_read_only_caller_is_not_left_in_a_transaction(site, entry_point, isolated_db):
    """AC2, in the caller shape `get_session()` hands every request.

    The caller opened no transaction, so anything open when the call returns
    was opened by the cache and belongs to nobody who will ever end it. The
    ticket measured that hold as indefinite: a Session that merely returned
    from one `fetch_reference` blocked every writer in the process for as long
    as it stayed open.

    Two instruments, because either alone is passable by the wrong fix. The
    driver probe says no transaction is open; the second connection says the
    database is actually writable. A fix that ends the transaction but leaves
    the file locked fails the second; a fix that quietly swaps pysqlite's
    isolation mode so `in_transaction` reads False while the lock stands fails
    it too.
    """
    probe = _probe_engine(isolated_db)
    url, transport = _prime_write_site(site, isolated_db, entry_point)

    with Session(isolated_db) as caller:
        payload = _call(entry_point, caller, url, transport=transport)
        assert payload.error == "", payload

        assert not _driver_transaction_open(caller), (
            f"a {site} write left an open transaction on the caller's connection"
        )
        assert _write_from_another_connection(probe, f"after-{site}") is None, (
            f"a {site} write left the database locked after the call returned"
        )

        assert _row(caller, url) is not None, "the lock was released by discarding the write"
        caller.commit()

    assert url in _durable_urls(isolated_db), "the caller committed and the cache row is not there"


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_a_blocked_url_leaves_no_transaction_behind(entry_point, isolated_db):
    """The refusal path returns before any write site, and must be just as clean.

    A payload is still a return, and the SSRF guard is the most-taken branch in
    a hostile setting. If the failure path could leave a lock, an attacker would
    need no slow server at all — just a URL the guard rejects.
    """
    probe = _probe_engine(isolated_db)

    with Session(isolated_db) as caller:
        payload = _call(
            entry_point,
            caller,
            "http://169.254.169.254/latest/meta-data",
            transport=httpx.MockTransport(_refuse),
        )
        assert payload.error != "", payload
        assert not _driver_transaction_open(caller), "a blocked URL left a transaction open"
        assert _write_from_another_connection(probe, "blocked") is None, (
            "a blocked URL left the database locked"
        )


def test_a_hit_that_serves_a_cached_page_still_records_its_hit(isolated_db):
    """The counter `_serve_hit` writes survives the AC2 fix.

    `hit_count` is the only observable that separates "the hit path writes and
    ends its transaction" from "the hit path was made lock-free by not writing
    at all". Without it, deleting the `_serve_hit` write passes every other
    assertion in this file.
    """
    url = _url(FIRST_HOST, "counted")
    _seed_committed(isolated_db, url)

    with Session(isolated_db) as caller:
        payload = reference_cache.fetch_reference(
            caller, url, transport=httpx.MockTransport(_refuse)
        )
        assert payload.cache == ReferenceCacheOutcome.HIT, payload
        caller.commit()

    with Session(isolated_db) as other:
        assert _row(other, url).hit_count == 1, "the hit was served without being recorded"


# --------------------------------------------------------------------------
# AC3 — composing with 616: a caller's own transaction is still the caller's
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
@pytest.mark.parametrize("site", WRITE_SITES)
def test_a_caller_with_pending_work_keeps_both_ends_of_its_transaction(
    site, entry_point, isolated_db
):
    """AC3: the lock fix must not be "commit the caller and be done with it".

    That is the shape 616 exists to forbid, and it is the cheapest way to make
    every AC1 and AC2 test above pass — release the lock by committing whatever
    the caller had in flight. Here the caller has flushed a row of its own and
    is therefore holding a write lock *by its own choice*; the cache is not
    entitled to end it. One uncommitted sentinel separates all three outcomes:
    a commit makes it durable, a rollback destroys it, a correctly nested write
    leaves it untouched and still committable.

    The `SessionTransaction` identity is the second half. Both a commit and a
    rollback end the outer transaction and autobegin a fresh one, so an
    unchanged object is what says neither happened.
    """
    url, transport = _prime_write_site(site, isolated_db, entry_point)

    with Session(isolated_db) as caller:
        _pending_caller_work(caller)
        outer = caller.get_transaction()

        payload = _call(entry_point, caller, url, transport=transport)
        assert payload.error == "", payload

        assert CALLER_URL not in _durable_urls(isolated_db), (
            "the caller's work was committed for it"
        )
        assert _row(caller, CALLER_URL) is not None, "the caller's work was rolled back"
        assert caller.get_transaction() is outer, "the caller's transaction was ended"

        caller.commit()

    durable = _durable_urls(isolated_db)
    assert CALLER_URL in durable, "the caller could no longer commit its own work"
    assert url in durable, "the cache write was lost from inside the caller's transaction"


# --------------------------------------------------------------------------
# Why a Session of the service's own, and not a bare SAVEPOINT
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_the_service_keeps_its_rows_out_of_a_read_only_callers_unit_of_work(
    entry_point, isolated_db
):
    """The invariant that separates this fix from the other one that passes.

    Dropping 616's explicit `BEGIN` and nesting anyway also releases the lock,
    and satisfies every other test in this file. It does it by relying on the
    accident 616's own docstring calls a defect: with nothing under it the
    SAVEPOINT is the outermost transaction, so `RELEASE` commits — at the
    driver, beneath SQLAlchemy. That is why neither the caller's
    `SessionTransaction` identity nor its driver `in_transaction` can catch it,
    and why no *outcome* on a read-only caller separates the two: such a caller
    has nothing for that commit to take.

    What separates them is whose unit of work the row is in. Nesting puts the
    service's row in the caller's Session and asks the caller to flush it, so
    the service owns neither end of the transaction that commits its write —
    the entanglement 608, 610 and 616 are each one consequence of. A Session of
    our own never asks the caller to flush anything, which is the invariant
    stated directly instead of through the symptom it used to produce.
    """
    url = _url(FIRST_HOST, "unentangled")
    flushed_for_caller: list[str] = []
    real_flush = Session.flush

    def flush(self, objects=None):
        if self is caller:
            flushed_for_caller.extend(
                getattr(obj, "url", "") for obj in (*self.new, *self.dirty) if hasattr(obj, "url")
            )
        return real_flush(self, objects)

    transport = httpx.MockTransport(_body_for(entry_point))
    with Session(isolated_db) as caller, patch.object(Session, "flush", new=flush):
        payload = _call(entry_point, caller, url, transport=transport)

    assert payload.error == "", payload
    assert flushed_for_caller == [], (
        f"the service asked the caller's Session to flush its own rows: {flushed_for_caller}"
    )
