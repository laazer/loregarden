import io
import json
import os
from types import SimpleNamespace
from unittest import mock

from loregarden.agents.cli_adapters import (
    permission_bypass_enabled,
    resolve_cli_invocation,
)
from loregarden.agents.executors import permission_bridge
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
from loregarden.services.run_errors import TIMEOUT_HARD_CAP_MULTIPLIER
from loregarden.services.subprocess_lines import SubprocessLineReader


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
    assert bare_mcp_tool_name("mcp__loregarden__loregarden_get_ticket") == "loregarden_get_ticket"
    assert is_auto_approved_mcp_tool("Bash") is False
    assert is_auto_approved_mcp_tool("mcp__other__something") is False


def test_memory_and_checkpoint_writes_are_auto_approved():
    """Bookkeeping writes must not need a human click.

    They land only in Loregarden's own stores (vault, memory graph, artifacts
    table), and agents are told to route every report through them rather than
    writing markdown into the repo — so gating them just spends the run timeout.
    """
    for tool in (
        "loregarden_append_checkpoint",
        "loregarden_append_learning",
        "loregarden_upsert_memory",
        "loregarden_create_memory_relation",
        "loregarden_upsert_blog_post",
        "loregarden_attach_artifact",
        "loregarden_search_memory",
        "loregarden_memory_status",
    ):
        assert is_auto_approved_mcp_tool(f"mcp__loregarden__{tool}") is True, tool


def test_workflow_mutating_mcp_tools_stay_gated():
    """Tools that move workflow state or write repo files keep the human gate."""
    for tool in (
        "loregarden_complete_stage",
        "loregarden_skip_stage",
        "loregarden_block_ticket",
        "loregarden_update_ticket",
        "loregarden_write_handoff",
        "loregarden_request_approval",
        "loregarden_start_orchestration",
        "loregarden_complete_orchestration",
    ):
        assert is_auto_approved_mcp_tool(f"mcp__loregarden__{tool}") is False, tool


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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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


def proc_factory(proc):
    """A `spawn_process` that hands back an already-built fake."""

    def spawn(*_args, **_kwargs):
        return proc

    return spawn


