from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.mcp_context import (
    build_mcp_run_context,
    load_loregarden_mcp_doc,
    loregarden_mcp_cli_config_json,
    resolve_mcp_url,
)
from loregarden.models.domain import AgentRun, Ticket, Workspace
from loregarden.services.seed import seed_database
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_resolve_mcp_url_env(monkeypatch):
    monkeypatch.setenv("LOREGARDEN_MCP_URL", "http://example.test/mcp")
    assert resolve_mcp_url() == "http://example.test/mcp"


def test_build_mcp_run_context_includes_ids():
    ticket = Ticket(
        id="ticket-1",
        external_id="03-wire-cli-agent-runner",
        title="Test",
        workspace_id="ws-1",
    )
    run = AgentRun(
        run_code="run_abc",
        ticket_id="ticket-1",
        workspace_id="ws-1",
        agent_id="static_qa",
        skill_name="run_tests",
        stage_key="testing",
        orchestration_run_id="orch-1",
    )
    workspace = Workspace(id="ws-1", slug="loregarden", name="Loregarden")
    text = build_mcp_run_context(ticket=ticket, run=run, workspace=workspace)
    assert "loregarden_get_ticket" in text
    assert "native MCP tools" in text
    assert "mcp__loregarden__loregarden_get_ticket" in text
    assert "Do **not** initialize MCP via Bash/curl" in text
    assert "ticket-1" in text
    assert "03-wire-cli-agent-runner" in text
    assert "orch-1" in text


def test_cli_prompt_includes_mcp_module():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket
        workspace = session.get(Workspace, ticket.workspace_id)
        run = AgentRun(
            run_code="run_mcp",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="run_tests",
            stage_key="testing",
        )
        executor = CliAgentExecutor(session)
        agent = {"role_file": "agents/9_static_qa/static_qa_v1.md"}
        prompt = executor._build_prompt(
            ticket,
            run,
            agent,
            resolve_agent_context_dir(workspace),
            workspace,
        )
        assert "Loregarden MCP (required for workflow state)" in prompt
        assert "loregarden_get_ticket" in prompt
        assert load_loregarden_mcp_doc(resolve_agent_context_dir(workspace))[:200] in prompt


def test_loregarden_mcp_cli_config_uses_http_transport_by_default():
    payload = loregarden_mcp_cli_config_json()
    assert '"mcpServers"' in payload
    assert '"type": "http"' in payload
    assert '"loregarden"' in payload
    assert "8000/mcp" in payload


def test_loregarden_mcp_cli_config_stdio_override(monkeypatch):
    monkeypatch.setenv("LOREGARDEN_MCP_TRANSPORT", "stdio")
    payload = loregarden_mcp_cli_config_json()
    assert '"type": "stdio"' in payload
    assert "mcp-server.sh" in payload
