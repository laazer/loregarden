"""DevDocs search over the reference cache.

Every byte here arrives through `reference_cache.fetch_cached_text`, so this
module imports no HTTP client and binds no engine. That is not tidiness: the
cache is where the SSRF guard, the size cap, the redirect handling and the
transaction discipline live, and a second fetch path would have to re-earn all
of them. It also means a catalog is paid for once per TTL across every agent
and every run, which is the point of the feature.

Two documents back a search. The **catalog** (`docs.json`) lists every docset —
slug, name, release. The **index** (`<slug>/index.json`) lists that docset's
entries — name, path, type. Both are cached under their own `ReferencePageKind`
so a later reader can tell them apart without sniffing the body.

Nothing here raises. Every failure is a payload naming its own `error_kind`,
because the caller is an agent that has to decide what to do next, and an
exception across an MCP boundary is a stack trace it cannot act on.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from loregarden.models.domain.enums import DevDocsError, ReferencePageKind
from loregarden.services.reference_cache import ReferencePayload, fetch_cached_text
from pydantic import BaseModel
from sqlmodel import Session

logger = logging.getLogger(__name__)

CATALOG_URL = "https://devdocs.io/docs.json"
DOCUMENTS_BASE = "https://documents.devdocs.io"

DEFAULT_LIMIT = 10
MAX_LIMIT = 50

#: How many docsets a "did you mean" list may carry. Long enough to contain the
#: answer for a vague needle, short enough that an agent reads it rather than
#: pasting it back as a new query.
MAX_SUGGESTIONS = 20

_JSON_ACCEPT = ("application/json",)


class DocsetSuggestion(BaseModel):
    """One docset a caller might have meant."""

    slug: str
    name: str
    release: str = ""
    alias: str = ""


class SearchResult(BaseModel):
    """One index entry, with the URL that reads it."""

    name: str
    type: str
    docset: str
    url: str


class SearchReferencePayload(BaseModel):
    """What `search_reference` returns, success or failure.

    A model rather than a bare dict for the reason 607 established on the cache
    itself: the next caller is an MCP tool that has to serialize this, and a
    payload it cannot `json.dumps` is one every consumer works around
    separately. `model_dump(mode="json")` is the contract.

    `error_kind` is the discriminator and `error` is the prose. Both are present
    on success too — empty — so a caller reads the same shape either way rather
    than testing which keys exist.
    """

    query: str
    docset: str = ""
    results: list[SearchResult] = []
    #: Before the limit, so a caller can tell "these are all of them" from
    #: "these are the first ten of four hundred" and narrow instead of paging.
    total_matches: int = 0
    suggestions: list[DocsetSuggestion] = []
    catalog_cache: str = ""
    index_cache: str = ""
    error_kind: DevDocsError | None = None
    error: str = ""


def _failure(
    query: str,
    kind: DevDocsError,
    error: str,
    *,
    docset: str = "",
    suggestions: list[DocsetSuggestion] | None = None,
    catalog_cache: str = "",
) -> SearchReferencePayload:
    return SearchReferencePayload(
        query=query,
        docset=docset,
        suggestions=suggestions or [],
        catalog_cache=catalog_cache,
        error_kind=kind,
        error=error,
    )


def _load_json(
    session: Session,
    url: str,
    *,
    kind: ReferencePageKind,
    transport: Any = None,
) -> tuple[Any, ReferencePayload]:
    """Fetch a JSON document through the cache and parse it.

    Returns `(parsed, payload)`; `parsed` is None when the fetch failed or the
    body was not JSON. The payload comes back either way so the caller can
    report the cache outcome and the cache's own reason.
    """
    payload = fetch_cached_text(
        session, url, kind=kind, accept_types=_JSON_ACCEPT, transport=transport
    )
    if payload.error:
        return None, payload
    try:
        return json.loads(payload.markdown), payload
    except json.JSONDecodeError as exc:
        # Not cached as a failure: `fetch_cached_text` already stored the body,
        # and a malformed catalog kept for a TTL is a broken feature for a TTL.
        # The next call re-fetches because this one refuses to use what it got.
        logger.warning("devdocs %s at %s is not JSON: %s", kind.value, url, exc)
        return None, payload


def _catalog_entries(raw: Any) -> list[dict[str, Any]] | None:
    """DevDocs' catalog is a list of objects. Anything else is not a catalog."""
    if not isinstance(raw, list):  # py-org: allow-isinstance
        return None
    return [entry for entry in raw if isinstance(entry, dict)]  # py-org: allow-isinstance


def _index_entries(raw: Any) -> list[dict[str, Any]] | None:
    """An index is `{"entries": [...]}`; a bare list is accepted too.

    DevDocs serves the object form. The list form costs one branch and means a
    mirror that flattened it does not read as a broken docset.
    """
    if isinstance(raw, dict):  # py-org: allow-isinstance
        raw = raw.get("entries")
    if not isinstance(raw, list):  # py-org: allow-isinstance
        return None
    return [entry for entry in raw if isinstance(entry, dict)]  # py-org: allow-isinstance


def _suggestions_for(
    entries: list[dict[str, Any]], needle: str, *, limit: int = MAX_SUGGESTIONS
) -> list[DocsetSuggestion]:
    """Docsets whose slug, name or alias contains `needle`, case-insensitively.

    An empty needle offers the head of the catalog rather than nothing: a caller
    who omitted the docset entirely needs somewhere to start.
    """
    lowered = needle.strip().lower()
    found: list[DocsetSuggestion] = []
    for entry in entries:
        slug = str(entry.get("slug", ""))
        name = str(entry.get("name", ""))
        alias = str(entry.get("alias", ""))
        if not lowered or any(lowered in field.lower() for field in (slug, name, alias)):
            found.append(
                DocsetSuggestion(
                    slug=slug, name=name, release=str(entry.get("release", "")), alias=alias
                )
            )
        if len(found) >= limit:
            break
    return found


