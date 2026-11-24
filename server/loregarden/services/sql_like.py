"""The one place a `LIKE` contains-pattern is built, with its ESCAPE.

Zero `ESCAPE` clauses existed tree-wide before this module, and every
contains-pattern was an `f"%{needle}%"` written beside its predicate. That
reads caller text as a wildcard expression in both directions: our callers
search for underscore-dense Python identifiers (`memory_store` matching
`memoryXstore`), and our data side is `legacy_external_id` and `tags_json`,
which are underscore- and percent-dense themselves.

A module-private helper would be the second copy by construction, so this is
the only builder. SQLModel callers spell
``col(X).like(p.pattern, escape=p.escape)``; raw-sqlite callers append
``ESCAPE ?`` and bind ``p.escape``.

A site that genuinely needs no escape — a fixed prefix with no caller text in
it — says so on the line with a ``like-ok:`` waiver and why, the same shape as
this repo's ``silent-ok:``/``ux-ok:`` waivers.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Backslash, because SQLite has no default escape character and the three
#: things needing escaping (`%`, `_`, the escape itself) must each be a single
#: character that is not itself a wildcard.
ESCAPE_CHARACTER = "\\"


@dataclass(frozen=True)
class LikePattern:
    """A `LIKE` pattern and the escape character that reads it.

    Frozen because the two travel together: a caller that kept the pattern and
    dropped the escape would silently restore the wildcard behaviour this
    module exists to remove.
    """

    pattern: str
    escape: str


def like_contains(value: str) -> LikePattern:
    """A pattern matching rows whose column *contains* `value` literally.

    `value` must be text. A missing MCP argument arriving as `None` would
    stringify to the pattern `%None%`, which matches rows whose text says
    "None" — a wrong answer dressed as a normal one — so a non-string is
    refused rather than coerced.
    """
    if not isinstance(value, str):  # py-org: allow-isinstance - caller text from an MCP argument
        raise TypeError(f"a LIKE needle must be text, not {type(value).__name__}")
    escaped = (
        value.replace(ESCAPE_CHARACTER, ESCAPE_CHARACTER * 2)
        .replace("%", f"{ESCAPE_CHARACTER}%")
        .replace("_", f"{ESCAPE_CHARACTER}_")
    )
    return LikePattern(pattern=f"%{escaped}%", escape=ESCAPE_CHARACTER)