class _FakeClock:
    """A clock the test moves, so a timing assertion states intent not luck."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ScriptedProc:
    """A process whose output the test writes, over a real pipe.

    Real pipe rather than a fake reader: `SubprocessLineReader` does its own
    framing and select, and faking that would test the fake. The bytes are real;
    only their *timing* is the test's to decide.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self._writer = os.fdopen(write_fd, "wb", buffering=0)
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO()
        self._chunks = list(chunks)
        self.returncode = None
        self.killed = False

    def emit_next(self) -> bool:
        """Write one queued chunk. False when there are none left."""
        if not self._chunks:
            return False
        self._writer.write(self._chunks.pop(0))
        self._writer.flush()
        return True

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self._writer.close()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):  # noqa: ARG002 - signature parity with Popen
        return self.returncode or 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_streaming_output_extends_the_idle_timeout(tmp_path):
    """Output resets the idle deadline, so a run that keeps talking is not killed
    for being slow.

    Driven rather than raced. This test used to launch a real subprocess printing
    every 0.2s against a 1s idle budget and assert the run survived — which
    encoded "the parent observes output within a second of it being written".
    True on an idle machine; under `-n auto` it failed twice and once took 118s
    against 19s idle (lg-workflow-integrity-654).

    Driving the clock makes it strictly stronger: the virtual run now outlives
    its idle budget four times over, which the wall-clock version could not have
    asserted without taking four seconds to do it.
    """
    from loregarden.models.domain import AgentRun, RunStatus, Ticket
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        run = AgentRun(
            run_code="run_streaming_idle_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        chunks = [json.dumps({"type": "message", "content": i}).encode() + b"\n" for i in range(8)]
        chunks.append(
            json.dumps({"type": "result", "is_error": False, "session_id": "s"}).encode() + b"\n"
        )
        proc = _ScriptedProc(chunks)

        IDLE_BUDGET = 1
        # A quarter of the budget between writes: the deadline must be reset by
        # the output itself, while nine of them stay inside the hard cap at
        # `IDLE_BUDGET * TIMEOUT_HARD_CAP_MULTIPLIER`. Driving the clock is what
        # made that ceiling visible — the first version of this test advanced
        # 4.5s against a 4s cap and was correctly killed, which the wall-clock
        # version could never have shown.
        STEP = IDLE_BUDGET / 4
        clock = _FakeClock(10_000.0)

        _real_readline = SubprocessLineReader.readline

        def readline_advancing(reader, timeout=0.5):  # noqa: ARG001 - parity
            """Emit the next chunk and let virtual time pass, as a real run would."""
            clock.advance(STEP)
            if not proc.emit_next():
                proc.finish()
                return None
            return _real_readline(reader, timeout=timeout)

        invocation = SimpleNamespace(
            argv=["unused"],
            cwd=str(tmp_path),
            adapter="claude",
            resume_session_id="",
            env={},
        )

        bridge = PermissionBridgeRunner(session)
        with (
            mock.patch.object(permission_bridge, "_now", clock),
            mock.patch.object(SubprocessLineReader, "readline", readline_advancing),
        ):
            result = bridge.run(
                run_id=run.id,
                ticket=ticket,
                invocation=invocation,
                prompt="do work",
                timeout_seconds=IDLE_BUDGET,
                spawn_process=proc_factory(proc),
            )

        assert result.status == RunStatus.SUCCEEDED, (
            f"stderr={result.stderr!r} killed={proc.killed} clock={clock.now}"
        )
        assert result.session_id == "s"
        assert proc.killed is False, "the idle timer killed a run that never went idle"
        # Nine reads at a quarter budget each: the run outlived its idle budget
        # twice over, purely because output kept resetting it.
        elapsed = clock.now - 10_000.0
        assert elapsed > IDLE_BUDGET * 2
        assert elapsed < IDLE_BUDGET * TIMEOUT_HARD_CAP_MULTIPLIER


def test_the_hard_cap_stops_a_run_that_never_stops_talking(tmp_path):
    """Output resets the idle deadline, but not forever.

    A run that emits just often enough to stay un-idle would otherwise run
    without bound, which is what `TIMEOUT_HARD_CAP_MULTIPLIER` exists to stop.
    Asserting it used to mean a test that really waited four times an idle
    budget; on a driven clock it costs nothing, so the ceiling is now pinned
    rather than assumed (lg-workflow-integrity-654).
    """
    from loregarden.models.domain import AgentRun, RunStatus, Ticket
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        run = AgentRun(
            run_code="run_hard_cap_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        IDLE_BUDGET = 1
        # Chatty forever: a message every quarter budget and no result event.
        chatty = [
            json.dumps({"type": "message", "content": i}).encode() + b"\n" for i in range(200)
        ]
        proc = _ScriptedProc(chatty)
        clock = _FakeClock(20_000.0)
        _real_readline = SubprocessLineReader.readline

        def readline_advancing(reader, timeout=0.5):  # noqa: ARG001 - parity
            clock.advance(IDLE_BUDGET / 4)
            if not proc.emit_next():
                proc.finish()
                return None
            return _real_readline(reader, timeout=timeout)

        invocation = SimpleNamespace(
            argv=["unused"],
            cwd=str(tmp_path),
            adapter="claude",
            resume_session_id="",
            env={},
        )
        with (
            mock.patch.object(permission_bridge, "_now", clock),
            mock.patch.object(SubprocessLineReader, "readline", readline_advancing),
        ):
            result = bridge_run = PermissionBridgeRunner(session).run(
                run_id=run.id,
                ticket=ticket,
                invocation=invocation,
                prompt="do work",
                timeout_seconds=IDLE_BUDGET,
                spawn_process=proc_factory(proc),
            )

        assert bridge_run is result
        assert result.status == RunStatus.FAILED
        assert proc.killed is True
        # Stopped at the ceiling, not at the idle budget it kept resetting.
        elapsed = clock.now - 20_000.0
        assert elapsed >= IDLE_BUDGET * TIMEOUT_HARD_CAP_MULTIPLIER


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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_triage", "subtype": "success"}
        )

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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
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
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_auto", "subtype": "success"}
        )

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


