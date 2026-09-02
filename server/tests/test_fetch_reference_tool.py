"""Behavioural tests for `loregarden.services.reference_cache`.

Written from the spec for lg-improved-memory-173. Four acceptance criteria, and
each one is pinned by tests that fail when the criterion is false rather than by
tests that merely exercise the happy path:

**AC1 — SSRF.** Three tiers. Scheme, missing host, `localhost`, `*.local` and
every IP *literal* must be judged with **no name resolution at all**: those tests
patch `socket.getaddrinfo` with a mock that raises, so an implementation that
resolves before parsing the literal fails loudly instead of silently taking a
dependency on DNS. Only hostname->private-IP patches the resolver, and it returns
real 5-tuples so `ipaddress.ip_address()` and `.is_global` stay production code.
The redirect bypass is driven through the real loop and proved by *counting* the
handler's requests — asserting a negative ("no second fetch") any other way
proves nothing.

**AC2 — cache semantics.** miss / hit / revalidated(304) / stale_error, each
hermetic, via `httpx.MockTransport` injected through the keyword-only
`transport=` seam. This departs from the repo's `patch("httpx.post")` habit
deliberately: faking the client would make the test simulate the manual redirect
loop it is supposed to exercise.

**AC3 — "never raises" is not the property.** `pytest.raises` proves nothing
here, because `except Exception: return {}` satisfies it. The property is that
payloads are *self-classifying*: `error` always present, `cache` always present
(on failures too), no success payload carrying empty markdown, and `stale_error`
as the single deliberate both-non-empty case.

**AC4 — no engine binding at import.**

**616 — the module owns none of the transaction it is handed.** The last
section adds what 616 makes true: the service neither commits nor rolls back
the caller's Session, its own writes unwind independently, and markup that
defeats extraction is `extraction_failed` rather than `internal_error`. Those
tests use a row the *caller* owns and has not committed as their instrument,
because "the caller's work survived" alone does not separate a SAVEPOINT from
a service that simply commits everything.

Contract this file pins, beyond the ticket description:

- `error` on a failure payload **contains** the failure kind's enum value, so a
  reason may add detail ("blocked: 169.254.169.254 is not global").
- A failure payload with no cached copy to serve reports `cache = MISS`.
- `fetch_cached_text` returns the same payload shape as `fetch_reference`; the
  raw (unextracted) body arrives in `markdown`.
"""

import ast
import json
import socket
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from loregarden.config import settings
from loregarden.models.domain.enums import (
    ReferenceCacheOutcome,
    ReferenceFetchError,
    ReferencePageKind,
    comparable_utc,
    utcnow,
)
from loregarden.models.domain.tables import ReferencePage
from loregarden.services import reference_cache
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

RESOLVER = "loregarden.services.reference_cache.socket.getaddrinfo"

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

#: Names the resolver mock knows. Everything else is a `gaierror`, so "the code
#: resolved a host this test did not expect" is a visible failure rather than a
#: silent pass.
KNOWN_HOSTS = {
    "docs.example": "93.184.216.34",
    "hop.example": "93.184.216.35",
    "evil.example": "10.0.0.5",
}


def _resolve(host, port, *args, **kwargs):
    """Stand in for `socket.getaddrinfo`.

    `*args`/`**kwargs` are here to absorb the stdlib signature (family, type,
    proto, flags), which callers pass positionally or by keyword; the mock must
    accept whatever the production call site sends without constraining it.
    """
    del args, kwargs
    if host not in KNOWN_HOSTS:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (KNOWN_HOSTS[host], port or 443))]


@pytest.fixture(name="resolver")
def resolver_fixture():
    """Patch DNS with the map above and hand the test the mock."""
    with patch(RESOLVER, side_effect=_resolve) as mock:
        yield mock


@pytest.fixture(name="no_resolve")
def no_resolve_fixture():
    """Make any name resolution an outright failure.

    This is the ordering pin for AC1 tier A: an implementation that calls
    `getaddrinfo` before parsing an IP literal trips this instead of quietly
    routing the whole literal tier through DNS.
    """
    guard = Mock(side_effect=AssertionError("getaddrinfo must not be reached"))
    with patch(RESOLVER, guard):
        yield guard


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _transport(route):
    """Return `(requests, transport)` for a MockTransport around `route`.

    The recorded request list is the load-bearing fixture in this file: for a
    cache hit it is the only thing proving the network was skipped, for the
    redirect bypass the only thing proving the second fetch never happened, and
    for a revalidation the only thing proving the conditional header was
    actually sent rather than merely computed.
    """
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return route(request)

    return requests, httpx.MockTransport(handle)


def _ok_html(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers=HTML_HEADERS, content=HTML_PAGE)


