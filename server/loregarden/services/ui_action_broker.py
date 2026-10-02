"""Relay an agent's UI action to an open Loregarden tab, and its answer back.

Tabs connect over `/ws/ui-actions` and say which catalog actions they can
perform right now. An invocation goes to the tab most likely to be the one the
operator is looking at — visible, then most recently focused — among those that
offer the action, and waits for that tab's reply.

Every way this can fail comes back as a named `UiActionFailure`, never as a
silent success or a hang: no tab open, the action not available in any open
tab (a ticket action with no ticket open), the tab not answering in time, the
tab closing mid-call, or the tab's handler failing.

The broker lives on the server's event loop; MCP tool calls run in worker
threads (`api/mcp.py`), so they come in through `invoke_from_thread`.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from loregarden.services.ui_action_catalog import UiAction
from pydantic import BaseModel

#: Long enough for a ticket save to round-trip; short enough that an agent is
#: not parked behind a tab that went to sleep.
INVOKE_TIMEOUT_SECONDS = 20.0

SendFrame = Callable[[dict[str, Any]], Awaitable[None]]


class UiActionFailure(StrEnum):
    UNKNOWN_ACTION = "unknown_action"
    INVALID_ARGUMENTS = "invalid_arguments"
    HUMAN_ONLY = "human_only"
    NO_TAB = "no_tab"
    NOT_AVAILABLE = "not_available"
    TIMEOUT = "timeout"
    TAB_CLOSED = "tab_closed"
    TAB_ERROR = "tab_error"


class UiActionOutcome(BaseModel):
    ok: bool
    action: str
    tab_id: str | None = None
    result: Any = None
    failure: UiActionFailure | None = None
    message: str = ""

    @classmethod
    def failed(cls, action: str, failure: UiActionFailure, message: str, tab_id: str | None = None):
        return cls(ok=False, action=action, failure=failure, message=message, tab_id=tab_id)


@dataclass
class _Tab:
    send: SendFrame
    available: frozenset[UiAction] = frozenset()
    visible: bool = True
    focused_at: float = 0.0
    pending: set[str] = field(default_factory=set)


class UiActionBroker:
    def __init__(self) -> None:
        self._tabs: dict[str, _Tab] = {}
        self._pending: dict[str, tuple[str, asyncio.Future[UiActionOutcome]]] = {}
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- tab side (event loop) ------------------------------------------------

    def connect(self, tab_id: str, send: SendFrame) -> None:
        self._loop = asyncio.get_running_loop()
        self._tabs[tab_id] = _Tab(send=send, focused_at=time.monotonic())

    def disconnect(self, tab_id: str) -> None:
        tab = self._tabs.pop(tab_id, None)
        if tab is None:
            return
        for request_id in tab.pending:
            _, future = self._pending.pop(request_id, (None, None))
            if future is not None and not future.done():
                future.set_result(
                    UiActionOutcome.failed(
                        "",
                        UiActionFailure.TAB_CLOSED,
                        "the tab closed before it answered; the action may or may not have run",
                        tab_id,
                    )
                )

    def register(self, tab_id: str, available: frozenset[UiAction]) -> None:
        if tab_id in self._tabs:
            self._tabs[tab_id].available = available

    def focus(self, tab_id: str, *, visible: bool) -> None:
        tab = self._tabs.get(tab_id)
        if tab is None:
            return
        tab.visible = visible
        if visible:
            tab.focused_at = time.monotonic()

    def resolve(self, tab_id: str, request_id: str, ok: bool, result: Any, error: str) -> None:
        entry = self._pending.get(request_id)
        # A reply for a request this tab was not sent is ignored: a stale tab
        # must not be able to answer for another.
        if entry is None or entry[0] != tab_id:
            return
        self._pending.pop(request_id)
        tab = self._tabs.get(tab_id)
        if tab is not None:
            tab.pending.discard(request_id)
        _, future = entry
        if future.done():
            return
        if ok:
            future.set_result(UiActionOutcome(ok=True, action="", tab_id=tab_id, result=result))
        else:
            future.set_result(
                UiActionOutcome.failed(
                    "", UiActionFailure.TAB_ERROR, error or "the tab reported a failure", tab_id
                )
            )

    def availability(self) -> dict[str, list[str]]:
        """Which tab offers what — the `list` tool's answer to "where can I act?"."""
        return {
            tab_id: sorted(a.value for a in tab.available) for tab_id, tab in self._tabs.items()
        }

    # -- agent side -----------------------------------------------------------

    async def invoke(
        self, action: UiAction, arguments: dict[str, Any], timeout: float = INVOKE_TIMEOUT_SECONDS
    ) -> UiActionOutcome:
        if not self._tabs:
            return UiActionOutcome.failed(action.value, UiActionFailure.NO_TAB, _NO_TAB_MESSAGE)
        offering = [(tab_id, tab) for tab_id, tab in self._tabs.items() if action in tab.available]
        if not offering:
            return UiActionOutcome.failed(
                action.value,
                UiActionFailure.NOT_AVAILABLE,
                f"no open tab can perform {action.value} right now"
                + (
                    " — open the ticket first with ticket.open"
                    if action.value.startswith("ticket.")
                    else ""
                ),
            )
        tab_id, tab = max(offering, key=lambda item: (item[1].visible, item[1].focused_at))
        request_id = str(next(self._ids))
        future: asyncio.Future[UiActionOutcome] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (tab_id, future)
        tab.pending.add(request_id)
        try:
            await tab.send(
                {"type": "invoke", "id": request_id, "action": action.value, "arguments": arguments}
            )
            outcome = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            outcome = UiActionOutcome.failed(
                action.value,
                UiActionFailure.TIMEOUT,
                f"the tab did not answer within {timeout:.0f}s; the action may or may not have run",
                tab_id,
            )
        finally:
            self._pending.pop(request_id, None)
            tab.pending.discard(request_id)
        return outcome.model_copy(update={"action": action.value})

    def invoke_from_thread(self, action: UiAction, arguments: dict[str, Any]) -> UiActionOutcome:
        """`invoke`, from a worker thread — how the synchronous MCP handlers call in."""
        loop = self._loop
        if loop is None or not self._tabs:
            return UiActionOutcome.failed(action.value, UiActionFailure.NO_TAB, _NO_TAB_MESSAGE)
        future = asyncio.run_coroutine_threadsafe(self.invoke(action, arguments), loop)
        # `invoke` bounds itself; the margin only covers getting onto the loop.
        return future.result(timeout=INVOKE_TIMEOUT_SECONDS + 5)


_NO_TAB_MESSAGE = (
    "no Loregarden tab is connected to this server. Open the app in a browser or the "
    "desktop app. (The loregarden CLI runs outside the server and never sees one — call "
    "this through the server's MCP endpoint.)"
)

#: The server's one broker. Tabs and MCP calls must meet at the same instance.
ui_action_broker = UiActionBroker()
