"""Fetch-through cache for reference documents, with the SSRF guard in front.

Every HTTP request either reference MCP tool makes flows through this module,
which is why the redirect loop is written by hand instead of handed to httpx:
`follow_redirects=True` would resolve a hop inside the client, where the SSRF
validator cannot see it, and a public page redirecting to `169.254.169.254` is
the canonical bypass. Each hop target is re-checked by the *same* validator,
DNS included — a cheaper re-check that only re-runs the string/`ipaddress` tier
blocks the metadata literal and still follows `302 -> evil.example` resolving
to 10.0.0.5.

Two ordering rules are load-bearing rather than incidental:

- `validate_reference_url` judges an IP *literal* before it resolves anything.
  Resolve first and the entire literal tier silently takes a dependency on DNS.
- `_fetch_once` judges the content type *before* it reads the body. Classifying
  afterwards means an unsupported type gets streamed in full first, and reports
  whatever the body did (`too_large`) rather than what it was.

Payloads are self-classifying, and that — not the absence of a traceback — is
what makes "never raises" honest. `error` is always present (`""` on success),
`cache` is present on failures too, no success payload carries empty markdown
(an empty extraction is `extraction_failed`), and `stale_error` is the single
deliberate case with both a body and an error.

The absence of a traceback is nevertheless structural rather than a list of
caught types. Enumerating types is what let a remote `charset=utf-9000` raise
`LookupError` straight out of both entry points. Now every path — validation,
fetch, decode, extraction, *and* persistence — runs inside
`_fetch_through_cache`, whose guard converts anything the narrow handlers below
it missed into an `internal_error` payload after logging the exception type and
the URL. `internal_error` is its own kind rather than another `fetch_error`
because the two ask for different responses: retry the URL, versus fix us.

Tests inject an `httpx.MockTransport` through the keyword-only `transport=`
seam. That departs from the repo's `patch("httpx.post")` habit deliberately:
faking the client would make the test simulate the redirect loop it exists to
exercise, whereas a transport drives the real one.

The module binds no engine and holds no module-level state — the caller's
`Session` is the first parameter of every public function.
"""

import ipaddress
import logging
import socket
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import trafilatura
from loregarden.config import settings
from loregarden.db.session import service_write
from loregarden.models.domain.enums import (
    ReferenceCacheOutcome,
    ReferenceFetchError,
    ReferencePageKind,
    comparable_utc,
    utcnow,
)
from loregarden.models.domain.tables import ReferencePage
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

HTTP_NOT_MODIFIED = 304
_REDIRECT_FLOOR = 300
_ERROR_FLOOR = 400

#: URL schemes this cache will fetch. Not a domain vocabulary of ours — these
#: are the two the guard understands, and everything else is refused.
_HTTP = "http"
_HTTPS = "https"
_DEFAULT_PORTS = {_HTTP: 80, _HTTPS: 443}

#: Media types `fetch_reference` accepts. `fetch_cached_text` takes its own.
_PAGE_ACCEPT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
)
_MARKDOWN_OUTPUT = "markdown"
#: What a body is decoded as when the remote names no usable charset.
_DEFAULT_ENCODING = "utf-8"
#: One byte, enough to make a codec prove it decodes bytes to text. An empty
#: `bytes` returns `""` without consulting the codec at all, so it proves
#: nothing.
_ENCODING_PROBE = b" "
#: How much of the body to inspect when deciding whether it is a fragment.
_FRAGMENT_PROBE_BYTES = 2048


class ReferencePayload(BaseModel):
    """What both public entry points return, success or failure.

    Public, unlike `_HopResult` below, because it is the surface: an MCP tool
    or any other caller reaches `model_dump(mode="json")` on this rather than
    reinventing a serializer. That is not a style preference — `fetched_at` is
    a `datetime`, so the bare dict this replaced could not be `json.dumps`-ed
    at all, and every consumer would have had to discover that for itself and
    work around it (607).

    `error` is on successes and `cache` is on failures for the same reason each
    outcome is one shape rather than two: a served-stale copy has to be
    distinguishable from a fresh one, and a caller should not have to know
    which outcome it got before it knows which fields exist.
    """

    url: str
    title: str
    markdown: str
    cache: ReferenceCacheOutcome
    fetched_at: datetime | None
    total_chars: int
    truncated: bool
    error: str