def _refuse(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("no HTTP request should have been made")


def _add_row(session, url, *, markdown="# cached\n\nstored body text.", age_seconds=0, **fields):
    row = ReferencePage(
        url=url,
        title=fields.pop("title", "Cached title"),
        content_markdown=markdown,
        content_chars=len(markdown),
        fetched_at=utcnow() - timedelta(seconds=age_seconds),
        **fields,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _stale_age() -> int:
    return settings.reference_cache_ttl_seconds + 3600


def _get_row(session, url):
    return session.exec(select(ReferencePage).where(ReferencePage.url == url)).first()


def _assert_kind(payload, kind: ReferenceFetchError) -> None:
    """`error` names this failure kind — and *only* this one.

    The contract is "contains", so a reason may add detail. Without the second
    half, an implementation that concatenates every kind into one string
    satisfies every `_assert_kind` in this file while classifying nothing.
    """
    assert kind.value in payload.error, payload
    others = [
        other.value
        for other in ReferenceFetchError
        if other is not kind and other.value in payload.error
    ]
    assert others == [], f"error names other kinds too: {others} — {payload}"


def _assert_self_classifying(payload) -> None:
    """Every payload, success or failure, carries both discriminators.

    Their *presence* stopped being an assertion at 607 and became the model's
    job — which is the point of having one. What is left to check is that the
    two stay meaningful together, which no type can express.
    """
    if payload.error == "":
        assert payload.markdown != "", "a success payload may never carry empty markdown"
    elif payload.markdown != "":
        # The one deliberate both-non-empty case. Any other failure that hands
        # back a body is indistinguishable from a served-stale copy.
        assert payload.cache == ReferenceCacheOutcome.STALE_ERROR, payload


# --------------------------------------------------------------------------
# AC1 tier A — judged without resolving anything
# --------------------------------------------------------------------------

LITERAL_AND_STRING_BLOCKS = [
    "ftp://docs.example/x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http:///no-host",
    "http://localhost/x",
    "http://LOCALHOST:8080/x",
    "http://printer.local/x",
    "http://127.0.0.1/x",
    "http://127.0.0.1:8000/x",
    "http://[::1]/x",
    "http://10.0.0.5/x",
    "http://172.16.0.1/x",
    "http://192.168.1.1/x",
    "http://169.254.169.254/latest/meta-data",
    "http://100.64.0.1/x",
    "http://[fe80::1]/x",
    "http://[fd00::1]/x",
    "http://0.0.0.0/x",
    "http://[::ffff:127.0.0.1]/x",
]


@pytest.mark.parametrize("url", LITERAL_AND_STRING_BLOCKS)
def test_validate_blocks_without_touching_dns(url, no_resolve):
    """AC1: schemes, missing host, localhost/.local and every IP literal are
    rejected by the string/`ipaddress` path alone."""
    assert reference_cache.validate_reference_url(url) != ""
    no_resolve.assert_not_called()


def test_validate_allows_a_public_ip_literal(no_resolve):
    """The literal path is a real predicate, not a blanket reject."""
    assert reference_cache.validate_reference_url("https://8.8.8.8/x") == ""
    no_resolve.assert_not_called()


def test_validate_never_raises_on_junk(no_resolve):
    """AC1's "never raises" clause, on inputs `urlparse` will not like."""
    for url in ("", "   ", "http://[oops", "://no-scheme", "http://:99999/x"):
        assert reference_cache.validate_reference_url(url) != ""


# --------------------------------------------------------------------------
# AC1 tier B — hostname -> address, with the real is_global predicate
# --------------------------------------------------------------------------


def test_validate_allows_a_globally_resolving_host(resolver):
    assert reference_cache.validate_reference_url("https://docs.example/guide") == ""
    assert resolver.called


def test_validate_blocks_a_host_resolving_private(resolver):
    assert reference_cache.validate_reference_url("https://evil.example/x") != ""


def test_validate_blocks_when_any_address_is_private():
    """ "Every returned address" — one global A record does not launder a
    private sibling."""
    both = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
    ]
    with patch(RESOLVER, return_value=both):
        assert reference_cache.validate_reference_url("https://mixed.example/x") != ""


def test_validate_blocks_when_resolution_fails(resolver):
    """Resolve failure is blocked, never allowed."""
    assert reference_cache.validate_reference_url("https://unknown.example/x") != ""


def test_validate_asks_the_resolver_about_the_url_host(resolver):
    """Cheap mitigation for what tier B cannot prove: at least the hostname
    reaching the resolver is the one from the URL."""
    reference_cache.validate_reference_url("https://docs.example/guide")
    args, kwargs = resolver.call_args
    assert "docs.example" in (list(args) + list(kwargs.values()))


# --------------------------------------------------------------------------
# AC1 tier C — the redirect bypass, through the real loop
# --------------------------------------------------------------------------


def test_redirect_to_link_local_is_blocked_and_never_fetched(session, resolver):
    """The canonical SSRF bypass: a page that resolves globally, redirecting to
    the cloud metadata endpoint. The request counter is what proves the
    re-validation lives inside the loop rather than being decorative."""

    def route(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    requests, transport = _transport(route)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/a", transport=transport
    )

    assert len(requests) == 1, "the hop target was fetched — re-validation is not in the loop"
    _assert_kind(payload, ReferenceFetchError.BLOCKED)
    _assert_self_classifying(payload)
    assert _get_row(session, "https://docs.example/a") is None


def test_redirect_to_a_privately_resolving_host_is_blocked_and_never_fetched(session, resolver):
    """The bypass the literal test above cannot catch.

    `169.254.169.254` in a `Location` is caught by the string/`ipaddress` tier
    alone, so a hop re-check that only re-runs *that* tier passes the test
    above while still fetching anything with a hostname. The hop must go
    through the whole validator, resolver included: `evil.example` looks like
    an ordinary public name and resolves to 10.0.0.5.
    """

    def route(request):
        if request.url.host == "docs.example":
            return httpx.Response(302, headers={"Location": "https://evil.example/x"})
        return _ok_html(request)

    requests, transport = _transport(route)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/a", transport=transport
    )

    assert len(requests) == 1, "the hop was fetched — the hop re-check does not resolve names"
    _assert_kind(payload, ReferenceFetchError.BLOCKED)
    _assert_self_classifying(payload)
    assert _get_row(session, "https://docs.example/a") is None


def test_relative_location_is_resolved_against_the_hop(session, resolver):
    def route(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"Location": "/b"})
        return _ok_html(request)

    requests, transport = _transport(route)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/a", transport=transport
    )

    assert [str(r.url) for r in requests] == [
        "https://docs.example/a",
        "https://docs.example/b",
    ]
    assert payload.error == ""


def test_missing_location_on_a_redirect_is_an_error_not_a_crash(session, resolver):
    _, transport = _transport(lambda _r: httpx.Response(302))
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/a", transport=transport
    )
    assert payload.error != ""
    _assert_self_classifying(payload)


def test_redirect_loop_is_capped(session, resolver):
    counter = {"n": 0}

    def route(_request):
        counter["n"] += 1
        return httpx.Response(302, headers={"Location": f"https://docs.example/hop{counter['n']}"})

    requests, transport = _transport(route)
    with patch.object(settings, "reference_fetch_max_redirects", 2):
        payload = reference_cache.fetch_reference(
            session, "https://docs.example/a", transport=transport
        )

    _assert_kind(payload, ReferenceFetchError.TOO_MANY_REDIRECTS)
    # Two-sided on purpose. An upper bound alone is satisfied by an
    # implementation that reports the cap without ever making a request, which
    # is the same payload for the opposite reason.
    assert 2 <= len(requests) <= 3, f"the cap did not bound the loop: {len(requests)} requests"


def test_a_redirect_without_a_content_type_is_not_unsupported(session, resolver):
    """The content-type gate must sit after the redirect check: a 3xx carries no
    content-type and must not be rejected as an unsupported one."""

    def route(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"Location": "https://docs.example/b"})
        return _ok_html(request)

    _, transport = _transport(route)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/a", transport=transport
    )
    assert payload.error == ""


def test_ssrf_is_checked_before_any_request_is_made(session, no_resolve):
    _, transport = _transport(_refuse)
    payload = reference_cache.fetch_reference(
        session, "http://169.254.169.254/latest/meta-data", transport=transport
    )
    _assert_kind(payload, ReferenceFetchError.BLOCKED)
    no_resolve.assert_not_called()


def test_fetch_cached_text_applies_the_same_guard(session, no_resolve):
    _, transport = _transport(_refuse)
    payload = reference_cache.fetch_cached_text(
        session,
        "http://127.0.0.1:9200/docs.json",
        kind=ReferencePageKind.CATALOG,
        accept_types=("application/json",),
        transport=transport,
    )
    _assert_kind(payload, ReferenceFetchError.BLOCKED)
    _assert_self_classifying(payload)


# --------------------------------------------------------------------------
# AC2 — the four cache outcomes
# --------------------------------------------------------------------------


def test_miss_fetches_extracts_and_caches(session, resolver):
    requests, transport = _transport(_ok_html)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert len(requests) == 1
    assert payload.cache == ReferenceCacheOutcome.MISS
    assert payload.error == ""
    assert "map()" in payload.markdown
    assert payload.title == "Array.prototype.map"

    row = _get_row(session, "https://docs.example/guide")
    assert row is not None
    assert row.content_markdown == payload.markdown
    assert row.content_chars == len(payload.markdown)


def test_fresh_row_is_a_hit_and_skips_the_network(session, resolver):
    _add_row(session, "https://docs.example/guide")
    _, transport = _transport(_refuse)

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload.cache == ReferenceCacheOutcome.HIT
    assert payload.markdown == "# cached\n\nstored body text."
    assert payload.error == ""
    session.expire_all()
    assert _get_row(session, "https://docs.example/guide").hit_count == 1


