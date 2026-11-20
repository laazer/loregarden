"""A per-workspace, per-checkout map of where every symbol is defined.

Deterministic and LLM-free: Python through the stdlib `ast`, TypeScript and
TSX through a line-anchored regex. Free to build and free to keep current,
which is what makes everything downstream of it cheap.

    build_symbol_map(session, workspace_id, repo_root)              -> BuildReport
    refresh_symbol_map(session, workspace_id, repo_root, tree=None) -> RefreshReport
    refresh_at_run_entry(session, workspace_id, repo_root, tree=None) -> RefreshReport
    definitions_for(session, workspace_id, repo_root, name)         -> MapRead[list[SymbolLocation]]
    outline_for(session, workspace_id, repo_root, path)             -> MapRead[list[SymbolLocation]]
    suggest_names(session, workspace_id, repo_root, name, limit=5)  -> MapRead[list[str]]
    map_status(session, workspace_id, repo_root)                    -> MapStatus

`repo_root` is explicit on every one of them and the package never reads a
workspace's recorded path. One derivation at the top of the request: a
second one in a helper is where a correct-looking answer gets reported,
because the identity would come from where the caller stands and the line
numbers from somewhere else.

Every reader returns a `MapRead` carrying a `MapStatus` beside its rows, so
"there is no map", "the map describes another commit", "a peer is writing it
right now" and "the map has nothing for this" never collapse into one empty
list — and the answer names how far behind it is rather than only that it is
stale.

The map IS built in production now (lg-code-knowledge-284):
`refresh_at_run_entry` runs from `CliAgentExecutor.execute`, once the run's
tree is settled. There is still no hook and no scheduler entry; the primary
checkout's own trigger is a later ticket.
"""

from __future__ import annotations

from loregarden.services.symbol_map.freshness import (
    MapStatus,  # noqa: F401 - the verdict value every reader carries
    map_status,
)
from loregarden.services.symbol_map.refresh import (
    RefreshFailure,  # noqa: F401 - the closed reason a declined refresh reports
    RefreshReport,  # noqa: F401 - the refresh's return type, named by consumers
    refresh_at_run_entry,
    refresh_symbol_map,
)
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

#: Exactly the seven functions. Consumers import from here and nothing deeper.
__all__ = [
    "build_symbol_map",
    "definitions_for",
    "map_status",
    "outline_for",
    "refresh_at_run_entry",
    "refresh_symbol_map",
    "suggest_names",
]
