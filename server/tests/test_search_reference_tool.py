"""DevDocs search over the reference cache (176).

Every test drives the real `search_reference` against a `MockTransport`, passed
through the cache's own `transport` seam. Nothing patches a module attribute:
`devdocs` binds `fetch_cached_text` at import, so patching
`reference_cache.fetch_cached_text` would leave the service calling the original
and the test would pass while exercising nothing — the failure mode 174 hit for
real.

Hosts are public IP literals so the SSRF guard judges them on the literal tier
and no resolver patch is needed: zero DNS, zero network.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from loregarden.config import settings
from loregarden.mcp import devdocs_tool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain.enums import (
    DevDocsError,
    ReferenceCacheOutcome,
    ReferenceFetchError,
    ReferencePageKind,
)
from loregarden.models.domain.tables import ReferencePage
from loregarden.services import devdocs
from loregarden.services.devdocs import (
    MAX_LIMIT,
    _entry_url,
    _rank_entries,
    search_reference,
)
from sqlmodel import Session, select

CATALOG = [
    {"name": "Python", "slug": "python~3.12", "release": "3.12"},
    {"name": "Python", "slug": "python~3.11", "release": "3.11"},
    {"name": "Angular", "slug": "angular", "alias": "ng", "release": "17"},
    {"name": "JavaScript", "slug": "javascript", "release": ""},
]

INDEX = {
    "entries": [
        {"name": "Array", "path": "global_objects/array", "type": "Standard objects"},
        {"name": "Array.prototype.map", "path": "global_objects/array/map", "type": "Methods"},
        {"name": "arrayBuffer", "path": "api/arraybuffer", "type": "API"},
        {"name": "Event", "path": "dom/event#properties", "type": "DOM"},
    ]
}


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _transport(catalog=CATALOG, index=INDEX, *, catalog_body=None, index_body=None):
    """Serve the catalog and any index, counting requests per URL."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        headers = {"content-type": "application/json"}
        if str(request.url) == devdocs.CATALOG_URL:
            body = catalog_body if catalog_body is not None else json.dumps(catalog)
        else:
            body = index_body if index_body is not None else json.dumps(index)
        return httpx.Response(200, headers=headers, content=body.encode())

    return seen, httpx.MockTransport(handle)


def _search(session, query, **kwargs):
    seen, transport = _transport(
        **{
            k: v
            for k, v in kwargs.items()
            if k in {"catalog", "index", "catalog_body", "index_body"}
        }
    )
    rest = {
        k: v
        for k, v in kwargs.items()
        if k not in {"catalog", "index", "catalog_body", "index_body"}
    }
    return search_reference(session, query, transport=transport, **rest), seen


# --------------------------------------------------------------------------
# AC1 — docset resolution, every tier
# --------------------------------------------------------------------------


def test_an_exact_versioned_slug_resolves(session):
    payload, _ = _search(session, "Array", docset="python~3.12")
    assert payload.error_kind is None, payload.error
    assert payload.docset == "python~3.12"


def test_an_alias_resolves_to_its_slug(session):
    payload, _ = _search(session, "Array", docset="ng")
    assert payload.error_kind is None, payload.error
    assert payload.docset == "angular"


def test_a_name_resolves_case_insensitively(session):
    payload, _ = _search(session, "Array", docset="jAvAsCrIpT")
    assert payload.error_kind is None, payload.error
    assert payload.docset == "javascript"


def test_a_name_matching_two_docsets_is_ambiguous_with_candidates(session):
    """Two Python releases share a name. Picking one answers from the wrong release."""
    payload, _ = _search(session, "Array", docset="Python")

    assert payload.error_kind == DevDocsError.DOCSET_AMBIGUOUS
    assert {s.slug for s in payload.suggestions} == {"python~3.12", "python~3.11"}
    assert payload.results == []


def test_an_unknown_docset_suggests_near_misses(session):
    payload, _ = _search(session, "Array", docset="pyth")

    assert payload.error_kind == DevDocsError.DOCSET_UNRESOLVED
    assert {s.slug for s in payload.suggestions} == {"python~3.12", "python~3.11"}


