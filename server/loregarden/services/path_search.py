"""Fuzzy path lookup for the composer's `@` reference picker.

The editor browses one directory at a time, which is the wrong shape for `@`:
typing `@appact` should find `client/src/components/AppActionBar.tsx` without
knowing where it lives. This walks the workspace once per keystroke-debounced
request and ranks candidates, files and directories alike.

Nothing is read here — an `@` reference inserts a path into the message and the
agent opens it with its own tools. So the file-type filter the editor applies
deliberately does not apply: a path is a path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Workspace
from loregarden.services.file_editor import BLOCKED_DIR_NAMES, resolve_editor_root

#: Ceiling on entries returned to the picker. A menu is read, not scrolled.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
#: Ceiling on entries *considered*. A pathological tree (a vendored monorepo, a
#: data dump) must not turn one keystroke into a multi-second walk.
MAX_SCANNED_ENTRIES = 20_000


@dataclass(frozen=True)
class PathCandidate:
    repo_path: str
    name: str
    is_dir: bool


def _walk(root: Path) -> list[PathCandidate]:
    candidates: list[PathCandidate] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Pruned in place so os.walk never descends into them — the whole point
        # is not paying for node_modules.
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name not in BLOCKED_DIR_NAMES
        )
        current = Path(dirpath)
        for name in dirnames:
            candidates.append(
                PathCandidate(
                    repo_path=(current / name).relative_to(root).as_posix(),
                    name=name,
                    is_dir=True,
                )
            )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            candidates.append(
                PathCandidate(
                    repo_path=(current / name).relative_to(root).as_posix(),
                    name=name,
                    is_dir=False,
                )
            )
        if len(candidates) >= MAX_SCANNED_ENTRIES:
            break
    return candidates[:MAX_SCANNED_ENTRIES]


def _subsequence_span(haystack: str, needle: str) -> int | None:
    """Length of the window the needle's characters span, in order, or None.

    Tighter spans rank higher, which is what makes `appact` prefer
    `AppActionBar.tsx` over a path that happens to scatter those letters across
    three directory names.
    """
    if not needle:
        return 0
    start = -1
    index = 0
    for position, char in enumerate(haystack):
        if char == needle[index]:
            if index == 0:
                start = position
            index += 1
            if index == len(needle):
                return position - start + 1
    return None


def _score(candidate: PathCandidate, query: str) -> int | None:
    """Lower is better; None means no match."""
    name = candidate.name.lower()
    path = candidate.repo_path.lower()

    if name == query:
        return 0
    if name.startswith(query):
        return 10 + len(name)
    name_hit = name.find(query)
    if name_hit >= 0:
        return 100 + name_hit + len(name)
    if path.startswith(query):
        return 200 + len(path)
    path_hit = path.find(query)
    if path_hit >= 0:
        return 300 + path_hit + len(path)
    span = _subsequence_span(name, query)
    if span is not None:
        return 400 + span + len(name)
    span = _subsequence_span(path, query)
    if span is not None:
        return 500 + span + len(path)
    return None


def search_workspace_paths(
    workspace: Workspace,
    query: str,
    *,
    context_root: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Rank workspace files and directories against ``query``.

    An empty query returns the top level, which is what the picker shows the
    moment `@` is typed and before anything else is.
    """
    root = resolve_editor_root(workspace, context_root)
    capped = max(1, min(limit, MAX_LIMIT))
    text = query.strip().lower()

    if not text:
        entries = sorted(
            (candidate for candidate in _top_level(root)),
            key=lambda candidate: (not candidate.is_dir, candidate.name.lower()),
        )
        return [_view(candidate) for candidate in entries[:capped]]

    scored: list[tuple[int, PathCandidate]] = []
    for candidate in _walk(root):
        score = _score(candidate, text)
        if score is not None:
            scored.append((score, candidate))
    scored.sort(key=lambda pair: (pair[0], pair[1].repo_path))
    return [_view(candidate) for _score_value, candidate in scored[:capped]]


def _top_level(root: Path) -> list[PathCandidate]:
    candidates: list[PathCandidate] = []
    try:
        with os.scandir(root) as scan:
            for entry in scan:
                if entry.name.startswith(".") or entry.name in BLOCKED_DIR_NAMES:
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                candidates.append(
                    PathCandidate(repo_path=entry.name, name=entry.name, is_dir=is_dir)
                )
    except PermissionError as exc:
        raise ValueError(f"Permission denied: {root}") from exc
    return candidates


def _view(candidate: PathCandidate) -> dict:
    return {
        "name": candidate.name,
        "repo_path": candidate.repo_path,
        "kind": "directory" if candidate.is_dir else "file",
    }
