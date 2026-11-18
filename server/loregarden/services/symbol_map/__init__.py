"""A per-workspace, per-checkout map of where every symbol is defined.

Deterministic and LLM-free: Python through the stdlib `ast`, TypeScript and
TSX through a line-anchored regex. Free to build and free to keep current,
which is what makes everything downstream of it cheap.

    build_symbol_map(session, workspace_id, repo_root)             -> BuildReport
    definitions_for(session, workspace_id, repo_root, name)        -> MapRead[list[SymbolLocation]]
    outline_for(session, workspace_id, repo_root, path)            -> MapRead[list[SymbolLocation]]
    suggest_names(session, workspace_id, repo_root, name, limit=5) -> MapRead[list[str]]
    map_freshness(session, workspace_id, repo_root)                -> Freshness

`repo_root` is explicit on every one of them and the package never reads a
workspace's recorded path. One derivation at the top of the request: a
second one in a helper is where a correct-looking answer gets reported,
because the identity would come from where the caller stands and the line
numbers from somewhere else.

Every reader returns a `MapRead` carrying a three-valued `Freshness` beside
its rows, so "there is no map", "the map describes another commit" and "the
map has nothing for this" never collapse into one empty list.

Nothing in this package builds the map in production. No tool, no command,
no hook, no scheduler entry — those are later tickets. What ships here is
the schema, the extractor, these five functions, one invariant, and the
tests that measure them.
"""

from __future__ import annotations

from loregarden.services.symbol_map.freshness import map_freshness
from loregarden.services.symbol_map.store import (
    BuildReport,  # noqa: F401 - the build's return type, named by consumers
    InterpreterTooOldError,  # noqa: F401 - raised through this surface
    MapRead,  # noqa: F401 - every reader's return type
    SymbolLocation,  # noqa: F401 - the row value readers hand back
    build_symbol_map,
    definitions_for,
    outline_for,
    suggest_names,
)

#: Exactly the five functions. Consumers import from here and nothing deeper.
__all__ = [
    "build_symbol_map",
    "definitions_for",
    "map_freshness",
    "outline_for",
    "suggest_names",
]