def test_stale_row_revalidates_with_conditional_headers(session, resolver):
    row = _add_row(
        session,
        "https://docs.example/guide",
        age_seconds=_stale_age(),
        etag='"v1"',
        last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
    )
    before_body = row.content_markdown
    before_fetched = row.fetched_at

    requests, transport = _transport(lambda _r: httpx.Response(304, headers={"ETag": '"v1"'}))
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert len(requests) == 1
    assert requests[0].headers.get("if-none-match") == '"v1"'
    assert requests[0].headers.get("if-modified-since") == "Wed, 21 Oct 2026 07:28:00 GMT"
    assert payload.cache == ReferenceCacheOutcome.REVALIDATED
    assert payload.error == ""
    assert payload.markdown == before_body

    session.expire_all()
    fresh = _get_row(session, "https://docs.example/guide")
    assert fresh.content_markdown == before_body
    assert fresh.fetched_at > before_fetched


def test_stale_row_with_a_200_is_re_extracted_and_updated(session, resolver):
    _add_row(session, "https://docs.example/guide", age_seconds=_stale_age(), etag='"v1"')
    _, transport = _transport(_ok_html)

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload.cache == ReferenceCacheOutcome.MISS
    assert "map()" in payload.markdown
    session.expire_all()
    assert "map()" in _get_row(session, "https://docs.example/guide").content_markdown


def test_stale_row_survives_a_transport_failure(session, resolver):
    def route(request):
        raise httpx.ConnectError("connection refused", request=request)

    _, transport = _transport(route)
    _add_row(session, "https://docs.example/guide", age_seconds=_stale_age())

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload.cache == ReferenceCacheOutcome.STALE_ERROR
    assert payload.markdown == "# cached\n\nstored body text."
    assert payload.error != ""


def test_refresh_forces_a_fetch_past_a_fresh_row(session, resolver):
    _add_row(session, "https://docs.example/guide")
    requests, transport = _transport(_ok_html)

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", refresh=True, transport=transport
    )

    assert len(requests) == 1
    assert "map()" in payload.markdown


def test_max_chars_truncates_the_payload_and_never_the_row(session, resolver):
    _, transport = _transport(_ok_html)
    full = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )
    stored = _get_row(session, "https://docs.example/guide").content_markdown

    _, transport2 = _transport(_refuse)
    capped = reference_cache.fetch_reference(
        session, "https://docs.example/guide", max_chars=40, transport=transport2
    )

    assert len(capped.markdown) <= 40
    assert capped.truncated is True
    assert capped.total_chars == full.total_chars
    assert full.truncated is False
    session.expire_all()
    assert _get_row(session, "https://docs.example/guide").content_markdown == stored


# --------------------------------------------------------------------------
# AC2/AC3 — failure kinds, none of them cached
# --------------------------------------------------------------------------


def _fail_route(kind: ReferenceFetchError):
    if kind is ReferenceFetchError.UNSUPPORTED_CONTENT_TYPE:
        return lambda _r: httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7"
        )
    if kind is ReferenceFetchError.EXTRACTION_FAILED:
        return lambda _r: httpx.Response(
            200, headers=HTML_HEADERS, content=b"<html><body></body></html>"
        )
    if kind is ReferenceFetchError.TOO_LARGE:
        return lambda _r: httpx.Response(
            200,
            headers=HTML_HEADERS,
            content=b"<html><body><p>" + b"x" * 5000 + b"</p></body></html>",
        )

    def connect_error(request):
        raise httpx.ConnectError("connection refused", request=request)

    return connect_error


NO_CACHE_KINDS = [
    ReferenceFetchError.UNSUPPORTED_CONTENT_TYPE,
    ReferenceFetchError.EXTRACTION_FAILED,
    ReferenceFetchError.TOO_LARGE,
    ReferenceFetchError.FETCH_ERROR,
]


@pytest.mark.parametrize("kind", NO_CACHE_KINDS, ids=lambda k: k.value)
def test_failure_kinds_are_reported_and_never_cached(session, resolver, kind):
    _, transport = _transport(_fail_route(kind))
    with patch.object(settings, "reference_fetch_max_bytes", 200):
        payload = reference_cache.fetch_reference(
            session, "https://docs.example/guide", transport=transport
        )

    _assert_kind(payload, kind)
    _assert_self_classifying(payload)
    assert payload.cache == ReferenceCacheOutcome.MISS
    assert _get_row(session, "https://docs.example/guide") is None


def test_content_type_is_judged_before_the_body_is_read(session, resolver):
    """The gate sits before the body, so an unsupported type is never streamed.

    Made observable by making the body also over the byte cap: an
    implementation that reads first and classifies afterwards reports
    `too_large`, which is a true statement about a body it should never have
    pulled.
    """
    _, transport = _transport(
        lambda _r: httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7" + b"x" * 5000
        )
    )
    with patch.object(settings, "reference_fetch_max_bytes", 200):
        payload = reference_cache.fetch_reference(
            session, "https://docs.example/guide", transport=transport
        )

    _assert_kind(payload, ReferenceFetchError.UNSUPPORTED_CONTENT_TYPE)
    assert _get_row(session, "https://docs.example/guide") is None


def test_a_success_payload_identifies_the_row_it_came_from(session, resolver):
    """`url` and `fetched_at` are in `SUCCESS_KEYS`, which only proves the keys
    exist. A caller dedupes on `url` and ages the copy by `fetched_at`, so both
    have to carry the real values — the normalized URL, and the row's own
    timestamp — rather than a constant."""
    _, transport = _transport(_ok_html)
    fresh = reference_cache.fetch_reference(
        session, "https://Docs.Example/guide#anchor", transport=transport
    )
    row = _get_row(session, "https://docs.example/guide")

    assert fresh.url == "https://docs.example/guide"
    assert fresh.fetched_at is not None
    assert row is not None

    _, transport2 = _transport(_refuse)
    hit = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport2
    )
    session.expire_all()
    assert hit.cache == ReferenceCacheOutcome.HIT
    assert hit.url == "https://docs.example/guide"
    assert hit.fetched_at == _get_row(session, "https://docs.example/guide").fetched_at


def test_an_http_error_status_is_reported_and_not_cached(session, resolver):
    _, transport = _transport(lambda _r: httpx.Response(404, headers=HTML_HEADERS, content=b"no"))
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert "404" in payload.error
    _assert_self_classifying(payload)
    assert _get_row(session, "https://docs.example/guide") is None


def test_a_timeout_is_an_error_payload(session, resolver):
    def route(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _, transport = _transport(route)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )
    assert payload.error != ""
    _assert_self_classifying(payload)


# --------------------------------------------------------------------------
# AC3 — payloads are self-classifying
# --------------------------------------------------------------------------

SUCCESS_KEYS = {
    "url",
    "title",
    "markdown",
    "cache",
    "fetched_at",
    "total_chars",
    "truncated",
    "error",
}


def test_every_success_payload_carries_the_documented_keys(session, resolver):
    """Asserted on the *serialized* envelope since 607, not on the object.

    `set(model)` iterates a Pydantic model's fields, so checking the object
    would only restate its class definition. The dict a caller actually
    receives is the thing the documented keys are a promise about.
    """
    _, transport = _transport(_ok_html)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )
    assert SUCCESS_KEYS <= set(payload.model_dump(mode="json"))


