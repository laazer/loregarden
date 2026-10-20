"""Telling a viewer who left from a socket that broke.

A push socket parks a task on its receive side (`_await_disconnect`) so a
closed tab is noticed at once. But a send can lose the race with that close:
uvloop refuses the write on the closed transport with a bare `RuntimeError`
("unable to perform operation on <TCPTransport closed=True ...>") before
Starlette's receive side has turned the close into `WebSocketDisconnect`. The
handler's catch-all then logged an ordinary closed tab as "Queue socket
failed" with a traceback, which reads as a server fault in the log.

The receive side is the authority on whether the viewer went, so it is asked,
given a moment to catch up, rather than the error's text.
"""

from __future__ import annotations

import asyncio

#: How long the receive side gets to notice a close that a send already hit.
#: The close is already on the wire; this is scheduling, not network, time.
CLOSE_GRACE_SECONDS = 0.5


async def peer_left(disconnected: asyncio.Task[None]) -> bool:
    """Whether the viewer closed the socket, by the receive side's word."""
    if not disconnected.done():
        await asyncio.wait({disconnected}, timeout=CLOSE_GRACE_SECONDS)
    if not disconnected.done():
        return False
    if not disconnected.cancelled():
        # Retrieved so asyncio does not report it as "never retrieved" when the
        # task is collected: it is the close we were waiting for, not a fault.
        disconnected.exception()
    return True
