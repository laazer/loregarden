"""Where a slow view save spent its time, said in the log when it is slow.

A view PATCH is one small UPDATE, and on a quiet server it takes about 20ms. On
the live server some took 6 to 11 seconds, and nothing recorded why: the
request could have waited for a worker thread (the handler is sync, so it runs
on the threadpool), for SQLite's write lock (`busy_timeout` lets a write wait up
to 30s behind another writer), or on the commit itself. Those have different
fixes, and a 23-minute probe of the live server could not catch one in the act.
So the save reports its own phases whenever it is slow.

`received_at` comes from an ``async`` dependency, which FastAPI resolves on the
event loop before it hands the sync handler to the threadpool. Handler start
minus that is the wait for a thread. SQLite takes the write lock at the first
write statement, which here is inside the commit, so the lock wait and the
commit are one phase: telling them apart would mean another round trip on every
save to learn something only slow saves need.
"""

from __future__ import annotations

import logging
import time

from loregarden.models.domain.view_save_enums import ViewSavePhase

logger = logging.getLogger(__name__)

#: Fifty times a quiet save. Below this a save is not worth a line in the log.
SLOW_SAVE_SECONDS = 1.0


async def save_received_at() -> float:
    """When the request reached the event loop, before any thread was assigned."""
    return time.perf_counter()


class ViewSaveTimer:
    """The phases of one save, each measured from the end of the one before."""

    def __init__(self, received_at: float) -> None:
        self._received_at = received_at
        #: Where each phase ended, in seconds since `received_at`.
        self._ends: dict[ViewSavePhase, float] = {}

    def mark(self, phase: ViewSavePhase) -> None:
        """Close `phase` at the current moment."""
        self._ends[phase] = time.perf_counter() - self._received_at

    def _rounded_phases(self) -> dict[str, float]:
        """Each phase's length in milliseconds, rounded at its boundaries.

        Rounding each length on its own let the phases sum to more than the
        rounded total (0.001 + 0.002 + 0.007 against 0.009). Rounding where
        each phase ends instead makes the lengths sum to the last rounded end,
        which rounding (monotonic) keeps at or below the rounded total.
        """
        phases: dict[str, float] = {}
        previous = 0.0
        for phase, end in self._ends.items():
            boundary = round(end, 3)
            phases[phase.value] = round(boundary - previous, 3)
            previous = boundary
        return phases

    def report(self, view_id: str, outcome: int) -> None:
        """Log the phases at warning level when the whole save was slow.

        `outcome` is the HTTP status the save answered with, so a slow refusal is
        reported as one rather than looking like a slow success.
        """
        total = time.perf_counter() - self._received_at
        if total < SLOW_SAVE_SECONDS:
            return
        phases = self._rounded_phases()
        logger.warning(
            "View save %s took %.2fs (status %d): %s",
            view_id,
            total,
            outcome,
            ", ".join(f"{name} {seconds:.2f}s" for name, seconds in phases.items()),
            extra={"view_save_phases": phases, "view_save_seconds": round(total, 3)},
        )
