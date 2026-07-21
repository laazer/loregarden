"""Fan-out from the services that change state to the sockets watching it.

Publishers are ordinary synchronous service code — often running in a worker
thread, since FastAPI hands `def` endpoints to a threadpool. Subscribers are
websocket handlers living on the event loop. `asyncio.Queue` is not thread
safe, so every hand-off crosses back to the loop explicitly.

Replaces the Flask-SocketIO "rooms" that were never mounted: the concept was
right, but nothing instantiated the server, so no handler could fire. A topic
here is what a room was there — `workspace:{id}`, `worktree:{id}`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Per-subscriber backlog. A tab that stops reading (backgrounded, throttled,
#: wedged) must not let the queue grow without bound; past this we drop the
#: oldest event, because for status snapshots the newest is the one that
#: matters and a stale backlog is worth nothing.
QUEUE_MAXSIZE = 64


class EventHub:
    """Topic-based pub/sub between sync services and async websockets."""

    def __init__(self) -> None:
        self._topics: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        """Open a queue of events for `topic`.

        Called from a websocket handler, so the running loop is captured here —
        publishers are sync and have no loop of their own to find.
        """
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._topics.setdefault(topic, set()).add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Release a subscription, dropping the topic once it is empty.

        Without the cleanup, every workspace ever opened would keep an entry
        forever in a process that runs for days.
        """
        subscribers = self._topics.get(topic)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._topics[topic]

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """Hand an event to everyone watching `topic`.

        Safe to call from any thread, and cheap when nobody is listening — the
        emit call sites are on hot service paths that must not care whether a
        browser happens to be open.
        """
        if self._loop is None or topic not in self._topics:
            return
        try:
            self._loop.call_soon_threadsafe(self._deliver, topic, event)
        except RuntimeError:
            # The loop these subscribers belonged to is gone — the dev server
            # restarts on every backend edit, and a queue operation still in
            # flight must not fail because nobody is left to tell. Publishing
            # is best-effort by design; the caller's work is not.
            logger.debug("Dropped %s event for %s: event loop is closed", event, topic)

    def subscriber_count(self, topic: str) -> int:
        """How many sockets are watching `topic`. Used by tests and logging."""
        return len(self._topics.get(topic, ()))

    def _deliver(self, topic: str, event: dict[str, Any]) -> None:
        """Push to each subscriber. Runs on the loop thread, never elsewhere."""
        for queue in self._topics.get(topic, set()):
            if queue.full():
                # Drop the oldest to make room; get_nowait cannot raise here
                # because nothing else consumes on this thread.
                queue.get_nowait()
            queue.put_nowait(event)


#: One hub per process. The emit helpers reach it by import rather than through
#: an init() call — the previous design had exactly such an initializer, and
#: nothing ever called it, so every emit was silently a no-op.
event_hub = EventHub()
