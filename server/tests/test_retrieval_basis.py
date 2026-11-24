"""The basis an empty answer ships with (AC1, AC10, AC11b).

`services/retrieval_basis.py` is a pure policy module: it imports
`models/domain/enums` and nothing else, so these tests build a basis by hand
rather than through either retrieval surface. The two surfaces are pinned in
`test_prior_work_basis.py` and `test_memory_search_basis.py`.

The criterion this module exists to defend is that a basis must not become a
new confident lie. The obvious field name is one `rows_in_scope` holding the
scan limit, which would tell every downstream stage that an unexamined corpus
was examined. Three separate fields is the shape that cannot say that.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.services.retrieval_basis import (
    EMPTY_IS_NOT_ABSENCE,
    ZERO_CORPUS_CARRIES_NO_INFORMATION,
    RetrievalBasis,
    reduce_to,
    untouched,
)

_MODULE = Path(__file__).resolve().parents[1] / "loregarden" / "services" / "retrieval_basis.py"


def _basis(**overrides) -> RetrievalBasis:
    fields = {
        "corpus_total": 1200,
        "scanned": 400,
        "not_examined": 800,
        "legs": {MemoryStoreKind.GRAPH.value: MemoryStoreState.READ},
    }
    fields.update(overrides)
    return RetrievalBasis(**fields)


# --- AC1(a) three fields, never one "rows in scope" -------------------------


def test_the_basis_reports_three_counts_and_never_one_rows_in_scope():
    """A single number here is the ticket's own failure class, reintroduced.

    `search_prior_work` scans at most 400 settled tickets with no `order_by`.
    One field called `rows_in_scope` would read as "the scope was examined" on
    a corpus of any size, and would pass its own tests.
    """
    basis = _basis()
    assert (basis.corpus_total, basis.scanned, basis.not_examined) == (1200, 400, 800)
    names = set(vars(basis))
    assert "rows_in_scope" not in names
    assert {"corpus_total", "scanned", "not_examined"} <= names


def test_not_examined_is_the_difference_and_cannot_be_asserted_away():
    """A basis whose arithmetic does not hold is not evidence of anything."""
    with pytest.raises((ValueError, AssertionError)):
        _basis(corpus_total=1200, scanned=400, not_examined=0)


# --- AC1(b) one leg vocabulary ----------------------------------------------


def test_leg_states_are_memory_store_state_members():
    basis = _basis(
        legs={
            MemoryStoreKind.GRAPH.value: MemoryStoreState.UNCONFIGURED,
            MemoryStoreKind.VAULT.value: MemoryStoreState.READ,
        }
    )
    assert all(isinstance(state, MemoryStoreState) for state in basis.legs.values())
    assert set(MemoryStoreState) >= set(basis.legs.values())


def test_a_parallel_leg_vocabulary_is_refused_at_construction():
    """The only thing a test can see of "do not declare a second enum".

    Both producers build a `RetrievalBasis`; if the type refuses anything that
    is not a `MemoryStoreState`, a second vocabulary cannot reach a reply
    without this going red.
    """
    with pytest.raises((TypeError, ValueError)):
        _basis(legs={MemoryStoreKind.GRAPH.value: "read"})


def test_the_module_imports_only_the_domain_enums():
    """No sqlmodel, no Session, no Path — the basis is policy, not a query.

    Read from the source rather than from `sys.modules`: an import pulled in
    transitively by another test would make a runtime check pass on a module
    that does import a database.
    """
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"sqlmodel", "sqlalchemy", "sqlite3", "pathlib"}
    assert not {name.split(".")[0] for name in imported} & forbidden
    loregarden_imports = {name for name in imported if name.startswith("loregarden")}
    assert loregarden_imports <= {"loregarden.models.domain.enums"}


# --- AC1(c) the quantity nobody persists ------------------------------------


def test_the_export_share_is_none_and_never_zero():
    """Nobody records what share of a workspace's knowledge reached a store.

    `None` says that. `0` would say the store holds none of it, which is a
    different claim and one no row in this system supports.
    """
    basis = _basis()
    assert basis.exported_into_store is None
    assert basis.exported_into_store is not False
    assert basis.exported_into_store != 0


def test_the_export_share_says_in_its_docstring_that_nothing_persists_it():
    """The field's own text is the only place a later reader meets the reason.

    Asserted on substance, not wording: the annotation must admit `None`, and
    the module must document the field rather than leave a number-shaped hole
    for someone to fill with a rate.
    """
    annotations = RetrievalBasis.__annotations__
    assert "None" in str(annotations["exported_into_store"])
    assert "exported_into_store" in (RetrievalBasis.__doc__ or "")


# --- AC1(d) the two fixed notes ---------------------------------------------


def test_every_basis_carries_both_fixed_notes():
    basis = _basis()
    assert EMPTY_IS_NOT_ABSENCE in basis.notes
    assert ZERO_CORPUS_CARRIES_NO_INFORMATION in basis.notes


def test_a_non_empty_reply_with_a_degraded_leg_still_carries_a_basis():
    """A basis is emitted on every reply, not only on an empty one.

    The case AC11 exists for: hits came back, and one leg was never read.
    """
    basis = _basis(
        corpus_total=10,
        scanned=10,
        not_examined=0,
        legs={
            MemoryStoreKind.GRAPH.value: MemoryStoreState.READ,
            MemoryStoreKind.VAULT.value: MemoryStoreState.UNCONFIGURED,
        },
    )
    assert EMPTY_IS_NOT_ABSENCE in basis.notes
    assert basis.legs[MemoryStoreKind.VAULT.value] is MemoryStoreState.UNCONFIGURED


def test_the_basis_serializes_for_the_mcp_envelope():
    """`tool_registry` has to `json.dumps` this; leg states must survive as values."""
    payload = _basis().as_dict()
    assert payload["corpus_total"] == 1200
    assert payload["exported_into_store"] is None
    assert payload["legs"][MemoryStoreKind.GRAPH.value] == MemoryStoreState.READ.value


# --- AC10 truncation is a stable multiset difference ------------------------


def test_the_omitted_set_is_correct_for_a_diversified_projection():
    """A tail slice is wrong the moment the projection stops being a prefix.

    Here the emitted three are picked for diversity — first, middle, last — so
    `all[len(emitted):]` would report `c, d, e` and name two rows that were in
    fact emitted.
    """
    everything = ["a", "b", "c", "d", "e", "f"]
    emitted = ["a", "c", "e"]
    reduction = reduce_to(everything, emitted, reason="diversified", key=str)
    assert (reduction.total, reduction.emitted, reduction.truncated) == (6, 3, 3)
    assert sorted(reduction.omitted) == ["b", "d", "f"]
    assert reduction.reason == "diversified"


def test_the_omitted_set_is_a_multiset_difference_and_keeps_repeats():
    """Two rows sharing a key are two rows. A set difference would lose one."""
    everything = ["a", "a", "b"]
    reduction = reduce_to(everything, ["a"], reason="cap", key=str)
    assert sorted(reduction.omitted) == ["a", "b"]
    assert reduction.truncated == 2


def test_nothing_omitted_reports_an_empty_set_not_a_missing_field():
    reduction = reduce_to(["a", "b"], ["a", "b"], reason="", key=str)
    assert (reduction.total, reduction.emitted, reduction.truncated) == (2, 2, 0)
    assert list(reduction.omitted) == []


def test_the_omitted_set_order_is_stable_across_calls():
    """Two builds of one reply must not differ only in the order of a diff."""
    everything = [f"row-{n}" for n in range(20)]
    emitted = everything[::4]
    first = reduce_to(everything, emitted, reason="cap", key=str)
    second = reduce_to(list(everything), list(emitted), reason="cap", key=str)
    assert list(first.omitted) == list(second.omitted)


# --- AC11(b) the sentence that shrinks rather than cuts ---------------------


def test_untouched_names_at_most_three_areas_and_says_which_scope():
    sentence = untouched(["memory", "runs", "gates", "queue", "hive"], scope="workspace ws")
    assert sentence.count(",") <= 2
    assert "workspace ws" in sentence
    assert sentence.endswith(".")
    assert "hive" not in sentence


def test_untouched_shrinks_its_list_rather_than_cutting_a_sentence():
    areas = ["a-very-long-milestone-name-one", "a-very-long-milestone-name-two"]
    full = untouched(areas, scope="workspace ws", budget=400)
    assert all(area in full for area in areas)

    tight = untouched(areas, scope="workspace ws", budget=len(full) - 10)
    assert tight == "" or (tight.endswith(".") and len(tight) <= len(full) - 10)
    if tight:
        assert sum(area in tight for area in areas) < len(areas)


def test_untouched_returns_an_empty_string_when_even_one_area_will_not_fit():
    assert untouched(["a-very-long-milestone-name-one"], scope="workspace ws", budget=12) == ""


def test_untouched_with_no_zero_hit_areas_says_nothing():
    """Nothing untouched is not a sentence about nothing."""
    assert untouched([], scope="workspace ws") == ""
