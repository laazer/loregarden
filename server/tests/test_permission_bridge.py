import json

from loregarden.agents.cli_adapters import (
    permission_bypass_enabled,
    resolve_cli_invocation,
)
from loregarden.agents.executors.permission_bridge import (
    ApprovalResolution,
    PermissionBridgeRunner,
    bare_mcp_tool_name,
    build_ask_user_question_input,
    build_control_response,
    enrich_mcp_tool_input,
    extract_permission_request,
    is_ask_user_question,
    is_auto_approved_mcp_tool,
)


class _FakeStdout:
    """Feeds a fixed sequence of stream-json lines to the permission loop,
    then reports EOF (closed) — shared across every test that drives
    PermissionBridgeRunner.run() through a scripted CLI conversation."""

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


def test_extract_permission_request_control_message():
    payload = {
        "type": "control_request",
        "request_id": "perm_1",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
        },
    }
    parsed = extract_permission_request(payload)
    assert parsed is not None
    assert parsed["request_id"] == "perm_1"
    assert parsed["tool_name"] == "Bash"


def test_build_control_response_allow():
    response = build_control_response(request_id="perm_1", approved=True)
    assert response["response"]["response"]["behavior"] == "allow"
    assert "updatedInput" not in response["response"]["response"]


def test_build_control_response_allow_with_tool_input():
    response = build_control_response(
        request_id="perm_1",
        approved=True,
        updated_input={"command": "npm test"},
    )
    assert response["response"]["response"]["updatedInput"] == {"command": "npm test"}


def test_build_control_response_with_updated_input():
    updated = {
        "questions": [{"question": "Pick one?", "options": [{"label": "A"}]}],
        "answers": {"Pick one?": "A"},
    }
    response = build_control_response(
        request_id="perm_1",
        approved=True,
        updated_input=updated,
    )
    assert response["response"]["response"]["updatedInput"] == updated


def test_is_ask_user_question():
    assert is_ask_user_question("AskUserQuestion") is True
    assert is_ask_user_question("Bash") is False


def test_auto_approved_mcp_tools():
    assert is_auto_approved_mcp_tool("mcp__loregarden__loregarden_get_ticket") is True
    assert is_auto_approved_mcp_tool("mcp__loregarden__loregarden_attach_artifact") is False
    assert bare_mcp_tool_name("mcp__loregarden__loregarden_get_ticket") == "loregarden_get_ticket"


def test_enrich_mcp_tool_input_fills_ticket_id():
    from loregarden.models.domain import Ticket

    ticket = Ticket(
        id="ticket-uuid",
        external_id="03-wire-cli-agent-runner",
        title="Test",
        workspace_id="ws-1",
    )
    enriched = enrich_mcp_tool_input(
        bare_tool="loregarden_get_ticket",
        tool_input={},
        ticket=ticket,
        workspace_slug="loregarden",
    )
    assert enriched == {"ticket_id": "ticket-uuid"}


def test_build_ask_user_question_input():
    tool_input = {
        "questions": [
            {
                "question": "How should I format the output?",
                "options": [{"label": "Summary"}, {"label": "Detailed"}],
            }
        ]
    }
    payload = build_ask_user_question_input(
        tool_input,
        answers={"How should I format the output?": "Summary"},
    )
    assert payload["questions"] == tool_input["questions"]
    assert payload["answers"]["How should I format the output?"] == "Summary"


def test_resolve_claude_adapter_uses_permission_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    monkeypatch.delenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", raising=False)
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

    assert inv.interactive is True
    assert "--permission-prompt-tool" in inv.argv
    assert "stdio" in inv.argv
    assert "--mcp-config" in inv.argv
    assert "--permission-mode" in inv.argv
    mode_index = inv.argv.index("--permission-mode")
    assert inv.argv[mode_index + 1] == "default"
    assert "--output-format" in inv.argv
    assert "stream-json" in inv.argv


def test_permission_bypass_restores_headless_print_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
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

    assert inv.interactive is False
    assert "-p" in inv.argv
    assert permission_bypass_enabled() is True


