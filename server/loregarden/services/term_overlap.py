"""Rank candidate memory records by how many distinctive terms they share.

The briefing used to query both memory stores with a whole ticket title as one
contiguous substring, which essentially never matched, so the "Related
learnings" section was dead. Term overlap replaces it: explainable, needs no
index to maintain, and a wrong hit costs a glance rather than a bad decision.

**The tokenizer here is a deliberate copy, not an accident.** `STOPWORDS`, the
token regex, the lowercasing and `_MIN_TERM_LEN` are verbatim from
`services/prior_work.py`. That module may not be touched in this change, and
importing it here would tie a pure policy module to SQLModel and the control
plane database. `prior_work.py` is the de-dupe site: when it is next opened for
other reasons, delete its private copy and import these names instead.

`_SCORED_BODY_CHARS` bounds how much of a candidate's body is scored. Overlap
over an unbounded body ranks by note *length* rather than relevance — the live
vault runs memory notes at ~292B average / 3389B max and learnings at ~583B
average / 4157B max, a better than tenfold spread, so a rambling weekly note
beats a short precise one on sheer surface area. 600 characters covers the
average note of either kind in full while capping the outliers at roughly a
seventh of their text. Titles and tags are always scored whole; only the body
is truncated.

`rank_by_overlap` returns ordered candidate *records*, never formatted lines
and never bare ids, so the sibling Reciprocal Rank Fusion ticket can use this
list as one input among several — reading rank from `enumerate()` and identity
from `(source, id)`.
"""

from __future__ import annotations

import re
from typing import Any

_MIN_TERM_LEN = 4
_SCORED_BODY_CHARS = 600
_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")

# Words that appear in most tickets here and so separate nothing.
STOPWORDS = frozenset(
    {
        "with",
        "from",
        "that",
        "this",
        "when",
        "then",
        "have",
        "into",
        "make",
        "made",
        "does",
        "onto",
        "over",
        "your",
        "which",
        "while",
        "where",
        "loregarden",
        "ticket",
        "tickets",
        "stage",
        "stages",
        "agent",
        "agents",
        "should",
        "would",
        "could",
        "using",
        "used",
        "also",
        "some",
        "more",
        "fix",
        "fixes",
        "fixed",
        "add",
        "adds",
        "added",
        "update",
        "updates",
    }
)


def terms(text: str) -> set[str]:
    """The distinctive words of `text`, lowercased.

    Tokens shorter than four characters are dropped, which loses acronyms like
    MCP, RRF and SQL. That is inherited from `prior_work.py` on purpose: the
    ranker mirrors a design that is already settled, and narrowing the minimum
    here would make the two call sites disagree about what a term is.
    """
    words = _WORD.findall((text or "").lower())
    return {w for w in words if len(w) >= _MIN_TERM_LEN and w not in STOPWORDS}


def _scored_text(candidate: dict[str, Any]) -> str:
    title = candidate.get("title") or ""
    tags = " ".join(candidate.get("tags") or [])
    body = (candidate.get("body") or "")[:_SCORED_BODY_CHARS]
    return f"{title} {tags} {body}"


def rank_by_overlap(
    query_terms: set[str], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Candidates sharing at least one query term, best match first.

    Scored on *distinct* shared terms, so a note repeating one word does not
    outrank a note touching four. Ties break on `updated_at` descending and
    then on `id` descending, which makes the order total: without a final key
    two equally recent, equally matching notes would shuffle between prompt
    builds.

    `updated_at` is compared as the **raw ISO-8601 string** and must stay that
    way. Both stores stamp tz-aware ISO-8601 from one clock, so lexical order
    is chronological order, and a note whose frontmatter omits `updated` reads
    back as `""` and sorts last. Parsing to `datetime` is how ticket 462 broke
    `search_prior_work`; here the resulting TypeError would be swallowed by
    `inherited_wisdom._safely` and quietly restore the dead section this
    ranker exists to fix.
    """
    if not query_terms:
        return []

    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        overlap = len(query_terms & terms(_scored_text(candidate)))
        if overlap:
            scored.append({**candidate, "overlap": overlap})

    scored.sort(
        key=lambda row: (row["overlap"], row.get("updated_at") or "", row.get("id") or ""),
        reverse=True,
    )
    return scored
