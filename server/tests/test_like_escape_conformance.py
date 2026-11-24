"""Every LIKE predicate in the tree, not just the builder (AC9).

`test_sql_like.py` pins what `services/sql_like.py` produces and how SQLite
reads it. It cannot see the half of the criterion that says *all twelve*
predicates migrate: a perfect builder nobody calls leaves every one of
today's unescaped needles exactly where it was, and every test in that module
stays green.

So this module reads the tree instead. It is a conformance sweep, deliberately
written against source text rather than behaviour, because the property is
"there is no second way to do this" and no single call can observe that.

The waiver is real and is the one AC9 names: `gate_parked_sweep.py` matches a
fixed prefix with no caller text in it. A site opts out by saying so on the
line, the same shape as this repo's `silent-ok:`/`ux-ok:` waivers, so a future
unescaped predicate has to argue in writing rather than slip in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from loregarden.services.sql_like import like_contains

_ROOT = Path(__file__).resolve().parents[1] / "loregarden"
_SCANNED = ("services", "api", "mcp")

#: A SQLModel/SQLAlchemy pattern-match call, or a raw-SQL LIKE against a bound
#: parameter. Migrations are out of scope: they are one-shots over data this
#: process already wrote, with no caller text in the needle.
_PREDICATE = re.compile(r"\.i?like\(|\bLIKE\s+[?:]")
_ESCAPED = re.compile(r"escape\s*=|ESCAPE\s*[?:]")
_WAIVER = "like-ok:"


def _predicate_sites() -> list[tuple[Path, int, str]]:
    sites: list[tuple[Path, int, str]] = []
    for directory in _SCANNED:
        for path in sorted((_ROOT / directory).rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                if not _PREDICATE.search(line):
                    continue
                # A predicate may spell its escape on the next line when the
                # call wraps; read a small window rather than one line.
                window = "\n".join(lines[number - 2 : number + 2])
                sites.append((path, number, window))
    return sites


def test_the_sweep_finds_the_predicates_it_is_supposed_to_police():
    """A conformance test that matches nothing passes for the wrong reason.

    The count is a floor, not a fixture: the criterion enumerates twelve
    predicates across eight modules, and a regex that silently stopped
    matching would make every assertion below vacuous.
    """
    sites = _predicate_sites()
    assert len(sites) >= 12
    assert len({path for path, _, _ in sites}) >= 8


def test_every_pattern_match_predicate_carries_an_escape_or_says_why_not():
    unescaped = [
        f"{path.relative_to(_ROOT)}:{number}"
        for path, number, window in _predicate_sites()
        if not _ESCAPED.search(window) and _WAIVER not in window
    ]
    assert unescaped == [], (
        f"these predicates still read caller text as a wildcard expression: {unescaped}"
    )


def test_no_module_builds_its_own_contains_pattern():
    """A module-private helper is the second copy by construction.

    Zero ESCAPE clauses existed tree-wide before this change, so the thing to
    stop is not a bad builder but a private one — an `f"%{needle}%"` written
    beside the predicate, which carries no escape character for anyone to
    pass to the driver.
    """
    needle_literal = re.compile(r"""f?["']%[{%]""")
    offenders: list[str] = []
    for directory in _SCANNED:
        for path in sorted((_ROOT / directory).rglob("*.py")):
            if path.name == "sql_like.py":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if needle_literal.search(line) and _WAIVER not in line:
                    offenders.append(f"{path.relative_to(_ROOT)}:{number}")
    assert offenders == [], f"contains-patterns built outside sql_like.py: {offenders}"


# --- the builder's own corners ---------------------------------------------


def test_the_builder_is_deterministic_across_calls():
    """Two replies built from one needle must not differ in their escape char.

    A per-call or random escape character would make a cached or compared
    pattern unstable, and the failure would only show where two patterns meet.
    """
    first, second = like_contains("memory_store"), like_contains("memory_store")
    assert (first.pattern, first.escape) == (second.pattern, second.escape)


def test_the_escape_character_is_not_itself_a_wildcard():
    """`%` or `_` as the escape character makes the pattern unreadable."""
    escape = like_contains("x").escape
    assert escape not in {"%", "_"}
    assert len(escape) == 1


@pytest.mark.parametrize("needle", [None, 7, b"bytes", ["memory_store"]])
def test_a_needle_that_is_not_text_is_refused_rather_than_stringified(needle):
    """`f"%{None}%"` is `%None%`, which matches a row whose text says "None".

    Caller text arrives from an MCP argument, so a missing field is a live
    input. Silently searching for the word "None" is a wrong answer dressed as
    a normal one.
    """
    with pytest.raises((TypeError, ValueError, AttributeError)):
        like_contains(needle)


def test_a_needle_of_every_escape_relevant_character_round_trips():
    """Combinatorial rather than one metacharacter at a time."""
    import sqlite3

    escape = like_contains("x").escape
    literal = f"a%b_c{escape}d%%e__f"
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.executemany(
            "INSERT INTO t (v) VALUES (?)",
            [(literal,), ("aXbYcZdQQeRRf",), ("a%b_c",), ("",)],
        )
        built = like_contains(literal)
        rows = {
            row[0]
            for row in conn.execute(
                "SELECT v FROM t WHERE v LIKE ? ESCAPE ?", (built.pattern, built.escape)
            )
        }
        assert rows == {literal}
    finally:
        conn.close()


def test_a_long_needle_is_still_escaped_character_for_character():
    """Stress: no truncation, no caching by prefix, no silent cap."""
    needle = "_" * 2000
    built = like_contains(needle)
    assert built.pattern.count(built.escape) == 2000
    assert built.pattern.startswith("%") and built.pattern.endswith("%")


def test_unicode_in_a_needle_survives_untouched():
    built = like_contains("память_store")
    assert "память" in built.pattern
