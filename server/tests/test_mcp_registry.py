"""The registry of MCP servers agents may reach."""

import json
from unittest.mock import patch

import pytest
from loregarden.agents.mcp_context import MCP_SERVER_NAME, loregarden_mcp_cli_config_json
from loregarden.models.domain import McpServer, McpServerCreate, McpServerUpdate
from loregarden.services.mcp_registry import (
    McpRegistryError,
    cli_server_entries,
    create_server,
    delete_server,
    list_servers,
    to_view,
    update_server,
)
from sqlmodel import Session


def _http(name: str = "github", **kwargs) -> McpServerCreate:
    return McpServerCreate(name=name, transport="http", url="https://mcp.example/sse", **kwargs)


def test_a_registered_server_round_trips(db_session: Session):
    created = create_server(db_session, _http(description="Issues and PRs"))
    assert created.name == "github"
    assert [s.name for s in list_servers(db_session)] == ["github"]

    view = to_view(created)
    assert view.transport == "http"
    assert view.description == "Issues and PRs"


def test_a_transport_must_carry_what_it_needs(db_session: Session):
    """A server missing its url or command registers cleanly and then fails
    inside an agent subprocess, where the cause is hard to see."""
    with pytest.raises(McpRegistryError, match="needs a url"):
        create_server(db_session, McpServerCreate(name="a", transport="http"))
    with pytest.raises(McpRegistryError, match="needs a command"):
        create_server(db_session, McpServerCreate(name="b", transport="stdio"))
    with pytest.raises(McpRegistryError, match="Unknown transport"):
        create_server(db_session, McpServerCreate(name="c", transport="carrier-pigeon"))


def test_names_cannot_collide(db_session: Session):
    """The name is the key under `mcpServers`, so a duplicate would shadow
    rather than conflict — silently replacing one server with another."""
    create_server(db_session, _http())
    with pytest.raises(McpRegistryError, match="already registered"):
        create_server(db_session, _http())


def test_renaming_onto_another_server_is_refused(db_session: Session):
    create_server(db_session, _http("github"))
    other = create_server(db_session, _http("linear"))
    with pytest.raises(McpRegistryError, match="already registered"):
        update_server(db_session, other.id, McpServerUpdate(name="github"))


def test_a_server_can_be_renamed_to_itself(db_session: Session):
    server = create_server(db_session, _http("github"))
    updated = update_server(db_session, server.id, McpServerUpdate(name="github", url="https://b/"))
    assert updated.url == "https://b/"


def test_an_update_writes_every_field_it_names_trimmed(db_session: Session):
    server = create_server(db_session, _http(description="old"))
    stamped = server.updated_at

    update_server(
        db_session,
        server.id,
        McpServerUpdate(
            name=" gitlab ",
            description="Merge requests",
            transport=" stdio ",
            command=" npx gitlab-mcp ",
            auth_env_var=" GITLAB_TOKEN ",
            args=["--read-only"],
        ),
    )

    with Session(db_session.get_bind()) as fresh:
        stored = fresh.get(McpServer, server.id)
        assert (stored.name, stored.description, stored.transport) == (
            "gitlab",
            "Merge requests",
            "stdio",
        )
        assert (stored.command, stored.auth_env_var) == ("npx gitlab-mcp", "GITLAB_TOKEN")
        assert json.loads(stored.args_json) == ["--read-only"]
        assert stored.updated_at > stamped


def test_an_update_that_leaves_the_server_unusable_is_refused(db_session: Session):
    server = create_server(db_session, _http())
    with pytest.raises(McpRegistryError, match="needs a command"):
        update_server(db_session, server.id, McpServerUpdate(transport="stdio"))


def test_deleting_removes_it(db_session: Session):
    server = create_server(db_session, _http())
    delete_server(db_session, server.id)
    assert list_servers(db_session) == []
    with pytest.raises(McpRegistryError, match="not found"):
        delete_server(db_session, server.id)


def test_the_credential_is_never_stored(db_session: Session):
    """Only the variable's name is kept. This database is copied into scratch
    dirs and worktrees; a token at rest here would travel with every copy."""
    server = create_server(db_session, _http(auth_env_var="GITHUB_MCP_TOKEN"))
    row = db_session.get(McpServer, server.id)
    stored = json.dumps(row.model_dump(), default=str)

    with patch.dict("os.environ", {"GITHUB_MCP_TOKEN": "super-secret"}):
        assert "super-secret" not in stored
        assert to_view(row).auth_present is True
    # Presence is reported, never the value.
    assert "super-secret" not in json.dumps(to_view(row).model_dump(), default=str)