def test_an_omitted_docset_is_refused_with_suggestions_for_the_query(session):
    payload, _ = _search(session, "angular")

    assert payload.error_kind == DevDocsError.DOCSET_REQUIRED
    assert "angular" in {s.slug for s in payload.suggestions}


def test_a_slug_beats_a_name_that_looks_like_one(session):
    """Ordering, not just membership: an exact slug must win over a name match.

    A resolver that checked names first would answer `javascript` here — the
    right answer for the wrong reason, and wrong the moment a name collides
    with another docset's slug.
    """
    catalog = [
        {"name": "javascript", "slug": "confusing"},
        {"name": "JavaScript", "slug": "javascript"},
    ]
    payload, _ = _search(session, "Array", docset="javascript", catalog=catalog)

    assert payload.docset == "javascript"


# --------------------------------------------------------------------------
# AC2 — ranking and URL building
# --------------------------------------------------------------------------


def test_ranking_is_exact_then_prefix_then_substring(session):
    ranked = _rank_entries(INDEX["entries"], "array")
    assert [e["name"] for e in ranked] == ["Array", "arrayBuffer", "Array.prototype.map"]


def test_ties_break_on_the_shorter_name(session):
    """An index holds both `Array` and `Array.prototype.flatMap`; "array" means the first."""
    entries = [
        {"name": "Array.prototype.flatMap", "path": "a"},
        {"name": "Arrays", "path": "b"},
    ]
    assert [e["name"] for e in _rank_entries(entries, "array")] == [
        "Arrays",
        "Array.prototype.flatMap",
    ]


def test_a_query_matching_nothing_returns_no_results_not_an_error(session):
    payload, _ = _search(session, "zzzznotathing", docset="javascript")
    assert payload.error_kind is None, payload.error
    assert payload.results == []
    assert payload.total_matches == 0


def test_an_anchor_survives_the_html_suffix():
    """`.html` belongs to the path, not the fragment."""
    assert _entry_url("dom", "dom/event#properties") == (
        "https://documents.devdocs.io/dom/dom/event.html#properties"
    )


def test_a_path_without_an_anchor_is_unchanged():
    assert _entry_url("js", "global_objects/array") == (
        "https://documents.devdocs.io/js/global_objects/array.html"
    )


def test_the_limit_is_capped_but_total_matches_is_not(session):
    """A caller has to tell "all of them" from "the first ten of four hundred"."""
    entries = [{"name": f"array{n}", "path": f"p{n}"} for n in range(100)]
    payload, _ = _search(
        session, "array", docset="javascript", index={"entries": entries}, limit=1000
    )

    assert len(payload.results) == MAX_LIMIT
    assert payload.total_matches == 100


# --------------------------------------------------------------------------
# AC3 — the documents land in the cache, under the right kind
# --------------------------------------------------------------------------


def test_the_catalog_is_fetched_once_and_served_from_cache(session):
    """The whole point of the feature: pay for the catalog once, not once per run."""
    seen, transport = _transport()

    first = search_reference(session, "Array", docset="javascript", transport=transport)
    second = search_reference(session, "Array", docset="javascript", transport=transport)

    assert first.catalog_cache == ReferenceCacheOutcome.MISS.value
    assert second.catalog_cache == ReferenceCacheOutcome.HIT.value
    assert seen.count(devdocs.CATALOG_URL) == 1, seen


def test_catalog_and_index_are_stored_under_their_own_kinds(session):
    _, transport = _transport()
    search_reference(session, "Array", docset="javascript", transport=transport)

    kinds = {row.url: row.kind for row in session.exec(select(ReferencePage)).all()}
    assert kinds[devdocs.CATALOG_URL] == ReferencePageKind.CATALOG
    index_url = f"{devdocs.DOCUMENTS_BASE}/javascript/index.json"
    assert kinds[index_url] == ReferencePageKind.INDEX


def test_each_docset_caches_its_own_index(session):
    seen, transport = _transport()
    search_reference(session, "Array", docset="javascript", transport=transport)
    search_reference(session, "Array", docset="angular", transport=transport)

    assert seen.count(f"{devdocs.DOCUMENTS_BASE}/javascript/index.json") == 1
    assert seen.count(f"{devdocs.DOCUMENTS_BASE}/angular/index.json") == 1
    assert seen.count(devdocs.CATALOG_URL) == 1, "the catalog was re-fetched per docset"


