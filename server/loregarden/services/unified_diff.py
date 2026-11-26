"""Parse `git diff` output into the sections a diff artifact renders.

A hunk header (`@@ -a,b +c,d @@`) says how many old and new lines follow, so
lines inside a hunk are read by count, never by their text: a deleted line
reading `-- note` arrives as `--- note`, and an added `++ b/x` as `+++ b/x`,
both of which look like file headers to a parser that goes by prefix.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

MAX_DIFF_LINES = 400
MAX_DIFF_LINE_CHARS = 500


class DiffRow(StrEnum):
    """A rendered row's kind, as the client's diff view reads it."""

    ADDED = "a"
    DELETED = "d"
    CONTEXT = "c"
    HUNK = "h"


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
#: Inside a hunk, a line's first character: its row, and the old and new lines it uses.
#: An empty line is blank context whose space an editor trimmed.
_HUNK_LINES: dict[str, tuple[DiffRow, int, int]] = {
    "-": (DiffRow.DELETED, 1, 0),
    "+": (DiffRow.ADDED, 0, 1),
    " ": (DiffRow.CONTEXT, 1, 1),
    "": (DiffRow.CONTEXT, 1, 1),
}
_TRUNCATED_ROW = {"type": DiffRow.CONTEXT, "ln": "", "text": "… diff truncated …"}


def path_from_diff_git(line: str) -> str:
    """The new path named by a `diff --git a/<old> b/<new>` line.

    When old and new are the same length (always, unless renamed) the line is
    `a/P b/Q` split evenly, however many spaces the paths hold.
    """
    rest = line.removeprefix("diff --git ")
    half, odd = divmod(len(rest) - 1, 2)
    if odd == 0 and rest[half : half + 1] == " ":
        old, new = rest[:half], rest[half + 1 :]
        if old.startswith("a/") and new.startswith("b/"):
            return new[2:]
    parts = rest.split()
    if len(parts) >= 2 and parts[1].startswith("b/"):
        return parts[1][2:]
    if parts and parts[0].startswith("a/"):
        return parts[0][2:]
    return rest


class _Sections:
    def __init__(self, stat_overrides: dict[str, tuple[int, int]] | None) -> None:
        self.stat_overrides = stat_overrides or {}
        self.sections: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.total_lines = 0

    def finalize(self) -> None:
        current = self.current
        self.current = None
        if not current or not current["lines"]:
            return
        path = current["path"]
        add, delete = self.stat_overrides.get(path, (current["add"], current["del"]))
        self.sections.append(
            {
                "path": path,
                "add": add,
                "del": delete,
                "lines": current["lines"],
                "truncated": current["truncated"],
            }
        )

    def start(self, path: str) -> None:
        self.finalize()
        self.current = {"path": path, "add": 0, "del": 0, "lines": [], "truncated": False}

    def push(self, row_type: DiffRow, text: str) -> None:
        current = self.current
        if current is None:
            return
        if self.total_lines >= MAX_DIFF_LINES:
            if not current["truncated"]:
                current["lines"].append(dict(_TRUNCATED_ROW))
                current["truncated"] = True
            return
        current["lines"].append({"type": row_type, "ln": "", "text": text[:MAX_DIFF_LINE_CHARS]})
        self.total_lines += 1
        if row_type is DiffRow.ADDED:
            current["add"] += 1
        elif row_type is DiffRow.DELETED:
            current["del"] += 1


def parse_unified_diff(
    text: str,
    *,
    stat_overrides: dict[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    out = _Sections(stat_overrides)
    old_left = new_left = 0

    for raw in text.splitlines():
        if old_left > 0 or new_left > 0:
            marker = raw[:1]
            if marker in _HUNK_LINES:
                row, old_used, new_used = _HUNK_LINES[marker]
                old_left -= old_used
                new_left -= new_used
                out.push(row, raw[1:])
                continue
            if marker == "\\":  # "\ No newline at end of file"
                continue
            # A line no hunk can hold: the counts were wrong, read it as a header.
            old_left = new_left = 0

        if raw.startswith("diff --git "):
            out.start(path_from_diff_git(raw))
        elif raw.startswith("+++ b/"):
            path = raw[6:].rstrip("\t")
            if out.current is None or out.current["path"] != path:
                out.start(path)
        elif hunk := _HUNK.match(raw):
            old_left = int(hunk.group(1) or 1)
            new_left = int(hunk.group(2) or 1)
            out.push(DiffRow.HUNK, raw)

    out.finalize()
    return out.sections
