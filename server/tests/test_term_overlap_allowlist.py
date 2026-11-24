"""An agent-supplied query is reduced to an allowlisted token set (AC8).

`_WORD = [a-zA-Z_][a-zA-Z0-9_]+` is already an allowlist tokenizer, and that is
the point: the property that every character able to reshape an expression is
gone before a term is built is currently *incidental*. These tests make it
explicit, so a later change that widens the regex for one caller has to
argue with a red test rather than with a comment.

Two policies ride on top of it. A **ceiling** drops terms so common they
separate nothing, and a **floor** keeps the rarest terms whatever the ceiling
says — a question written entirely out of common words must still return
something, because a thin match beats no match. Corpus counts arrive as an
argument; no `Session` reaches this module.

The ceiling's own behaviour is pinned at `prod_shape` volume in
`test_prior_work_basis.py`, where a real corpus count exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

from loregarden.services.term_overlap import STOPWORDS, select_terms, terms

_MODULE = Path(__file__).resolve().parents[1] / "loregarden" / "services" / "term_overlap.py"

_METACHARACTERS = "%_\\'\"();-- /**/ [] {} ^$.|?+ \t\n"


# --- the allowlist itself ---------------------------------------------------


def test_a_query_of_metacharacters_yields_the_same_terms_as_one_without_them():
    """Nothing able to reshape an expression survives tokenizing.

    The needle reaching a LIKE predicate is built from these terms, so this is
    the property that makes `sql_like`'s ESCAPE a second line rather than the
    only one.
    """
    clean = "retrieval basis emptiness"
    dirty = f"retrieval{_METACHARACTERS}basis' OR 1=1 --{_METACHARACTERS}emptiness"
    assert select_terms(dirty).kept == select_terms(clean).kept


def test_every_kept_term_is_drawn_from_the_allowlisted_alphabet():
    selection = select_terms("DROP TABLE memory_nodes; -- 100% of them")
    assert all(term.replace("_", "").isalnum() for term in selection.kept)
    assert all(term == term.lower() for term in selection.kept)


def test_the_allowlist_entry_point_is_public_and_is_the_one_the_module_uses():
    """A private reduction is a reduction nobody else can reuse.

    `prior_work.py` deletes its verbatim copy and imports these names; an entry
    point that stayed underscore-prefixed would invite a third copy.
    """
    assert not terms.__name__.startswith("_")
    assert not select_terms.__name__.startswith("_")
    assert select_terms("memory store retrieval").kept <= terms("memory store retrieval")


def test_prior_work_no_longer_keeps_its_own_tokenizer():
    """term_overlap's docstring named prior_work as the de-dupe site."""
    source = (_MODULE.parent / "prior_work.py").read_text(encoding="utf-8")
    assert "_STOPWORDS" not in source
    assert "_MIN_TERM_LEN" not in source
    assert "term_overlap" in source


def test_no_session_reaches_this_module():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {name.split(".")[0] for name in imported} & {"sqlmodel", "sqlalchemy"}


# --- the ceiling ------------------------------------------------------------


def test_the_ceiling_drops_a_term_matching_more_than_a_fifth_of_the_corpus():
    selection = select_terms(
        "memory retrieval basis",
        document_frequency={"memory": 300, "retrieval": 4, "basis": 2},
        corpus_total=1000,
    )
    assert "memory" not in selection.kept
    assert {"retrieval", "basis"} <= selection.kept
    assert selection.dropped["memory"]


def test_a_term_exactly_at_the_threshold_is_kept():
    """ "More than a fifth" is the written rule; a boundary off by one changes
    which terms an agent's question keeps."""
    selection = select_terms(
        "memory retrieval",
        document_frequency={"memory": 200, "retrieval": 1},
        corpus_total=1000,
    )
    assert "memory" in selection.kept


def test_the_ceiling_reports_that_it_did_not_run_rather_than_a_zero():
    """The memory graph leg has no corpus count; a zero there would read as
    "nothing is common", which is a measurement nobody made."""
    selection = select_terms("memory retrieval basis")
    assert selection.ceiling_applied is False
    assert selection.kept == terms("memory retrieval basis")
    assert selection.dropped == {} or all(
        reason != "too_common" for reason in selection.dropped.values()
    )


def test_the_ceiling_reports_that_it_ran_when_a_corpus_count_exists():
    selection = select_terms(
        "memory retrieval",
        document_frequency={"memory": 300, "retrieval": 4},
        corpus_total=1000,
    )
    assert selection.ceiling_applied is True


# --- the floor --------------------------------------------------------------


def test_a_question_of_only_common_words_still_returns_its_rarest_terms():
    """The ceiling without a floor turns a vague question into no question.

    Every term here is over the threshold; the floor keeps the two rarest
    rather than returning nothing, because a thin match beats no match.
    """
    selection = select_terms(
        "workflow orchestration pipeline stages",
        document_frequency={
            "workflow": 900,
            "orchestration": 800,
            "pipeline": 700,
            "stages": 950,
        },
        corpus_total=1000,
    )
    assert selection.kept
    assert selection.kept <= {"pipeline", "orchestration"}
    assert "stages" not in selection.kept


def test_the_floor_keeps_the_rarest_and_not_an_arbitrary_pair():
    selection = select_terms(
        "alpha bravo charlie",
        document_frequency={"alpha": 999, "bravo": 500, "charlie": 600},
        corpus_total=1000,
        floor=1,
    )
    assert selection.kept == {"bravo"}


def test_the_floor_cannot_resurrect_a_term_the_allowlist_never_built():
    """A floor that reached past the tokenizer would be a second entry point."""
    text = "'; DROP TABLE memory_nodes --"
    selection = select_terms(text, document_frequency={}, corpus_total=1000)
    assert selection.kept == terms(text)
    assert all(term.replace("_", "").isalnum() for term in selection.kept)


# --- what was dropped, and why, for the basis -------------------------------


def test_the_selection_says_which_terms_were_dropped_and_why():
    """The basis reports this; a bare set would make an empty answer unexplainable."""
    selection = select_terms(
        "memory retrieval this",
        document_frequency={"memory": 900, "retrieval": 2},
        corpus_total=1000,
    )
    assert set(selection.dropped) >= {"memory"}
    assert all(reason.strip() for reason in selection.dropped.values())
    assert "this" in STOPWORDS
    assert "this" not in selection.kept