def test_a_success_payload_survives_json_dumps(session, resolver):
    """607's reason for existing, on the outcome that actually broke.

    A *failure* payload has `fetched_at=None` and serialized fine before this
    ticket, so a test that reached for the convenient one would have passed
    against the unfixed module. Only a success carries a real datetime, which
    is what `json.dumps` refused — the exact call 174's MCP tool has to make.
    """
    _, transport = _transport(_ok_html)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload.fetched_at is not None, "not the outcome under test — no datetime present"
    with pytest.raises(TypeError, match="datetime"):
        # The defect itself, still reachable: the field really is a datetime, so
        # `mode="json"` below is doing the work rather than the test passing on a
        # value that was already a string.
        json.dumps(payload.model_dump())

    restored = json.loads(json.dumps(payload.model_dump(mode="json")))

    assert restored["cache"] == ReferenceCacheOutcome.HIT.value or restored["cache"] == "miss"
    assert isinstance(restored["fetched_at"], str), (  # py-org: allow-isinstance
        "fetched_at did not survive as a JSON scalar"
    )
    assert restored["markdown"] == payload.markdown


def test_stale_error_is_the_only_payload_with_both_a_body_and_an_error(session, resolver):
    """Told apart from a success by `cache`, and from a hard failure by having a
    body at all — which is exactly why `cache` must be on failure payloads."""

    def route(request):
        raise httpx.ConnectError("connection refused", request=request)

    _add_row(session, "https://docs.example/stale", age_seconds=_stale_age())
    _, transport = _transport(route)
    stale = reference_cache.fetch_reference(
        session, "https://docs.example/stale", transport=transport
    )
    _, transport2 = _transport(route)
    hard = reference_cache.fetch_reference(
        session, "https://docs.example/fresh", transport=transport2
    )

    assert stale.markdown != "" and stale.error != ""
    assert stale.cache == ReferenceCacheOutcome.STALE_ERROR
    assert hard.markdown == "" and hard.error != ""
    assert hard.cache != ReferenceCacheOutcome.STALE_ERROR


def test_no_public_entry_point_raises(session, resolver):
    """The weak half of AC3, kept only as a smoke check — the sweep above is the
    half that actually constrains the implementation."""

    def route(request):
        raise httpx.ConnectError("boom", request=request)

    for url in ("http://169.254.169.254/x", "not-a-url", "https://docs.example/guide"):
        _, transport = _transport(route)
        _assert_self_classifying(reference_cache.fetch_reference(session, url, transport=transport))
        _, transport2 = _transport(route)
        _assert_self_classifying(
            reference_cache.fetch_cached_text(
                session,
                url,
                kind=ReferencePageKind.INDEX,
                accept_types=("application/json",),
                transport=transport2,
            )
        )


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# AC3 — the escapes that came from *arguments*, not from the work (620)
#
# The boundary caught everything the fetch could throw, but two statements ran
# above it: `url.strip()` and `tuple(accept_types)`. Both assume the declared
# types, so the one input class that escaped as an exception was a caller
# passing the wrong type — and 174, the first caller, sources URLs from JSON
# and database rows where None is live. These are out-of-contract by
# annotation, which is exactly why nothing else in this file covers them.
# --------------------------------------------------------------------------


#: Not str, and each broken in a different way: absent, a different scalar, and
#: an object with no string protocol at all.
OUT_OF_CONTRACT_URLS = [None, 42, object()]


@pytest.mark.parametrize("bad_url", OUT_OF_CONTRACT_URLS, ids=["none", "int", "object"])
@pytest.mark.parametrize("entry_point", ["fetch_reference", "fetch_cached_text"])
def test_a_url_that_is_not_a_string_is_a_payload_not_an_exception(bad_url, entry_point, session):
    """Fails if `url.strip()` moves back above the boundary's `try`."""
    call = _entry_point(entry_point)

    payload = call(session, bad_url)

    _assert_kind(payload, ReferenceFetchError.INTERNAL_ERROR)
    _assert_self_classifying(payload)


@pytest.mark.parametrize("bad_url", OUT_OF_CONTRACT_URLS, ids=["none", "int", "object"])
def test_the_payload_for_a_bad_url_names_the_type_and_keeps_url_a_string(bad_url, session):
    """The payload stays well-formed for an input that broke its own contract.

    `url` must remain a string — 607 turns this envelope into a model, and a
    None there would fail validation instead of reporting the caller's mistake.
    The type is what the error names, for two reasons: it is the actual defect
    (the None, not the URL), and it is all that can be read off the value
    without running a `__repr__` that could itself raise inside the handler
    that exists to stop things raising.
    """
    payload = reference_cache.fetch_reference(session, bad_url)

    assert isinstance(payload.url, str), (  # py-org: allow-isinstance
        f"url came back as {type(payload['url']).__name__}, not a string"
    )
    assert type(bad_url).__name__ in payload.url, payload
    assert type(bad_url).__name__ in payload.error, payload


@pytest.mark.parametrize("bad_types", [None, 42], ids=["none", "int"])
def test_accept_types_that_is_not_iterable_is_a_payload_not_an_exception(bad_types, session):
    """The second statement that ran above the boundary, in its own entry point.

    Fails if `tuple(accept_types)` moves back into `fetch_cached_text`'s body.
    The URL here is valid, so a passing payload would mean the fetch was
    attempted — the assertion is that it was classified instead.
    """
    payload = reference_cache.fetch_cached_text(
        session,
        "https://93.184.216.34/guide",
        kind=ReferencePageKind.CATALOG,
        accept_types=bad_types,
    )

    _assert_kind(payload, ReferenceFetchError.INTERNAL_ERROR)
    _assert_self_classifying(payload)


def _entry_point(name: str):
    if name == "fetch_reference":
        return reference_cache.fetch_reference
    return lambda session, url: reference_cache.fetch_cached_text(
        session, url, kind=ReferencePageKind.CATALOG, accept_types=("application/json",)
    )


# AC3 — the escapes an enumerated `except` list let through
#
# Each of these three raised out of a public entry point before the boundary
# guard existed, and none of them is an `httpx` error, which is why catching
# `TimeoutException`/`HTTPError` never saw them. Asserting "no exception" is
# not enough — five wrong implementations once passed this file — so each
# asserts the payload's *kind*, and `_assert_kind` also pins that the error
# names no other kind.
# --------------------------------------------------------------------------


class _Boom(Exception):
    """Deliberately not an `httpx` error, and not a `LookupError` either."""


def test_a_charset_the_remote_invented_is_ignored_not_raised(session, resolver):
    """`charset` comes from the remote `Content-Type`, so `utf-9000` is a crash
    primitive any reachable server holds.

    The assertion is that the page still *succeeds* — falling back to utf-8
    beats both raising and turning a perfectly readable body into a failure —
    so this fails against an implementation that merely catches the
    `LookupError` downstream as well as against one that does not catch it.
    """
    _, transport = _transport(
        lambda _r: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-9000"},
            content=HTML_PAGE,
        )
    )

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload.error == "", payload
    assert payload.cache == ReferenceCacheOutcome.MISS
    assert "map() method" in payload.markdown
    _assert_self_classifying(payload)


def test_a_charset_naming_a_non_text_codec_is_ignored_not_raised(session, resolver):
    """`base64` is a real codec that `codecs.lookup` accepts and `bytes.decode`
    refuses — so a label check that only looks it up still raises."""
    _, transport = _transport(
        lambda _r: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=base64"},
            content=HTML_PAGE,
        )
    )

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload.error == "", payload
    assert "map() method" in payload.markdown


