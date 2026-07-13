"""Studio agents persisted in SQLite must include memory MCP tools and protocol."""

from loregarden.models.domain import StudioAgent
from loregarden.services.studio_service import (
    DEFAULT_MEMORY_MCP_TOOLS,
    DEFAULT_STAGE_MCP_TOOLS,
    studio_agent_config,
)
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


def test_legacy_studio_agent_merges_memory_tools_at_runtime():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            StudioAgent(
                slug="legacy-custom",
                name="Legacy Custom",
                role_body="You are a custom agent.",
                mcp_enabled=True,
                mcp_tools_json='["loregarden_get_ticket", "loregarden_attach_artifact"]',
            )
        )
        session.commit()

        cfg = studio_agent_config(session, "legacy-custom")
        assert cfg is not None
        for tool in DEFAULT_MEMORY_MCP_TOOLS:
            assert tool in cfg["mcp_tools"]
        assert "memory_protocol_v1.md" in cfg["role_body"]
        assert "You are a custom agent." in cfg["role_body"]


def test_studio_defaults_include_memory_tools(client):
    res = client.get("/api/studio/defaults")
    assert res.status_code == 200
    body = res.json()
    for tool in DEFAULT_MEMORY_MCP_TOOLS:
        assert tool in body["mcp_tools"]
    assert body["memory_mcp_tools"] == DEFAULT_MEMORY_MCP_TOOLS
    for tool in DEFAULT_STAGE_MCP_TOOLS:
        assert tool in body["mcp_tools"]
