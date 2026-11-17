"""Pure predicates over a commit subject and a path. No git, no I/O, no DB.

Both rule sets are frozen module constants rather than inline literals,
because each is a closed vocabulary the rest of the package compares against
and the order of the path rules is normative.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from loregarden.models.domain.code_signal_enums import PathKind

#: A Conventional-Commits subject whose TYPE is a fix: an optional `(scope)`,
#: an optional `!`, then the colon. Anchored and evaluated in Python, never
#: handed to git's own subject search: measured on this repository, asking git
#: for `^fix` returns 330 against 316 subjects that really start with `fix`,
#: because it reads the body too. The anchor is what rejects `fixes:`, `fixup!`,
#: `Revert "fix: …"` and `patchwork:`; the colon is what rejects `fix thing`.
FIX_SUBJECT_PATTERN = re.compile(r"(?:fix|bugfix|hotfix|patch)(?:\([^()]*\))?!?:", re.IGNORECASE)

#: Directory segments that make everything beneath them a test. Matched as
#: whole path SEGMENTS, never as substrings: `src/contest/app.py` and
#: `latest_results.py` are source, and misreading either zeroes the bug-fix
#: count that consumers are here for.
TEST_DIR_SEGMENTS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing"})
#: File-name shapes that make a file a test wherever it sits.
TEST_NAME_PREFIXES = ("test_", "conftest")
TEST_NAME_INFIXES = (".test.", ".spec.", "_test.")

DOC_DIR_SEGMENTS = frozenset({"doc", "docs"})
DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})

CONFIG_DIR_SEGMENTS = frozenset({".github", ".vscode", ".idea", "config"})
CONFIG_SUFFIXES = frozenset(
    {".toml", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".json", ".lock", ".env", ".properties"}
)
CONFIG_NAMES = frozenset(
    {
        "dockerfile",
        "makefile",
        "taskfile",
        "procfile",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".dockerignore",
        ".npmrc",
        ".nvmrc",
    }
)


def is_fix_subject(subject: str) -> bool:
    """Whether this commit subject declares itself a fix."""
    return FIX_SUBJECT_PATTERN.match(subject) is not None


def _is_test(segments: tuple[str, ...], name: str) -> bool:
    if any(segment.lower() in TEST_DIR_SEGMENTS for segment in segments[:-1]):
        return True
    return name.startswith(TEST_NAME_PREFIXES) or any(part in name for part in TEST_NAME_INFIXES)


def _is_doc(segments: tuple[str, ...], suffix: str) -> bool:
    return suffix in DOC_SUFFIXES or any(
        segment.lower() in DOC_DIR_SEGMENTS for segment in segments[:-1]
    )


def _is_config(segments: tuple[str, ...], name: str, suffix: str) -> bool:
    if name in CONFIG_NAMES or suffix in CONFIG_SUFFIXES:
        return True
    return any(segment.lower() in CONFIG_DIR_SEGMENTS for segment in segments[:-1])


def classify_path(path: str) -> PathKind:
    """What kind of file this path is. FIRST MATCH WINS, in this order.

    The order is normative: TEST before DOC before CONFIG before SOURCE, so
    `server/tests/fixtures/x.json` is a TEST and not a CONFIG. SOURCE is the
    fallthrough rather than an "unknown" kind — treating a language this list
    has not met as not-code would silently drop its bug-fix signal.
    """
    parts = PurePosixPath(path).parts
    name = parts[-1].lower() if parts else ""
    suffix = PurePosixPath(name).suffix

    if _is_test(parts, name):
        return PathKind.TEST
    if _is_doc(parts, suffix):
        return PathKind.DOC
    if _is_config(parts, name, suffix):
        return PathKind.CONFIG
    return PathKind.SOURCE


__all__ = ["FIX_SUBJECT_PATTERN", "classify_path", "is_fix_subject"]