class _HopResult(BaseModel):
    """One HTTP exchange, as the redirect loop and the cache layer see it.

    A non-empty `location` means "follow me"; a non-null `error_kind` means the
    exchange produced no usable body. Both empty means `body` is real.
    """

    status: int = 0
    location: str = ""
    body: str = ""
    content_type: str = ""
    etag: str = ""
    last_modified: str = ""
    error_kind: ReferenceFetchError | None = None
    error: str = ""


def _describe(kind: ReferenceFetchError, detail: str) -> str:
    """An error string naming exactly one failure kind, plus its detail."""
    return f"{kind.value}: {detail}"


def _failed(kind: ReferenceFetchError, detail: str) -> _HopResult:
    return _HopResult(error_kind=kind, error=_describe(kind, detail))


# ---------------------------------------------------------------------------
# URL normalization and the SSRF guard
# ---------------------------------------------------------------------------


def normalize_reference_url(url: str) -> str:
    """One spelling per cached row: no fragment, no surrounding whitespace,
    lowercase scheme and host, and no redundant `:80`/`:443`.

    Never raises: a URL this cannot parse is returned stripped, and
    `validate_reference_url` refuses it a moment later.
    """
    raw = url.strip()
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return raw
    if not host:
        return raw
    scheme = parts.scheme.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The host as an address, or None when it is a name needing resolution."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _resolved_addresses(host: str, port: int) -> list[str] | None:
    """Every address `host` resolves to, or None when resolution failed.

    A name that does not resolve is blocked rather than allowed: "I could not
    check" is not "it is safe".
    """
    try:
        infos = socket.getaddrinfo(host, port)
    except OSError as exc:
        logger.info("reference guard could not resolve %s: %s", host, exc)
        return None
    except UnicodeError as exc:
        logger.info("reference guard rejected the hostname %s: %s", host, exc)
        return None
    return [info[4][0] for info in infos]


def validate_reference_url(url: str) -> str:
    """`""` when the URL may be fetched, otherwise the reason it may not.

    Never raises. The literal check runs *before* any resolution, so an IP
    literal is judged by `ipaddress` alone and the literal tier never depends
    on DNS.
    """
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        return f"unparsable url ({exc})"
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        return f"scheme {scheme or '(none)'!r} is not http or https"
    if not host:
        return "url carries no hostname"
    host = host.lower()
    if host == "localhost" or host.endswith(".local"):
        return f"{host} names the local machine or network"
    literal = _ip_literal(host)
    if literal is not None:
        return "" if literal.is_global else f"{host} is not a global address"
    addresses = _resolved_addresses(host, port or _DEFAULT_PORTS[scheme])
    if addresses is None:
        return f"{host} did not resolve"
    private = [address for address in addresses if not ipaddress.ip_address(address).is_global]
    if private:
        return f"{host} resolves to non-global {', '.join(private)}"
    return ""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _wrap_fragment(body: str) -> tuple[str, bool]:
    """Give a bare fragment an element trafilatura will treat as the article.

    DevDocs pages start at `<h1>` with no `<body>`, and extracted raw the
    heading is silently dropped. Measured on trafilatura 2.2.0: an
    `<html><body>` wrapper does *not* bring it back, an `<article>` wrapper
    does.
    """
    if "<body" in body[:_FRAGMENT_PROBE_BYTES].lower():
        return body, False
    return f"<html><body><article>{body}</article></body></html>", True