def _resolve_docset(
    entries: list[dict[str, Any]], requested: str
) -> tuple[str, list[DocsetSuggestion]]:
    """Turn what the caller typed into a slug.

    Three tiers, most specific first: an exact slug (`python~3.12`), an exact
    alias (`ng`), then a case-insensitive name. Ordering matters — a name that
    happens to equal another docset's slug must not win over that slug.

    Returns `(slug, candidates)`. A slug with no candidates resolved; no slug
    with candidates is ambiguous; neither is unresolved.
    """
    wanted = requested.strip()
    lowered = wanted.lower()

    for entry in entries:
        if str(entry.get("slug", "")) == wanted:
            return wanted, []
    for entry in entries:
        if str(entry.get("alias", "")) == wanted:
            return str(entry.get("slug", "")), []

    by_name = [entry for entry in entries if str(entry.get("name", "")).lower() == lowered]
    if len(by_name) == 1:
        return str(by_name[0].get("slug", "")), []
    if len(by_name) > 1:
        # Versioned docsets share a name — "Python" is python~3.12 and
        # python~3.11. Picking one would silently answer from the wrong release.
        return "", _suggestions_for(by_name, "")
    return "", []


def _rank_entries(entries: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Exact name, then prefix, then substring; ties by shorter name, then order.

    Shorter-name tiebreak because an index contains both `Array` and
    `Array.prototype.flatMap`, and a search for "array" means the first.
    Original order is the final tiebreak so the result is stable rather than
    dependent on the sort's implementation.
    """
    lowered = query.strip().lower()
    if not lowered:
        return []

    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for position, entry in enumerate(entries):
        name = str(entry.get("name", ""))
        haystack = name.lower()
        if haystack == lowered:
            tier = 0
        elif haystack.startswith(lowered):
            tier = 1
        elif lowered in haystack:
            tier = 2
        else:
            continue
        ranked.append((tier, len(name), position, entry))

    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in ranked]


def _entry_url(slug: str, path: str) -> str:
    """Build the document URL, keeping any anchor after the `.html`.

    DevDocs index paths carry fragments — `dom/event#properties` — and `.html`
    belongs to the path, not the fragment. Appending it blindly would produce
    `dom/event#properties.html`, which is a URL that fetches the wrong document
    and loses the anchor.
    """
    document, _, anchor = path.partition("#")
    url = f"{DOCUMENTS_BASE}/{slug}/{document}.html"
    return f"{url}#{anchor}" if anchor else url


def search_reference(
    session: Session,
    query: str,
    *,
    docset: str = "",
    limit: int = DEFAULT_LIMIT,
    transport: Any = None,
) -> SearchReferencePayload:
    """Search one DevDocs docset. Never raises.

    `transport` is the cache's own test seam, threaded through so a test can be
    hermetic without patching a module attribute — the mistake that makes a mock
    miss a name the callee bound at import.
    """
    capped = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))

    raw_catalog, catalog_payload = _load_json(
        session, CATALOG_URL, kind=ReferencePageKind.CATALOG, transport=transport
    )
    if catalog_payload.error:
        return _failure(
            query,
            DevDocsError.CATALOG_UNAVAILABLE,
            f"could not read the DevDocs catalog: {catalog_payload.error}",
            docset=docset,
        )
    entries = _catalog_entries(raw_catalog)
    if entries is None:
        return _failure(
            query,
            DevDocsError.CATALOG_INVALID,
            f"the DevDocs catalog at {CATALOG_URL} was not a list of docsets",
            docset=docset,
        )

    catalog_cache = catalog_payload.cache.value

    if not docset.strip():
        return _failure(
            query,
            DevDocsError.DOCSET_REQUIRED,
            "name a docset to search; the query alone cannot pick one",
            suggestions=_suggestions_for(entries, query),
            catalog_cache=catalog_cache,
        )

    slug, candidates = _resolve_docset(entries, docset)
    if not slug and candidates:
        return _failure(
            query,
            DevDocsError.DOCSET_AMBIGUOUS,
            f"{docset!r} matches more than one docset; name one by slug",
            docset=docset,
            suggestions=candidates,
            catalog_cache=catalog_cache,
        )
    if not slug:
        return _failure(
            query,
            DevDocsError.DOCSET_UNRESOLVED,
            f"no docset matches {docset!r}",
            docset=docset,
            suggestions=_suggestions_for(entries, docset),
            catalog_cache=catalog_cache,
        )

    raw_index, index_payload = _load_json(
        session,
        f"{DOCUMENTS_BASE}/{slug}/index.json",
        kind=ReferencePageKind.INDEX,
        transport=transport,
    )
    if index_payload.error:
        return _failure(
            query,
            DevDocsError.INDEX_UNAVAILABLE,
            f"could not read the index for {slug}: {index_payload.error}",
            docset=slug,
            catalog_cache=catalog_cache,
        )
    index = _index_entries(raw_index)
    if index is None:
        return _failure(
            query,
            DevDocsError.INDEX_INVALID,
            f"the index for {slug} was not a list of entries",
            docset=slug,
            catalog_cache=catalog_cache,
        )

    matches = _rank_entries(index, query)
    return SearchReferencePayload(
        query=query,
        docset=slug,
        results=[
            SearchResult(
                name=str(entry.get("name", "")),
                type=str(entry.get("type", "")),
                docset=slug,
                url=_entry_url(slug, str(entry.get("path", ""))),
            )
            for entry in matches[:capped]
        ],
        total_matches=len(matches),
        catalog_cache=catalog_cache,
        index_cache=index_payload.cache.value,
    )