def test_permission_bridge_creates_inbox_item_and_continues(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, ApprovalKind, RunStatus, Ticket
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_perm_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("do work", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_99",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Edit",
                    "tool_input": {"path": "src/main.py"},
                },
            }
        )
        result_line = json.dumps({"type": "result", "session_id": "sess_1", "subtype": "success"})

        approvals_seen: list[str] = []
        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([permission_line, result_line])
            return captured_proc

        def fake_wait(approval_id, **kwargs):
            approvals_seen.append(approval_id)
            return ApprovalResolution(approved=True)

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="do work",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert approvals_seen
        approval = session.get(Approval, approvals_seen[0])
        assert approval.kind == ApprovalKind.CLI_PERMISSION
        assert captured_proc is not None
        control_writes = []
        for raw in captured_proc.stdin.writes:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            for line in text.splitlines():
                if line.strip().startswith("{"):
                    control_writes.append(json.loads(line))
        allow_response = next(
            item for item in control_writes if item.get("type") == "control_response"
        )
        assert allow_response["response"]["response"]["updatedInput"] == {
            "path": "src/main.py",
        }


def test_permission_bridge_denies_out_of_scope_write_without_human_approval(tmp_path):
    """A scoped agent (backend_implementer) attempting to Edit a file outside
    its declared /server/** scope must be denied automatically — no pending
    Approval created, no human round-trip needed. Regression for ticket 33:
    a backend_implementer agent implemented frontend code because nothing
    technically stopped it, only prompt text."""
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, RunStatus, Ticket, Workspace
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()

        repo_root = tmp_path / "repo"
        (repo_root / "client" / "src").mkdir(parents=True)
        (repo_root / "server").mkdir()
        workspace = session.get(Workspace, ticket.workspace_id)
        workspace.repo_path = str(repo_root)
        session.add(workspace)
        session.commit()

        run = AgentRun(
            run_code="run_scope_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            stage_key="implementation",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("implement the button", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude", prompt_file=prompt_file, workspace_root=repo_root
        )

        target = str(repo_root / "client" / "src" / "ImportTicketsModal.tsx")
        lines = [
            json.dumps(
                {
                    "type": "control_request",
                    "request_id": "perm_scope_1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "Edit",
                        "tool_input": {"file_path": target, "old_string": "a", "new_string": "b"},
                    },
                }
            ),
            json.dumps({"type": "result", "session_id": "sess_scope", "subtype": "success"}),
        ]
        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc(lines)
            return captured_proc

        def fake_wait(approval_id, **kwargs):
            raise AssertionError("must not wait for human approval — scope violations auto-deny")

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="implement the button",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.FAILED
        assert "backend_implementer" in result.stderr
        assert session.exec(select(Approval).where(Approval.run_id == run.id)).first() is None
        session.refresh(ticket)
        assert "backend_implementer" in ticket.blocking_issues

        assert captured_proc is not None
        writes = "".join(
            raw.decode("utf-8") if isinstance(raw, bytes) else raw
            for raw in captured_proc.stdin.writes
        )
        assert '"behavior": "deny"' in writes


def test_permission_bridge_bash_allow_passes_command(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, ApprovalKind, RunStatus, Ticket
    from loregarden.services.orchestration import ApprovalService
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_bash_perm",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("run tests", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_bash_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "tool_input": {"command": "npm test"},
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

        def fake_wait(approval_id, **kwargs):
            ApprovalService(session).resolve(approval_id, approved=True)
            approval = session.get(Approval, approval_id)
            stored = json.loads(approval.response_json or "{}")
            return ApprovalResolution(
                approved=True,
                updated_input=stored.get("updated_input"),
            )

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="run tests",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.SUCCEEDED
        approval = session.exec(select(Approval).where(Approval.run_id == run.id)).first()
        assert approval.kind == ApprovalKind.CLI_PERMISSION
        assert approval.tool_name == "Bash"
        assert captured_proc is not None
        control_writes = []
        for raw in captured_proc.stdin.writes:
            for line in raw.splitlines():
                if line.strip().startswith("{"):
                    control_writes.append(json.loads(line))
        allow_response = next(
            item for item in control_writes if item.get("type") == "control_response"
        )
        assert allow_response["response"]["response"]["updatedInput"] == {
            "command": "npm test",
        }


def test_permission_bridge_auto_approves_mcp_get_ticket(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, RunStatus, Ticket
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_mcp_auto",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("qa", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_mcp_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "mcp__loregarden__loregarden_get_ticket",
                    "tool_input": {},
                },
            }
        )
        result_line = json.dumps({"type": "result", "session_id": "sess_mcp", "subtype": "success"})

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([permission_line, result_line])
            return captured_proc

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="qa",
            timeout_seconds=30,
            spawn_process=fake_spawn,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert session.exec(select(Approval).where(Approval.run_id == run.id)).first() is None
        assert captured_proc is not None
        control_writes = []
        for raw in captured_proc.stdin.writes:
            for line in raw.splitlines():
                if line.strip().startswith("{"):
                    control_writes.append(json.loads(line))
        allow_response = next(
            item for item in control_writes if item.get("type") == "control_response"
        )
        assert allow_response["response"]["response"]["updatedInput"] == {
            "ticket_id": ticket.id,
        }


