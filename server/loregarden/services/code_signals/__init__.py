"""Behavioural git signals: churn, co-change, ownership, bug-fix history.

Derived entirely from one `git log --numstat` read over a time window. No LLM,
no third-party dependency, no operator-visible surface — the signals are
reachable as these four functions and as rows in two tables.

    compute_git_signals(repo_root, config) -> GitSignals
    refresh_workspace_signals(session, workspace_id, repo_root, config) -> RefreshReport
    signals_for_path(session, workspace_id, path) -> FileSignals | None
    top_files_by(session, workspace_id, signal, limit) -> list[FileSignals]

The package imports nothing from the orchestrator, the API or the MCP layer:
a derived index must not be able to move a ticket, and the consumers import
this rather than the reverse.
"""

from __future__ import annotations

from loregarden.services.code_signals import (
    classify,
    compute,
    config,
    git_history,
    provenance,
    store,
)
from loregarden.services.code_signals.classify import classify_path, is_fix_subject
from loregarden.services.code_signals.compute import (
    CoChangePair,
    FileSignals,
    GitSignals,
    compute_git_signals,
    compute_signals,
)
from loregarden.services.code_signals.config import SignalConfig
from loregarden.services.code_signals.git_history import (
    CommitRecord,
    GitSignalsParseError,
    GitSignalsReadError,
    log_args,
    parse_log_stream,
)
from loregarden.services.code_signals.store import (
    RefreshReport,
    refresh_workspace_signals,
    signals_for_path,
    top_files_by,
)

__all__ = [
    "CoChangePair",
    "CommitRecord",
    "FileSignals",
    "GitSignals",
    "GitSignalsParseError",
    "GitSignalsReadError",
    "RefreshReport",
    "SignalConfig",
    "classify",
    "classify_path",
    "compute",
    "compute_git_signals",
    "compute_signals",
    "config",
    "git_history",
    "is_fix_subject",
    "log_args",
    "parse_log_stream",
    "provenance",
    "refresh_workspace_signals",
    "signals_for_path",
    "store",
    "top_files_by",
]
