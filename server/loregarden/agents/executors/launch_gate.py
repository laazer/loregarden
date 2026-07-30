"""Serialize CLI launches that contend for a shared credential store.

`cursor-agent` re-reads its saved login from the macOS keychain on every launch.
The lanes of a parallel stage start within milliseconds of each other, and
concurrent access to the same keychain item makes the losers die with
`errSecDuplicateItem` or "couldn't find your saved login" before they ever reach a
model — a failure the orchestrator then has to interpret with no stage report to
go on. Letting one launch at a time clear authentication removes the contention
without serializing the work itself: the slot is released as soon as a process
produces output, which is proof it is past its credential read.

Adapters that authenticate from a config file (`claude`) or not at all (`local`,
`lmstudio`) are unaffected and take a no-op slot.
"""

from __future__ import annotations

import threading

_KEYCHAIN_ADAPTERS = frozenset({"cursor"})

# A process that has produced no output by now is not waiting on the keychain any
# more (or never will), so it must stop holding its siblings back.
MAX_HOLD_SECONDS = 30.0

# Waiting forever would trade a keychain race for a deadlock, which is worse: a
# waiter this patient launches unserialized instead.
MAX_WAIT_SECONDS = 90.0

_launch_lock = threading.Lock()


class LaunchSlot:
    """The right to be the process currently clearing authentication."""

    def __init__(self, lock: threading.Lock | None) -> None:
        self._lock = lock

    def release(self) -> None:
        """Idempotent — callers release on first output and again in `finally`."""
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.release()


def acquire_launch_slot(adapter: str) -> LaunchSlot:
    """Block until `adapter` may launch, returning the slot to release."""
    if adapter not in _KEYCHAIN_ADAPTERS:
        return LaunchSlot(None)
    if not _launch_lock.acquire(timeout=MAX_WAIT_SECONDS):
        return LaunchSlot(None)
    return LaunchSlot(_launch_lock)