def test_permission_bridge_orchestrated_agent_denied_create_ticket_end_to_end(tmp_path):
    """Per a9-create-ticket-mcp-tool's triage decision: an orchestrated pipeline
    agent (track_workflow_stage=True, the default every stage run uses) must be
    auto-*denied* `loregarden_create_ticket` — never even reaching the human
    approval inbox. This pins the orchestrated side of the interim allowlist end
    to end, not just the pure predicate in `is_orchestrated_agent_denied_mcp_tool`."""
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()

        run = AgentRun(
            run_code="run_orchestrated_create_ticket_denied",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            stage_key="implement",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace = tmp_path / "repo"
        workspace.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("implement", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=workspace,
        )

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_create_ticket_denied",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "mcp__loregarden__loregarden_create_ticket",
                    "tool_input": {"workspace_slug": "loregarden", "title": "spawned mid-run"},
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_orch_denied", "subtype": "success"}
        )

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([permission_line, result_line])
            return captured_proc

        def fail_wait_for_approval(approval_id, **kwargs):
            raise AssertionError(
                "orchestrated create_ticket must be auto-denied, not routed to the "
                "human approval inbox"
            )

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="implement",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fail_wait_for_approval,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert captured_proc is not None
        control_writes = []
        for raw in captured_proc.stdin.writes:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            for line in text.splitlines():
                if line.strip().startswith("{"):
                    control_writes.append(json.loads(line))
        response = next(item for item in control_writes if item.get("type") == "control_response")
        assert response["response"]["response"]["behavior"] == "deny"
        assert (
            "denied to orchestrated pipeline agents" in response["response"]["response"]["message"]
        )
        approvals = session.exec(select(Approval).where(Approval.run_id == run.id)).all()
        assert approvals == []


def test_permission_bridge_interactive_triage_create_ticket_is_not_auto_denied(tmp_path):
    """Interactive chat (track_workflow_stage=False) gets full Loregarden MCP access.

    Pipeline stage runs still deny create_ticket; triage / Home / branch auto-approve
    it instead of routing to the inbox or the orchestrated deny path.
    """
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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()

        run = AgentRun(
            run_code="run_triage_create_ticket_allowed",
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
            orchestrated=False,
        )
        assert "LOREGARDEN_MCP_ORCHESTRATED" not in " ".join(invocation.argv)

        permission_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_create_ticket_interactive",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "mcp__loregarden__loregarden_create_ticket",
                    "tool_input": {
                        "workspace_slug": "loregarden",
                        "title": "created from Ticket Studio chat",
                    },
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_interactive_allowed", "subtype": "success"}
        )

        captured_proc: _FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = _FakeProc([permission_line, result_line])
            return captured_proc

        def fail_wait(approval_id, **kwargs):
            raise AssertionError(
                f"Interactive triage must auto-approve Loregarden MCP, not inbox {approval_id}"
            )

        bridge = PermissionBridgeRunner(session, track_workflow_stage=False)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="triage prompt",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fail_wait,
        )

        assert result.status == RunStatus.SUCCEEDED
        assert session.exec(select(Approval).where(Approval.run_id == run.id)).first() is None
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
        assert allow_response["response"]["response"]["behavior"] == "allow"


