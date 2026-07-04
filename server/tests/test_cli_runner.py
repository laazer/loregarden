import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.local_runner import main as local_runner_main
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TicketState
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
    )
    assert "local_runner" in " ".join(inv.argv)
    assert inv.argv[-1] == str(prompt_file)


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
