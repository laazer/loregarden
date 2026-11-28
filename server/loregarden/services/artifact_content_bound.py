"""Bound an artifact row's parsed content for the per-request artifact feed.

`list_ticket_artifacts` returns the whole parsed `content` of up to 200 rows, and
`TicketOutputs` polls it every 2 s while the tab is open. The land-verification
receipt's `content_json.output` is the concatenated stdout+stderr of every verify
command, unbounded: a green 42-test run measured 869 bytes, seven failures 16,575
(~2.4 KB per failure), and a conftest-level break putting 500 tests red is ~1.2 MB
in one row — served synchronously over SQLite on the event loop. The receipt is
right as written (lg-milestone-that-1419 AC9 requires the FULL output on the
record), so the bound belongs here, on the read side.

Why an algorithm over the parsed JSON and not a slice of the stored text: a text
slice hands the client the `{"_raw": ...}` arm of the feed's decode, `reportVerdict`
returns null, and the verdict pill disappears from the row. The four rules below
are ordered, and the fields a row renders from are what they protect.

Why per-field trimming is not enough, measured read-only on the live database
2026-10-29: truncating every top-level string to 2 KiB brings only 336 of the
2,139 oversized rows (15.7%) under 16 KiB, and the median oversized row holds
just 0.3% of its bytes in its largest top-level string — `context` and `plan`
rows are large from many small fields and nested structure, not from one string.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

#: The bound itself: no feed row's re-encoded content exceeds this (AC1).
ARTIFACT_FEED_CONTENT_LIMIT = 16384

#: Rule (a): a top-level value this size or smaller is kept verbatim.
SMALL_VALUE_LIMIT = 2048

#: Rule (c): the budget for the leading slice of the largest dropped string.
PREFIX_LIMIT = 8192

#: Rule (b): the per-member cap when salvaging scalars from a dropped object.
_SCALAR_SALVAGE_LIMIT = 256

#: Rule (d) never drops these. `status`/`stage_key`/`confidence` are what
#: `reportVerdict` colours the row's pill from, with no fetch (AC3).
_PROTECTED_KEYS = ("status", "stage_key", "confidence")

#: The key a content that is not a JSON object is reduced to, matching the key
#: the feed's own decode fallback already uses for unparseable rows.
_RAW_KEY = "_raw"


# An artifact's content is whatever JSON an agent attached — there is no schema to
# model it with, and the rules below are stated over JSON *shapes* (object, array,
# string, scalar) precisely because the shape is all that is known about it. These
# three predicates are the only place that shape is read.
# fmt: off
def _is_object(value: Any) -> bool:
    return isinstance(value, dict)  # py-org: allow-isinstance - unmodellable agent JSON


def _is_text(value: Any) -> bool:
    return isinstance(value, str)  # py-org: allow-isinstance - unmodellable agent JSON


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))  # py-org: allow-isinstance - unmodellable agent JSON
# fmt: on


def _utf8(text: str) -> bytes:
    """`text` as UTF-8, with anything UTF-8 cannot encode replaced.

    Agents write content through `json.dumps`, which escapes a lone surrogate, so
    `{"output": "\\ud800"}` stores cleanly and `json.loads` hands it back as a
    character UTF-8 cannot encode. Nothing re-encoded a feed row before this
    bound, so that input was harmless; measuring one in UTF-8 bytes is the first
    place in this codebase that can raise on a row the database already holds, and
    the feed it would raise on is polled every 2 s. Replacing keeps the
    measurement honest and keeps what we hand back renderable.
    """
    return text.encode("utf-8", "replace")


def feed_content_bytes(value: Any) -> int:
    """The size the bound is measured in: `json.dumps(..., ensure_ascii=False)` as UTF-8.

    Also what a shortened feed row reports as `content_shown_bytes`, so the number
    the client renders is the one the bound was taken against.
    """
    return len(_utf8(json.dumps(value, ensure_ascii=False)))


#: Internal spelling, used by the rules below.
_encoded = feed_content_bytes


def _as_text(value: Any) -> str:
    """The text rule (c) slices: a string is itself; anything else is its JSON."""
    return value if _is_text(value) else json.dumps(value, ensure_ascii=False)


def _prefix(text: str, budget: int) -> str:
    """The longest leading slice of `text` encoding to <= `budget` bytes.

    Never splits a character: 8192 bytes does not land on a boundary for a 3-byte
    one, so the slice is trimmed back until it decodes.
    """
    data = _utf8(text)
    if len(data) <= budget:
        return text
    cut = data[: max(budget, 0)]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""


def _salvaged_scalars(value: dict[str, Any]) -> dict[str, Any] | None:
    """Rule (b): the scalar members of a dropped object, or None if none survive.

    `handoff.required_items_met`/`total_required_items` are the two numbers
    `handoffScore` builds the badge from; the `checklist` array beside them is what
    made the row oversized, so the array goes and the numbers stay.
    """
    reduced = {
        key: member
        for key, member in value.items()
        if _is_scalar(member) and _encoded(member) <= _SCALAR_SALVAGE_LIMIT
    }
    if not reduced or _encoded(reduced) > SMALL_VALUE_LIMIT:
        return None
    return reduced


def _partition(content: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rule (a): keep what is small — nested containers included."""
    kept: dict[str, Any] = {}
    dropped: dict[str, Any] = {}
    for key, value in content.items():
        if _encoded(value) <= SMALL_VALUE_LIMIT:
            kept[key] = value
        else:
            dropped[key] = value
    return kept, dropped