def test_a_non_httpx_exception_mid_body_becomes_an_internal_error_payload(session, resolver):
    """The wider half of the same root cause: the headers pass every gate and
    the failure happens while the body is being streamed."""

    def erupt():
        yield b"<html><body><main><p>partial"
        raise _Boom("the transport came apart mid-body")

    _, transport = _transport(lambda _r: httpx.Response(200, headers=HTML_HEADERS, content=erupt()))

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    _assert_kind(payload, ReferenceFetchError.INTERNAL_ERROR)
    assert payload.cache == ReferenceCacheOutcome.MISS
    assert payload.markdown == ""
    assert _get_row(session, "https://docs.example/guide") is None
    _assert_self_classifying(payload)


def test_a_non_httpx_exception_mid_body_still_serves_a_stale_copy(session, resolver):
    """Why the conversion happens at `_fetch_once` and not only at the boundary.

    The outer guard alone would make every payload well-formed while quietly
    throwing away a cached copy we could still serve: converting the exception
    into a hop result keeps the failure inside the cache layer, where
    stale-if-error lives. Delete the `_fetch_once` handler and this is the
    assertion that notices.
    """

    def erupt():
        yield b"<html><body><main><p>partial"
        raise _Boom("the transport came apart mid-body")

    _add_row(session, "https://docs.example/stale", age_seconds=_stale_age())
    _, transport = _transport(lambda _r: httpx.Response(200, headers=HTML_HEADERS, content=erupt()))

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/stale", transport=transport
    )

    assert payload.cache == ReferenceCacheOutcome.STALE_ERROR
    assert payload.markdown == "# cached\n\nstored body text."
    _assert_kind(payload, ReferenceFetchError.INTERNAL_ERROR)
    _assert_self_classifying(payload)


def test_a_failing_write_becomes_an_internal_error_payload(session, resolver):
    """Persistence is inside the promise too: the fetch and the extraction both
    succeeded, and the database is what would not take it.

    Rewritten for 616. It patched `session.commit` and asserted
    `rollback.called` — both spellings of a contract the module no longer has:
    it does not commit the caller's Session, so patching `commit` injects
    nothing, and rolling the caller back is now the defect rather than the
    remedy. What survives is the half worth keeping: a write the database
    refuses is still an `INTERNAL_ERROR` payload rather than an exception. The
    caller's transaction is left exactly as found — including left needing its
    own rollback, which is the caller's call to make and not ours.
    """
    _, transport = _transport(_ok_html)
    url = "https://docs.example/guide"

    with _write_fails(session, url):
        payload = reference_cache.fetch_reference(session, url, transport=transport)

    _assert_kind(payload, ReferenceFetchError.INTERNAL_ERROR)
    assert payload.cache == ReferenceCacheOutcome.MISS
    assert payload.markdown == ""
    _assert_self_classifying(payload)


def test_an_internal_error_is_distinguishable_from_a_remote_failure(session, resolver):
    """The reason `INTERNAL_ERROR` is its own kind rather than a third spelling
    of `FETCH_ERROR`: a caller deciding whether to retry the URL must be able to
    tell "the remote misbehaved" from "we have a bug"."""

    def erupt():
        yield b"<html><body>"
        raise _Boom("ours")

    _, ours = _transport(lambda _r: httpx.Response(200, headers=HTML_HEADERS, content=erupt()))
    _, theirs = _transport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request))
    )

    internal = reference_cache.fetch_reference(session, "https://docs.example/a", transport=ours)
    remote = reference_cache.fetch_reference(session, "https://docs.example/b", transport=theirs)

    assert internal.error != remote.error
    _assert_kind(internal, ReferenceFetchError.INTERNAL_ERROR)
    _assert_kind(remote, ReferenceFetchError.FETCH_ERROR)


# --------------------------------------------------------------------------
# fetch_cached_text — raw passthrough on the same machinery
# --------------------------------------------------------------------------


def test_fetch_cached_text_stores_the_raw_body_without_extraction(session, resolver):
    body = b'{"entries": [{"name": "Array.prototype.map"}]}'
    _, transport = _transport(
        lambda _r: httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    )

    payload = reference_cache.fetch_cached_text(
        session,
        "https://docs.example/docs.json",
        kind=ReferencePageKind.CATALOG,
        accept_types=("application/json",),
        transport=transport,
    )

    assert payload.error == ""
    assert payload.markdown == body.decode()
    row = _get_row(session, "https://docs.example/docs.json")
    assert row.kind == ReferencePageKind.CATALOG
    assert row.content_markdown == body.decode()


def test_fetch_cached_text_rejects_a_type_outside_accept_types(session, resolver):
    _, transport = _transport(_ok_html)
    payload = reference_cache.fetch_cached_text(
        session,
        "https://docs.example/docs.json",
        kind=ReferencePageKind.CATALOG,
        accept_types=("application/json",),
        transport=transport,
    )
    _assert_kind(payload, ReferenceFetchError.UNSUPPORTED_CONTENT_TYPE)
    assert _get_row(session, "https://docs.example/docs.json") is None


def test_fetch_cached_text_serves_a_fresh_row_without_the_network(session, resolver):
    _add_row(session, "https://docs.example/docs.json", markdown='{"entries": []}')
    _, transport = _transport(_refuse)

    payload = reference_cache.fetch_cached_text(
        session,
        "https://docs.example/docs.json",
        kind=ReferencePageKind.CATALOG,
        accept_types=("application/json",),
        transport=transport,
    )
    assert payload.cache == ReferenceCacheOutcome.HIT
    assert payload.markdown == '{"entries": []}'


# --------------------------------------------------------------------------
# normalization and the fragment shim
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  https://Docs.Example/Guide#frag  ", "https://docs.example/Guide"),
        ("HTTP://Docs.Example:80/a", "http://docs.example/a"),
        ("https://docs.example:443/a", "https://docs.example/a"),
        ("https://docs.example:8443/a", "https://docs.example:8443/a"),
    ],
)
def test_normalize_reference_url(raw, expected):
    assert reference_cache.normalize_reference_url(raw) == expected


def test_normalization_makes_two_spellings_one_cache_row(session, resolver):
    requests, transport = _transport(_ok_html)
    reference_cache.fetch_reference(session, "https://docs.example/guide", transport=transport)
    second = reference_cache.fetch_reference(
        session, "https://Docs.Example/guide#anchor", transport=transport
    )

    assert second.cache == ReferenceCacheOutcome.HIT
    assert len(requests) == 1
    assert len(session.exec(select(ReferencePage)).all()) == 1


def test_fragment_shim_keeps_a_heading_that_bare_extraction_drops():
    """DevDocs pages start at `<h1>` with no `<body>`. Extracted raw,
    trafilatura silently drops the heading; the shim must keep it. That is the
    observable difference the shim exists for, and it is all this test pins.

    The *mechanism* is left to the implementer on purpose. Measured on
    trafilatura 2.2.0: wrapping the fragment in `<html><body>` and passing
    `favor_recall=True` does **not** keep the heading — that recipe, recorded
    by test-design, was not verified and does not work. Wrapping in an
    `<article>` element does, and so would prepending the extracted metadata
    title. Either satisfies this assertion; the version-sensitive part is the
    recipe, not the property.
    """
    fragment = (
        "<h1>Array.prototype.map</h1>"
        "<p>The map() method creates a new array populated with the results of "
        "calling a provided function on every element in the calling array.</p>"
        "<p>It does not mutate the array on which it is called.</p>"
    )
    markdown, _title = reference_cache._extract_markdown(
        fragment, "text/html", "https://docs.example/map"
    )
    assert "Array.prototype.map" in markdown


