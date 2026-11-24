"""Every pattern-match predicate carries an explicit ESCAPE (AC9).

Zero `ESCAPE` clauses exist tree-wide today, which is why a module-private
helper would be the second copy by construction. `services/sql_like.py` is the
one place that builds a contains-pattern, and these tests pin both the pattern
it produces and the behaviour of that pattern against real SQLite — a builder
that returns the right string and an escape character nobody passes to the
driver is the same bug with better unit tests.

The hazard is our own data in both directions: the needle is caller text and
our callers search for underscore-dense Python identifiers, while the data side
is `legacy_external_id` and `tags_json`.
"""

from __future__ import annotations

import sqlite3

import pytest
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.sql_like import LikePattern, like_contains
from loregarden.testing.prod_shape import build_prod_shape
from sqlmodel import Session

_ROWS = (
    "memory_store",
    "memoryXstore",
    "memory store",
    "100% done",
    "100 done",
    "prefix_memory_store_suffix",
)


def _matcher(values):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(v,) for v in values])

    def matching(pattern: LikePattern) -> set[str]:
        return {
            row[0]
            for row in conn.execute(
                "SELECT v FROM t WHERE v LIKE ? ESCAPE ?", (pattern.pattern, pattern.escape)
            )
        }

    return conn, matching


@pytest.fixture(name="rows")
def rows_fixture():
    conn, matching = _matcher(_ROWS)
    yield matching
    conn.close()


def test_the_builder_returns_a_pattern_and_the_escape_character_that_reads_it():
    built = like_contains("plain")
    assert built.pattern == "%plain%"
    assert len(built.escape) == 1


def test_an_underscore_in_the_needle_matches_only_a_literal_underscore(rows):
    """The named case: an agent searching memory for `memory_store`.

    Unescaped, `_` is SQL's any-single-character wildcard, so `memoryXstore`
    comes back as a hit and the agent is told two unrelated modules are the
    same subject.
    """
    hits = rows(like_contains("memory_store"))
    assert "memory_store" in hits
    assert "prefix_memory_store_suffix" in hits
    assert "memoryXstore" not in hits
    assert "memory store" not in hits


def test_a_percent_in_the_needle_matches_only_a_literal_percent(rows):
    assert rows(like_contains("100%")) == {"100% done"}


def test_the_escape_character_itself_is_escaped():
    """Otherwise a needle carrying the escape char builds a broken pattern.

    The character is read back from the builder rather than assumed, so the
    test still pins the property if the implementation picks a different one.
    """
    escape = like_contains("x").escape
    literal = f"back{escape}slash"
    conn, matching = _matcher([literal, "backslash", f"back{escape}{escape}slash"])
    try:
        assert matching(like_contains(literal)) == {literal}
    finally:
        conn.close()


def test_a_needle_of_nothing_but_metacharacters_matches_only_a_literal_one():
    conn, matching = _matcher(["%_%", "anything", ""])
    try:
        assert matching(like_contains("%_%")) == {"%_%"}
    finally:
        conn.close()


def test_an_empty_needle_matches_every_row(rows):
    """`%%` is the honest answer to "contains the empty string"; no special case."""
    assert rows(like_contains("")) == set(_ROWS)


def test_the_pattern_is_frozen_so_a_caller_cannot_edit_the_escape_off():
    built = like_contains("memory_store")
    with pytest.raises((AttributeError, TypeError)):
        built.escape = "!"


# --- the measurement AC9 asks be recorded as evidence -----------------------


def test_escaping_the_memory_needle_drops_only_wildcard_hits_at_prod_shape_volume(
    client, isolated_db, capsys
):
    """Before/after hit counts at `prod_shape` volume, not on a three-row fixture.

    A tokenizer or escape change to an agent's recall path is a silent
    retrieval regression unless somebody counts. The assertion is the honest
    one: escaping can only ever remove hits, and it must remove the planted
    wildcard collision and nothing else.

    The two counts are printed so the implement stage can lift them into the
    evidence artifact the criterion asks for.
    """
    memory = AgentMemoryService.from_settings()
    with Session(isolated_db) as session:
        build_prod_shape(session, graph_path=memory.graph_path)

    slug = "loregarden"
    graph = memory.require_graph(slug)
    graph.upsert_node(
        title="memory_store needle",
        body="The literal identifier an agent searches for.",
        workspace_slug=slug,
        node_type="memory",
    )
    collision = graph.upsert_node(
        title="memoryXstore collision",
        body="Matched only because an unescaped underscore is a wildcard.",
        workspace_slug=slug,
        node_type="memory",
    )

    def ids(needle_pattern: str, escape: str | None) -> set[str]:
        sql = "SELECT id FROM memory_nodes WHERE title LIKE ?" + (" ESCAPE ?" if escape else "")
        params = (needle_pattern, escape) if escape else (needle_pattern,)
        with graph.connection() as conn:
            return {row[0] for row in conn.execute(sql, params)}

    before = ids("%memory_store%", None)
    built = like_contains("memory_store")
    after = ids(built.pattern, built.escape)

    print(f"AC9 memory_store needle at prod_shape volume: before={len(before)} after={len(after)}")
    assert after < before
    assert before - after == {collision["id"]}