def test_a_missing_credential_is_visible_without_reading_it(db_session: Session):
    server = create_server(db_session, _http(auth_env_var="NOT_SET_ANYWHERE"))
    assert to_view(server).auth_present is False


def test_the_credential_reaches_the_subprocess_config(db_session: Session):
    create_server(db_session, _http(auth_env_var="GITHUB_MCP_TOKEN"))
    with patch.dict("os.environ", {"GITHUB_MCP_TOKEN": "tok"}):
        entries = cli_server_entries(db_session)
    assert entries["github"]["headers"] == {"Authorization": "Bearer tok"}


def test_an_http_server_with_no_token_set_sends_no_authorization(db_session: Session):
    create_server(db_session, _http(auth_env_var="GITHUB_MCP_TOKEN"))
    create_server(db_session, _http(name="open"))
    with patch.dict("os.environ", {}, clear=True):
        entries = cli_server_entries(db_session)
    assert entries["github"] == {"type": "http", "url": "https://mcp.example/sse"}
    assert entries["open"] == {"type": "http", "url": "https://mcp.example/sse"}


def test_a_stdio_server_is_launched_and_passed_its_credential_by_name(db_session: Session):
    create_server(
        db_session,
        McpServerCreate(
            name="local",
            transport="stdio",
            command="mcp-local",
            args=["--port", "0"],
            auth_env_var="LOCAL_MCP_TOKEN",
        ),
    )
    create_server(db_session, McpServerCreate(name="plain", transport="stdio", command="mcp-plain"))
    with patch.dict("os.environ", {"LOCAL_MCP_TOKEN": "tok"}):
        entries = cli_server_entries(db_session)

    assert entries["local"] == {
        "type": "stdio",
        "command": "mcp-local",
        "args": ["--port", "0"],
        "env": {"LOCAL_MCP_TOKEN": "tok"},
    }
    assert entries["plain"] == {"type": "stdio", "command": "mcp-plain", "args": []}


def test_a_disabled_server_is_withheld_from_agents(db_session: Session):
    """Parked, not deleted — a misbehaving server keeps its configuration."""
    server = create_server(db_session, _http())
    update_server(db_session, server.id, McpServerUpdate(enabled=False))
    assert cli_server_entries(db_session) == {}
    assert len(list_servers(db_session)) == 1


def test_the_cli_config_carries_loregarden_and_the_registry(db_session: Session):
    create_server(db_session, _http())
    config = json.loads(loregarden_mcp_cli_config_json(db_session))
    assert set(config["mcpServers"]) == {MCP_SERVER_NAME, "github"}


def test_a_registered_server_cannot_displace_loregarden(db_session: Session):
    """Losing the control plane's own tools would break the workflow the agent
    is running, so its entry is written last."""
    create_server(db_session, _http(name=MCP_SERVER_NAME))
    config = json.loads(loregarden_mcp_cli_config_json(db_session))
    assert config["mcpServers"][MCP_SERVER_NAME]["url"] != "https://mcp.example/sse"


def test_without_a_session_the_config_is_unchanged(db_session: Session):
    """Callers outside a request — docs, tests — get what they always got."""
    create_server(db_session, _http())
    config = json.loads(loregarden_mcp_cli_config_json())
    assert list(config["mcpServers"]) == [MCP_SERVER_NAME]


def test_a_broken_registry_does_not_stop_a_run(db_session: Session):
    with patch(
        "loregarden.agents.mcp_context.cli_server_entries", side_effect=RuntimeError("boom")
    ):
        config = json.loads(loregarden_mcp_cli_config_json(db_session))
    assert list(config["mcpServers"]) == [MCP_SERVER_NAME]


def test_the_api_lists_creates_and_deletes(client):
    created = client.post(
        "/api/mcp-servers",
        json={"name": "linear", "transport": "http", "url": "https://mcp.linear.app/sse"},
    )
    assert created.status_code == 200, created.text
    server_id = created.json()["id"]

    assert any(s["name"] == "linear" for s in client.get("/api/mcp-servers").json())

    patched = client.patch(f"/api/mcp-servers/{server_id}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    assert client.delete(f"/api/mcp-servers/{server_id}").status_code == 200
    assert client.get("/api/mcp-servers").json() == []


def test_the_api_rejects_a_bad_registration(client):
    bad = client.post("/api/mcp-servers", json={"name": "x", "transport": "http"})
    assert bad.status_code == 400
    assert "url" in bad.json()["detail"]


def test_patching_an_unknown_server_is_404(client):
    assert client.patch("/api/mcp-servers/nope", json={"enabled": True}).status_code == 404