def _largest_dropped_value(dropped: dict[str, Any], exclude: set[str]) -> str | None:
    """Rule (c): which dropped key gets a prefix of itself put back.

    The largest dropped *value*, whatever its shape, not only the largest dropped
    string. Measured read-only over the live database 2026-10-29: the three kinds
    that dominate the oversized rows hold their bulk in structure, not in a
    top-level string — a `context` row in a nested object, a `diff` row in a
    `sections` array, a `log` row in a `lines` array — so the earlier
    string-only rule found nothing to slice and 1,689 of 2,136 bounded rows
    (79.1%) came back blank or near-blank, 806 of them literally `{}`. The
    reader then opened on an empty pane, which is the AC7 failure this clause
    exists for. The prefix is therefore a JSON string standing at a key that
    held an object or an array; that is deliberate, and it is already what the
    non-object arm of this same rule does.

    Sorted by size then key, so two values of equal size resolve the same way on
    every poll: a prefix that moves between keys reflows the table under the
    operator's cursor and makes `content_shown_bytes` a number that was true once.
    """
    candidates = sorted(
        (key for key in dropped if key not in exclude),
        key=lambda key: (-_encoded(dropped[key]), key),
    )
    return candidates[0] if candidates else None


def _drop_until_it_fits(result: dict[str, Any], keys: list[str], limit: int) -> None:
    """Delete `keys`, in order, until `result` re-encodes within `limit`."""
    for key in keys:
        if _encoded(result) <= limit:
            return
        del result[key]


def _by_descending_size(result: dict[str, Any], keys: Iterable[str]) -> list[str]:
    """`keys` largest-encoding first, ties by key so two polls agree."""
    return sorted(keys, key=lambda key: (-_encoded(result[key]), key))


def _enforce_bound(
    result: dict[str, Any],
    *,
    limit: int,
    protected: set[str],
    reduced_keys: frozenset[str],
    prefix_key: str | None,
    prefix_text: str,
    prefix_budget: int,
) -> None:
    """Rule (d): guarantee the bound, in place, giving up the floor last."""
    _drop_until_it_fits(
        result,
        _by_descending_size(result, (key for key in result if key not in protected)),
        limit,
    )

    while prefix_key is not None and _encoded(result) > limit:
        prefix_budget //= 2
        if prefix_budget < 1:
            # An empty prefix still does not fit, so the prefix key goes absent
            # rather than present and empty.
            del result[prefix_key]
            break
        result[prefix_key] = _prefix(prefix_text, prefix_budget)

    # The terminal arm. Rule (b) caps each reduced object at SMALL_VALUE_LIMIT
    # individually and nothing caps their sum, so nine or more of them exceed
    # the limit with no prefix at all — the shape AC2's own rationale names as
    # the common oversized row (`context` and `plan` rows, large from many small
    # fields and nested structure). AC1 is unconditional, so the reduced objects
    # go too, largest first. `status`/`stage_key`/`confidence` are the true
    # floor: a few dozen bytes, and the only thing the row's pill needs (AC3).
    _drop_until_it_fits(
        result,
        _by_descending_size(result, (key for key in result if key in reduced_keys)),
        limit,
    )


def _bound_object(content: dict[str, Any], limit: int) -> dict[str, Any]:
    """The four ordered rules over a JSON object's top-level keys."""
    kept, dropped = _partition(content)

    # (b) salvage the scalar members of a dropped object.
    salvaged: set[str] = set()
    for key, value in dropped.items():
        reduced = _salvaged_scalars(value) if _is_object(value) else None
        if reduced is not None:
            kept[key] = reduced
            salvaged.add(key)
    reduced_keys = frozenset(salvaged)

    # (c) leave a prefix of the largest dropped value, at its own key.
    prefix_key = _largest_dropped_value(dropped, salvaged)
    prefix_text = _as_text(dropped[prefix_key]) if prefix_key else ""
    prefix_budget = min(PREFIX_LIMIT, len(_utf8(prefix_text)))
    if prefix_key:
        kept[prefix_key] = _prefix(prefix_text, prefix_budget)

    # Rebuilt in the row's own key order, so the shortened row reads like the row.
    result = {key: kept[key] for key in content if key in kept}
    _enforce_bound(
        result,
        limit=limit,
        protected=set(_PROTECTED_KEYS) | salvaged | ({prefix_key} if prefix_key else set()),
        reduced_keys=reduced_keys,
        prefix_key=prefix_key,
        prefix_text=prefix_text,
        prefix_budget=prefix_budget,
    )
    return result


def bound_feed_content(
    content: Any,
    *,
    limit: int = ARTIFACT_FEED_CONTENT_LIMIT,
) -> tuple[Any, bool]:
    """Return `content` bounded to `limit` re-encoded bytes, and whether anything went.

    A row within the limit is returned verbatim, whatever its shape, and the flag
    is False — so the feed marks only the rows something happened to.

    A content that is not a JSON object (an array, a bare string, or the feed's
    `{"_raw": …}` decode fallback) is replaced by an object with rule (c) applied
    to it as the single dropped string under `_raw`, and rule (d) enforced over
    the result exactly as for an object: `_prefix` budgets against the RAW UTF-8
    bytes of the text, while the bound is measured on its JSON *encoding*, so a
    prefix of 8192 quote characters re-encodes to 16384 bytes plus the envelope
    and a prefix of multi-byte or escape-heavy text is smaller than PREFIX_LIMIT
    suggests. The halving loop is what reconciles the two units.
    """
    if _encoded(content) <= limit:
        return content, False
    if _is_object(content):
        return _bound_object(content, limit), True
    text = _as_text(content)
    budget = min(PREFIX_LIMIT, limit)
    result = {_RAW_KEY: _prefix(text, budget)}
    _enforce_bound(
        result,
        limit=limit,
        protected={_RAW_KEY},
        reduced_keys=frozenset(),
        prefix_key=_RAW_KEY,
        prefix_text=text,
        prefix_budget=budget,
    )
    return result, True