# --------------------------------------------------------------------------
# AC4 — never raises, and every failure names its kind
# --------------------------------------------------------------------------


def test_a_transport_failure_is_a_structured_payload(session):
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    payload = search_reference(
        session, "Array", docset="javascript", transport=httpx.MockTransport(explode)
    )

    assert payload.error_kind == DevDocsError.CATALOG_UNAVAILABLE
    assert payload.error, "an unavailable catalog must say why"


def test_a_malformed_catalog_is_reported_and_not_trusted(session):
    payload, _ = _search(session, "Array", docset="javascript", catalog_body="{not json")
    assert payload.error_kind == DevDocsError.CATALOG_INVALID


def test_a_catalog_that_is_json_but_not_a_list_is_invalid(session):
    """Valid JSON of the wrong shape is the case a bare `json.loads` guard misses."""
    payload, _ = _search(session, "Array", docset="javascript", catalog_body='{"docs": []}')
    assert payload.error_kind == DevDocsError.CATALOG_INVALID


def test_a_malformed_index_is_reported_after_the_docset_resolved(session):
    payload, _ = _search(session, "Array", docset="javascript", index_body="[[[")
    assert payload.error_kind == DevDocsError.INDEX_INVALID
    assert payload.docset == "javascript", "the docset resolved before the index failed"


def test_a_bare_list_index_is_accepted(session):
    """A mirror that flattened `{"entries": [...]}` is not a broken docset."""
    payload, _ = _search(
        session, "Array", docset="javascript", index_body=json.dumps(INDEX["entries"])
    )
    assert payload.error_kind is None, payload.error
    assert payload.results


def test_every_failure_carries_a_kind_and_a_message(session):
    """AC4 swept, so a new error path cannot ship as prose with no discriminator.

    The cache is emptied between cases. Without that the malformed-body cases
    prove nothing: the first call caches a *good* catalog, and every later call
    is served that hit instead of the broken body the case is about. The first
    draft of this test passed the two shape errors for exactly that reason.
    """

    def clear_cache():
        for row in session.exec(select(ReferencePage)).all():
            session.delete(row)
        session.commit()

    cases = []
    for build in (
        lambda: _search(session, "Array")[0],
        lambda: _search(session, "Array", docset="nope")[0],
        lambda: _search(session, "Array", docset="Python")[0],
        lambda: _search(session, "Array", docset="javascript", catalog_body="{")[0],
        lambda: _search(session, "Array", docset="javascript", index_body="{")[0],
    ):
        clear_cache()
        cases.append(build())

    for payload in cases:
        assert payload.error_kind is not None, payload
        assert payload.error, payload
        assert payload.results == [], payload


def test_the_payload_survives_json_dumps(session):
    """177 wires this to MCP; a payload it cannot serialize is one it cannot send."""
    payload, _ = _search(session, "Array", docset="javascript")
    restored = json.loads(json.dumps(payload.model_dump(mode="json")))
    assert restored["docset"] == "javascript"
    assert restored["error_kind"] is None


def test_a_stale_index_revalidates_with_304_instead_of_refetching_the_body(session, isolated_db):
    """AC3's other half: a stale copy is re-checked, not re-downloaded.

    An index is large and changes rarely, so the TTL expiring should cost a
    conditional request rather than the whole body. `revalidated` is the
    outcome that says the stored body stood and only its age reset — distinct
    from `hit` (never asked) and `miss` (fetched anew).
    """
    _, transport = _transport()
    search_reference(session, "Array", docset="javascript", transport=transport)

    index_url = f"{devdocs.DOCUMENTS_BASE}/javascript/index.json"
    row = session.exec(select(ReferencePage).where(ReferencePage.url == index_url)).one()
    row.fetched_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.reference_cache_ttl_seconds + 60
    )
    row.etag = '"v1"'
    session.add(row)
    session.commit()

    bodies_served: list[str] = []

    def conditional(request: httpx.Request) -> httpx.Response:
        if str(request.url) == index_url:
            return httpx.Response(304, headers={"ETag": '"v1"'})
        bodies_served.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(CATALOG).encode(),
        )

    payload = search_reference(
        session, "Array", docset="javascript", transport=httpx.MockTransport(conditional)
    )

    assert payload.error_kind is None, payload.error
    assert payload.index_cache == ReferenceCacheOutcome.REVALIDATED.value
    assert index_url not in bodies_served, "the index body was re-downloaded"
    assert payload.results, "the stored index still answered the search"


