"""Server-Sent Events endpoint for queue notifications."""

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from loregarden.db.session import get_session
from loregarden.models.domain import QueuedRun, QueuePosition
from loregarden.websocket_events import ws

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel", tags=["notifications"])


@router.get("/workspace/{workspace_id}/notifications")
async def get_notifications(workspace_id: str = Path(...)):
    """
    Server-Sent Events endpoint for queue notifications.

    Streams real-time events for run completion, promotion, and failures.
    Falls back mechanism for clients that don't support WebSocket.
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        """Generate SSE events for workspace."""
        subscription_id = f"notifications:{workspace_id}"
        event_queue: asyncio.Queue = asyncio.Queue()

        def on_event(event_type: str, data: dict):
            """Handle event from WebSocket system."""
            try:
                asyncio.create_task(event_queue.put((event_type, data)))
            except Exception as e:
                logger.warning(f"Failed to queue notification: {e}")

        # Subscribe to workspace events
        ws.subscribe(subscription_id, on_event)

        try:
            while True:
                try:
                    # Wait for events with timeout to keep connection alive
                    event_type, data = await asyncio.wait_for(
                        event_queue.get(), timeout=30.0
                    )

                    # Format as SSE
                    yield f"event: {event_type}\n"
                    yield f"data: {json.dumps(data)}\n\n"

                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            logger.debug(f"Notification stream closed for {workspace_id}")
        except Exception as e:
            logger.error(f"Error in notification stream: {e}")
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
        finally:
            # Unsubscribe on disconnect
            ws.unsubscribe(subscription_id, on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable proxy buffering
        },
    )


@router.post("/{workspace_id}/notify")
async def send_notification(
    workspace_id: str = Path(...),
    event_type: str = None,
    data: dict = None,
):
    """
    Internal endpoint to send notifications to all connected clients.

    Used by other API endpoints to broadcast events.
    """
    if not event_type or not data:
        return {"status": "error", "message": "Missing event_type or data"}

    try:
        # Broadcast to all subscribers of this workspace
        ws.emit(
            f"notifications:{workspace_id}",
            event_type=event_type,
            data=data,
        )

        logger.debug(f"Notification sent: {event_type} in {workspace_id}")
        return {"status": "sent", "event_type": event_type}

    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return {"status": "error", "message": str(e)}
