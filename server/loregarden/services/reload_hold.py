"""Holding new work back, for a limited time, so the server can reload.

`follow_main` reloads only when nothing is in flight, and with several tickets
being driven that moment may not come for hours: of 136 landings to 2026-11-24
the machine was next idle 5 hours later at the 90th percentile, while the agent
runs going at the landing had all ended within 44 minutes. What kept it busy was
the next stage of each ticket, and the next ticket of each lane.

So once a landed change has waited `follow_main_hold_after_seconds`:

- no lane starts an entry (`QueueLaneService.start_lane_head`), and
- a ticket being driven stops at its next stage boundary and goes back to the
  front of its lane (`reload_yield`),

for at most `follow_main_hold_seconds`. Nothing running is interrupted. A hold
that ends without a reload is not tried again for
`follow_main_hold_cooldown_seconds`, so work that will not finish cannot keep the
lanes idle. It is not `drain`, which drops a dispatch because the process is
going away; this process may well stay up.

Process-wide and in memory: a reload is what ends it, and a new process has
nothing to hold for. Every reader checks the deadline, so a tick that never comes
back cannot leave the lanes held.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from loregarden.config import settings

logger = logging.getLogger(__name__)

#: What a lane entry held back records, so the board says why it did not start.
RELOAD_HOLD_REASON = (
    "Waiting: the server is about to reload onto a landed change. This entry stays queued."
)


@dataclass(frozen=True)
class HoldTick:
    holding: bool
    #: What this tick did, for the follow-main status; "" when there is nothing to add.
    detail: str = ""


@dataclass
class _Hold:
    #: When the landed change first had to wait, or None when nothing waits.
    waiting_since: float | None = None
    #: When the hold ends, or None when there is none.
    until: float | None = None
    #: No hold starts before this: the cooldown after one that did not work.
    not_before: float = 0.0
    #: Tickets stepping out of their lane right now (see `yielding`).
    yields: int = 0


_hold = _Hold()
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _is_holding(now: float) -> bool:
    return _hold.until is not None and now < _hold.until


def note_waiting(cannot_yield: list[str]) -> HoldTick:
    """A tick that wanted to reload and found work in flight: start, keep or end the hold.

    `cannot_yield` names what is running that no hold would bring to a stop (a
    person's approval, a terminal agent). Holding the lanes for it would idle
    them for nothing, so there is no hold while there is any, and the wait starts
    again: otherwise a prompt answered and asked again would start and stop the
    lanes with it.
    """
    now = _now()
    with _lock:
        if _hold.waiting_since is None:
            _hold.waiting_since = now
        if _hold.until is not None and now >= _hold.until:
            _hold.until = None
            _hold.not_before = now + settings.follow_main_hold_cooldown_seconds
            logger.warning(
                "Reload hold: ended with work still running; new work starts again, and the "
                "next hold waits at least %.0f min",
                settings.follow_main_hold_cooldown_seconds / 60,
            )
        if cannot_yield:
            if _hold.until is not None:
                logger.warning("Reload hold: ended early; %s", "; ".join(cannot_yield))
            _hold.until = None
            _hold.waiting_since = now
            return HoldTick(False, f"new work is not held: {'; '.join(cannot_yield)}")
        if (
            _hold.until is None
            and settings.follow_main_hold_seconds > 0
            and now >= _hold.not_before
            and now - _hold.waiting_since >= settings.follow_main_hold_after_seconds
        ):
            _hold.until = now + settings.follow_main_hold_seconds
            logger.warning(
                "Reload hold: no lane starts work and driven tickets pause at their next stage, "
                "for at most %.0f min",
                settings.follow_main_hold_seconds / 60,
            )
        if _hold.until is None:
            return HoldTick(False)
        return HoldTick(
            True, f"new work is held for at most {(_hold.until - now) / 60:.0f} more min"
        )


def note_settled() -> None:
    """Nothing is waiting to reload any more. A cooldown still running keeps running."""
    with _lock:
        if _hold.until is not None:
            logger.warning("Reload hold: ended; nothing is waiting to reload")
        _hold.until = None
        _hold.waiting_since = None


def reset() -> None:
    """Forget everything, for a process that boots more than one app (tests)."""
    with _lock:
        _hold.waiting_since = None
        _hold.until = None
        _hold.not_before = 0.0
        _hold.yields = 0


def holds_starts() -> bool:
    """Must a lane leave its next entry queued?"""
    with _lock:
        return _hold.yields > 0 or _is_holding(_now())


@contextmanager
def yielding(*, required: bool = False) -> Iterator[bool]:
    """Whether a driven ticket must stop here, holding the lanes until it has.

    The entry that continues a ticket is queued before its run ends, so a crash
    between the two loses nothing. A lane that started that entry in the gap
    would adopt the run about to end, and the ticket with it. The hold may expire
    inside the gap, so the lanes stay held for as long as this is open.

    `required` stops the ticket whether or not the hold is still on: its child
    stopped for one.
    """
    with _lock:
        stops = required or _is_holding(_now())
        if stops:
            _hold.yields += 1
    try:
        yield stops
    finally:
        if stops:
            with _lock:
                _hold.yields -= 1
