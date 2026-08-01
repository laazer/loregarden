"""A websocket carrying parallel-execution status for one workspace.

The queue dashboard used to poll `/api/parallel/status/{id}` every few seconds
from every open tab. This pushes the same snapshot instead: once on connect,
again whenever a service reports the queue changed, and on a slow tick so the
elapsed-time readouts keep moving and a missed event can never leave the screen
staler than the poll it replaced.

Native FastAPI, on the one process that already exists. The Flask-SocketIO
module this supersedes was never instantiated or mounted, so its handlers could
not fire and its `emit_*` helpers were no-ops at every call site.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loregarden.core.auth import websocket_token_ok
from loregarden.db.session import engine
from loregarden.services.event_hub import event_hub
from loregarden.services.queue_status import build_queue_status
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["queue-events"])

#: 1008 "policy violation" is what a refused connection is, per RFC 6455.
POLICY_VIOLATION = 1008

#: How long to wait for an event before sending a snapshot anyway.
#:
#: Two jobs. It keeps `elapsed_seconds` and queue wait times advancing on
#: screen, and it bounds how stale the dashboard can get if a state change
#: happens on a path that does not emit — matching the 5s poll this replaced,
#: so the socket is never *worse* than polling was.
REFRESH_INTERVAL_SECONDS = 5.0


#: Event types worth telling the client about individually.
#:
#: ``execution_update`` is deliberately absent: it means "the queue changed",
#: which is what the snapshot already says, and forwarding it would fire a
#: toast for every reorder.
NOTIFIABLE_EVENTS = frozenset({"queue_promoted", "run_completed", "error"})


def _topic(workspace_id: str) -> str:
    return f"workspace:{workspace_id}"


async def _snapshot(workspace_id: str) -> dict:
    """Read current queue state.

    A fresh session per snapshot on purpose: a session held for the life of the
    socket caches loaded rows, so the second snapshot and every one after would
    return the first one's data and the dashboard would freeze while looking
    connected.
    """
    with Session(engine) as session:
        return await build_queue_status(session, workspace_id)


async def _send_snapshot(websocket: WebSocket, workspace_id: str) -> None:
    await websocket.send_json({"type": "queue_status", "data": await _snapshot(workspace_id)})


def _notifiable(events: list[dict]) -> list[dict]:
    """The events a person should be told about, each at most once.

    A burst that promotes the same run twice is one promotion as far as the
    dashboard is concerned; without the dedupe a chain of completions would
    stack duplicate toasts.
    """
    seen: set[tuple] = set()
    forwarded = []
    for event in events:
        if event.get("type") not in NOTIFIABLE_EVENTS:
            continue
        key = (event.get("type"), (event.get("data") or {}).get("runId"))
        if key in seen:
            continue
        seen.add(key)
        forwarded.append(event)
    return forwarded


async def _send_events(websocket: WebSocket, events: list[dict]) -> None:
    for event in _notifiable(events):
        await websocket.send_json({"type": "queue_event", "data": event})


async def _await_disconnect(websocket: WebSocket) -> None:
    """Park on the receive side so a closed socket is noticed immediately.

    Without this the handler would only discover a disconnect on its next send,
    up to REFRESH_INTERVAL_SECONDS later — every closed tab holding a task and
    a DB read that long.
    """
    while True:
        await websocket.receive_text()


@router.websocket("/queue/{workspace_id}")
async def queue_socket(websocket: WebSocket, workspace_id: str) -> None:
    if not websocket_token_ok(websocket):
        await websocket.close(code=POLICY_VIOLATION, reason="Missing or invalid API token")
        return

    await websocket.accept()

    topic = _topic(workspace_id)
    queue = event_hub.subscribe(topic)
    disconnected = asyncio.create_task(_await_disconnect(websocket))

    try:
        await _send_snapshot(websocket, workspace_id)

        while not disconnected.done():
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {pending, disconnected},
                timeout=REFRESH_INTERVAL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            batch: list[dict] = []
            if pending in done:
                # Coalesce: a burst of events (a run finishing promotes the
                # next, which updates the queue, which...) should cost one
                # snapshot, not one per event. The events themselves are kept
                # rather than dropped — the snapshot says what the queue looks
                # like now, not that a run just finished, and the toast stack
                # needs the latter.
                batch.append(pending.result())
                while not queue.empty():
                    batch.append(queue.get_nowait())
            else:
                pending.cancel()

            if disconnected.done():
                break
            await _send_events(websocket, batch)
            await _send_snapshot(websocket, workspace_id)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - one bad socket must not take the loop down
        logger.warning("Queue socket for workspace %s failed", workspace_id, exc_info=True)
    finally:
        disconnected.cancel()
        event_hub.unsubscribe(topic, queue)
