"""Whether a tool call needs the operator, across every MCP server."""

from loregarden.models.domain import McpServerCreate, McpServerUpdate
from loregarden.services.mcp_registry import create_server, update_server
from loregarden.services.tool_policy import (
    POLICY_AUTO,
    server_auto_approves,
    split_mcp_tool,
)
from sqlmodel import Session


def _server(session: Session, name="github", **kwargs):
    return create_server(
        session,
        McpServerCreate(name=name, transport="http", url="https://mcp.example/sse", **kwargs),
    )


def test_a_tool_name_splits_into_server_and_tool():
    assert split_mcp_tool("mcp__github__create_issue") == ("github", "create_issue")
    # Server names with underscores are why this is a regex and not a split.
    assert split_mcp_tool("mcp__my_server__do_a_thing") == ("my_server", "do_a_thing")


def test_a_plain_tool_name_is_not_an_mcp_call():
    assert split_mcp_tool("Bash") is None
    assert split_mcp_tool("") is None


def test_a_registered_server_prompts_by_default(db_session: Session):
    """Registering a server grants reach, not trust."""
    _server(db_session)
    assert server_auto_approves(db_session, "github") is False


def test_a_trusted_server_runs_unattended(db_session: Session):
    server = _server(db_session)
    update_server(db_session, server.id, McpServerUpdate(tool_policy=POLICY_AUTO))
    assert server_auto_approves(db_session, "github") is True


def test_a_disabled_server_is_not_trusted(db_session: Session):
    """Disabling withholds the server from agents; it must not leave behind a
    standing approval for calls that somehow still arrive."""
    server = _server(db_session, tool_policy=POLICY_AUTO)
    update_server(db_session, server.id, McpServerUpdate(enabled=False))
    assert server_auto_approves(db_session, "github") is False


def test_an_unregistered_server_is_not_trusted(db_session: Session):
    """A call naming a server nobody registered is exactly the case to look at."""
    assert server_auto_approves(db_session, "somebody-elses-server") is False


def test_an_unknown_policy_is_refused(db_session: Session):
    import pytest
    from loregarden.services.mcp_registry import McpRegistryError

    with pytest.raises(McpRegistryError, match="Unknown tool policy"):
        _server(db_session, name="odd", tool_policy="whatever")


def test_the_policy_survives_the_api(client):
    created = client.post(
        "/api/mcp-servers",
        json={
            "name": "linear",
            "transport": "http",
            "url": "https://mcp.linear.app/sse",
            "tool_policy": "auto",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["tool_policy"] == "auto"

    patched = client.patch(
        f"/api/mcp-servers/{created.json()['id']}", json={"tool_policy": "prompt"}
    )
    assert patched.json()["tool_policy"] == "prompt"


def test_the_bridge_asks_the_registry_before_prompting(db_session: Session):
    """The wiring, not just the rule.

    U1a made third-party servers reachable while the auto-approve check still
    recognised only loregarden's prefix, so every such call stopped for a human.
    """
    from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner

    bridge = PermissionBridgeRunner(db_session)
    server = _server(db_session)

    # Registered but not trusted: still prompts.
    assert bridge._third_party_auto_approved("mcp__github__create_issue", None) is None

    update_server(db_session, server.id, McpServerUpdate(tool_policy=POLICY_AUTO))
    assert bridge._third_party_auto_approved("mcp__github__create_issue", None) == "github"


def test_loregardens_own_tools_keep_their_finer_allowlist(db_session: Session):
    """Loregarden is never whole-server trusted: its allowlist distinguishes
    reads and bookkeeping writes from workflow-state mutations, which a
    server-level decision cannot express."""
    from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner

    bridge = PermissionBridgeRunner(db_session)
    create_server(
        db_session,
        McpServerCreate(
            name="loregarden", transport="http", url="https://x/", tool_policy=POLICY_AUTO
        ),
    )

    # `bare_mcp` is set for loregarden tools, which short-circuits this path.
    assert (
        bridge._third_party_auto_approved("mcp__loregarden__block_ticket", "block_ticket") is None
    )
    # Even without it, the server name alone is refused.
    assert bridge._third_party_auto_approved("mcp__loregarden__block_ticket", None) is None
