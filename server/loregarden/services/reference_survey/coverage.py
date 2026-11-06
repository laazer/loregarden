"""The directory map a pass reports its coverage against.

SKILL.md has required mapping every top-level directory with a file count before
reading anything; here the map is data. It is taken with `lstat` and a directory
walk, nothing else: no file is opened, no link followed, nothing run.

A count is OBSERVED at the commit it was taken at. "This directory is where the
rules live" is an inference drawn from it, and is recorded as one elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Two levels: the top level the skill asks for, and one below it so a pass can
#: be assigned `docs/design` rather than all 400 files of `docs`.
MAP_DEPTH = 2
#: How deep the map may go to break up a directory larger than a pass's budget.
MAX_SPLIT_DEPTH = 6

_SKIP_DIRS = frozenset({".git"})


@dataclass(frozen=True)
class MapEntry:
    path: str
    file_count: int
    byte_count: int


def map_tree(
    root: Path,
    *,
    depth: int = MAP_DEPTH,
    split_over: int | None = None,
    max_depth: int = MAX_SPLIT_DEPTH,
) -> list[MapEntry]:
    """Every directory down to `depth`, with the files and bytes beneath it, and
    deeper under any directory holding more than `split_over` files.

    Files directly under a directory the map descends into get a leaf of their
    own (`.` for the root, `docs/.` for `docs`), so the leaves of the map sum to
    the whole tree and a slice built from leaves misses no file.

    `split_over` is the pass's file budget. A leaf larger than the budget was
    never picked while anything ranked was left (`passes.assign_slice` fills to
    the budget), and on beluga that starved its whole UI, `packages/mac` with
    895 files, through five passes. Descending into a directory too big to read
    at once gives the slice pieces it can take. Only loose files can still make
    an oversized leaf, and `max_depth` bounds the walk.
    """
    totals, loose, children = _walk(root.resolve())

    def descends(path: str, level: int) -> bool:
        if level < depth:
            return True
        too_big = split_over is not None and totals[path][0] > split_over
        # A directory of loose files descends too: its files become `path/.`, so
        # an oversized leaf named for a directory always means "not split yet"
        # and `oversized_directory_leaves` can tell it apart from the stored map.
        return too_big and level < max_depth

    entries: dict[str, list[int]] = {}
    pending = [("", 0)]
    while pending:
        path, level = pending.pop()
        if path:
            entries[path] = totals[path]
        if path and not descends(path, level):
            continue
        if loose.get(path):
            entries[f"{path}/." if path else "."] = loose[path]
        pending.extend((child, level + 1) for child in children.get(path, ()))
    return [MapEntry(path, counts[0], counts[1]) for path, counts in sorted(entries.items())]


def _walk(root: Path) -> tuple[dict[str, list[int]], dict[str, list[int]], dict[str, set[str]]]:
    """[files, bytes] beneath each directory, [files, bytes] directly in each, and
    each directory's subdirectories; keyed by path relative to `root` ("" is root)."""
    totals: dict[str, list[int]] = {}
    loose: dict[str, list[int]] = {}
    children: dict[str, set[str]] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        parts = Path(current).relative_to(root).parts
        here = "/".join(parts)
        for d in dirs:
            child = "/".join((*parts, d))
            children.setdefault(here, set()).add(child)
            totals.setdefault(child, [0, 0])
        for name in files:
            size = (Path(current) / name).lstat().st_size
            for key in ("/".join(parts[:n]) for n in range(1, len(parts) + 1)):
                totals[key][0] += 1
                totals[key][1] += size
            bucket = loose.setdefault(here, [0, 0])
            bucket[0] += 1
            bucket[1] += size
    return totals, loose, children


def oversized_directory_leaves(
    paths: dict[str, int], split_over: int, *, max_depth: int = MAX_SPLIT_DEPTH
) -> list[str]:
    """Map leaves that are directories holding more than `split_over` files, above
    the depth limit: a map taken before splitting, which a fresh one would break
    up. Loose files (`path/.`) and directories at `max_depth` are what a split map
    legitimately keeps whole, so they are not reported."""
    keys = set(paths)
    return [
        path
        for path, count in paths.items()
        if count > split_over
        and path != "."
        and not path.endswith("/.")
        and len(path.split("/")) < max_depth
        and is_leaf(path, keys)
    ]


def is_leaf(path: str, paths: set[str]) -> bool:
    """An entry with nothing mapped beneath it."""
    prefix = "" if path == "." else path + "/"
    return (
        path == "."
        or path.endswith("/.")
        or not any(p.startswith(prefix) for p in paths if p != path)
    )


def tree_file_total(entries: list[MapEntry]) -> int:
    """Files in the whole tree: the sum over the map's leaves."""
    paths = {e.path for e in entries}
    return sum(e.file_count for e in entries if is_leaf(e.path, paths))