def test_full_documents_keep_their_title_and_body():
    markdown, title = reference_cache._extract_markdown(
        HTML_PAGE.decode(), "text/html", "https://docs.example/map"
    )
    assert title == "Array.prototype.map"
    assert "map()" in markdown


def test_unextractable_body_yields_empty_pair():
    assert reference_cache._extract_markdown(
        "<html><body></body></html>", "text/html", "https://docs.example/x"
    ) == ("", "")


# --------------------------------------------------------------------------
# 616 — the module is handed a Session and owns none of its transaction
#
# Every test in this section puts a row the CALLER owns, and has not committed,
# into the Session before calling the service. That row is the instrument: a
# rollback destroys it, a commit makes it durable, and a SAVEPOINT leaves it
# exactly as it was. One sentinel therefore separates all three, which is why
# "the caller's work survived" is never asserted on its own here — a service
# that commits everything also leaves the row alive.
#
# The service's own row is the second instrument, and it is the one a partial
# fix loses: nesting the writes without ever unwinding them satisfies every
# caller-side assertion while leaving a half-written cache row behind.
# --------------------------------------------------------------------------

CALLER_URL = "https://caller.example/unrelated-pending-work"

#: Deeply nested markup, served through the real transport. `trafilatura`
#: 2.2.0 does not in fact raise on it — it returns `None`, which is already
#: `extraction_failed` — so the tests that need a *raise* inject it at the
#: library boundary below. The markup is still real, and still travels the
#: whole fetch/decode path, because the extraction call is the only thing
#: standing in for a version or an input that does raise.
NESTED_MARKUP = (
    "<html><head><title>nested</title></head><body><main>"
    + "<div>" * 4000
    + "<p>the deepest paragraph in a document built to exhaust a recursive parser.</p>"
    + "</div>" * 4000
    + "</main></body></html>"
).encode("utf-8")


def _pending_caller_work(session):
    """Work the caller has in flight in its own Session and has not committed."""
    row = ReferencePage(
        url=CALLER_URL, title="caller work", content_markdown="the caller's own row"
    )
    session.add(row)
    return row


def _durable_urls(engine) -> set[str]:
    """The URLs a *different* connection can see — that is, what is committed.

    Read on its own Session deliberately: the caller's own Session would show
    its uncommitted rows too, and could not tell "still pending" from "someone
    committed my transaction out from under me".
    """
    with Session(engine) as other:
        return set(other.exec(select(ReferencePage.url)).all())


def _durable_row(engine, url):
    """A snapshot of the committed row for `url`, or None — read off-Session.

    Every mutable field a write site touches is in it, so "nothing of ours
    landed" is checked as an equality against the pre-call snapshot rather than
    as mere absence. `_serve_hit` and `_revalidated` update a row that already
    exists, so absence proves nothing there.
    """
    with Session(engine) as other:
        row = other.exec(select(ReferencePage).where(ReferencePage.url == url)).first()
        if row is None:
            return None
        return (
            row.title,
            row.content_markdown,
            row.content_chars,
            row.etag,
            row.last_modified,
            row.hit_count,
            comparable_utc(row.fetched_at),
        )


def _write_fails(session, url):
    """Fail the service's own write to `url`, and nothing else.

    Patching `session.flush` outright is not enough: SQLAlchemy autoflushes on
    every query, so a blanket failure fires on the service's first *read* and
    never reaches a write at all. This delegates to the real flush unless the
    Session is holding the service's own row for `url`, which keeps the
    caller's unrelated work flushing normally and makes the failure a
    persistence failure rather than a read failure.

    Patched on the class rather than on one instance, since 638: a caller that
    has only read is written for on the service's *own* Session, so an
    injection bound to the caller's instance reaches nothing and the write it
    was meant to fail succeeds instead. The `url` guard is what keeps a
    class-wide patch narrow.
    """
    real_flush = Session.flush

    def flush(self, objects=None):
        ours = [obj for obj in (*self.new, *self.dirty) if getattr(obj, "url", None) == url]
        if ours:
            raise OperationalError("INSERT INTO reference_pages", {}, _Boom("disk I/O error"))
        return real_flush(self, objects)

    return patch.object(Session, "flush", new=flush)


def _extraction_raises():
    """`trafilatura.extract` raising the way hostile markup can make it raise."""
    return patch.object(
        reference_cache.trafilatura,
        "extract",
        side_effect=RecursionError("maximum recursion depth exceeded"),
    )


def test_hostile_markup_never_touches_the_callers_pending_work(session, isolated_db, resolver):
    """616 AC1, and the ticket's second reproduction: a remote page destroyed
    the caller's uncommitted row.

    `_extract_markdown` holds no handler of its own, so whatever the extractor
    raises reaches the boundary — which rolls the caller's Session back on
    every escape, not only on the failed commit its docstring describes. The
    trigger is page content, so this is remotely reachable.
    """
    _, transport = _transport(
        lambda _r: httpx.Response(200, headers=HTML_HEADERS, content=NESTED_MARKUP)
    )
    _pending_caller_work(session)
    outer = session.get_transaction()

    with _extraction_raises():
        payload = reference_cache.fetch_reference(
            session, "https://docs.example/guide", transport=transport
        )

    assert CALLER_URL not in _durable_urls(isolated_db), "the caller's work was committed for it"
    assert _get_row(session, CALLER_URL) is not None, "the caller's work was rolled back"
    assert session.get_transaction() is outer, "the caller's transaction was ended"
    _assert_self_classifying(payload)

    session.commit()
    assert CALLER_URL in _durable_urls(isolated_db)


def test_markup_that_makes_extraction_raise_is_extraction_failed(session, resolver):
    """616 AC2. A page we could not read is the page's problem, not ours.

    `internal_error` tells the caller to stop retrying this URL and come fix
    us; `extraction_failed` tells it the truth. The distinction only exists if
    `_extract_markdown` handles the raise where it happens.
    """
    _, transport = _transport(
        lambda _r: httpx.Response(200, headers=HTML_HEADERS, content=NESTED_MARKUP)
    )

    with _extraction_raises():
        payload = reference_cache.fetch_reference(
            session, "https://docs.example/guide", transport=transport
        )

    _assert_kind(payload, ReferenceFetchError.EXTRACTION_FAILED)
    assert payload.cache == ReferenceCacheOutcome.MISS
    assert payload.markdown == ""
    assert _get_row(session, "https://docs.example/guide") is None, "an unreadable page was cached"
    _assert_self_classifying(payload)


