from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.lmstudio_runner import main as lmstudio_runner_main
from loregarden.agents.executors.local_runner import main as local_runner_main
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TicketState, Workspace
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


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


def test_resolve_claude_model_precedence(tmp_path, monkeypatch):
    """ticket override > stage override > agent default > workspace setting."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CLAUDE_BIN", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    ws = Workspace(slug="test", name="Test", claude_model="workspace-model")

    def model_for(**overrides):
        inv = resolve_cli_invocation(
            agent_id="planner",
            adapter="claude",
            prompt="stage task",
            prompt_file=prompt_file,
            skill_name="plan",
            workspace_root=workspace_root,
            workspace=ws,
            **overrides,
        )
        idx = inv.argv.index("--model")
        return inv.argv[idx + 1]

    assert model_for() == "workspace-model"
    assert model_for(agent_model="agent-model") == "agent-model"
    assert model_for(agent_model="agent-model", stage_model="stage-model") == "stage-model"
    assert (
        model_for(
            agent_model="agent-model",
            stage_model="stage-model",
            ticket_claude_model="ticket-model",
        )
        == "ticket-model"
    )


def test_resolve_cursor_model_precedence(tmp_path, monkeypatch):
    """Mirrors test_resolve_claude_model_precedence for the cursor adapter."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "cursor")
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CURSOR_BIN", "cursor-agent")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    ws = Workspace(slug="test", name="Test", cursor_model="workspace-model")

    def model_for(**overrides):
        inv = resolve_cli_invocation(
            agent_id="backend_implementer",
            adapter="cursor",
            prompt="stage task",
            prompt_file=prompt_file,
            skill_name="implement",
            workspace_root=workspace_root,
            workspace=ws,
            **overrides,
        )
        idx = inv.argv.index("--model")
        return inv.argv[idx + 1]

    assert model_for() == "workspace-model"
    assert model_for(agent_model="agent-model") == "agent-model"
    assert model_for(agent_model="agent-model", stage_model="stage-model") == "stage-model"
    assert (
        model_for(
            agent_model="agent-model",
            stage_model="stage-model",
            ticket_cursor_model="ticket-model",
        )
        == "ticket-model"
    )


def test_resolve_adapter_ticket_override(tmp_path, monkeypatch):
    # The `force_local_cli_adapter` autouse fixture sets LOREGARDEN_CLI_ADAPTER=local for
    # every test; clear it here since this test exercises the ticket-override tier, which
    # sits below the env-var tier in the precedence chain.
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CURSOR_BIN", "cursor-agent")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hello", encoding="utf-8")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ws = Workspace(slug="test", name="Test", cli_adapter="claude")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="hello",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=workspace,
        workspace=ws,
        ticket_adapter="cursor",
    )
    assert inv.argv[0] == "cursor-agent"


def test_local_runner_success(tmp_path, monkeypatch):
    monkeypatch.delenv("LOREGARDEN_FORCE_AGENT_FAIL", raising=False)
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("ticket body", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "local_runner",
            "--agent-id",
            "planner",
            "--skill",
            "plan",
            "--prompt-file",
            str(prompt_file),
        ],
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


def test_cli_executor_threads_model_precedence_into_invocation(isolated_db, monkeypatch):
    """execute() must resolve ticket/stage/agent model overrides and pass them through
    to resolve_cli_invocation — not just resolve_cli_invocation's own precedence logic,
    which test_resolve_claude_model_precedence covers in isolation.

    Uses the `isolated_db` fixture (rather than a bare local engine, like the other tests
    in this file) because get_agent()'s StudioAgent lookup reads through the module-global
    `loregarden.db.session.engine` binding — a locally-created engine would silently miss it.
    """
    import json

    from loregarden.agents.executors import cli as cli_executor_module
    from loregarden.models.domain import StudioAgent

    engine = isolated_db
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()

        from loregarden.services.orchestration import OrchestrationService

        template = OrchestrationService(session).get_template_for_ticket(ticket)
        stages = json.loads(template.stages_json)
        for stage in stages:
            stage["model"] = "stage-pin"
        template.stages_json = json.dumps(stages)
        session.add(template)

        agent = StudioAgent(
            slug="model-pin-agent",
            name="Model Pin Agent",
            role_body="Do a focused thing.",
            adapter="claude",
            default_model="agent-pin",
        )
        session.add(agent)

        ticket.orchestration_runtime_json = json.dumps(
            {
                "cli_adapter": "default",
                "claude_model": "ticket-pin",
                "cursor_model": "",
                "lmstudio_base_url": "",
                "lmstudio_model": "",
            }
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

        run = AgentRun(
            run_code="run_precedence",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="model-pin-agent",
            stage_key=ticket.workflow_stage_key,
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()

        captured: dict = {}
        real_resolve = cli_executor_module.resolve_cli_invocation

        def fake_resolve(**kwargs):
            captured.update(kwargs)
            return real_resolve(**kwargs)

        monkeypatch.setattr(cli_executor_module, "resolve_cli_invocation", fake_resolve)

        executor = CliAgentExecutor(session)
        executor.execute(run, ticket, skip_git_branch=True)

        assert captured["ticket_claude_model"] == "ticket-pin"
        assert captured["stage_model"] == "stage-pin"
        assert captured["agent_model"] == "agent-pin"