# --------------------------------------------------------------------------
# 177 — the MCP surface, and the suite-wide network refusal
# --------------------------------------------------------------------------


def test_a_fetch_with_no_transport_is_refused_by_the_suite(session):
    """The autouse fixture, asserted rather than assumed.

    A conftest fixture nothing tests is one that can stop working silently —
    and this one failing open means tests start making real requests and still
    pass, just slowly. So: no transport, and the refusal must arrive.
    """
    payload = search_reference(session, "Array", docset="javascript")

    assert payload.error_kind == DevDocsError.CATALOG_UNAVAILABLE
    assert "refuses real network" in payload.error


def test_the_refusal_is_a_transport_error_not_an_internal_one(session):
    """Where the refusal is raised decides how it is classified.

    `httpx.Client(...)` is constructed outside the cache's narrow
    `except httpx.TimeoutException/HTTPError` handlers. A fixture that raised
    from the constructor would escape them and land on the module's outermost
    boundary as INTERNAL_ERROR — the suite's own refusal reported as a bug in
    the cache, on every test that forgot a transport.
    """
    payload = search_reference(session, "Array", docset="javascript")

    assert ReferenceFetchError.INTERNAL_ERROR.value not in payload.error, payload.error
    assert ReferenceFetchError.FETCH_ERROR.value in payload.error, payload.error


def test_the_mcp_tool_returns_parseable_json(session):
    _, transport = _transport()
    with patch.object(devdocs_tool, "search_reference", _with(transport)):
        raw = execute_tool(
            session,
            "loregarden_search_reference",
            {"query": "Array", "docset": "javascript"},
        )

    payload = json.loads(raw)
    assert payload["error_kind"] is None, payload
    assert payload["docset"] == "javascript"
    assert payload["results"], payload
    assert set(payload["results"][0]) == {"name", "type", "docset", "url"}
    assert payload["total_matches"] >= len(payload["results"])


def test_the_mcp_tool_reports_a_refused_docset_as_a_payload(session):
    """No transport, so the suite's refusal answers — still JSON, still no raise."""
    raw = execute_tool(session, "loregarden_search_reference", {"query": "Array"})

    payload = json.loads(raw)
    assert payload["error_kind"] == DevDocsError.CATALOG_UNAVAILABLE.value
    assert payload["results"] == []


def _with(transport):
    """Bind a transport into the handler's own reference to `search_reference`.

    Patched on `devdocs_tool`, not on `devdocs`: the handler binds the name at
    import, so patching the service module would leave it calling the original.
    """

    def call(session, query, *, docset="", limit=devdocs.DEFAULT_LIMIT):
        return devdocs.search_reference(
            session, query, docset=docset, limit=limit, transport=transport
        )

    return call


def test_the_suite_never_resolves_a_real_hostname():
    """The hermeticity guard covers DNS, not just HTTP.

    `_url_block_reason` calls `socket.getaddrinfo` as an SSRF check BEFORE any
    request is built, so patching only `httpx` left every test in this file
    making a real lookup for devdocs.io. That passed on a machine with working
    DNS and failed as 20 errors reading "devdocs.io did not resolve" on one
    without — a pre-push run, on a change that touched none of this.

    Tested by resolving a name that cannot exist: real DNS returns nothing for
    it, so an answer proves the stub is in force. Identity comparison does not
    work here — `reference_cache.socket` IS the stdlib module, so patching its
    attribute changes every reference to it at once.
    """
    from loregarden.services.reference_cache import _resolved_addresses

    assert _resolved_addresses("no-such-host.invalid", 443) is not None, (
        "the autouse fixture must stub name resolution; without it this suite "
        "depends on devdocs.io being reachable"
    )