def _extract_markdown(body: str, content_type: str, url: str) -> tuple[str, str]:
    """`(markdown, title)` for one document; `("", "")` when nothing extracts.

    Non-HTML types are already text and pass straight through.

    Markup we could not read is the *page's* failure, so anything the extractor
    raises on it is caught here and reported as "nothing extracted". Letting it
    reach the AC3 boundary instead would label a hostile remote document an
    `internal_error`, telling the caller to stop retrying the URL and come fix
    us. The two trafilatura calls get *separate* handlers, because they do not
    mean the same thing: `extract` raising means there is no text, but
    `extract_metadata` raising only means there is no title. Sharing one handler
    threw away a body we had already extracted, so the page was never cached and
    every call re-fetched it — an amplifier a hostile page could aim (626).
    """
    if "html" not in content_type:
        return body, ""
    document, wrapped = _wrap_fragment(body)
    try:
        markdown = trafilatura.extract(
            document,
            url=url,
            output_format=_MARKDOWN_OUTPUT,
            include_links=True,
            include_formatting=True,
            include_tables=True,
            include_comments=False,
            favor_recall=wrapped,
        )
    except Exception as exc:  # noqa: BLE001 - the page's markup, not our bug; reported below
        logger.warning("reference extraction failed for %s: %s: %s", url, type(exc).__name__, exc)
        return "", ""
    if not markdown:
        return "", ""
    try:
        metadata = trafilatura.extract_metadata(document)
    except Exception as exc:  # noqa: BLE001 - a missing title is not a failed extraction
        logger.warning(
            "reference metadata failed for %s: %s: %s — keeping the extracted body",
            url,
            type(exc).__name__,
            exc,
        )
        metadata = None
    title = (metadata.title if metadata is not None else None) or ""
    return markdown, title


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _classify_response(
    response: httpx.Response, accept_types: tuple[str, ...]
) -> _HopResult | None:
    """The verdict reachable from the headers alone, or None to read the body.

    Every branch here is decided before a single byte of the body is pulled —
    that is the point of the function existing separately.
    """
    if response.status_code == HTTP_NOT_MODIFIED:
        return _HopResult(
            status=response.status_code,
            etag=response.headers.get("etag", ""),
            last_modified=response.headers.get("last-modified", ""),
        )
    if _REDIRECT_FLOOR <= response.status_code < _ERROR_FLOOR:
        location = response.headers.get("location", "")
        if not location:
            return _failed(
                ReferenceFetchError.FETCH_ERROR,
                f"status {response.status_code} carried no location header",
            )
        return _HopResult(status=response.status_code, location=location)
    if response.status_code >= _ERROR_FLOOR:
        return _failed(
            ReferenceFetchError.FETCH_ERROR,
            f"http_{response.status_code} from {response.url}",
        )
    media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in accept_types:
        return _failed(
            ReferenceFetchError.UNSUPPORTED_CONTENT_TYPE,
            f"{media_type or '(none)'} is not one of {', '.join(accept_types)}",
        )
    return None


def _usable_encoding(label: str) -> str:
    """`label` if bytes can actually be decoded with it, otherwise `utf-8`.

    `httpx.Response.charset_encoding` is whatever the remote server wrote in
    its `Content-Type`, so it is attacker-supplied: `charset=utf-9000` makes
    `bytes.decode` raise `LookupError`, and so does `charset=base64`, which
    names a real codec that is not a *text* codec. Neither is an `httpx` error,
    so neither was caught anywhere.

    The probe is a decode rather than a `codecs.lookup`, because lookup accepts
    `base64`/`hex`/`rot13` and only the decode path rejects them. One byte is
    enough — an empty `bytes` short-circuits before the codec is consulted.

    Validating here rather than catching downstream is the point: the decode in
    `_read_body` cannot raise at all.
    """
    if not label:
        return _DEFAULT_ENCODING
    try:
        _ENCODING_PROBE.decode(label, errors="replace")
    except (LookupError, ValueError, UnicodeError) as exc:
        logger.info("reference fetch ignoring unusable charset %r: %s", label, exc)
        return _DEFAULT_ENCODING
    return label