def test_permission_bridge_workspace_scoped_approval_has_no_ticket(tmp_path):
    """Home Baxter chat scopes approvals to a workspace, not a work item."""
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.agents.executors.permission_bridge import HOME_CHAT_STAGE_KEY
    from loregarden.models.domain import (
        AgentRun,
        Approval,
        ApprovalKind,
        RunStatus,
        Workspace,
    )
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
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()

        run = AgentRun(
            run_code="run_home_chat",
            ticket_id=None,
            workspace_id=workspace.id,
            agent_id="triage",
            stage_key=HOME_CHAT_STAGE_KEY,
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        repo = tmp_path / "repo"
        repo.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("home chat prompt", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude",
            prompt_file=prompt_file,
            workspace_root=repo,
        )

        tool_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "perm_home",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_home", "subtype": "success"}
        )

        def fake_spawn(*args, **kwargs):
            return _FakeProc([tool_line, result_line])

        def fake_wait(approval_id, **kwargs):
            ApprovalService(session).resolve(approval_id, approved=True)
            return ApprovalResolution(approved=True)

        bridge = PermissionBridgeRunner(session, track_workflow_stage=False)
        result = bridge.run(
            run_id=run.id,
            workspace=workspace,
            invocation=invocation,
            prompt="home chat prompt",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert result.status == RunStatus.SUCCEEDED
        approval = session.exec(select(Approval).where(Approval.run_id == run.id)).first()
        assert approval is not None
        assert approval.ticket_id is None
        assert approval.workspace_id == workspace.id
        assert approval.kind == ApprovalKind.CLI_PERMISSION
        assert approval.stage_key == HOME_CHAT_STAGE_KEY
        assert "Home chat" in approval.impact


def _home_chat_bridge_run(
    tmp_path, session, workspace, tool_name, tool_input, *, wait_for_approval=None
):
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.agents.executors.permission_bridge import HOME_CHAT_STAGE_KEY
    from loregarden.models.domain import AgentRun, RunStatus

    run = AgentRun(
        run_code=f"run_home_{tool_name.lower()}_{id(tool_input)}",
        ticket_id=None,
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key=HOME_CHAT_STAGE_KEY,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("home chat prompt", encoding="utf-8")
    invocation = build_interactive_invocation(
        adapter="claude",
        prompt_file=prompt_file,
        workspace_root=repo,
    )
    tool_line = json.dumps(
        {
            "type": "control_request",
            "request_id": "perm_home_cli",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
        }
    )
    result_line = json.dumps(
        {"type": "result", "session_id": "sess_home_cli", "subtype": "success"}
    )
    captured: dict[str, _FakeProc] = {}

    def fake_spawn(*_args, **_kwargs):
        captured["proc"] = _FakeProc([tool_line, result_line])
        return captured["proc"]

    kwargs = {}
    if wait_for_approval is not None:
        kwargs["wait_for_approval"] = wait_for_approval
    result = PermissionBridgeRunner(session, track_workflow_stage=False).run(
        run_id=run.id,
        workspace=workspace,
        invocation=invocation,
        prompt="home chat prompt",
        timeout_seconds=30,
        spawn_process=fake_spawn,
        **kwargs,
    )
    return run.id, result, captured["proc"]


def test_permission_bridge_home_chat_auto_approves_write(tmp_path):
    from loregarden.models.domain import Approval, RunStatus, Workspace
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
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
        run_id, result, proc = _home_chat_bridge_run(
            tmp_path,
            session,
            workspace,
            "Write",
            {"file_path": "server/foo.py", "content": "x = 1\n"},
        )
        assert result.status == RunStatus.SUCCEEDED
        writes = "".join(proc.stdin.writes)
        assert '"behavior": "allow"' in writes
        assert session.exec(select(Approval).where(Approval.run_id == run_id)).first() is None


def test_permission_bridge_home_chat_auto_approves_git_commit(tmp_path):
    from loregarden.models.domain import Approval, RunStatus, Workspace
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
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
        run_id, result, proc = _home_chat_bridge_run(
            tmp_path,
            session,
            workspace,
            "Bash",
            {"command": "git add -A && git commit -m 'home chat fix'"},
        )
        assert result.status == RunStatus.SUCCEEDED
        writes = "".join(proc.stdin.writes)
        assert '"behavior": "allow"' in writes
        assert session.exec(select(Approval).where(Approval.run_id == run_id)).first() is None


def test_permission_bridge_home_chat_gates_force_push(tmp_path):
    from loregarden.models.domain import Approval, RunStatus, Workspace
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
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()

        def fake_wait(approval_id, **_kwargs):
            ApprovalService(session).resolve(approval_id, approved=True)
            return ApprovalResolution(approved=True)

        run_id, result, _proc = _home_chat_bridge_run(
            tmp_path,
            session,
            workspace,
            "Bash",
            {"command": "git push --force origin HEAD"},
            wait_for_approval=fake_wait,
        )
        assert result.status == RunStatus.SUCCEEDED
        approval = session.exec(select(Approval).where(Approval.run_id == run_id)).first()
        assert approval is not None
        assert approval.tool_name == "Bash"
