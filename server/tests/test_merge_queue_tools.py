"""The merge queue's MCP tools (lg-milestone-that-971): policy first, then the calls.

The policy assertions are the point: status is a read and auto-approves; enqueue
and dequeue are writes and must reach the approvals inbox on a stage run
without the grant, which is "not auto-approved" in the permission bridge.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.executors.tool_auto_approve import (
    LOREGARDEN_MCP_PREFIX,
    is_auto_approved_mcp_tool,
)
from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    READ_ONLY_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    TRIAGE_OPS_MCP_TOOLS,
    McpTool,
)
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services import merge_queue_requests
from sqlmodel import Session

WRITES = (McpTool.ENQUEUE_MERGE, McpTool.DEQUEUE_MERGE)


def test_status_auto_approves():
    assert McpTool.MERGE_QUEUE_STATUS in READ_ONLY_MCP_TOOLS
    assert is_auto_approved_mcp_tool(LOREGARDEN_MCP_PREFIX + McpTool.MERGE_QUEUE_STATUS, {})


@pytest.mark.parametrize("tool", WRITES)
def test_enqueue_and_dequeue_go_to_the_inbox(tool):
    assert tool not in AUTO_APPROVED_MCP_TOOLS
    assert not is_auto_approved_mcp_tool(LOREGARDEN_MCP_PREFIX + tool, {"entry_id": "x"})


@pytest.mark.parametrize("tool", (McpTool.MERGE_QUEUE_STATUS, *WRITES))
def test_they_are_operator_moves_not_stage_defaults(tool):
    """A stage landing the ticket it was dispatched for is the shape 494 has to settle."""
    assert tool in TRIAGE_OPS_MCP_TOOLS
    assert tool not in STAGE_DEFAULT_MCP_TOOLS


def _call(client: TestClient, tool: McpTool, args: dict) -> dict:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool.value, "arguments": args},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _text(reply: dict) -> str:
    assert "error" not in reply, reply
    return reply["result"]["content"][0]["text"]


@pytest.fixture(name="tree")
def tree_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        ws = Workspace(slug="mqt", name="mqt", repo_path="/nonexistent")
        session.add(ws)
        session.commit()
        ticket = Ticket(
            external_id="mqt-1", workspace_id=ws.id, title="t", work_item_type=WorkItemType.FEATURE
        )
        session.add(ticket)
        session.commit()
        return ticket.id


def test_enqueue_status_and_dequeue_over_mcp(client, tree):
    with (
        mock.patch.object(
            merge_queue_requests, "published_branch", return_value=("integration/mqt-1", "e" * 40)
        ),
        mock.patch.object(merge_queue_requests, "GhQueueGitHub") as gh,
    ):
        gh.return_value.open_pull_request.return_value = (3, "https://github.com/o/r/pull/3")
        enqueued = json.loads(_text(_call(client, McpTool.ENQUEUE_MERGE, {"ticket_id": tree})))

    assert enqueued["outcome"] == "admitted"
    entry_id = enqueued["entry"]["id"]

    status = json.loads(_text(_call(client, McpTool.MERGE_QUEUE_STATUS, {"workspace_slug": "mqt"})))
    assert status["head_id"] == entry_id
    assert [e["pr_number"] for e in status["entries"]] == [3]

    withdrawn = json.loads(
        _text(
            _call(
                client,
                McpTool.DEQUEUE_MERGE,
                {"entry_id": entry_id, "action": "withdraw", "reason": "smoke"},
            )
        )
    )
    retried = json.loads(
        _text(_call(client, McpTool.DEQUEUE_MERGE, {"entry_id": entry_id, "action": "retry"}))
    )
    assert (withdrawn["state"], retried["state"]) == ("withdrawn", "queued")


def test_a_refusal_is_an_error_not_an_empty_answer(client, tree):
    with (
        mock.patch.object(
            merge_queue_requests, "published_branch", return_value=("integration/mqt-1", "e" * 40)
        ),
        mock.patch.object(merge_queue_requests, "GhQueueGitHub") as gh,
    ):
        gh.return_value.open_pull_request.return_value = None
        reply = _call(client, McpTool.ENQUEUE_MERGE, {"ticket_id": tree})

    assert "loregarden_land_ticket" in json.dumps(reply)
    assert "result" not in reply or reply["result"].get("isError"), reply
