"""Wait for a merge-queue entry to finish, so a caller does not hand-roll a polling loop.

Agents waiting on a landing each wrote a shell loop around
`loregarden_merge_queue_status`, one per wait, and each loop had the same blind
spot: the queue runs inside the main server, and a loop polling the database
cannot tell "still testing" from "nothing is moving it". A server that died
mid-wait would have held the loop for its full four hours.

`wait_for_entry` reads the entry until it reaches a terminal state, reports each
change of state or detail as it happens, and probes the server so a dead queue
ends the wait with that reason instead of a timeout.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from loregarden.models.domain import MergeQueueState
from loregarden.models.domain.merge_queue_enums import MERGE_QUEUE_TERMINAL_STATES
from loregarden.services.merge_queue_requests import EntryDetail


class EntryDidNotMerge(RuntimeError):
    """The entry finished without merging: failed, timed out or withdrawn."""

    def __init__(self, entry: EntryDetail) -> None:
        super().__init__(f"{entry.state.value} | {entry.detail}")
        self.entry = entry


class WaitStopped(RuntimeError):
    """The wait ended with no verdict: the deadline passed or the queue's server is gone."""


@dataclass(frozen=True)
class WaitPolicy:
    poll_seconds: float = 30.0
    #: Give up after this long with the entry still live.
    timeout_seconds: float = 5 * 3600.0
    #: Stop once the server has not answered for this long: nothing moves the queue.
    unreachable_seconds: float = 600.0


def wait_for_entry(
    read: Callable[[], EntryDetail],
    server_up: Callable[[], bool],
    report: Callable[[str], None],
    policy: WaitPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> EntryDetail:
    """Return the merged entry; raise `EntryDidNotMerge` or `WaitStopped` otherwise."""
    started = clock()
    unreachable_since: float | None = None
    last_seen: tuple[MergeQueueState, str, str] | None = None
    while True:
        entry = read()
        seen = (entry.state, entry.wait, entry.detail)
        if seen != last_seen:
            report(f"{entry.state.value} | {entry.detail}")
            last_seen = seen
        if entry.state is MergeQueueState.MERGED:
            return entry
        if entry.state in MERGE_QUEUE_TERMINAL_STATES:
            raise EntryDidNotMerge(entry)

        now = clock()
        if server_up():
            unreachable_since = None
        elif unreachable_since is None:
            unreachable_since = now
            report("the server that runs the merge queue is not answering /health")
        elif now - unreachable_since >= policy.unreachable_seconds:
            minutes = policy.unreachable_seconds / 60
            raise WaitStopped(
                f"the server that runs the merge queue has not answered /health for "
                f"{minutes:.0f} min, so nothing can move entry {entry.id} (still "
                f"{entry.state.value}); start it with `task server`, then wait again"
            )
        if now - started >= policy.timeout_seconds:
            hours = policy.timeout_seconds / 3600
            raise WaitStopped(
                f"entry {entry.id} is still {entry.state.value} after {hours:.1f} h: {entry.detail}"
            )
        sleep(policy.poll_seconds)
