"""R1 — the term-overlap policy module, scored with plain dicts and no store.

Every test here maps to a spec criterion (AC1.x) and names the wrong
implementation it exists to catch. The module is pure by design, so nothing in
this file touches a vault, a database, or the clock.
"""

from loregarden.services import prior_work, term_overlap


def _candidate(
    candidate_id: str,
    *,
    title: str = "",
    body: str = "",
    tags: list[str] | None = None,
    updated_at: str = "",
    source: str = "obsidian",
) -> dict:
    return {
        "id": candidate_id,
        "source": source,
        "title": title,
        "body": body,
        "tags": list(tags or []),
        "updated_at": updated_at,
    }


# --- terms() ---------------------------------------------------------------


def test_terms_of_empty_text_is_empty():
    """AC1.1 — no query means no terms, and never an exception."""
    assert term_overlap.terms("") == set()


def test_terms_drops_stopwords_entirely():
    """AC1.1 — an all-stopword query must reduce to nothing, which is what makes
    the empty-query early return in recall_related reachable."""
    assert term_overlap.terms("the that with this") == set()


def test_terms_keeps_distinctive_words_lowercased():
    """AC1.1 — catches a tokenizer that forgets to lowercase or splits wrongly."""
    assert term_overlap.terms("Inherited wisdom retrieval") == {
        "inherited",
        "wisdom",
        "retrieval",
    }


def test_terms_drops_tokens_shorter_than_four_characters():
    """AC1.2 — the inherited minimum length silently drops MCP, RRF, SQL. It is
    asserted so nobody 'fixes' it quietly, and so every other fixture in this
    change knows to carry its overlap in words of four or more characters."""
    assert term_overlap.terms("MCP RRF SQL retrieval") == {"retrieval"}


def test_terms_keeps_a_four_character_word():
    """AC1.2, the other side of the boundary. Dropping 3-character tokens is
    also satisfied by `len(w) > _MIN_TERM_LEN`, which drops 4-character words
    too — fast, loop, path, rate, cost, gate, hook. 'fast' is a term of the
    briefing suite's own realistic title, so the off-by-one would quietly halve
    the query and every downstream fixture would still pass, because they all
    carry their overlap in 5+ character words.

    Pairs with the test above: together they pin `>= 4` exactly, and neither
    alone can."""
    assert term_overlap.terms("fast retrieval") == {"fast", "retrieval"}


def test_terms_drops_stopwords_longer_than_the_minimum_length():
    """AC1.1, the half a length rule cannot fake. The all-stopword fixture above
    uses only 4-character stopwords, so raising _MIN_TERM_LEN satisfies it with
    STOPWORDS = frozenset() and no stopword set at all. These four are 5, 6, 6
    and 10 characters, so only a real stopword set removes them — and they are
    the words loregarden ticket titles are actually made of."""
    assert term_overlap.terms("which should loregarden ticket retrieval") == {"retrieval"}


# --- rank_by_overlap() -----------------------------------------------------


def test_empty_query_terms_rank_to_nothing():
    """AC1.3 — catches a ranker that treats an empty query as 'matches all',
    which would flood the briefing with the whole vault."""
    candidates = [_candidate("a", title="Trusted server throttle", body="anything")]
    assert term_overlap.rank_by_overlap(set(), candidates) == []


def test_candidate_sharing_no_term_is_dropped():
    """AC1.4 — catches a ranker that keeps zero-overlap rows and relies on the
    caller's limit to hide them; unrelated notes would then fill the section."""
    wanted = term_overlap.terms("trusted server throttle")
    ranked = term_overlap.rank_by_overlap(
        wanted, [_candidate("a", title="Sprite batching", body="Unrelated prose.")]
    )
    assert ranked == []


def test_higher_overlap_ranks_first_and_carries_its_count():
    """AC1.5 — catches a ranker that returns candidates in input order, or that
    scores by occurrence count rather than distinct terms."""
    wanted = term_overlap.terms("trusted server throttle called")
    weak = _candidate("weak", title="Throttle notes", body="throttle throttle throttle")
    strong = _candidate("strong", title="Trusted server", body="the throttle is called once")
    ranked = term_overlap.rank_by_overlap(wanted, [weak, strong])
    assert [row["id"] for row in ranked] == ["strong", "weak"]
    assert ranked[0]["overlap"] == 4
    assert ranked[1]["overlap"] == 1


def test_tags_are_scored_in_full():
    """AC1.5 — the scored text is title + tags + bounded body; a ranker that
    scores the body alone loses every tag-only match."""
    wanted = term_overlap.terms("throttle")
    ranked = term_overlap.rank_by_overlap(
        wanted, [_candidate("a", title="Notes", body="nothing here", tags=["throttle"])]
    )
    assert [row["id"] for row in ranked] == ["a"]


def test_equal_overlap_breaks_the_tie_on_recency():
    """AC1.6 — catches a ranker with no tiebreak at all (whose order then falls
    out of list_notes' memory-directory-first concatenation, silently)."""
    wanted = term_overlap.terms("throttle")
    older = _candidate("older", title="Throttle A", updated_at="2026-01-01T00:00:00+00:00")
    newer = _candidate("newer", title="Throttle B", updated_at="2026-06-01T00:00:00+00:00")
    ranked = term_overlap.rank_by_overlap(wanted, [older, newer])
    assert [row["id"] for row in ranked] == ["newer", "older"]