def test_permission_bridge_auto_approves_via_agent_run_flag(tmp_path):
    """A manually-started single-stage run (no orchestration_run_id) still auto-approves
    when AgentRun.auto_approve is set directly, not just when it belongs to an
    OrchestrationRun with auto_approve=True."""
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, RunStatus, Ticket
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_manual_auto",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            orchestration_run_id=None,
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
        prompt_file.write_text("qa", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_manual_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "tool_input": {"command": "npm test"},
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_manual", "subtype": "success"}
        )

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([permission_line, result_line])
            return captured_proc

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="qa",
            timeout_seconds=30,
            spawn_process=fake_spawn,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert session.exec(select(Approval).where(Approval.run_id == run.id)).first() is None
        assert captured_proc is not None
        control_writes = []
        for raw in captured_proc.stdin.writes:
            for line in raw.splitlines():
                if line.strip().startswith("{"):
                    control_writes.append(json.loads(line))
        allow_response = next(
            item for item in control_writes if item.get("type") == "control_response"
        )
        assert allow_response["response"]["response"]["updatedInput"] == {
            "command": "npm test",
        }


def test_permission_bridge_finishes_on_result_when_process_stays_alive(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, RunStatus, Ticket
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_hung",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="run_tests",
            stage_key="testing",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("review code", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        result_line = json.dumps(
            {"type": "result", "session_id": "sess_done", "subtype": "success"}
        )

        class HungAfterResultProc:
            returncode = None
            killed = False

            def __init__(self):
                self.stdout = _FakeStdout([result_line])
                self.stdin = type(
                    "In",
                    (),
                    {
                        "write": lambda *a, **k: None,
                        "flush": lambda *a, **k: None,
                        "close": lambda *a, **k: None,
                    },
                )()
                self.stderr = None

            def poll(self):
                return None if not self.killed else 0

            def wait(self, timeout=None):
                return 0 if self.killed else None

            def kill(self):
                self.killed = True
                self.returncode = 0

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="review code",
            timeout_seconds=30,
            spawn_process=lambda *a, **k: HungAfterResultProc(),
        )

        assert result.status == RunStatus.SUCCEEDED
        assert "result" in result.stdout


def test_permission_bridge_question_returns_answers(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, ApprovalKind, RunStatus, Ticket
    from loregarden.services.orchestration import ApprovalService
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_question_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("do work", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        question_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "q_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "AskUserQuestion",
                    "tool_input": {
                        "questions": [
                            {
                                "question": "Which test runner?",
                                "header": "Runner",
                                "options": [
                                    {"label": "pytest", "description": "Python tests"},
                                    {"label": "npm test", "description": "Frontend tests"},
                                ],
                                "multiSelect": False,
                            }
                        ]
                    },
                },
            }
        )
        result_line = json.dumps({"type": "result", "session_id": "sess_q", "subtype": "success"})

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([question_line, result_line])
            return captured_proc

        def fake_wait(approval_id, **kwargs):
            ApprovalService(session).resolve(
                approval_id,
                approved=True,
                answers={"Which test runner?": "pytest"},
            )
            approval = session.get(Approval, approval_id)
            return ApprovalResolution(
                approved=True,
                updated_input=json.loads(approval.response_json)["updated_input"],
            )

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="do work",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert captured_proc is not None
        approval = session.exec(select(Approval).where(Approval.run_id == run.id)).first()
        assert approval.kind == ApprovalKind.CLI_QUESTION
        assert captured_proc.stdin.writes
        response = json.loads(captured_proc.stdin.writes[1].strip())
        updated = response["response"]["response"]["updatedInput"]
        assert updated["answers"]["Which test runner?"] == "pytest"


