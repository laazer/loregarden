"""The repo's own map of itself, and a check that it still tells the truth.

AGENTS.md carries a hand-written structure tree and a "where to look" table.
Written by someone who knows the codebase, it is better than anything derived
from the source — it says which file matters and why, which no parser knows.

Its weakness is drift. Modules get split, functions move, and the map keeps
pointing at where things used to be, sending an agent confidently to the wrong
file. So it is parsed rather than trusted: the paths and symbols it names are
checked against the code, and `verify_code_map` reports what has rotted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAP_FILENAME = "AGENTS.md"
_STRUCTURE_HEADING = "## STRUCTURE"
_LOOKUP_HEADING = "## WHERE TO LOOK"
_FENCE = "```"
#: A backticked token that looks like a Python identifier, e.g. `apply_stage_route`.
_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
#: A backticked path with a file extension, e.g. `services/workflow_state.py`.
_PATH = re.compile(r"`([\w./-]+\.(?:py|ts|tsx|md|json|yaml|yml))`")
#: Roots a bare path in the map may be relative to.
_SEARCH_ROOTS = ("server/loregarden", "server", "client/src", "client", "")


@dataclass
class MapEntry:
    """One row of the "where to look" table."""

    task: str
    paths: list[str]
    symbols: list[str] = field(default_factory=list)


@dataclass
class MapDrift:
    """Something the map claims that the code no longer supports."""

    entry: str
    detail: str


def _section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start == -1:
        return ""
    rest = text[start + len(heading) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def structure_tree(text: str) -> str:
    """The fenced directory tree, without its fences."""
    section = _section(text, _STRUCTURE_HEADING)
    if _FENCE not in section:
        return ""
    body = section.split(_FENCE)[1]
    return body.strip("\n")


def lookup_entries(text: str) -> list[MapEntry]:
    """Rows of the "where to look" table, with the paths and symbols they name."""
    entries: list[MapEntry] = []
    for line in _section(text, _LOOKUP_HEADING).splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("Task", "") or set(cells[0]) <= {"-", " "}:
            continue
        task, location = cells[0], cells[1]
        notes = cells[2] if len(cells) > 2 else ""
        paths = _PATH.findall(location)
        # Symbols are named in the notes; the location column holds paths.
        symbols = [s for s in _SYMBOL.findall(notes) if not s.endswith(("py", "ts"))]
        if paths:
            entries.append(MapEntry(task=task, paths=paths, symbols=symbols))
    return entries


def _resolve(repo_root: Path, path: str) -> Path | None:
    for root in _SEARCH_ROOTS:
        candidate = repo_root / root / path if root else repo_root / path
        if candidate.exists():
            return candidate
    return None


def _defines(source: str, symbol: str) -> bool:
    """Whether `source` declares `symbol`, rather than merely mentioning it.

    A symbol keeps appearing as a caller in the file it used to live in long
    after it moved, so a substring match would never catch the drift that
    matters. Functions, classes, module constants and config keys all count —
    the map legitimately points at all four.
    """
    name = re.escape(symbol)
    patterns = (
        rf"^\s*(?:async\s+)?(?:def|class)\s+{name}\b",  # function or class
        rf"^{name}\s*[:=]",  # module-level constant or annotated global
        rf"[\"']{name}[\"']\s*:",  # dict/config key, e.g. "role_file":
    )
    return any(re.search(pattern, source, re.M) for pattern in patterns)


def verify_code_map(repo_root: Path) -> list[MapDrift]:
    """Claims in the map that the code no longer supports.

    Checks that each referenced file exists and that each symbol named alongside
    it is still defined there. A moved function is the drift that actually costs
    an agent time: the file resolves, so nothing looks wrong until it reads the
    wrong one.
    """
    source = repo_root / MAP_FILENAME
    if not source.is_file():
        return [MapDrift(entry=MAP_FILENAME, detail="missing")]

    text = source.read_text(encoding="utf-8")
    drift: list[MapDrift] = []

    for entry in lookup_entries(text):
        bodies: list[str] = []
        for path in entry.paths:
            resolved = _resolve(repo_root, path)
            if resolved is None:
                drift.append(MapDrift(entry=entry.task, detail=f"{path} does not exist"))
                continue
            bodies.append(resolved.read_text(encoding="utf-8", errors="ignore"))

        if not bodies:
            continue
        combined = "\n".join(bodies)
        for symbol in entry.symbols:
            if not _defines(combined, symbol):
                where = ", ".join(entry.paths)
                drift.append(
                    MapDrift(entry=entry.task, detail=f"{symbol} is not defined in {where}")
                )
    return drift


def render_code_map(repo_root: Path, *, max_chars: int = 4000) -> str:
    """The map as a prompt block, or "" when the repo has none.

    Structure first, then where to look — an agent needs the shape of the repo
    before the index into it.
    """
    source = repo_root / MAP_FILENAME
    if not source.is_file():
        return ""
    text = source.read_text(encoding="utf-8")

    tree = structure_tree(text)
    lookup = _section(text, _LOOKUP_HEADING).strip()
    if not tree and not lookup:
        return ""

    parts: list[str] = []
    if tree:
        parts += ["### Repository structure", "```", tree, "```"]
    if lookup:
        parts += ["", "### Where to look", lookup]
    return "\n".join(parts)[:max_chars]
