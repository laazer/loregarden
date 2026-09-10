"""Incremental per-model token totals over append-only JSONL transcripts.

The usage breakdown used to re-read every transcript written inside its window
on every poll. Measured on this machine that was 757 MB across 486 files, and
it landed on the ``GET /api/usage`` request thread: 68s warm, 95s cold. With the
browser's six-connection budget per origin, one such request stalls every other
call queued behind it.

CLI transcripts are append-only, so this module remembers how far into each file
it has already counted and reads only the bytes appended since. Totals are kept
in day buckets so the rolling window can be re-applied without re-reading, which
makes ``days_back`` day-granular: the boundary day is included whole.

Row semantics stay with the caller — this module never parses a transcript row,
it only decides which bytes are new and which buckets survive the window.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptRow:
    """One accounted transcript row: when it happened, and what it spent."""

    timestamp: float | None
    model: str
    tokens: float


ReadRow = Callable[[str], TranscriptRow | None]


@dataclass
class _FileState:
    """What has already been counted out of one transcript."""

    mtime_ns: int = 0
    offset: int = 0
    # day (YYYY-MM-DD, UTC) -> model -> tokens
    days: dict[str, dict[str, float]] = field(default_factory=dict)


_state: dict[str, _FileState] = {}
# One lock for the whole index: two concurrent scans of the same tree would each
# consume the same appended bytes and double-count them, so a second caller waits
# for the first (and then reads only its own delta) instead of racing it.
_lock = threading.Lock()


def reset_index() -> None:
    """Forget every counted byte, so the next scan re-reads from the start."""
    with _lock:
        _state.clear()


def scan_tokens_by_model(
    root: Path,
    *,
    days_back: int,
    read_row: ReadRow,
    pattern: str = "*.jsonl",
) -> dict[str, float]:
    """Tokens per model across ``root``'s transcripts, over the last ``days_back`` days."""
    if not root.is_dir():
        return {}
    since = datetime.now(tz=timezone.utc).timestamp() - days_back * 86400
    cutoff_day = _day_key(since) or ""
    totals: dict[str, float] = {}
    seen: set[str] = set()
    with _lock:
        for path in root.rglob(pattern):
            try:
                stat = path.stat()
            except OSError:
                # silent-ok: a live session can rotate or delete a transcript between
                # the rglob and this stat; the next poll re-walks the tree
                continue
            key = str(path)
            seen.add(key)
            state = _usable_state(key, stat.st_size, stat.st_mtime_ns)
            if state is None:
                # A transcript last written before the window can only hold rows
                # outside it, so skip the read rather than parsing the whole
                # history to discard nearly all of it.
                if stat.st_mtime < since:
                    _state.pop(key, None)
                    continue
                state = _FileState()
                _state[key] = state
            if stat.st_size > state.offset:
                _count_appended(path, state, read_row, fallback_day=_day_key(stat.st_mtime))
            state.mtime_ns = stat.st_mtime_ns
            _drop_days_before(state, cutoff_day)
            for models in state.days.values():
                for model, tokens in models.items():
                    totals[model] = totals.get(model, 0.0) + tokens
        _forget_deleted(root, seen)
    return totals


def _usable_state(key: str, size: int, mtime_ns: int) -> _FileState | None:
    """The remembered state for a file, or ``None`` when it must be re-read.

    A transcript only ever grows, so a shrink — or a same-size file whose mtime
    moved — means something rewrote it and the counted bytes no longer describe
    its contents.
    """
    state = _state.get(key)
    if state is None:
        return None
    if size < state.offset:
        return None
    if size == state.offset and mtime_ns != state.mtime_ns:
        return None
    return state


def _count_appended(
    path: Path,
    state: _FileState,
    read_row: ReadRow,
    *,
    fallback_day: str | None,
) -> None:
    consumed = state.offset
    try:
        with path.open("rb") as handle:
            handle.seek(consumed)
            for raw in handle:
                if not raw.endswith(b"\n"):
                    # A half-flushed final row: leave it unconsumed so the next
                    # pass reads it whole rather than splitting it in two.
                    break
                consumed += len(raw)
                row = read_row(raw.decode("utf-8", errors="ignore"))
                if row is None:
                    continue
                day = (
                    _day_key(row.timestamp) if row.timestamp is not None else None
                ) or fallback_day
                if day is None:
                    # No row timestamp and no readable file mtime: there is no
                    # window this row can be placed in.
                    logger.warning(
                        "transcript row in %s has no usable timestamp; its tokens are "
                        "missing from the usage breakdown",
                        path,
                    )
                    continue
                bucket = state.days.setdefault(day, {})
                bucket[row.model] = bucket.get(row.model, 0.0) + row.tokens
    except OSError as exc:
        logger.warning(
            "could not read transcript %s; its turns are missing from the usage breakdown: %s",
            path,
            exc,
        )
    state.offset = consumed


def _drop_days_before(state: _FileState, cutoff_day: str) -> None:
    for day in [day for day in state.days if day < cutoff_day]:
        del state.days[day]


def _forget_deleted(root: Path, seen: set[str]) -> None:
    prefix = str(root) + os.sep
    for key in [key for key in _state if key.startswith(prefix) and key not in seen]:
        del _state[key]


def _day_key(epoch: float) -> str | None:
    """The UTC day an epoch falls in, or ``None`` if it is not a real instant."""
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None
