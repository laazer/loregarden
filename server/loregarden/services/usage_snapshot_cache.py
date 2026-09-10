"""Serves the usage snapshot from memory, with the refresh off the request thread.

Building a snapshot costs three provider round-trips plus a walk of the local CLI
transcripts. Paying that on every poll put seconds on ``GET /api/usage`` — 7s in
the wild, and far worse on a cold transcript tree — and because a browser gives
one origin six connections, a request that slow stalls every call queued behind
it. The whole board looked hung on a page load.

So a reading younger than the TTL is returned as-is, an older one is returned
while a refresh runs in a background thread, and the very first poll after a
restart falls back to the reading already on disk. Staleness is never hidden:
each provider carries ``from_cache``/``cached_at`` and the snapshot carries
``fetched_at``, both of which the usage modal already shows.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from loregarden.services.usage_service import get_usage_snapshot, snapshot_from_stored_cache

logger = logging.getLogger(__name__)

SNAPSHOT_TTL_SECONDS = 120

_lock = threading.Lock()
_snapshot: dict[str, Any] | None = None
_taken_at = 0.0
_refresh_thread: threading.Thread | None = None


def reset_cache() -> None:
    """Drop the in-memory reading, so the next read builds a fresh one."""
    global _snapshot, _taken_at
    with _lock:
        _snapshot = None
        _taken_at = 0.0


def read_usage_snapshot(*, force: bool = False) -> dict[str, Any]:
    """The current usage snapshot. ``force`` waits for live provider numbers."""
    if force:
        return _refresh()
    with _lock:
        cached = _snapshot
        age = time.monotonic() - _taken_at
    if cached is not None:
        if age >= SNAPSHOT_TTL_SECONDS:
            _start_background_refresh()
        return cached
    stored = snapshot_from_stored_cache()
    if stored is None:
        # Nothing has ever been read on this machine, so there is no honest
        # answer to serve; this one poll pays for the first snapshot.
        return _refresh()
    _start_background_refresh()
    return stored


def _refresh() -> dict[str, Any]:
    global _snapshot, _taken_at
    snapshot = get_usage_snapshot()
    with _lock:
        _snapshot = snapshot
        _taken_at = time.monotonic()
    return snapshot


def _refresh_in_background() -> None:
    try:
        _refresh()
    except (OSError, httpx.HTTPError):
        logger.exception(
            "background usage refresh failed; the stored reading stays on screen, "
            "its age keeps showing in the modal, and the next poll retries"
        )


def _start_background_refresh() -> None:
    global _refresh_thread
    with _lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_thread = threading.Thread(
            target=_refresh_in_background,
            name="usage-refresh",
            daemon=True,
        )
        thread = _refresh_thread
    thread.start()