def _read_body(response: httpx.Response, max_bytes: int) -> _HopResult:
    """Stream the body with a running counter, aborting past `max_bytes`."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            return _failed(
                ReferenceFetchError.TOO_LARGE,
                f"body from {response.url} passed {max_bytes} bytes",
            )
        chunks.append(chunk)
    return _HopResult(
        status=response.status_code,
        body=b"".join(chunks).decode(
            _usable_encoding(response.charset_encoding or ""), errors="replace"
        ),
        content_type=response.headers.get("content-type", "").split(";")[0].strip().lower(),
        etag=response.headers.get("etag", ""),
        last_modified=response.headers.get("last-modified", ""),
    )


def _fetch_once(
    client: httpx.Client,
    url: str,
    *,
    etag: str,
    last_modified: str,
    accept_types: tuple[str, ...],
) -> _HopResult:
    """One GET, no redirect following, conditional when we hold a copy."""
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        with client.stream("GET", url, headers=headers) as response:
            verdict = _classify_response(response, accept_types)
            if verdict is not None:
                return verdict
            return _read_body(response, settings.reference_fetch_max_bytes)
    except httpx.TimeoutException as exc:
        logger.warning("reference fetch timed out for %s: %s", url, exc)
        return _failed(ReferenceFetchError.FETCH_ERROR, f"timed out fetching {url} ({exc})")
    except httpx.HTTPError as exc:
        logger.warning("reference fetch failed for %s: %s", url, exc)
        return _failed(ReferenceFetchError.FETCH_ERROR, f"could not fetch {url} ({exc})")
    except Exception as exc:  # noqa: BLE001 - see below
        # Enumerating exception types here is what let the charset LookupError
        # out. Anything a transport, a codec or this module can raise while a
        # body is being streamed is converted rather than propagated, and it is
        # INTERNAL_ERROR rather than FETCH_ERROR because we did not anticipate
        # it: "retry the URL" is the wrong advice for our own bug.
        logger.exception("reference fetch raised an unhandled %s for %s", type(exc).__name__, url)
        return _failed(
            ReferenceFetchError.INTERNAL_ERROR, f"unhandled {type(exc).__name__} fetching {url}"
        )


def _fetch_with_redirects(
    url: str,
    *,
    etag: str,
    last_modified: str,
    accept_types: tuple[str, ...],
    transport: httpx.BaseTransport | None,
) -> _HopResult:
    """Follow redirects by hand, re-validating every hop with the full guard."""
    max_redirects = settings.reference_fetch_max_redirects
    current = url
    with httpx.Client(
        timeout=settings.reference_fetch_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for _hop in range(max_redirects + 1):
            result = _fetch_once(
                client,
                current,
                etag=etag,
                last_modified=last_modified,
                accept_types=accept_types,
            )
            if not result.location:
                return result
            target = normalize_reference_url(urljoin(current, result.location))
            reason = validate_reference_url(target)
            if reason:
                logger.warning("reference redirect from %s blocked: %s", current, reason)
                return _failed(ReferenceFetchError.BLOCKED, f"redirect to {target}: {reason}")
            # Conditional headers describe the copy we hold of the *original*
            # URL; sending them to a different resource invites a bogus 304.
            etag = ""
            last_modified = ""
            current = target
    return _failed(
        ReferenceFetchError.TOO_MANY_REDIRECTS,
        f"{url} redirected more than {max_redirects} times",
    )


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def _payload(
    *,
    url: str,
    title: str,
    markdown: str,
    cache: ReferenceCacheOutcome,
    fetched_at: datetime | None,
    error: str,
    max_chars: int,
) -> ReferencePayload:
    """The one payload shape. `max_chars <= 0` means no cap, and truncation
    never touches what is stored."""
    total = len(markdown)
    truncated = 0 < max_chars < total
    return ReferencePayload(
        url=url,
        title=title,
        markdown=markdown[:max_chars] if truncated else markdown,
        cache=cache,
        fetched_at=fetched_at,
        total_chars=total,
        truncated=truncated,
        error=error,
    )


def _failure_payload(url: str, error: str) -> ReferencePayload:
    """A failure with nothing cached to serve: `MISS`, and no body at all.

    `cache` is on failure payloads for the same reason `error` is on successes
    — without it a served-stale copy is indistinguishable from a fresh one.
    """
    return _payload(
        url=url,
        title="",
        markdown="",
        cache=ReferenceCacheOutcome.MISS,
        fetched_at=None,
        error=error,
        max_chars=0,
    )


def _row_payload(
    row: ReferencePage, cache: ReferenceCacheOutcome, error: str, max_chars: int
) -> ReferencePayload:
    return _payload(
        url=row.url,
        title=row.title,
        markdown=row.content_markdown,
        cache=cache,
        fetched_at=row.fetched_at,
        error=error,
        max_chars=max_chars,
    )


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


def _find_row(session: Session, url: str) -> ReferencePage | None:
    return session.exec(select(ReferencePage).where(ReferencePage.url == url)).first()


def _caller_view(session: Session, url: str) -> ReferencePage | None:
    """The row as the *caller's* Session sees it once our write has committed.

    A write on our own connection is invisible to an instance the caller's
    identity map already holds — `_find_row` would hand back the stale one it
    loaded before the fetch — so the row it returns is refreshed rather than
    trusted.
    """
    row = _find_row(session, url)
    if row is not None:
        session.refresh(row)
    return row


def _is_fresh(row: ReferencePage) -> bool:
    age = (utcnow() - comparable_utc(row.fetched_at)).total_seconds()
    return age < settings.reference_cache_ttl_seconds


def _serve_hit(session: Session, row: ReferencePage, max_chars: int) -> ReferencePayload:
    with service_write(session) as writer:
        counted = _find_row(writer, row.url)
        if counted is None:
            # The row we are serving was deleted between our read and our write.
            # The page in hand is still worth serving; only its tally is lost.
            logger.warning("reference cache could not count a hit for %s", row.url)
        else:
            counted.hit_count += 1
            writer.add(counted)
            writer.flush()
    served = _caller_view(session, row.url) or row
    return _row_payload(served, ReferenceCacheOutcome.HIT, "", max_chars)


def _store(
    session: Session,
    url: str,
    row: ReferencePage | None,
    *,
    markdown: str,
    title: str,
    result: _HopResult,
    kind: ReferencePageKind,
) -> ReferencePage | None:
    """Insert or update the cached copy. None means the write did not land."""
    try:
        with service_write(session) as writer:
            target = _find_row(writer, url) or ReferencePage(url=url)
            target.title = title
            target.content_markdown = markdown
            target.content_chars = len(markdown)
            target.etag = result.etag
            target.last_modified = result.last_modified
            target.kind = kind
            target.fetched_at = utcnow()
            writer.add(target)
            writer.flush()
    except IntegrityError as exc:
        # Another caller inserted this URL between our read and our write. The
        # unique index is the arbiter; the SAVEPOINT this raised out of is
        # already unwound by the `with`, so re-read and use whatever it kept.
        logger.info("reference cache insert raced for %s: %s", url, exc)
    # Both paths read the row back the same way: the write landed, or the unique
    # index kept someone else's and that is the copy the caller should get.
    return _caller_view(session, url)


def _serve_stale_or_fail(
    url: str, row: ReferencePage | None, error: str, max_chars: int
) -> ReferencePayload:
    """A stale copy beats nothing — but only if it is labelled as one."""
    if row is not None and row.content_markdown:
        return _row_payload(row, ReferenceCacheOutcome.STALE_ERROR, error, max_chars)
    return _failure_payload(url, error)


def _revalidated(
    session: Session, url: str, row: ReferencePage | None, result: _HopResult, max_chars: int
) -> ReferencePayload:
    """A 304 means the stored body stands; only its age resets."""
    if row is None:
        return _failure_payload(
            url,
            _describe(ReferenceFetchError.FETCH_ERROR, f"{url} answered 304 with nothing cached"),
        )
    with service_write(session) as writer:
        aged = _find_row(writer, url)
        if aged is None:
            # Deleted under us. The stored body we already hold still stands.
            logger.warning("reference cache could not reset the age of %s", url)
        else:
            if result.etag:
                aged.etag = result.etag
            if result.last_modified:
                aged.last_modified = result.last_modified
            aged.fetched_at = utcnow()
            writer.add(aged)
            writer.flush()
    served = _caller_view(session, url) or row
    return _row_payload(served, ReferenceCacheOutcome.REVALIDATED, "", max_chars)


def _resolve_result(
    session: Session,
    url: str,
    row: ReferencePage | None,
    result: _HopResult,
    *,
    kind: ReferencePageKind,
    extract: bool,
    max_chars: int,
) -> ReferencePayload:
    """Turn one hop result into a payload, storing it when it is worth storing."""
    if result.error_kind is not None:
        return _serve_stale_or_fail(url, row, result.error, max_chars)
    if result.status == HTTP_NOT_MODIFIED:
        return _revalidated(session, url, row, result, max_chars)
    if extract:
        markdown, title = _extract_markdown(result.body, result.content_type, url)
    else:
        markdown, title = result.body, ""
    if not markdown:
        # Never cached: an empty page stored now is served empty for a TTL.
        error = _describe(ReferenceFetchError.EXTRACTION_FAILED, f"no text extracted from {url}")
        return _serve_stale_or_fail(url, row, error, max_chars)
    stored = _store(session, url, row, markdown=markdown, title=title, result=result, kind=kind)
    if stored is None:
        error = _describe(ReferenceFetchError.FETCH_ERROR, f"could not cache {url}")
        return _serve_stale_or_fail(url, row, error, max_chars)
    return _row_payload(stored, ReferenceCacheOutcome.MISS, "", max_chars)


def _fetch_through_cache(
    session: Session,
    url: str,
    *,
    kind: ReferencePageKind,
    accept_types: Iterable[str],
    extract: bool,
    refresh: bool,
    max_chars: int,
    transport: httpx.BaseTransport | None,
) -> ReferencePayload:
    """The boundary both public entry points sit on. Never raises.

    "Never raises" is a property of *this function*, not a claim about the
    functions under it: validation, fetch, decode, extraction and persistence
    each keep their own narrow handlers, and whatever still escapes them lands
    here as an `INTERNAL_ERROR` payload with its type and URL logged. That is
    what makes AC3 structural — a new call site added below cannot quietly
    reintroduce an escape, and the guard is not inert, so a swallowed failure
    is still a visible one.

    The argument normalization is inside the `try` for the same reason. Both
    `url.strip()` and `tuple(accept_types)` assume the declared types, and both
    used to run above this handler — so a `url` of None or an `accept_types` of
    None reached a caller as an exception rather than a payload, which is the
    one input class a caller cannot see coming (620). 174 sources URLs from
    JSON and database rows, where None is live.

    This boundary does not touch the caller's transaction. Every write below it
    happens inside a SAVEPOINT that unwinds on the way out, so there is nothing
    of ours left pending for a rollback here to clean up — and a rollback here
    would discard the caller's own uncommitted work, which is not ours to end.
    """
    # Named before the `try`, because the handler needs something to report even
    # when the first statement inside it is what failed. The type is all that is
    # safely knowable about an out-of-contract value — interpolating the object
    # would run its `__repr__`, and one that raises would defeat this boundary
    # from inside its own handler. It is also the more useful thing to say: the
    # defect in `fetch_reference(session, None)` is the None, not the URL.
    reported = f"<{type(url).__name__}>"
    try:
        reported = url.strip()
        return _fetch_through_cache_uncaught(
            session,
            url,
            kind=kind,
            accept_types=tuple(accept_types),
            extract=extract,
            refresh=refresh,
            max_chars=max_chars,
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 - the AC3 boundary; see docstring
        logger.exception(
            "reference cache raised an unhandled %s for %s", type(exc).__name__, reported
        )
        return _failure_payload(
            reported,
            _describe(
                ReferenceFetchError.INTERNAL_ERROR,
                f"unhandled {type(exc).__name__} handling {reported}",
            ),
        )


def _fetch_through_cache_uncaught(
    session: Session,
    url: str,
    *,
    kind: ReferencePageKind,
    accept_types: tuple[str, ...],
    extract: bool,
    refresh: bool,
    max_chars: int,
    transport: httpx.BaseTransport | None,
) -> ReferencePayload:
    """The cache path itself. May raise; `_fetch_through_cache` is its boundary."""
    normalized = normalize_reference_url(url)
    reason = validate_reference_url(normalized)
    if reason:
        logger.warning("reference url %s blocked: %s", normalized, reason)
        return _failure_payload(normalized, _describe(ReferenceFetchError.BLOCKED, reason))
    row = _find_row(session, normalized)
    if row is not None and not refresh and _is_fresh(row):
        return _serve_hit(session, row, max_chars)
    result = _fetch_with_redirects(
        normalized,
        etag=row.etag if row is not None else "",
        last_modified=row.last_modified if row is not None else "",
        accept_types=accept_types,
        transport=transport,
    )
    return _resolve_result(
        session, normalized, row, result, kind=kind, extract=extract, max_chars=max_chars
    )


def fetch_reference(
    session: Session,
    url: str,
    *,
    refresh: bool = False,
    max_chars: int = 0,
    transport: httpx.BaseTransport | None = None,
) -> ReferencePayload:
    """Fetch a reference page through the cache, extracting it to markdown.

    Never raises: every failure comes back as a self-classifying payload.

    Transaction contract
    --------------------
    **This module never commits or rolls back the Session you hand it.** Which
    transaction the cache's own write lands in depends on what you were already
    holding, and you need not arrange either shape deliberately — the module
    reads which one you are in.

    *If you have only read* — the shape `db/session.py`'s `get_session()` hands
    every request — the cache writes on a Session of its own and commits there.
    The page is durably cached by the time this returns. You need not commit,
    and closing or rolling back your Session afterwards cannot discard it. Your
    Session is left exactly as it was found.

    *If you are mid-write*, holding a transaction of your own, the cache write
    nests inside it as a SAVEPOINT and you keep both ends. It becomes durable
    when you commit, and your rollback discards it along with your own work —
    which is the point: the module must not end a transaction it did not start.

    Either way **neither entry point leaves a transaction open that you did not
    already have**, so no database lock outlives this call. That is not a free
    nicety: a lock held across a remote fetch locks the whole SQLite file and
    hands the remote server a lever on the control plane's write availability
    (638).
    """
    return _fetch_through_cache(
        session,
        url,
        kind=ReferencePageKind.PAGE,
        accept_types=_PAGE_ACCEPT_TYPES,
        extract=True,
        refresh=refresh,
        max_chars=max_chars,
        transport=transport,
    )


def fetch_cached_text(
    session: Session,
    url: str,
    *,
    kind: ReferencePageKind,
    accept_types: tuple[str, ...],
    refresh: bool = False,
    max_chars: int = 0,
    transport: httpx.BaseTransport | None = None,
) -> ReferencePayload:
    """Fetch a raw text document through the same cache, without extraction.

    DevDocs catalog and index JSON ride here, so that all HTTP for both tools
    goes through one module and one hermetic patch point. The payload shape is
    `fetch_reference`'s; the unextracted body arrives in `markdown`.

    Never raises.

    Transaction contract
    --------------------
    The same as `fetch_reference`, whose docstring carries the reasoning: this
    module never commits or rolls back the Session you hand it; a caller that
    has only read gets a page that is durably cached when this returns, written
    on a Session of the module's own; a caller mid-write gets the write nested
    in its own transaction and owns both ends. Neither entry point leaves a
    transaction open that the caller did not already have.

    It matters more here than there. Both DevDocs tools fetch a catalog and
    then an index on one Session, so this is the entry point that would have
    held a lock across the second remote read.
    """
    return _fetch_through_cache(
        session,
        url,
        kind=kind,
        accept_types=accept_types,
        extract=False,
        refresh=refresh,
        max_chars=max_chars,
        transport=transport,
    )
