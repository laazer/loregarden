"""Agents driving the open UI: the catalog, the approval split, the broker, the wire.

The properties that matter, each pinned below:

- the server decides an action's effect, and the approval check reads it — a
  view change skips the inbox, a write does not, a human-only action never runs;
- every failure reaches the agent by name: no tab, not available, timeout, tab
  closed, tab error — never a hang and never a success it did not have;
- an MCP call really does round-trip through a connected tab;
- the client's catalog and page list cannot drift from the server's.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from loregarden.agents.executors.tool_auto_approve import is_auto_approved_mcp_tool
from loregarden.core import auth
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.ui_action_tools import invoke_ui_action
from loregarden.services.ui_action_broker import UiActionBroker, UiActionFailure, ui_action_broker
from loregarden.services.ui_action_catalog import CATALOG, UiAction, UiActionEffect, UiPage
from starlette.websockets import WebSocketDisconnect
from tests.mcp_helpers import call_mcp

_CLIENT = Path(__file__).resolve().parents[2] / "client" / "src"
_INVOKE = f"mcp__loregarden__{McpTool.INVOKE_UI_ACTION.value}"


def _tool_payload(response: dict) -> dict[str, Any]:
    return json.loads(response["result"]["content"][0]["text"])


# --------------------------------------------------------------------------- #
# The approval split is the catalog's effect, decided before any tab is asked
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", list(UiAction))
def test_only_view_actions_skip_the_inbox(action: UiAction):
    expected = CATALOG[action].effect is UiActionEffect.VIEW
    assert is_auto_approved_mcp_tool(_INVOKE, {"action": action.value}) is expected


@pytest.mark.parametrize("tool_input", [None, {}, {"action": "no.such.action"}])
def test_an_invoke_that_names_no_known_action_is_not_auto_approved(tool_input):
    assert is_auto_approved_mcp_tool(_INVOKE, tool_input) is False


def test_listing_is_auto_approved():
    assert is_auto_approved_mcp_tool(f"mcp__loregarden__{McpTool.LIST_UI_ACTIONS.value}", {})


def test_a_write_and_a_human_only_action_exist_to_be_gated():
    """The parametrized split above is vacuous if the catalog has only views."""
    effects = {spec.effect for spec in CATALOG.values()}
    assert {UiActionEffect.VIEW, UiActionEffect.WRITE, UiActionEffect.HUMAN_ONLY} <= effects


# --------------------------------------------------------------------------- #
# The tool refuses before reaching a tab
# --------------------------------------------------------------------------- #


def _invoke(arguments: dict[str, Any]) -> dict[str, Any]:
    return json.loads(invoke_ui_action(None, arguments))  # type: ignore[arg-type]


def test_a_human_only_action_is_refused_even_with_a_tab_offering_it():
    """Refused in the tool, so an auto_approve run (no inbox at all) cannot reach it."""
    outcome = _invoke(
        {"action": UiAction.APPROVAL_RESOLVE.value, "arguments": {"approval_id": "a"}}
    )
    assert outcome["ok"] is False
    assert outcome["failure"] == UiActionFailure.HUMAN_ONLY


def test_an_unknown_action_is_named():
    outcome = _invoke({"action": "ticket.delete"})
    assert outcome["failure"] == UiActionFailure.UNKNOWN_ACTION


def test_arguments_are_validated_against_the_catalog():
    outcome = _invoke({"action": UiAction.NAVIGATE_PAGE.value, "arguments": {"page": "nowhere"}})
    assert outcome["failure"] == UiActionFailure.INVALID_ARGUMENTS


def test_no_tab_is_a_named_failure_not_a_hang():
    outcome = _invoke({"action": UiAction.NAVIGATE_PAGE.value, "arguments": {"page": "queue"}})
    assert outcome["failure"] == UiActionFailure.NO_TAB
    assert "CLI runs outside the server" in outcome["message"]


# --------------------------------------------------------------------------- #
# Broker routing and failure, on its own loop
# --------------------------------------------------------------------------- #


class _FakeTab:
    """A tab that answers invocations the way the test tells it to."""

    def __init__(self, broker: UiActionBroker, tab_id: str, reply: str = "ok") -> None:
        self.broker, self.tab_id, self.reply, self.received = broker, tab_id, reply, []

    async def send(self, frame: dict[str, Any]) -> None:
        self.received.append(frame)
        if self.reply == "ok":
            self.broker.resolve(self.tab_id, frame["id"], True, {"done": frame["action"]}, "")
        elif self.reply == "error":
            self.broker.resolve(self.tab_id, frame["id"], False, None, "handler blew up")
        elif self.reply == "close":
            self.broker.disconnect(self.tab_id)
        # "silent": never answers


def _tab(
    broker: UiActionBroker, tab_id: str, actions: set[UiAction], reply: str = "ok"
) -> _FakeTab:
    tab = _FakeTab(broker, tab_id, reply)
    broker.connect(tab_id, tab.send)
    broker.register(tab_id, frozenset(actions))
    return tab


def test_the_visible_tab_offering_the_action_is_the_one_asked():
    async def scenario():
        broker = UiActionBroker()
        hidden = _tab(broker, "hidden", {UiAction.TICKET_OPEN})
        visible = _tab(broker, "visible", {UiAction.TICKET_OPEN})
        other = _tab(broker, "other", {UiAction.NAVIGATE_PAGE})
        broker.focus("hidden", visible=False)
        outcome = await broker.invoke(UiAction.TICKET_OPEN, {"ticket_id": "x"})
        return outcome, hidden, visible, other

    outcome, hidden, visible, other = asyncio.run(scenario())
    assert outcome.ok and outcome.tab_id == "visible"
    assert outcome.result == {"done": "ticket.open"}
    assert (len(hidden.received), len(visible.received), len(other.received)) == (0, 1, 0)


def test_an_action_no_tab_offers_says_what_to_do_first():
    async def scenario():
        broker = UiActionBroker()
        _tab(broker, "t", {UiAction.NAVIGATE_PAGE})
        return await broker.invoke(UiAction.TICKET_SET_STATE, {})

    outcome = asyncio.run(scenario())
    assert outcome.failure == UiActionFailure.NOT_AVAILABLE
    assert "ticket.open" in outcome.message


@pytest.mark.parametrize(
    ("reply", "failure"),
    [
        ("error", UiActionFailure.TAB_ERROR),
        ("close", UiActionFailure.TAB_CLOSED),
        ("silent", UiActionFailure.TIMEOUT),
    ],
)
def test_every_way_a_tab_can_fail_reaches_the_agent_by_name(reply: str, failure: UiActionFailure):
    async def scenario():
        broker = UiActionBroker()
        _tab(broker, "t", {UiAction.NAVIGATE_PAGE}, reply=reply)
        outcome = await broker.invoke(UiAction.NAVIGATE_PAGE, {"page": "queue"}, timeout=0.2)
        return outcome, broker

    outcome, broker = asyncio.run(scenario())
    assert outcome.ok is False and outcome.failure == failure
    assert outcome.action == UiAction.NAVIGATE_PAGE.value
    assert broker._pending == {}  # nothing left waiting


def test_a_tab_cannot_answer_for_a_request_it_was_not_sent():
    async def scenario():
        broker = UiActionBroker()
        _tab(broker, "asked", {UiAction.NAVIGATE_PAGE}, reply="silent")
        _tab(broker, "imposter", set())
        task = asyncio.create_task(broker.invoke(UiAction.NAVIGATE_PAGE, {}, timeout=0.3))
        await asyncio.sleep(0.05)
        request_id = next(iter(broker._pending))
        broker.resolve("imposter", request_id, True, "forged", "")
        return await task

    outcome = asyncio.run(scenario())
    assert outcome.failure == UiActionFailure.TIMEOUT


# --------------------------------------------------------------------------- #
# The wire: an MCP call round-trips through a connected tab
# --------------------------------------------------------------------------- #


def test_an_mcp_invoke_round_trips_through_a_connected_tab(client):
    with client.websocket_connect("/ws/ui-actions") as socket:
        socket.send_json({"type": "register", "actions": ["navigate.page", "not.a.real.action"]})
        # A synchronous read through the server: by the time it answers, the
        # register frame above has been handled (same socket, in order).
        listing = _tool_payload(call_mcp(client, McpTool.LIST_UI_ACTIONS.value, {}))
        offered = {a["action"]: a["offered_by_tabs"] for a in listing["actions"]}
        assert offered["navigate.page"] and not offered["ticket.open"]

        answers: dict[str, Any] = {}
        caller = threading.Thread(
            target=lambda: answers.update(
                call_mcp(
                    client,
                    McpTool.INVOKE_UI_ACTION.value,
                    {"action": "navigate.page", "arguments": {"page": "queue"}},
                )
            )
        )
        caller.start()
        frame = socket.receive_json()
        assert frame["type"] == "invoke" and frame["action"] == "navigate.page"
        assert frame["arguments"] == {"page": "queue"}
        socket.send_json(
            {"type": "result", "id": frame["id"], "ok": True, "result": {"path": "/queue"}}
        )
        caller.join(timeout=10)

    outcome = _tool_payload(answers)
    assert outcome["ok"] is True and outcome["result"] == {"path": "/queue"}
    assert ui_action_broker.availability() == {}  # the closed tab is gone


def test_the_socket_refuses_a_missing_token(client):
    with mock.patch.object(auth.settings, "api_token", "secret"):
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws/ui-actions") as socket:
                socket.receive_json()
    assert refused.value.code == 1008


# --------------------------------------------------------------------------- #
# The client mirrors the server
# --------------------------------------------------------------------------- #


def _ts_keys(path: Path, block_start: str) -> set[str]:
    text = path.read_text()
    block = text[text.index(block_start) :]
    block = block[: block.index("\n}\n") if "\n}\n" in block else block.index(";\n")]
    return set(re.findall(r'^\s*"?([a-z][\w.-]*)"?\s*[:|]', block, re.MULTILINE)) | set(
        re.findall(r'\|\s*"([\w.-]+)"', block)
    )


def test_the_client_catalog_names_exactly_the_servers_actions():
    keys = _ts_keys(
        _CLIENT / "lib" / "agentActions" / "catalog.ts", "export interface UiActionArgs"
    )
    action_names = {k for k in keys if "." in k}
    assert action_names == {a.value for a in UiAction}


def test_the_page_enum_matches_the_clients_pages():
    text = (_CLIENT / "lib" / "appNavigation.ts").read_text()
    union = text[
        text.index("export type AppPage =") : text.index(";", text.index("export type AppPage ="))
    ]
    assert set(re.findall(r'"([\w-]+)"', union)) == {p.value for p in UiPage}
