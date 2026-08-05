import json

from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
from loregarden.agents.executors.tool_auto_approve import denied_cli_tool_message
from loregarden.models.domain import AgentRun, Approval, RunStatus, Ticket
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


class _FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)
        self._closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0) + "\n"
        self._closed = True
        return ""


class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []

    def write(self, data):
        self.writes.append(data.decode("utf-8") if isinstance(data, bytes) else data)

    def flush(self):
        return None


class _FakeProc:
    returncode = 0

    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stdin = _FakeStdin()
        self.stderr = None

    def poll(self):
        return 0 if self.stdout._closed else None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = 1


def test_git_commit_hook_bypass_is_denied_by_policy():
    assert denied_cli_tool_message("Bash", {"command": "git commit --no-verify -m test"})
    assert denied_cli_tool_message("Bash", {"command": "git -C repo commit -n -m test"})
    assert denied_cli_tool_message("Bash", {"command": "git commit -anm test"})
    assert denied_cli_tool_message("Bash", {"command": "npm test && git commit -n -m test"})
    assert denied_cli_tool_message("Bash", {"command": "git commit -m test"}) == ""
    assert denied_cli_tool_message("WebFetch", {"command": "git commit -n"}) == ""


def test_permission_bridge_denies_git_commit_hook_bypass_even_with_auto_approve(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation

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
            run_code="run_git_no_verify_perm",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
            auto_approve=True,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("commit", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_bash_no_verify",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git commit --no-verify -m bad"},
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_bash", "subtype": "success"}
        )

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([permission_line, result_line])
            return captured_proc

        result = PermissionBridgeRunner(session).run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="commit",
            timeout_seconds=30,
            spawn_process=fake_spawn,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert captured_proc is not None
        writes = "".join(captured_proc.stdin.writes)
        assert '"behavior": "deny"' in writes
        assert "hook bypass is forbidden" in writes
        approval = session.exec(select(Approval).where(Approval.run_id == run.id)).first()
        assert approval is None
