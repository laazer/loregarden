"""Memoized, single-flight caching for model-discovery probes.

Every adapter discovers its model catalog the same way: shell out to a CLI, or
call a local server, and hope it answers. Three properties matter, and all three
are easy to get wrong once per module:

1. **The probe is memoized.** ``codex debug models`` and the LM Studio endpoints
   were called afresh on every ``runtime-options`` request, so opening Settings
   paid for the discovery of every adapter, every time.
2. **The lock is never held across the probe.** Holding it makes concurrent
   callers queue behind a subprocess. ``runtime_options_payload`` runs inside a
   request holding a database connection, so a queue of callers is a queue of
   held connections — fifteen of them exhausted the pool and the API stopped
   answering, ``/health`` included.
3. **A failure is held for longer than the probe's own budget.** A hung CLI
   burns its whole timeout before failing, so a shorter failure TTL lets the
   next caller start probing the instant the last one gave up: a probe is then
   permanently in flight rather than retried now and then.

The third is enforced at construction rather than documented, because it is an
ordering between two numbers that live in different modules and drift apart.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class ProbeCache(Generic[T]):
    """Holds one discovery probe's results, one entry per key.

    An empty result counts as a failure: every probe here returns a list of
    models, and "the CLI answered with nothing" is the same non-answer as "the
    CLI is not installed" — both should be retried sooner than a real catalog is
    refreshed.
    """

    def __init__(
        self,
        *,
        probe_budget_seconds: float,
        success_ttl_seconds: float,
        failure_ttl_seconds: float,
    ) -> None:
        if failure_ttl_seconds <= probe_budget_seconds:
            raise ValueError(
                "failure_ttl_seconds must outlast probe_budget_seconds "
                f"({failure_ttl_seconds} <= {probe_budget_seconds}); otherwise a probe that "
                "hangs for its whole budget is retried the moment it gives up, and a broken "
                "CLI keeps a probe permanently in flight."
            )
        self._probe_budget_seconds = probe_budget_seconds
        self._success_ttl_seconds = success_ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        #: Guards the fields below, and nothing else. Never held across a probe.
        self._state_lock = threading.Lock()
        self._entries: dict[str, tuple[list[T], float]] = {}
        self._probe_locks: dict[str, threading.Lock] = {}

    def _probe_lock_for(self, key: str) -> threading.Lock:
        with self._state_lock:
            lock = self._probe_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._probe_locks[key] = lock
            return lock

    def _read(self, key: str) -> tuple[list[T], bool]:
        """The memoized answer for ``key``, and whether it is still fresh."""
        with self._state_lock:
            entry = self._entries.get(key)
        if entry is None:
            return [], False
        found, expires_at = entry
        return list(found), time.monotonic() < expires_at

    def _store(self, key: str, found: list[T]) -> None:
        ttl = self._success_ttl_seconds if found else self._failure_ttl_seconds
        with self._state_lock:
            self._entries[key] = (list(found), time.monotonic() + ttl)

    def get(self, key: str, probe: Callable[[], list[T]]) -> list[T]:
        """Return the memoized answer for ``key``, probing only if it is stale.

        At most one caller per key probes at a time. Anyone arriving while a
        probe is in flight is served the last known answer immediately rather
        than waiting: a model catalog decorates a picker, and no request should
        hold a database connection for the length of a subprocess to render it.
        """
        cached, fresh = self._read(key)
        if fresh:
            return cached

        lock = self._probe_lock_for(key)
        if not lock.acquire(blocking=False):
            return cached

        try:
            # The holder may have finished between the read above and the acquire.
            cached, fresh = self._read(key)
            if fresh:
                return cached
            found = probe()
            self._store(key, found)
            return list(found)
        finally:
            lock.release()

    def reset(self) -> None:
        """Drop every memoized answer so the next call re-probes.

        The probe locks are deliberately kept: one of them may be held by a
        probe that is still running, and replacing it would let a second caller
        into a probe this cache thinks nobody is running.
        """
        with self._state_lock:
            self._entries.clear()
