import asyncio
import os
from unittest import mock
from unittest.mock import patch

import httpx
import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.mcp_context import (
    SandboxMcpUrlError,
    build_mcp_run_context,
    load_loregarden_mcp_doc,
    load_memory_protocol_doc,
    load_stage_report_contract_doc,
    loregarden_mcp_cli_config_json,
    resolve_api_base_url,
    resolve_mcp_url,
    unverified_api_base_url,
)
from loregarden.config import settings
from loregarden.main import app
from loregarden.models.domain import (
    AgentRun,
    ControlPlaneTransport,
    MemoryBriefingAssembly,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services import sandbox_endpoint
from loregarden.services.sandbox_endpoint import (
    INSTANCE_ID,
    reset_verification,
    verify_this_instance,
)
from loregarden.services.seed import seed_database
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_resolve_mcp_url_env(monkeypatch):
    monkeypatch.setenv("LOREGARDEN_MCP_URL", "http://example.test/mcp")
    assert resolve_mcp_url() == "http://example.test/mcp"


@pytest.fixture(name="sandbox_settings")
def sandbox_settings_fixture(monkeypatch):
    monkeypatch.delenv("LOREGARDEN_MCP_URL", raising=False)
    monkeypatch.delenv("LOREGARDEN_API_URL", raising=False)
    reset_verification()
    with (
        patch.object(settings, "sandbox", True),
        patch.object(settings, "mcp_url", "http://127.0.0.1:8000/mcp"),
        patch.object(settings, "dev_host", "127.0.0.1"),
        patch.object(settings, "dev_port", 8123),
    ):
        yield
    reset_verification()


def _verify(base: str, transport: httpx.AsyncBaseTransport) -> bool:
    async def run() -> bool:
        async with httpx.AsyncClient(transport=transport) as client:
            return await verify_this_instance(base, client=client)

    return asyncio.run(run())


def _answering_as(instance_id: str) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(200, json={"instance_id": instance_id})
    )


def test_a_sandbox_hands_out_its_own_url_once_it_answers_as_itself(sandbox_settings):
    """Main's default would have the sandbox's agents write to production."""
    assert unverified_api_base_url() == "http://127.0.0.1:8123"
    # The real /health, so the id this proves is the one the server serves.
    assert _verify("http://127.0.0.1:8123", httpx.ASGITransport(app=app))

    assert resolve_mcp_url() == "http://127.0.0.1:8123/mcp"
    assert resolve_api_base_url() == "http://127.0.0.1:8123"


def test_a_sandbox_bound_to_a_wildcard_hands_out_loopback(sandbox_settings):
    """0.0.0.0 is where it listens, not where an agent can be sent."""
    with patch.object(settings, "dev_host", "0.0.0.0"):
        assert unverified_api_base_url() == "http://127.0.0.1:8123"


def test_agents_are_refused_until_the_endpoint_is_proven(sandbox_settings):
    with pytest.raises(SandboxMcpUrlError, match="not verified"):
        resolve_mcp_url()


def test_another_server_on_the_url_is_refused(sandbox_settings):
    assert not _verify("http://127.0.0.1:8123", _answering_as("someone-else"))
    with pytest.raises(SandboxMcpUrlError, match="different server"):
        resolve_mcp_url()


def test_a_url_that_never_answers_is_refused(sandbox_settings):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with (
        patch.object(sandbox_endpoint, "VERIFY_TIMEOUT_SECONDS", 0.05),
        patch.object(sandbox_endpoint, "_RETRY_SECONDS", 0.01),
    ):
        assert not _verify("http://127.0.0.1:8123", httpx.MockTransport(refuse))
    with pytest.raises(SandboxMcpUrlError, match="did not answer"):
        resolve_mcp_url()


def test_an_inherited_url_must_be_the_verified_one(sandbox_settings, monkeypatch):
    """A shell that exported main's URL must not leak it into the sandbox's agents."""
    assert _verify("http://127.0.0.1:8123", _answering_as(INSTANCE_ID))
    monkeypatch.setenv("LOREGARDEN_MCP_URL", "http://127.0.0.1:8000/mcp")
    with pytest.raises(SandboxMcpUrlError):
        resolve_mcp_url()


def test_a_sandbox_with_no_url_and_no_port_refuses(sandbox_settings):
    with patch.object(settings, "dev_port", None), pytest.raises(SandboxMcpUrlError):
        unverified_api_base_url()


def test_main_still_uses_the_configured_default(monkeypatch):
    monkeypatch.delenv("LOREGARDEN_MCP_URL", raising=False)
    monkeypatch.delenv("LOREGARDEN_API_URL", raising=False)
    with patch.object(settings, "sandbox", False), patch.object(settings, "dev_port", 8000):
        assert resolve_mcp_url() == settings.mcp_url.rstrip("/")


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
    text = build_mcp_run_context(
        ticket=ticket, run=run, workspace=workspace, transport=ControlPlaneTransport.MCP
    )
    assert "loregarden_get_ticket" in text
    assert "native MCP tools" in text
    assert "mcp__loregarden__loregarden_get_ticket" in text
    assert "Do **not** initialize MCP via Bash/curl" in text
    assert "ticket-1" in text
    assert "03-wire-cli-agent-runner" in text
    assert "orch-1" in text
    assert "Loregarden memory (workspace-scoped)" in text or "Loregarden artifacts" in text
    assert "loregarden_memory_status" in text
    assert "loregarden_upsert_blog_post" in text
    assert "memory_sqlite_path" in text
    assert "create_memory_relation" in text
    assert 'workspace_slug="loregarden"' in text
    assert "## Stage outcome (stage runs)" in text
    assert "loregarden_complete_stage" in text
    assert "<<<LOREGARDEN_STAGE_REPORT>>>" in text


