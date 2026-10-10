"""`/ws/ui-actions`: the socket an open tab uses to offer UI actions to agents.

The one socket here that carries requests *to* the browser. The tab sends what
it can do (`register`), whether the operator is looking at it (`focus`), and
the answer to each request (`result`); the server sends `invoke`. See
`services/ui_action_broker.py` for routing and failure handling.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loregarden.core.auth import websocket_token_ok
from loregarden.services.ui_action_broker import ui_action_broker
from loregarden.services.ui_action_catalog import UiAction
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["ui-actions"])

POLICY_VIOLATION = 1008


class _Register(BaseModel):
    type: Literal["register"]
    #: Unknown names are dropped rather than refused: a tab built from a newer
    #: client must still offer the actions this server knows.
    actions: list[str]


class _Focus(BaseModel):
    type: Literal["focus"]
    visible: bool


class _Result(BaseModel):
    type: Literal["result"]
    id: str
    ok: bool
    result: Any = None
    error: str = ""


_Frame: TypeAdapter[_Register | _Focus | _Result] = TypeAdapter(
    Annotated[_Register | _Focus | _Result, Field(discriminator="type")]
)


def _handle(tab_id: str, frame: _Register | _Focus | _Result) -> None:
    match frame:
        case _Register(actions=names):
            known = {UiAction.try_parse(name) for name in names}
            ui_action_broker.register(tab_id, frozenset(a for a in known if a is not None))
        case _Focus(visible=visible):
            ui_action_broker.focus(tab_id, visible=visible)
        case _Result():
            ui_action_broker.resolve(tab_id, frame.id, frame.ok, frame.result, frame.error)


@router.websocket("/ui-actions")
async def ui_action_socket(websocket: WebSocket) -> None:
    if not websocket_token_ok(websocket):
        await websocket.close(code=POLICY_VIOLATION, reason="Missing or invalid API token")
        return

    await websocket.accept()
    tab_id = uuid.uuid4().hex
    ui_action_broker.connect(tab_id, websocket.send_json)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = _Frame.validate_json(raw)
            except ValidationError:
                # A malformed frame is the client's bug, not a reason to drop the
                # tab's other actions; logged where an operator will see it.
                logger.warning("ui-actions: tab %s sent an unreadable frame: %.200s", tab_id, raw)
                continue
            _handle(tab_id, frame)
    except WebSocketDisconnect:
        pass  # silent-ok: the normal end of a tab; disconnect() below fails its in-flight requests by name
    finally:
        ui_action_broker.disconnect(tab_id)
