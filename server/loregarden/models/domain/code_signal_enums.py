"""Vocabulary for the behavioural git signals (lg-code-knowledge-299).

Its own module rather than a corner of `enums`, following the
`merge_queue_enums` / `pull_request_enums` precedent. Three closed sets, so
none of them is a string compared across the package.
"""

from __future__ import annotations

from enum import StrEnum


class SignalKind(StrEnum):
    """Which top-N signal `top_files_by` reads.

    Exactly one member per top-N index on `file_git_signals`. An index with no
    caller is a guess, and a member with no index is a silent table scan, so
    the two lists are kept the same length on purpose.
    """

    CHURN_COMMITS = "churn_commits"
    BUGFIX_COMMITS = "bugfix_commits"


class PathKind(StrEnum):
    """What a path is, for bug-fix attribution only.

    Rows are written for every path that appears in the window whatever its
    kind; the kind decides only whether a `fix:` commit raises that path's
    `bugfix_commits`. A doc-only, test-only or config-only fix therefore
    raises nobody's count (AC3).
    """

    TEST = "test"
    DOC = "doc"
    CONFIG = "config"
    SOURCE = "source"


class EmptyBasis(StrEnum):
    """Why a computation came back with no rows — never why it failed.

    Exactly two members, because exactly two are reachable. A read that fails
    raises `GitSignalsReadError` instead, so "empty" and "failed" cannot
    collapse into one answer and a refresh cannot delete good rows over a bad
    read. The plan's third member, `NO_SOURCE_FILES`, was dropped in the spec
    stage: rows exist for every path in the window regardless of `PathKind`,
    so nothing can set it.
    """

    NO_HISTORY = "no_history"
    NO_COMMITS_IN_WINDOW = "no_commits_in_window"


__all__ = ["EmptyBasis", "PathKind", "SignalKind"]