def _handoff_run(stage_type: str) -> str:
    ticket = Ticket(id="t", external_id="ext", title="T", workspace_id="ws")
    run = AgentRun(
        run_code="run_h",
        ticket_id="t",
        workspace_id="ws",
        agent_id="architecture_reviewer",
        stage_key="script_review",
    )
    workspace = Workspace(id="ws", slug="blobert", name="Blobert")
    stage_def = WorkflowStageDef(key="script_review", name="Script Review", stage_type=stage_type)
    return build_mcp_run_context(
        ticket=ticket,
        run=run,
        workspace=workspace,
        stage_def=stage_def,
        transport=ControlPlaneTransport.MCP,
    )


def test_handoff_instruction_linear_stage_tells_agent_to_write_handoff():
    text = _handoff_run("agent")
    assert "**Finishing agents:**" in text
    assert "loregarden_write_handoff" in text


def test_handoff_instruction_parallel_stage_suppresses_write_handoff():
    """A parallel reviewer has no `(from → to)` pair; telling it to write a handoff
    makes it invent an uncataloged downstream the strict gate then rejects."""
    text = _handoff_run("parallel")
    assert "parallel review stage" in text
    assert "Do **not** call" in text
    assert "**Finishing agents:**" not in text


def test_handoff_instruction_defaults_to_linear_when_stage_unknown():
    ticket = Ticket(id="t", external_id="ext", title="T", workspace_id="ws")
    run = AgentRun(run_code="run_h", ticket_id="t", workspace_id="ws", agent_id="static_qa")
    workspace = Workspace(id="ws", slug="loregarden", name="Loregarden")
    text = build_mcp_run_context(
        ticket=ticket, run=run, workspace=workspace, transport=ControlPlaneTransport.MCP
    )
    assert "**Finishing agents:**" in text


def test_cli_prompt_includes_mcp_module():
    """The module reaches a run that has MCP.

    The adapter is pinned because the suite forces `local`, which this process
    wires no MCP server into — such a run is told about the CLI instead, and
    that is the subject of test_stage_prompt_transport.py.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket
        workspace = session.get(Workspace, ticket.workspace_id)
        run = AgentRun(
            run_code="run_mcp",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="",
            stage_key="testing",
        )
        executor = CliAgentExecutor(session)
        agent = {"role_file": "agents/9_static_qa/static_qa_v1.md", "adapter": "claude"}
        stage_def = executor._resolve_stage_def(ticket, run)
        with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
            prompt = executor._build_prompt(
                ticket,
                run,
                agent,
                resolve_agent_context_dir(workspace),
                workspace,
                stage_def,
                assembly_source=MemoryBriefingAssembly.DISPATCH,
            )
        assert "Loregarden MCP (required for workflow state)" in prompt
        assert "Loregarden memory (workspace-scoped)" in prompt or "Loregarden artifacts" in prompt
        assert "Memory protocol module" in prompt
        assert "loregarden_get_ticket" in prompt
        assert load_loregarden_mcp_doc(transport=ControlPlaneTransport.MCP)[:200] in prompt
        assert load_memory_protocol_doc(resolve_agent_context_dir(workspace))[:200] in prompt


def test_cli_prompt_includes_stage_report_contract():
    """Agents only emit a stage report if the prompt carries the contract.

    Nothing loaded it before, so the Context tab's per-stage reports were empty
    for every run that did not happen to read the module off disk itself.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket
        workspace = session.get(Workspace, ticket.workspace_id)
        run = AgentRun(
            run_code="run_report",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="",
            stage_key="testing",
        )
        executor = CliAgentExecutor(session)
        prompt = executor._build_prompt(
            ticket,
            run,
            {"role_file": "agents/9_static_qa/static_qa_v1.md", "adapter": "claude"},
            resolve_agent_context_dir(workspace),
            workspace,
            executor._resolve_stage_def(ticket, run),
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )
        assert "## Stage report contract" in prompt
        assert "<<<LOREGARDEN_STAGE_REPORT>>>" in prompt
        assert "<<<END_STAGE_REPORT>>>" in prompt


def test_stage_report_contract_doc_excludes_v1_sections():
    """The rest of workflow_enforcement_v1.md contradicts the live architecture.

    Injecting the whole module would tell agents to go find a ticket markdown
    file that does not exist, and pin a stage enum whose values are not the
    workflow_template keys `reroute_to_stage` is validated against.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket
        workspace = session.get(Workspace, ticket.workspace_id)
        doc = load_stage_report_contract_doc(resolve_agent_context_dir(workspace))

        assert "<<<LOREGARDEN_STAGE_REPORT>>>" in doc
        assert "reroute_to_stage" in doc
        assert "TICKET AUTHORITY" not in doc
        assert "STAGE ENUM (STRICT)" not in doc
        assert "IMPLEMENTATION_BACKEND" not in doc
        assert "00_backlog/" not in doc


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
