"""The fan-out between synchronous services and asynchronous sockets."""

import asyncio
import threading

import pytest
from loregarden.services.event_hub import QUEUE_MAXSIZE, EventHub


@pytest.fixture(name="hub")
def hub_fixture():
    """A private hub per test — the module singleton is shared process-wide."""
    return EventHub()


@pytest.mark.asyncio
async def test_a_subscriber_receives_what_is_published(hub):
    queue = hub.subscribe("workspace:ws-1")

    hub.publish("workspace:ws-1", {"type": "execution_update"})

    assert await asyncio.wait_for(queue.get(), timeout=1) == {"type": "execution_update"}


@pytest.mark.asyncio
async def test_events_do_not_cross_topics(hub):
    watched = hub.subscribe("workspace:ws-1")

    hub.publish("workspace:ws-2", {"type": "execution_update"})
    hub.publish("workspace:ws-1", {"type": "run_completed"})

    # If topics leaked, the ws-2 event would arrive first.
    assert await asyncio.wait_for(watched.get(), timeout=1) == {"type": "run_completed"}


@pytest.mark.asyncio
async def test_every_subscriber_on_a_topic_gets_the_event(hub):
    """Two browser tabs on the same workspace are two subscribers."""
    first = hub.subscribe("workspace:ws-1")
    second = hub.subscribe("workspace:ws-1")

    hub.publish("workspace:ws-1", {"type": "execution_update"})

    assert await asyncio.wait_for(first.get(), timeout=1) == {"type": "execution_update"}
    assert await asyncio.wait_for(second.get(), timeout=1) == {"type": "execution_update"}


@pytest.mark.asyncio
async def test_publishing_from_another_thread_reaches_the_loop(hub):
    """The case the whole design exists for.

    FastAPI runs `def` endpoints in a threadpool, so the services that emit
    these events are usually *not* on the event loop. `asyncio.Queue` is not
    thread safe; if the hand-off did not go back through the loop, this would
    either lose events or corrupt the queue.
    """
    queue = hub.subscribe("workspace:ws-1")

    thread = threading.Thread(
        target=hub.publish, args=("workspace:ws-1", {"type": "from-a-thread"})
    )
    thread.start()
    thread.join()

    assert await asyncio.wait_for(queue.get(), timeout=1) == {"type": "from-a-thread"}


@pytest.mark.asyncio
async def test_publishing_to_nobody_is_a_no_op(hub):
    """Emit call sites sit on hot paths and must not care whether a browser
    happens to be open."""
    hub.publish("workspace:ws-1", {"type": "execution_update"})  # must not raise

    assert hub.subscriber_count("workspace:ws-1") == 0


@pytest.mark.asyncio
async def test_unsubscribing_drops_the_topic(hub):
    """A process that runs for days must not keep an entry for every workspace
    anyone ever opened."""
    queue = hub.subscribe("workspace:ws-1")
    assert hub.subscriber_count("workspace:ws-1") == 1

    hub.unsubscribe("workspace:ws-1", queue)

    assert hub.subscriber_count("workspace:ws-1") == 0
    assert "workspace:ws-1" not in hub._topics


@pytest.mark.asyncio
async def test_a_backlogged_subscriber_keeps_the_newest_events(hub):
    """A tab that stopped reading (backgrounded, throttled) must not grow the
    queue without bound, and for status snapshots the newest event is the one
    worth keeping."""
    queue = hub.subscribe("workspace:ws-1")

    for index in range(QUEUE_MAXSIZE + 5):
        hub.publish("workspace:ws-1", {"index": index})
    await asyncio.sleep(0)  # let the loop run the queued deliveries

    assert queue.qsize() == QUEUE_MAXSIZE
    assert queue.get_nowait() == {"index": 5}


@pytest.mark.asyncio
async def test_a_closed_loop_costs_the_event_not_the_caller(hub):
    """The dev server restarts on every backend edit, so a subscriber's loop
    can be gone while a queue operation is still in flight."""
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    hub.subscribe("workspace:ws-1")
    hub._loop = dead_loop

    hub.publish("workspace:ws-1", {"type": "execution_update"})  # must not raise
