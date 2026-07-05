import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.lmstudio_runner import main as lmstudio_runner_main
from loregarden.agents.executors.local_runner import main as local_runner_main
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TicketState, Workspace
from loregarden.services.seed import seed_database


def test_resolve_local_adapter(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hello", encoding="utf-8")
    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="local",
        prompt="hello",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
    )
    assert "local_runner" in " ".join(inv.argv)
    assert inv.argv[-1] == str(prompt_file)


def test_resolve_claude_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CLAUDE_BIN", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=workspace,
    )

    assert inv.argv[0] == "claude"
    assert "-p" in inv.argv
    assert "--permission-mode" in inv.argv
    assert "--mcp-config" in inv.argv
    mcp_index = inv.argv.index("--mcp-config")
    assert '"type": "http"' in inv.argv[mcp_index + 1]


def test_resolve_claude_model_from_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CLAUDE_BIN", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    ws = Workspace(slug="test", name="Test", claude_model="sonnet")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=workspace_root,
        workspace=ws,
    )

    model_idx = inv.argv.index("--model")
    assert inv.argv[model_idx + 1] == "sonnet"


def test_resolve_lmstudio_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "lmstudio")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("implement feature", encoding="utf-8")
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    ws = Workspace(
        slug="test",
        name="Test",
        lmstudio_base_url="http://127.0.0.1:8080/v1",
        lmstudio_model="my-model",
    )

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="implement feature",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=workspace_root,
        workspace=ws,
    )

    assert inv.adapter == "lmstudio"
    assert "lmstudio_runner" in " ".join(inv.argv)
    assert "--base-url" in inv.argv
    base_idx = inv.argv.index("--base-url")
    assert inv.argv[base_idx + 1] == "http://127.0.0.1:8080/v1"
    model_idx = inv.argv.index("--model")
    assert inv.argv[model_idx + 1] == "my-model"


def test_resolve_cursor_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "cursor")
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CURSOR_BIN", "cursor-agent")
    prompt_file = tmp_path / "prompt.md"
    prompt = "implement feature X"
    prompt_file.write_text(prompt, encoding="utf-8")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    inv = resolve_cli_invocation(
        agent_id="backend_implementer",
        adapter="cursor",
        prompt=prompt,
        prompt_file=prompt_file,
        skill_name="implement",
        workspace_root=workspace,
    )

    assert inv.argv[0] == "cursor-agent"
    assert inv.argv[1] == "agent"
    assert "-p" in inv.argv
    assert "--approve-mcps" in inv.argv
    assert "--workspace" in inv.argv
    assert str(workspace) in inv.argv


def test_resolve_adapter_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "local")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hello", encoding="utf-8")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="hello",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
    )
    assert "local_runner" in " ".join(inv.argv)


def test_agent_registry_cli_adapters():
    from loregarden.agents.registry import AGENTS

    assert AGENTS["planner"]["adapter"] == "claude"
    assert AGENTS["backend_implementer"]["adapter"] == "cursor"
    assert AGENTS["frontend_implementer"]["adapter"] == "cursor"


def test_local_runner_success(tmp_path, monkeypatch):
    monkeypatch.delenv("LOREGARDEN_FORCE_AGENT_FAIL", raising=False)
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("ticket body", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["local_runner", "--agent-id", "planner", "--skill", "plan", "--prompt-file", str(prompt_file)],
    )
    assert local_runner_main() == 0


def test_local_runner_forced_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["local_runner", "--agent-id", "planner", "--skill", "", "--prompt-file", str(prompt_file)],
    )
    assert local_runner_main() == 1


def test_lmstudio_runner_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_LMSTUDIO_STUB_RESPONSE", "stub lmstudio reply")
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("hello lmstudio", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "lmstudio_runner",
            "--prompt-file",
            str(prompt_file),
            "--base-url",
            "http://127.0.0.1:1234/v1",
        ],
    )
    assert lmstudio_runner_main() == 0


def test_lmstudio_runner_forced_fail(tmp_path, monkeypatch):
    monkeypatch.delenv("LOREGARDEN_LMSTUDIO_STUB_RESPONSE", raising=False)
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["lmstudio_runner", "--prompt-file", str(prompt_file)],
    )
    assert lmstudio_runner_main() == 1


def test_cli_executor_unknown_agent():
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
        run = AgentRun(
            run_code="run_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="nonexistent_agent",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        executor = CliAgentExecutor(session)
        completed = executor.execute(run, ticket)
        assert completed.status == RunStatus.FAILED
        session.refresh(ticket)
        assert ticket.state == TicketState.BLOCKED