def test_a_title_we_could_not_read_does_not_throw_away_the_body(session, resolver):
    """626. A page whose metadata parse raises is still cached, and once.

    Sharing one handler between `extract` and `extract_metadata` discarded a
    body already in hand, so the page was never cached and every call re-fetched
    it — request amplification a hostile page can aim, since the metadata parse
    runs on markup the remote controls. The request count is the instrument: a
    cached page is fetched once however many times it is asked for.
    """
    url = "https://docs.example/guide"
    requests, transport = _transport(_ok_html)

    with patch.object(
        reference_cache.trafilatura,
        "extract_metadata",
        side_effect=RecursionError("maximum recursion depth exceeded"),
    ):
        first = reference_cache.fetch_reference(session, url, transport=transport)
        second = reference_cache.fetch_reference(session, url, transport=transport)

    assert first.error == "", first
    assert first.markdown != "", "the extracted body was thrown away with the title"
    assert first.title == "", first
    _assert_self_classifying(first)
    assert second.cache == ReferenceCacheOutcome.HIT, second
    assert len(requests) == 1, f"the page was re-fetched {len(requests)} times instead of cached"


def test_nested_markup_that_extracts_to_nothing_is_extraction_failed(session, resolver):
    """The same classification without any injected failure — a guard, not a
    discriminator: it passes today and must keep passing. It is here because it
    is the *only* behaviour real nested markup produces at trafilatura 2.2.0,
    which is why the two tests above inject the raise rather than provoke it."""
    _, transport = _transport(
        lambda _r: httpx.Response(200, headers=HTML_HEADERS, content=b"<html><body></body></html>")
    )

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    _assert_kind(payload, ReferenceFetchError.EXTRACTION_FAILED)
    _assert_self_classifying(payload)


@pytest.mark.parametrize("outcome", ["hit", "revalidated", "miss", "extraction_raise"])
def test_no_outcome_commits_or_rolls_back_the_callers_session(
    outcome, session, isolated_db, resolver
):
    """616 AC3, swept over every path that reaches a write site.

    Three of the four commit today, so the assertion that discriminates is not
    "the caller's row survived" — it does — but that it is *still uncommitted*
    when the call returns. The identity of the outer `SessionTransaction` is
    the second half: both a commit and a rollback end it and autobegin another,
    so an unchanged object means neither happened.
    """
    url = "https://docs.example/guide"
    extraction = nullcontext()
    if outcome == "hit":
        _add_row(session, url)
        _, transport = _transport(_refuse)
    elif outcome == "revalidated":
        _add_row(session, url, age_seconds=_stale_age(), etag='"v1"')
        _, transport = _transport(lambda _r: httpx.Response(304, headers={"ETag": '"v1"'}))
    else:
        _, transport = _transport(_ok_html)
        if outcome == "extraction_raise":
            extraction = _extraction_raises()

    _pending_caller_work(session)
    outer = session.get_transaction()

    with extraction:
        payload = reference_cache.fetch_reference(session, url, transport=transport)

    _assert_self_classifying(payload)
    assert CALLER_URL not in _durable_urls(isolated_db), "the caller's work was committed for it"
    assert _get_row(session, CALLER_URL) is not None, "the caller's work was rolled back"
    assert session.get_transaction() is outer, "the caller's transaction was ended"

    session.commit()
    assert CALLER_URL in _durable_urls(isolated_db), "the caller could no longer commit its work"


@pytest.mark.parametrize("site", ["miss", "hit", "revalidated"])
def test_a_write_that_fails_partway_leaves_no_half_written_row(
    site, session, isolated_db, resolver
):
    """The trap, and the direction a partial fix loses — at every write site.

    Both instruments at once. A fix that nests the writes but never unwinds
    them satisfies every caller-side assertion in this section while leaving
    the row it was half way through writing pending in the caller's Session —
    the caller's own commit, the very commit AC3 exists to protect, then
    inserts it. Data loss traded for data corruption, so the assertions run in
    both directions: the caller's work survives *and* nothing of ours does.

    Parametrized over all three sites deliberately. Mutation showed that with
    only the `miss` case, a fix that nests `_store` and leaves `_serve_hit` or
    `_revalidated` on a bare `flush` survives the whole file: the other tests
    reach those two sites only on the success path, where a flat write and a
    released SAVEPOINT are indistinguishable. A bare `flush` that raises
    deactivates the caller's transaction, so the damage is the same denial of
    data 616 exists to stop — the caller's later `commit()` cannot land.
    """
    url = "https://docs.example/guide"
    if site == "hit":
        _add_row(session, url)
        _, transport = _transport(_refuse)
    elif site == "revalidated":
        _add_row(session, url, age_seconds=_stale_age(), etag='"v1"')
        _, transport = _transport(lambda _r: httpx.Response(304, headers={"ETag": '"v2"'}))
    else:
        _, transport = _transport(_ok_html)

    before = _durable_row(isolated_db, url)
    _pending_caller_work(session)
    outer = session.get_transaction()

    with _write_fails(session, url):
        payload = reference_cache.fetch_reference(session, url, transport=transport)

    _assert_kind(payload, ReferenceFetchError.INTERNAL_ERROR)
    _assert_self_classifying(payload)
    assert CALLER_URL not in _durable_urls(isolated_db), "the caller's work was committed for it"
    assert _get_row(session, CALLER_URL) is not None, "the caller's work was rolled back"
    assert session.get_transaction() is outer, "the caller's transaction was ended"

    session.commit()
    assert CALLER_URL in _durable_urls(isolated_db), "the caller could no longer commit its work"
    assert _durable_row(isolated_db, url) == before, "a write that failed was committed anyway"


def _seed_committed(engine, url, **fields):
    """Put a committed row in the database without touching the caller's Session.

    `_add_row` commits on the Session it is given, which would emit the very
    `BEGIN` the tests below exist to do without.
    """
    with Session(engine) as other:
        _add_row(other, url, **fields)


def _fresh_caller_transport(site, isolated_db):
    """`(transport, url)` for one write site, with a caller that has only read."""
    url = "https://docs.example/guide"
    if site == "hit":
        _seed_committed(isolated_db, url)
        _, transport = _transport(_refuse)
    elif site == "revalidated":
        _seed_committed(isolated_db, url, age_seconds=_stale_age(), etag='"v1"')
        _, transport = _transport(lambda _r: httpx.Response(304, headers={"ETag": '"v2"'}))
    else:
        _, transport = _transport(_ok_html)
    return transport, url


@pytest.mark.parametrize("site", ["miss", "hit", "revalidated"])
def test_a_read_only_callers_rollback_cannot_destroy_the_cache_write(site, isolated_db, resolver):
    """616 AC3 in the caller shape `get_session()` actually hands every request.

    This test asserted the opposite until 638, and the reason is worth keeping.
    616 fixed "the service commits the caller's Session" by nesting the cache
    write in the caller's transaction, and chose "a caller rollback discards
    the service's write" as its instrument. That instrument pinned the
    mechanism rather than the criterion: it is also satisfied by a service that
    holds the caller's connection — and a write lock on the whole database
    file — from its first write until the caller ends the transaction, which is
    the defect 638 exists to remove.

    So for this caller shape the contract is now the other way round. A caller
    that has only read has no work of its own for the service to commit, which
    makes 616's criterion vacuous here; the tests that still discriminate on it
    are the ones below that give the caller pending work. What is left to pin
    for a read-only caller is that the service touched its Session at all: the
    write goes to a connection of the service's own, so it is durable when it
    returns and a later caller rollback has nothing of ours to discard.

    Durability alone does not pin that, and the second assertion is why. A
    service that nests a SAVEPOINT in a read-only caller reaches the same
    durable row by the route 616 rejected: with no `BEGIN` under it the
    SAVEPOINT *is* the outermost transaction, so `RELEASE` commits the caller's
    Session, and a test that asked only "did the row survive" passes that
    implementation too. The caller's `SessionTransaction` separates them — both
    a commit and a rollback end it and autobegin another, so an unchanged
    object means the service never ended anything of the caller's.
    """
    transport, url = _fresh_caller_transport(site, isolated_db)

    with Session(isolated_db) as caller:
        assert not caller.in_transaction(), "the caller must start the way get_session() hands it"
        _get_row(caller, url)  # the caller's own read, as any request makes first
        outer = caller.get_transaction()
        assert outer is not None, "the caller's read should have begun a transaction to protect"
        payload = reference_cache.fetch_reference(caller, url, transport=transport)
        assert caller.get_transaction() is outer, (
            "the service ended the caller's transaction — it wrote through the caller's Session"
        )
        _assert_self_classifying(payload)
        assert payload.error == "", payload
        assert _get_row(caller, url) is not None, "the service's write never landed at all"
        caller.rollback()

    assert _durable_row(isolated_db, url) is not None, (
        "the caller's rollback discarded the service's write — it wrote through the caller"
    )