def test_missing_timestamp_sorts_last_without_raising():
    """AC1.6 — a note whose frontmatter omits `updated` reads back as ''. Sorting
    must stay on the raw ISO string: parsing to datetime is how ticket 462 broke
    search_prior_work, and here the TypeError would land in _safely() and turn
    the whole section silently dead again — the exact bug this ticket fixes."""
    wanted = term_overlap.terms("throttle")
    stamped = _candidate("stamped", title="Throttle A", updated_at="2026-01-01T00:00:00+00:00")
    unstamped = _candidate("unstamped", title="Throttle B", updated_at="")
    ranked = term_overlap.rank_by_overlap(wanted, [unstamped, stamped])
    assert [row["id"] for row in ranked] == ["stamped", "unstamped"]


def test_mixed_and_missing_timestamps_never_raise():
    """AC1.6 — the defensive half of the same trap: a mixed corpus (one node
    with a timestamp, one without, one with a differently-shaped one) must rank
    rather than explode."""
    wanted = term_overlap.terms("throttle")
    candidates = [
        _candidate("a", title="Throttle A", updated_at="2026-01-01T00:00:00+00:00"),
        _candidate("b", title="Throttle B", updated_at=""),
        _candidate("c", title="Throttle C", updated_at="2026-01-01T00:00:00.123456+00:00"),
    ]
    ranked = term_overlap.rank_by_overlap(wanted, candidates)
    assert len(ranked) == 3


def test_only_the_first_scored_body_chars_count():
    """AC1.7 — the anti-length-bias bound. Overlap over an unbounded body ranks
    by note LENGTH (live vault: memory avg 292B, learnings max 4157B). Catches
    an implementation that scores the whole body: there, the far-away term would
    match and both candidates would survive."""
    wanted = term_overlap.terms("throttle")
    padding = "generic filler prose about nothing much at all. " * 40
    assert len(padding) > term_overlap._SCORED_BODY_CHARS

    far = _candidate("far", body=padding + " throttle")
    near = _candidate("near", body="throttle " + padding)

    ranked = term_overlap.rank_by_overlap(wanted, [far, near])
    assert [row["id"] for row in ranked] == ["near"]


def test_a_long_generic_note_ranks_below_a_short_distinctive_one():
    """AC1.7 / AC1 intent — the decoy. Without the body bound the long note ties
    on overlap and wins on its newer timestamp; with the bound its extra terms
    fall off the end and the short distinctive note leads."""
    wanted = term_overlap.terms("trusted server throttle called")
    padding = "generic filler prose about nothing much at all. " * 40
    long_generic = _candidate(
        "long",
        title="Weekly notes",
        body="server " + padding + " trusted throttle called",
        updated_at="2026-06-01T00:00:00+00:00",
    )
    short_distinctive = _candidate(
        "short",
        title="Trusted server",
        body="the throttle is called per tool",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    ranked = term_overlap.rank_by_overlap(wanted, [long_generic, short_distinctive])
    assert [row["id"] for row in ranked] == ["short", "long"]


def test_ranking_is_deterministic_for_fully_tied_candidates():
    """AC1.8 — two candidates equal in overlap AND timestamp still need a total
    order, or the briefing shuffles between prompt builds. Spec pins id as the
    final key, descending."""
    wanted = term_overlap.terms("throttle")
    stamp = "2026-01-01T00:00:00+00:00"
    candidates = [
        _candidate("aaa", title="Throttle A", updated_at=stamp),
        _candidate("bbb", title="Throttle B", updated_at=stamp),
    ]
    first = term_overlap.rank_by_overlap(wanted, candidates)
    second = term_overlap.rank_by_overlap(wanted, candidates)
    assert [row["id"] for row in first] == ["bbb", "aaa"]
    assert [row["id"] for row in first] == [row["id"] for row in second]


def test_ranked_records_keep_their_identity_for_the_rrf_seam():
    """R1 return_value — the sibling RRF ticket reads rank from enumerate() and
    identity from (source, id). Catches a ranker that returns formatted briefing
    lines or bare ids, which would foreclose that ticket."""
    wanted = term_overlap.terms("throttle")
    ranked = term_overlap.rank_by_overlap(
        wanted, [_candidate("a", title="Throttle", source="sqlite")]
    )
    assert ranked[0]["id"] == "a"
    assert ranked[0]["source"] == "sqlite"


def test_the_tokenizer_has_not_drifted_from_the_copy_it_was_taken_from():
    """AC3 guard — this module's tokenizer is a deliberate verbatim copy of
    `prior_work.py`'s, which the ticket forbids touching. Nothing else notices
    if one of the two is edited later, and two call sites disagreeing about
    what a term is would be silent: the briefing would just quietly rank
    differently from prior-work search. Reading these names does not modify
    `prior_work`, so AC3 still holds."""
    sample = "Cap how fast a trusted MCP server can be called when throttled"

    assert prior_work._STOPWORDS == term_overlap.STOPWORDS
    assert prior_work._MIN_TERM_LEN == term_overlap._MIN_TERM_LEN
    assert prior_work._terms(sample) == term_overlap.terms(sample)
