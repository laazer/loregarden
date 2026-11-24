"""The corners of the basis, the reduction and the sentence (AC1, AC10, AC11b).

`test_retrieval_basis.py` pins the shape the criteria describe. This module
pins what happens at the edges of that shape, because a basis is evidence and
a piece of evidence that is wrong at an edge is worse than none: every
downstream stage reads it as measured fact.

The three failure modes here are the ones a green suite does not catch.
A frozen dataclass holding a *mutable* mapping is not frozen in the way the
caller needs. A multiset difference computed from a projection nobody checked
against its source reports omissions that never existed. And a sentence under
a character budget is only safe if the budget is honoured at every length,
not at the one length a test picked.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.services.retrieval_basis import (
    EMPTY_IS_NOT_ABSENCE,
    ZERO_CORPUS_CARRIES_NO_INFORMATION,
    reduce_to,
    untouched,
)

# One default basis, built next door: the shape these tests push on the edges
# of is the same shape `test_retrieval_basis.py` pins in the middle.
from tests.test_retrieval_basis import _basis

# --- AC1 the counts cannot describe an impossible scan ----------------------


@pytest.mark.parametrize(
    ("corpus_total", "scanned", "not_examined"),
    [
        (-1, 0, 0),
        (10, -1, 11),
        (10, 4, -1),
        (10, 20, -10),
        (0, 1, -1),
    ],
    ids=[
        "negative-corpus",
        "negative-scanned",
        "negative-remainder",
        "scanned-exceeds",
        "scan-without-corpus",
    ],
)
def test_counts_that_describe_an_impossible_scan_are_refused(corpus_total, scanned, not_examined):
    """Reading more rows than exist is the arithmetic form of this ticket's
    failure class: a number that cannot be true, asserted as measurement."""
    with pytest.raises((ValueError, AssertionError)):
        _basis(corpus_total=corpus_total, scanned=scanned, not_examined=not_examined)


def test_an_empty_corpus_is_a_legitimate_basis_and_says_so():
    """Zero is the one case where an empty list carries no information at all,
    which is why AC1(d) makes that a fixed note rather than a conditional."""
    basis = _basis(corpus_total=0, scanned=0, not_examined=0)
    assert ZERO_CORPUS_CARRIES_NO_INFORMATION in basis.notes
    assert EMPTY_IS_NOT_ABSENCE in basis.notes


def test_the_two_fixed_notes_are_two_distinct_sentences():
    """One note doing both jobs would make the zero-corpus case unreadable."""
    assert EMPTY_IS_NOT_ABSENCE.strip()
    assert ZERO_CORPUS_CARRIES_NO_INFORMATION.strip()
    assert EMPTY_IS_NOT_ABSENCE != ZERO_CORPUS_CARRIES_NO_INFORMATION


def test_the_basis_is_frozen_against_a_downstream_edit():
    basis = _basis()
    with pytest.raises((AttributeError, TypeError)):
        basis.corpus_total = 0


def test_the_legs_mapping_cannot_be_changed_after_the_reply_is_built():
    """A frozen dataclass around a live dict is not frozen.

    Two replies built in one request would otherwise be able to rewrite each
    other's leg states, and the second reader would see a state nothing
    sampled.
    """
    source = {MemoryStoreKind.GRAPH.value: MemoryStoreState.READ}
    basis = _basis(legs=source)
    source[MemoryStoreKind.VAULT.value] = MemoryStoreState.ERRORED
    assert MemoryStoreKind.VAULT.value not in basis.legs
    with pytest.raises((TypeError, AttributeError)):
        basis.legs[MemoryStoreKind.GRAPH.value] = MemoryStoreState.ERRORED


def test_every_leg_state_including_errored_survives_the_envelope():
    """ERRORED is in the vocabulary and is the state a half-read store is in.

    A basis that can only express READ and UNCONFIGURED would record a store
    that raised as a store that was never configured.
    """
    basis = _basis(
        legs={
            MemoryStoreKind.GRAPH.value: MemoryStoreState.ERRORED,
            MemoryStoreKind.VAULT.value: MemoryStoreState.NOT_QUERIED,
        }
    )
    payload = basis.as_dict()
    assert payload["legs"][MemoryStoreKind.GRAPH.value] == MemoryStoreState.ERRORED.value


def test_the_basis_survives_json_dumps_without_a_custom_encoder():
    """`tool_registry` serializes this. An `Enum` member in a value is a
    `TypeError` at the boundary, where it reads as a tool fault."""
    text = json.dumps(_basis().as_dict())
    assert json.loads(text)["not_examined"] == 800


# --- AC10 the reduction cannot invent or lose an omission -------------------


def test_a_projection_holding_something_the_source_never_had_is_refused():
    """A multiset difference silently absorbs it, and `truncated` then counts
    rows that were never candidates. The reduction is evidence about one
    collection; two collections is a bug, not a reduction."""
    with pytest.raises((ValueError, AssertionError)):
        reduce_to(["a", "b"], ["a", "ghost"], reason="cap", key=str)


def test_truncated_always_equals_the_size_of_the_omitted_set():
    """The one invariant a reader uses without looking at `omitted`."""
    for everything, emitted in (
        (["a", "b", "c"], []),
        (["a", "a", "a"], ["a"]),
        ([], []),
        (list("abcdef"), list("ace")),
    ):
        reduction = reduce_to(everything, emitted, reason="cap", key=str)
        assert reduction.truncated == len(list(reduction.omitted))
        assert reduction.total == len(everything)
        assert reduction.emitted == len(emitted)


def test_the_omitted_set_is_keyed_by_the_key_and_not_by_object_identity():
    """Rows are dicts here, which are unhashable; the key function is the
    whole reason the field family takes one."""
    everything = [{"id": n, "title": f"row {n}"} for n in range(4)]
    emitted = [dict(everything[1])]  # an equal row, not the same object
    reduction = reduce_to(everything, emitted, reason="diversified", key=lambda row: row["id"])
    assert reduction.truncated == 3
    assert {row["id"] for row in reduction.omitted} == {0, 2, 3}


def test_two_distinct_rows_sharing_a_key_are_one_multiset_entry_each():
    """A key collision must not delete a row from the omitted set.

    Two settled tickets can share a title; keying on the title and losing one
    would under-report what was dropped, which is the direction that reads as
    reassurance.
    """
    everything = [
        {"id": "a", "title": "same"},
        {"id": "b", "title": "same"},
        {"id": "c", "title": "other"},
    ]
    reduction = reduce_to(everything, [everything[0]], reason="cap", key=lambda r: r["title"])
    assert reduction.truncated == 2


def test_an_empty_source_reduces_to_an_empty_reduction_rather_than_raising():
    reduction = reduce_to([], [], reason="", key=str)
    assert (reduction.total, reduction.emitted, reduction.truncated) == (0, 0, 0)


def test_a_truncated_reduction_names_a_reason():
    """`reason` empty with `truncated` non-zero is a silent cap, which is the
    thing AC10 exists to stop."""
    with pytest.raises((ValueError, AssertionError)):
        reduce_to(["a", "b"], ["a"], reason="", key=str)


# --- AC11(b) the sentence honours its budget at every length ----------------


@pytest.mark.parametrize("budget", list(range(0, 160, 7)))
def test_the_sentence_never_exceeds_its_budget_at_any_length(budget):
    """Swept rather than sampled: a shrink loop that stops one area too late
    passes at the budget a single test happened to pick."""
    sentence = untouched(
        ["retrieval", "memory-graph", "prior-work", "an-unusually-long-milestone-name"],
        scope="workspace loregarden",
        budget=budget,
    )
    assert len(sentence) <= budget
    assert sentence == "" or sentence.endswith(".")


@pytest.mark.parametrize("budget", [0, -1, -1000])
def test_a_budget_of_nothing_or_less_returns_nothing(budget):
    assert untouched(["retrieval"], scope="workspace ws", budget=budget) == ""


def test_the_sentence_is_the_same_on_two_builds_of_one_reply():
    """A set-ordered area list would reshuffle between prompt builds and make
    two identical replies diff against each other."""
    areas = ["retrieval", "memory-graph", "prior-work", "gates"]
    first = untouched(areas, scope="workspace ws")
    second = untouched(list(areas), scope="workspace ws")
    assert first == second


def test_the_sentence_keeps_the_areas_it_was_given_first():
    """The callers pass the *largest* zero-hit areas first. Dropping from the
    front would name the three smallest, which is the least useful three."""
    areas = ["biggest", "middle", "smallest", "tiny"]
    sentence = untouched(areas, scope="workspace ws")
    assert "biggest" in sentence
    assert "tiny" not in sentence


def test_a_single_area_is_still_a_whole_sentence():
    """One zero-hit area is the common case, and a bare word is not a note."""
    sentence = untouched(["retrieval"], scope="workspace ws")
    assert sentence.endswith(".")
    assert "retrieval" in sentence
    assert len(sentence.split()) > 1


def test_duplicate_areas_are_named_once():
    """`GROUP BY` can hand back the same milestone under two spellings of
    nothing; naming it twice burns one of the three slots."""
    sentence = untouched(["retrieval", "retrieval", "memory-graph"], scope="workspace ws")
    assert sentence.count("retrieval") == 1
