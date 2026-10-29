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

_SKIP_DIRS = frozenset({".git"})


@dataclass(frozen=True)
class MapEntry:
    path: str
    file_count: int
    byte_count: int


def map_tree(root: Path, *, depth: int = MAP_DEPTH) -> list[MapEntry]:
    """Every directory down to `depth`, with the files and bytes beneath it.

    Files directly under a directory that is not at the map's depth get a leaf
    of their own (`.` for the root, `docs/.` for `docs`), so the leaves of the map
    sum to the whole tree and a slice built from leaves misses no file.
    """
    root = root.resolve()
    totals: dict[str, list[int]] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        rel = Path(current).relative_to(root)
        parts = rel.parts
        for name in files:
            size = (Path(current) / name).lstat().st_size
            keys = ["/".join(parts[:n]) for n in range(1, min(len(parts), depth) + 1)]
            if len(parts) < depth:
                # Loose files get a leaf of their own, so the leaves sum to the tree.
                keys.append("/".join((*parts, ".")) if parts else ".")
            for key in keys:
                bucket = totals.setdefault(key, [0, 0])
                bucket[0] += 1
                bucket[1] += size
        for d in dirs:
            key = "/".join((*parts, d))
            if len(parts) + 1 <= depth:
                totals.setdefault(key, [0, 0])
    return [MapEntry(path, counts[0], counts[1]) for path, counts in sorted(totals.items())]


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
