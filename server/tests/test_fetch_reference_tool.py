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

Contract this file pins, beyond the ticket description:

- `error` on a failure payload **contains** the failure kind's enum value, so a
  reason may add detail ("blocked: 169.254.169.254 is not global").
- A failure payload with no cached copy to serve reports `cache = MISS`.
- `fetch_cached_text` returns the same payload shape as `fetch_reference`; the
  raw (unextracted) body arrives in `markdown`.
"""

import ast
import socket
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
    utcnow,
)
from loregarden.models.domain.tables import ReferencePage
from loregarden.services import reference_cache
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
    assert kind.value in payload["error"], payload


def _assert_self_classifying(payload) -> None:
    """Every payload, success or failure, carries both discriminators."""
    assert "error" in payload, payload
    assert "cache" in payload, payload
    if payload["error"] == "":
        assert payload["markdown"] != "", "a success payload may never carry empty markdown"


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
    assert payload["error"] == ""


def test_missing_location_on_a_redirect_is_an_error_not_a_crash(session, resolver):
    _, transport = _transport(lambda _r: httpx.Response(302))
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/a", transport=transport
    )
    assert payload["error"] != ""
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
    assert len(requests) <= 3, "the cap did not bound the loop"


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
    assert payload["error"] == ""


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
    assert payload["cache"] == ReferenceCacheOutcome.MISS
    assert payload["error"] == ""
    assert "map()" in payload["markdown"]
    assert payload["title"] == "Array.prototype.map"

    row = _get_row(session, "https://docs.example/guide")
    assert row is not None
    assert row.content_markdown == payload["markdown"]
    assert row.content_chars == len(payload["markdown"])


def test_fresh_row_is_a_hit_and_skips_the_network(session, resolver):
    _add_row(session, "https://docs.example/guide")
    _, transport = _transport(_refuse)

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert payload["cache"] == ReferenceCacheOutcome.HIT
    assert payload["markdown"] == "# cached\n\nstored body text."
    assert payload["error"] == ""
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
    assert payload["cache"] == ReferenceCacheOutcome.REVALIDATED
    assert payload["error"] == ""
    assert payload["markdown"] == before_body

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

    assert payload["cache"] == ReferenceCacheOutcome.MISS
    assert "map()" in payload["markdown"]
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

    assert payload["cache"] == ReferenceCacheOutcome.STALE_ERROR
    assert payload["markdown"] == "# cached\n\nstored body text."
    assert payload["error"] != ""


def test_refresh_forces_a_fetch_past_a_fresh_row(session, resolver):
    _add_row(session, "https://docs.example/guide")
    requests, transport = _transport(_ok_html)

    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", refresh=True, transport=transport
    )

    assert len(requests) == 1
    assert "map()" in payload["markdown"]


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

    assert len(capped["markdown"]) <= 40
    assert capped["truncated"] is True
    assert capped["total_chars"] == full["total_chars"]
    assert full["truncated"] is False
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
    assert payload["cache"] == ReferenceCacheOutcome.MISS
    assert _get_row(session, "https://docs.example/guide") is None


def test_an_http_error_status_is_reported_and_not_cached(session, resolver):
    _, transport = _transport(lambda _r: httpx.Response(404, headers=HTML_HEADERS, content=b"no"))
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )

    assert "404" in payload["error"]
    _assert_self_classifying(payload)
    assert _get_row(session, "https://docs.example/guide") is None


def test_a_timeout_is_an_error_payload(session, resolver):
    def route(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _, transport = _transport(route)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )
    assert payload["error"] != ""
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
    _, transport = _transport(_ok_html)
    payload = reference_cache.fetch_reference(
        session, "https://docs.example/guide", transport=transport
    )
    assert SUCCESS_KEYS <= set(payload)


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

    assert stale["markdown"] != "" and stale["error"] != ""
    assert stale["cache"] == ReferenceCacheOutcome.STALE_ERROR
    assert hard["markdown"] == "" and hard["error"] != ""
    assert hard["cache"] != ReferenceCacheOutcome.STALE_ERROR


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

    assert payload["error"] == ""
    assert payload["markdown"] == body.decode()
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
    assert payload["cache"] == ReferenceCacheOutcome.HIT
    assert payload["markdown"] == '{"entries": []}'


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

    assert second["cache"] == ReferenceCacheOutcome.HIT
    assert len(requests) == 1
    assert len(session.exec(select(ReferencePage)).all()) == 1


def test_fragment_shim_keeps_a_heading_that_bare_extraction_drops():
    """DevDocs pages start at `<h1>` with no `<body>`. Extracted raw, trafilatura
    silently drops the heading; the shim plus `favor_recall` keeps it. This is
    the observable difference the shim exists for."""
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
