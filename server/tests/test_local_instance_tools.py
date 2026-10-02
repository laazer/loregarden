"""The local-instance MCP tools, as loregarden advertises and polices them.

The tools' behaviour is lore-eden's and tested there against real processes.
What is loregarden's is the naming, the approval policy, and that a call
through `/mcp` reaches the same manager the Instances panel uses — so one
real launch goes all the way through here too.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from lore_eden.instances import (
    CommandTemplate,
    FileInstanceRegistry,
    InstanceKind,
    InstanceManager,
    TemplateCatalog,
)
from loregarden.agents.executors.tool_auto_approve import (
    is_auto_approved_mcp_tool,
    is_orchestrated_agent_denied_mcp_tool,
)
from loregarden.mcp import local_instance_tools
from loregarden.mcp.tool_ids import (
    LOCAL_INSTANCE_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
)


@pytest.mark.parametrize("tool", LOCAL_INSTANCE_MCP_TOOLS)
def test_offered_to_every_stage_and_never_waits_on_the_inbox(tool) -> None:
    assert tool in STAGE_DEFAULT_MCP_TOOLS
    assert is_auto_approved_mcp_tool(f"mcp__loregarden__{tool.value}", {})
    assert not is_orchestrated_agent_denied_mcp_tool(tool.value)


@pytest.fixture(name="http_manager")
def http_manager_fixture(tmp_path: Path):
    """A manager whose one template serves a directory with the stdlib."""
    catalog = TemplateCatalog()
    catalog.register(
        CommandTemplate(
            project="loregarden",
            name="static",
            kind=InstanceKind.SERVER,
            command=[sys.executable, "-m", "http.server", "{port}", "--bind", "{host}"],
            cwd=str(tmp_path),
            health_path="/",
            ready_timeout_seconds=15,
        )
    )
    manager = InstanceManager(FileInstanceRegistry(tmp_path / "registry"), catalog)
    with patch.object(local_instance_tools, "get_instance_manager", return_value=manager):
        yield manager
    for view in manager.list().instances:
        manager.stop(view.id)


def _call(client: TestClient, tool: str, **arguments) -> dict:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    # A tool that raises comes back as an `isError` result whose text is the
    # bare exception message, not JSON. Decoding it anyway reported only
    # "JSONDecodeError: Expecting value" and threw the message away.
    result = body["result"]
    text = result["content"][0]["text"]
    assert not result.get("isError"), f"{tool} raised: {text}"
    return json.loads(text)


def test_an_agent_launches_checks_and_stops_a_real_instance(
    client: TestClient, http_manager: InstanceManager
) -> None:
    listing = _call(client, "loregarden_list_instances")
    assert [t["name"] for t in listing["templates"]] == ["static"]

    launched = _call(client, "loregarden_launch_instance", template="static", wait_seconds=15)
    assert launched["ok"], launched
    instance = launched["instance"]
    assert instance["state"] == "ready"

    status = _call(client, "loregarden_instance_status", instance_id=instance["id"], log_lines=5)
    assert status["health"]["ok"] is True

    assert _call(client, "loregarden_stop_instance", instance_id=instance["id"])["ok"]
    assert http_manager.registry.get(instance["id"]) is None


def test_a_bad_parameter_is_a_classified_answer_not_an_rpc_error(
    client: TestClient, http_manager: InstanceManager
) -> None:
    result = _call(client, "loregarden_launch_instance", template="static", params={"shell": "sh"})
    assert result == {
        "ok": False,
        "error_kind": "invalid_params",
        "retryable": False,
        "error": "unknown parameter(s) for static: shell",
    }


def test_a_tool_that_raises_fails_naming_the_exception(client: TestClient) -> None:
    """The helper must report what the tool said, not a JSON decode error.

    An `OSError` escaping a launch reached `/mcp` as `isError` text
    ("[Errno 35] ...") and the helper decoded it, so a pre-push failure read
    "Expecting value: line 1 column 2" with the cause discarded.
    """

    with (
        # The manager is looked up per call, inside the dispatched handler, so
        # this reaches the real `/mcp` path; patching `HANDLERS` would not —
        # `tool_registry` copied it at import.
        patch.object(
            local_instance_tools,
            "get_instance_manager",
            side_effect=OSError(35, "Resource temporarily unavailable"),
        ),
        pytest.raises(AssertionError, match=r"loregarden_list_instances raised: \[Errno 35\]"),
    ):
        _call(client, "loregarden_list_instances")