def test_permission_bridge_agent_timeout(tmp_path):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, RunStatus, Ticket
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_timeout_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("do work", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        class HungStdout:
            def readline(self):
                return ""

        class HungProc:
            returncode = None

            def __init__(self):
                self.stdout = HungStdout()
                self.stdin = type(
                    "In", (), {"write": lambda *a, **k: None, "flush": lambda *a, **k: None}
                )()
                self.stderr = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                import subprocess

                raise subprocess.TimeoutExpired(["claude"], timeout or 0)

            def kill(self):
                self.returncode = -9

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="do work",
            timeout_seconds=2,
            spawn_process=lambda *a, **k: HungProc(),
        )

        assert result.status == RunStatus.FAILED
        assert result.stderr == "Agent timed out after 2s"


def test_permission_bridge_triage_question_does_not_mutate_stage(tmp_path):
    """track_workflow_stage=False must not touch the ticket's active workflow
    stage — a triage turn is a side channel, not the active stage."""
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, ApprovalKind, RunStatus, Ticket
    from loregarden.services.orchestration import ApprovalService
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
        stage_key_before = ticket.workflow_stage_key
        stage_status_before = ticket.workflow_stage_status

        run = AgentRun(
            run_code="run_triage_question_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="triage",
            stage_key="triage",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("triage prompt", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        question_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "q_triage",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "AskUserQuestion",
                    "tool_input": {
                        "questions": [
                            {
                                "question": "Which behavior did you mean?",
                                "header": "Clarify",
                                "options": [
                                    {"label": "A", "description": "First"},
                                    {"label": "B", "description": "Second"},
                                ],
                                "multiSelect": False,
                            }
                        ]
                    },
                },
            }
        )
        result_line = json.dumps({"type": "result", "session_id": "sess_triage", "subtype": "success"})

        def fake_spawn(*args, **kwargs):
            return _FakeProc([question_line, result_line])

        def fake_wait(approval_id, **kwargs):
            ApprovalService(session).resolve(
                approval_id, approved=True, answers={"Which behavior did you mean?": "A"}
            )
            approval = session.get(Approval, approval_id)
            return ApprovalResolution(
                approved=True,
                updated_input=json.loads(approval.response_json)["updated_input"],
            )

        bridge = PermissionBridgeRunner(session, track_workflow_stage=False)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="triage prompt",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.SUCCEEDED
        session.refresh(ticket)
        assert ticket.workflow_stage_key == stage_key_before
        assert ticket.workflow_stage_status == stage_status_before

        approval = session.exec(select(Approval).where(Approval.run_id == run.id)).first()
        assert approval.kind == ApprovalKind.CLI_QUESTION
        assert approval.stage_key == "triage"


def test_permission_bridge_triage_read_only_mcp_tool_auto_approved(tmp_path):
    """A triage turn calling an auto-approved read-only MCP tool completes
    without creating any Approval row (and without touching stage status)."""
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.models.domain import AgentRun, Approval, RunStatus, Ticket
    from loregarden.services.seed import seed_database
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlmodel.pool import StaticPool

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
            run_code="run_triage_auto_approve_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="triage",
            stage_key="triage",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("triage prompt", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        tool_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "tool_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "mcp__loregarden__loregarden_get_ticket",
                    "tool_input": {"ticket_id": ticket.id},
                },
            }
        )
        result_line = json.dumps({"type": "result", "session_id": "sess_auto", "subtype": "success"})

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([tool_line, result_line])
            return captured_proc

        bridge = PermissionBridgeRunner(session, track_workflow_stage=False)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="triage prompt",
            timeout_seconds=30,
            spawn_process=fake_spawn,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert captured_proc is not None
        # The tool call should have been auto-approved (an "allow" control response
        # written to stdin) without ever creating a pending Approval row.
        assert any("allow" in write for write in captured_proc.stdin.writes)
        approvals = session.exec(select(Approval).where(Approval.run_id == run.id)).all()
        assert approvals == []
