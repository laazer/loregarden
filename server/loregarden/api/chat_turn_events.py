"""One chat turn's reasoning, pushed as it is produced.

Surface-agnostic on purpose. Every chat surface already publishes an
``active_turn_id`` — the pending assistant row for Home, branch and studio
chat, the ``AgentRun`` for ticket triage — so a channel keyed by that id serves
all four with one endpoint and one client hook, rather than four of each.

The REST read is not a lesser fallback, it is the starting state: a panel that
opens mid-turn, or after a reload, needs everything produced so far, and only
the row has it. The socket carries what comes next.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loregarden.core.auth import websocket_token_ok
from loregarden.db.session import engine, get_session
from loregarden.services.chat_thinking import chat_turn_topic, read_chat_turn_thinking
from loregarden.services.event_hub import event_hub
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat-turn-events"])

#: 1008 "policy violation" is what a refused connection is, per RFC 6455.
POLICY_VIOLATION = 1008

#: How long to wait on an event before checking the socket is still there.
#: Nothing is resent on this tick — a turn that is thinking hard can be quiet
#: for a while, and re-pushing an unchanged transcript would only cost frames.
IDLE_TICK_SECONDS = 15.0


@router.get("/api/chat-turns/{turn_id}/thinking")
def get_chat_turn_thinking(turn_id: str, session: Session = Depends(get_session)) -> dict:
    """Everything this turn has thought so far.

    Never a 404: a turn with no reasoning yet, and a turn that has already
    settled, are both "nothing live here" as far as the panel is concerned, and
    an error would make the caller handle a non-error.
    """
    return read_chat_turn_thinking(session, turn_id)


async def _await_disconnect(websocket: WebSocket) -> None:
    """Park on the receive side so a closed socket is noticed immediately."""
    while True:
        await websocket.receive_text()


def _initial_frame(turn_id: str) -> dict:
    with Session(engine) as session:
        return read_chat_turn_thinking(session, turn_id)


@router.websocket("/ws/chat-turns/{turn_id}")
async def chat_turn_socket(websocket: WebSocket, turn_id: str) -> None:
    if not websocket_token_ok(websocket):
        await websocket.close(code=POLICY_VIOLATION, reason="Missing or invalid API token")
        return

    await websocket.accept()

    # Subscribed before the first read, so reasoning produced between the read
    # and the subscription is queued rather than lost.
    topic = chat_turn_topic(turn_id)
    queue = event_hub.subscribe(topic)
    disconnected = asyncio.create_task(_await_disconnect(websocket))

    try:
        await websocket.send_json({"type": "chat_thinking", "data": _initial_frame(turn_id)})

        while not disconnected.done():
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {pending, disconnected},
                timeout=IDLE_TICK_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if pending not in done:
                pending.cancel()
                continue
            # Coalesce: token deltas arrive faster than a socket should frame
            # them, and each carries the whole transcript, so only the last one
            # in a burst says anything the others do not.
            event = pending.result()
            while not queue.empty():
                event = queue.get_nowait()
            if disconnected.done():
                break
            await websocket.send_json(event)
            if event.get("type") == "chat_thinking_done":
                break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - one bad socket must not take the loop down
        logger.warning("Chat turn socket failed for %s", turn_id, exc_info=True)
    finally:
        disconnected.cancel()
        event_hub.unsubscribe(topic, queue)
