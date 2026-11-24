"""Rank candidate memory records by how many distinctive terms they share.

The briefing used to query both memory stores with a whole ticket title as one
contiguous substring, which essentially never matched, so the "Related
learnings" section was dead. Term overlap replaces it: explainable, needs no
index to maintain, and a wrong hit costs a glance rather than a bad decision.

**The tokenizer here is the one allowlist, and it is now public.** `STOPWORDS`,
the token regex, the lowercasing and `_MIN_TERM_LEN` used to be duplicated
verbatim in `services/prior_work.py`; that copy is gone and `prior_work`
imports these names. The property that matters is no longer incidental: every
character able to reshape a SQL or FTS expression is absent from a term by
construction, because `_WORD` is an allowlist rather than a blocklist. That is
the first line of defence; `services/sql_like.py`'s ESCAPE is the second.

`select_terms` adds the two policies that ride on top of the allowlist. A
**ceiling** drops terms so common they separate nothing. A **floor** keeps the
rarest terms whatever the ceiling says, because a question written entirely
out of common words must still ask something — a thin match beats no match.
Corpus counts arrive as an argument; no `Session` reaches this module, and
where no corpus count exists the selection says the ceiling did not run rather
than reporting a zero nobody measured.

A third policy, `MAX_QUERY_TERMS`, bounds how many terms a question asks with
at all, on both paths. It is here rather than in the graph leg because both
legs read the selection: a bound applied where the SQL is built would leave
the vault leg matching terms the graph never asked about. Its drops carry
their own reason and travel the same route the ceiling's do.

`_SCORED_BODY_CHARS` bounds how much of a candidate's body is scored. Overlap
over an unbounded body ranks by note *length* rather than relevance — the live
vault runs memory notes at ~292B average / 3389B max and learnings at ~583B
average / 4157B max, a better than tenfold spread, so a rambling weekly note
beats a short precise one on sheer surface area. 600 characters covers the
average note of either kind in full while capping the outliers at roughly a
seventh of their text. Titles, aliases and tags are always scored whole; only the body
is truncated.

`rank_by_overlap` returns ordered candidate *records*, never formatted lines
and never bare ids, so the sibling Reciprocal Rank Fusion ticket can use this
list as one input among several — reading rank from `enumerate()` and identity
from `(source, id)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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


#: A term matching more than this share of the counted corpus separates
#: nothing. "More than a fifth" is the written rule; the boundary itself is
#: kept, because a term matching exactly a fifth still halves the candidates.
CEILING_SHARE = 5

#: Below this many counted rows the ceiling is arithmetic about noise: on a
#: corpus of one, every term matches 100% of it and the ceiling would delete
#: the whole query. The selection then reports that it did not run.
MIN_CEILING_CORPUS = 20

#: How many of the rarest terms survive a ceiling that dropped everything.
DEFAULT_FLOOR = 2

#: How many terms one question may ask with, whichever leg answers it.
#:
#: The graph leg builds one four-predicate `OR` group per surviving term, and
#: SQLite refuses an expression tree past `SQLITE_MAX_EXPR_DEPTH`. Measured on
#: this branch, 995 terms search fine and 996 raise "Expression tree is too
#: large (maximum depth 1000)" — which is a *build* constant, not a contract,
#: so a cap chosen just under the measured break would be green here and red
#: on a SQLite built with a lower limit. 64 is better than a tenfold margin
#: against it, and is also about where a question stops being a question: no
#: real ask carries sixty-four distinct discriminative words, so the bound is
#: reached by machine-generated text rather than by anyone's sentence.
#:
#: The bound sits HERE, where the terms are selected, and never inside
#: `memory_agent_search.graph_rows_for_terms`. A cap on the graph leg would
#: leave the vault leg matching terms the graph never asked about, and the two
#: legs would then be answering different questions while reporting one.
MAX_QUERY_TERMS = 64


@dataclass(frozen=True)
class TermSelection:
    """The terms a query actually asked with, and what it lost on the way.

    `dropped` is term -> why, because the first question an empty answer
    raises is whether the query was used at all. `ceiling_applied` is False,
    never a zero, when no corpus count existed: the memory graph leg gets the
    allowlist and the floor only, and a zero there would read as "nothing is
    common", which is a measurement nobody made.
    """

    kept: set[str]
    dropped: dict[str, str] = field(default_factory=dict)
    ceiling_applied: bool = False


def _apply_cap(kept: set[str], cap: int = MAX_QUERY_TERMS) -> tuple[set[str], dict[str, str]]:
    """`kept` bounded to `cap` terms, and the ones that did not fit, with why.

    Longest first, because no corpus count exists on the memory leg and length
    is the standing proxy for rarity there — and ties break on the term itself,
    because `terms()` returns a `set` and slicing one would make the same
    question narrow differently per `PYTHONHASHSEED`: one agent asking one
    thing twice, getting two answers and two `partial["terms"]`.

    The drops carry their own reason rather than the ceiling's. They are two
    different things an agent can do next: a ceiling drop means the word
    separates nothing and a shorter question would not help, a cap drop means
    the question was too long and asking the rest again would.
    """
    if len(kept) <= cap:
        return kept, {}
    ordered = sorted(kept, key=lambda term: (-len(term), term))
    reason = (
        f"the question asked with {len(kept)} terms and this search asks with "
        f"at most {cap} at once; the {cap} longest were kept, so ask again "
        "with the rest to cover them"
    )
    return set(ordered[:cap]), dict.fromkeys(ordered[cap:], reason)


def select_terms(
    text: str,
    *,
    document_frequency: dict[str, int] | None = None,
    corpus_total: int = 0,
    floor: int = DEFAULT_FLOOR,
) -> TermSelection:
    """The allowlisted terms of `text`, minus the ones that separate nothing.

    `document_frequency` counts how many scanned rows each term appears in,
    and is counted by the caller in the pass that already tokenizes every
    candidate — no second query and no index. It is passed in rather than
    looked up here so that no `Session` reaches this module.

    The floor only fires when the ceiling left nothing: a query with one
    surviving rare term keeps that term and the common ones stay dropped.
    """
    built = terms(text)
    frequencies = document_frequency or {}
    ceiling_applied = corpus_total >= MIN_CEILING_CORPUS and document_frequency is not None
    if not ceiling_applied:
        kept, capped = _apply_cap(built)
        return TermSelection(kept=kept, dropped=capped, ceiling_applied=False)

    threshold = corpus_total // CEILING_SHARE
    dropped = {
        term: (
            f"matched {frequencies.get(term, 0)} of {corpus_total} scanned rows, "
            f"more than a fifth of them, so it separates nothing"
        )
        for term in built
        if frequencies.get(term, 0) > threshold
    }
    kept = built - set(dropped)
    if not kept and dropped:
        # A question written entirely out of common words still has rarest
        # terms, and asking with them beats asking with nothing.
        rarest = sorted(dropped, key=lambda term: (frequencies.get(term, 0), term))[: max(floor, 0)]
        kept = set(rarest)
        dropped = {term: reason for term, reason in dropped.items() if term not in kept}
    kept, capped = _apply_cap(kept)
    return TermSelection(kept=kept, dropped={**dropped, **capped}, ceiling_applied=True)


def _scored_text(candidate: dict[str, Any]) -> str:
    title = candidate.get("title") or ""
    tags = " ".join(candidate.get("tags") or [])
    aliases = " ".join(candidate.get("aliases") or [])
    body = (candidate.get("body") or "")[:_SCORED_BODY_CHARS]
    return f"{title} {aliases} {tags} {body}"


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