@pytest.mark.parametrize("site", ["miss", "hit", "revalidated"])
def test_a_get_session_shaped_caller_keeps_its_page_after_close(site, isolated_db, resolver):
    """636. The lifecycle `get_session()` actually performs: close, never commit.

    `db/session.py`'s dependency is `with Session(engine) as session: yield
    session`, and `Session.__exit__` closes, which rolls back. It never commits.
    So for the shape every FastAPI request arrives in, "the caller will commit"
    was never true — and while the cache wrote through the caller's connection,
    every fetched page was discarded at request end. The cache was inert rather
    than broken, and would have re-fetched every URL on every request: the
    request-amplification shape 626 exists for.

    The two neighbouring tests are not this one. One rolls back explicitly and
    one commits explicitly; both are callers that made a decision. This caller
    makes none — it simply falls out of a `with` block, which is the only thing
    the dependency ever does, and is the lifecycle no test covered.

    Fails if the write is discarded at close: the row is read back through a
    Session that never saw the fetch.

    The assertion is that the durable row *changed*, not that one exists. Two of
    these three sites seed a committed row before the fetch — `hit` and
    `revalidated` need something to hit — so "a row is there afterwards" is true
    whether or not our write landed, and pins nothing for them. What each site
    actually writes does differ from what it started with: an insert where there
    was nothing, an incremented `hit_count`, a reset `fetched_at` and a new
    ETag. Comparing the whole row before and against after catches all three
    with one assertion, and caught this test being vacuous for two of them.
    """
    transport, url = _fresh_caller_transport(site, isolated_db)
    before = _durable_row(isolated_db, url)

    with Session(isolated_db) as caller:
        assert not caller.in_transaction(), "the caller must start the way get_session() hands it"
        payload = reference_cache.fetch_reference(caller, url, transport=transport)
        _assert_self_classifying(payload)
        assert payload.error == "", payload
    # __exit__ closed it. No commit was made, and none should have been needed.

    after = _durable_row(isolated_db, url)
    assert after is not None, "nothing was cached at all"
    assert after != before, (
        "the write was discarded when the caller's Session closed — the cache is inert "
        f"for the one caller shape get_session() produces (row unchanged: {before})"
    )


@pytest.mark.parametrize("site", ["miss", "hit", "revalidated"])
def test_a_read_only_caller_that_commits_still_gets_the_cache_write(site, isolated_db, resolver):
    """The other direction, so the test above cannot be passed by writing nothing.

    Same caller shape; the caller commits instead of rolling back, and the
    service's write must then be durable.
    """
    transport, url = _fresh_caller_transport(site, isolated_db)
    before = _durable_row(isolated_db, url)

    with Session(isolated_db) as caller:
        payload = reference_cache.fetch_reference(caller, url, transport=transport)
        _assert_self_classifying(payload)
        caller.commit()

    after = _durable_row(isolated_db, url)
    assert after is not None, "the caller committed and the cache row is not there"
    assert after != before, "the caller committed and nothing of the service's write landed"


def test_a_concurrent_insert_of_the_same_url_is_absorbed(session, isolated_db, resolver):
    """The `IntegrityError` race, which moves from `commit` to `flush`.

    Ticket 173's handler exists for a real window: another caller inserts the
    same URL between our read and our write, and the unique index is the
    arbiter. Today the violation surfaces from `commit`; under a SAVEPOINT it
    surfaces from `flush`, and the recovery is to unwind the nested
    transaction and re-read — not to roll the caller back, which is what makes
    this the delicate site. Ticket 173's AC3 stays in force: nothing escapes.

    The window is opened by making the first `_find_row` report nothing while
    the row is really there. Two connections cannot stage this against SQLite:
    the caller's pending write holds the write lock, so the competing commit
    dies with "database is locked" instead of racing.
    """
    url = "https://docs.example/guide"
    winner_markdown = "# winner\n\nthe row the unique index kept."
    _add_row(session, url, markdown=winner_markdown, age_seconds=_stale_age())
    real_find_row = reference_cache._find_row
    reads: list[str] = []

    # Blind for both reads that precede the write — the one that decides there
    # is nothing cached, and the one `_store` makes inside its own write
    # transaction — because the insert this stages is the one that lands after
    # both. The recovery re-read afterwards is real, and is what must find the
    # row the unique index kept.
    def find_row(session_arg, url_arg):
        reads.append(url_arg)
        return None if len(reads) <= 2 else real_find_row(session_arg, url_arg)

    _pending_caller_work(session)
    outer = session.get_transaction()
    _, transport = _transport(_ok_html)

    with patch.object(reference_cache, "_find_row", side_effect=find_row):
        payload = reference_cache.fetch_reference(session, url, transport=transport)

    _assert_self_classifying(payload)
    assert ReferenceFetchError.INTERNAL_ERROR.value not in payload.error, payload
    assert payload.markdown == winner_markdown, "the loser did not re-read the kept row"
    assert session.get_transaction() is outer, "the caller's transaction was ended"
    assert _get_row(session, CALLER_URL) is not None, "the caller's work was rolled back"

    session.commit()
    with Session(isolated_db) as other:
        kept = other.exec(select(ReferencePage).where(ReferencePage.url == url)).all()
    assert len(kept) == 1
    assert kept[0].content_markdown == winner_markdown


# --------------------------------------------------------------------------
# AC4 — no engine binding at import
# --------------------------------------------------------------------------


def test_module_binds_no_engine_at_import():
    source = Path(reference_cache.__file__).read_text(encoding="utf-8")
    targets = {
        name.id
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)  # py-org: allow-isinstance
        for name in node.targets
        if isinstance(name, ast.Name)  # py-org: allow-isinstance
    }
    assert "engine" not in targets
    assert not hasattr(reference_cache, "engine")


def test_conftest_engine_bindings_do_not_mention_this_module():
    conftest = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    bindings = next(
        node.value
        for node in ast.parse(conftest).body
        if isinstance(node, ast.Assign)  # py-org: allow-isinstance
        and any(
            isinstance(t, ast.Name) and t.id == "_ENGINE_BINDINGS" for t in node.targets
        )  # py-org: allow-isinstance
    )
    names = [element.value for element in bindings.elts]
    assert not [name for name in names if "reference_cache" in name]
