"""A websocket carrying one shell session.

Native FastAPI rather than the Flask-SocketIO layer the plan assumed: that
server is never instantiated or mounted, so its handlers cannot fire and the
"room/broadcast pattern" it described does not exist at runtime. Building on it
would have meant reviving a whole second stack first.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loregarden.config import settings
from loregarden.db.session import get_session
from loregarden.models.domain import Workspace
from loregarden.services.terminal_session import TerminalSession
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terminal", tags=["terminal"])

#: Close codes. 1008 is the protocol's "policy violation", which is what a
#: refused connection is.
POLICY_VIOLATION = 1008


def _token_ok(websocket: WebSocket) -> bool:
    """Whether this connection may open a shell.

    Checked here rather than left to TokenAuthMiddleware, which extends
    BaseHTTPMiddleware and therefore never sees a websocket scope. Without this
    the terminal would be the one endpoint that ignores the API token — and it
    is the endpoint where that matters most, since it is a shell.
    """
    expected = settings.api_token
    if not expected:
        # No token configured: the whole API is already open to local
        # processes, and refusing only the terminal would be theatre.
        return True
    presented = websocket.query_params.get("token") or ""
    header = websocket.headers.get("x-loregarden-token") or ""
    return hmac.compare_digest(presented or header, expected)


async def _pump_output(websocket: WebSocket, session: TerminalSession) -> None:
    """Shell output to the browser, off the event loop.

    `os.read` on a pty blocks until there is something to read, so it runs in a
    worker thread; doing it inline would stall every other request whenever the
    terminal is idle.
    """
    loop = asyncio.get_running_loop()
    while True:
        data = await loop.run_in_executor(None, session.read)
        if not data:
            break
        await websocket.send_text(data.decode("utf-8", errors="replace"))


@router.websocket("/{workspace_slug}")
async def terminal_socket(
    websocket: WebSocket,
    workspace_slug: str,
    db: Session = Depends(get_session),
) -> None:
    if not _token_ok(websocket):
        await websocket.close(code=POLICY_VIOLATION, reason="Missing or invalid API token")
        return

    workspace = db.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if not workspace:
        await websocket.close(code=POLICY_VIOLATION, reason="Unknown workspace")
        return

    root = resolve_workspace_root(workspace)
    if not root.is_dir():
        await websocket.close(code=POLICY_VIOLATION, reason=f"Workspace path missing: {root}")
        return

    await websocket.accept()
    session = TerminalSession(root)
    pump = asyncio.create_task(_pump_output(websocket, session))

    try:
        while True:
            raw = await websocket.receive_text()
            # Control frames are JSON; anything else is keystrokes, which must
            # pass through untouched or a literal '{' would be swallowed.
            if raw.startswith('{"type"'):
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    session.write(raw)
                    continue
                if message.get("type") == "resize":
                    session.resize(int(message.get("rows", 24)), int(message.get("cols", 80)))
                    continue
                if message.get("type") == "input":
                    session.write(str(message.get("data", "")))
                    continue
            session.write(raw)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a broken socket must still reap the shell
        logger.warning("Terminal socket for %s failed", workspace_slug, exc_info=True)
    finally:
        # The shell outlives the socket unless it is reaped here, and an
        # orphaned login shell per browser refresh adds up quickly.
        pump.cancel()
        session.close()
