import json

from loregarden.agents.executors.permission_bridge import (
    ApprovalResolution,
    PermissionBridgeRunner,
)
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.orchestration import ApprovalService
from loregarden.services.permission_allowlist import (
    add_ticket_allow_rule,
    add_workspace_allow_rule,
    is_permission_allowed,
    is_workspace_allowed,
    permission_rule_matches,
)
from loregarden.services.seed import seed_database
from sqlmodel import Session, select


def test_permission_rule_matches_exact_tool_input():
    rule = {"tool_name": "Bash", "tool_input": {"command": "npm test"}}
    assert permission_rule_matches(rule, "Bash", {"command": "npm test"}) is True
    assert permission_rule_matches(rule, "Bash", {"command": "npm run lint"}) is False
    assert permission_rule_matches(rule, "Write", {"command": "npm test"}) is False


def test_add_workspace_allow_rule_deduplicates(isolated_db):
    import uuid

    with Session(isolated_db) as session:
        seed_database(session)
        workspace = session.exec(select(Workspace).limit(1)).first()
        assert workspace is not None
        command = f"npm test --allowlist-dedupe-{uuid.uuid4().hex[:8]}"
        add_workspace_allow_rule(
            session,
            workspace.id,
            "Bash",
            {"command": command},
        )
        added_again = add_workspace_allow_rule(
            session,
            workspace.id,
            "Bash",
            {"command": command},
        )
        assert added_again is False
        stored = json.loads(
            session.get(Workspace, workspace.id).permission_allowlist_json  # type: ignore[union-attr]
        )
        matching = [
            rule for rule in stored if permission_rule_matches(rule, "Bash", {"command": command})
        ]
        assert len(matching) == 1


def test_resolve_cli_permission_with_always_allow(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket).limit(1)).first()
        assert ticket is not None
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.CLI_PERMISSION,
            title="Allow Bash?",
            stage_key="testing",
            tool_name="Bash",
            tool_input_json='{"command":"npm test"}',
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        session.commit()
        session.refresh(approval)
        approval_id = approval.id
        workspace_id = ticket.workspace_id

        ApprovalService(session).resolve(
            approval_id,
            approved=True,
            always_allow=True,
        )

        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        assert is_workspace_allowed(session, workspace_id, "Bash", {"command": "npm test"}) is True


def test_permission_bridge_auto_approves_workspace_allowlist(tmp_path, isolated_db):
    from loregarden.agents.cli_adapters import build_interactive_invocation

    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket is not None
        add_workspace_allow_rule(
            session,
            ticket.workspace_id,
            "Bash",
            {"command": "npm test"},
        )
        run = AgentRun(
            run_code="run_allowlist",
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
                "request_id": "perm_allowlist_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "tool_input": {"command": "npm test"},
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_allowlist", "subtype": "success"}
        )

        class FakeStdout:
            def __init__(self, lines):
                self.lines = list(lines)
                self._closed = False

            def readline(self):
                if self.lines:
                    return self.lines.pop(0) + "\n"
                self._closed = True
                return ""

        class FakeStdin:
            def __init__(self):
                self.writes: list[str] = []

            def write(self, data):
                self.writes.append(data.decode("utf-8") if isinstance(data, bytes) else data)

            def flush(self):
                return None

        class FakeProc:
            returncode = 0

            def __init__(self):
                self.stdout = FakeStdout([permission_line, result_line])
                self.stdin = FakeStdin()

            def poll(self):
                return 0 if self.stdout._closed else None

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.returncode = 1

        captured_proc: FakeProc | None = None

        def fake_spawn(*args, **kwargs):
            nonlocal captured_proc
            captured_proc = FakeProc()
            return captured_proc

        bridge = PermissionBridgeRunner(session)
        result = bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="run tests",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=lambda approval_id, **kwargs: ApprovalResolution(approved=True),
        )

        assert result.status == RunStatus.SUCCEEDED
        approval = session.exec(select(Approval).where(Approval.run_id == run.id)).first()
        assert approval is None
        assert captured_proc is not None
        control_writes = []
        for raw in captured_proc.stdin.writes:
            for line in raw.splitlines():
                if line.strip().startswith("{"):
                    control_writes.append(json.loads(line))
        allow_response = next(
            item for item in control_writes if item.get("type") == "control_response"
        )
        assert allow_response["response"]["response"]["updatedInput"] == {"command": "npm test"}


def test_resolve_cli_permission_with_ticket_and_stage_allow(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket).limit(1)).first()
        assert ticket is not None
        ticket.workflow_stage_key = "testing"
        session.add(ticket)
        session.commit()

        ticket_rule = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.CLI_PERMISSION,
            title="Allow Bash for ticket?",
            stage_key="testing",
            tool_name="Bash",
            tool_input_json='{"command":"npm run ticket-scope"}',
            status=ApprovalStatus.PENDING,
        )
        stage_rule = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.CLI_PERMISSION,
            title="Allow Bash for stage?",
            stage_key="testing",
            tool_name="Bash",
            tool_input_json='{"command":"npm run stage-scope"}',
            status=ApprovalStatus.PENDING,
        )
        session.add(ticket_rule)
        session.add(stage_rule)
        session.commit()
        session.refresh(ticket_rule)
        session.refresh(stage_rule)

        ApprovalService(session).resolve(
            ticket_rule.id,
            approved=True,
            allow_for_ticket=True,
        )
        ApprovalService(session).resolve(
            stage_rule.id,
            approved=True,
            allow_for_stage=True,
        )

        assert (
            is_permission_allowed(
                session,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                stage_key="testing",
                tool_name="Bash",
                tool_input={"command": "npm run ticket-scope"},
            )
            == "ticket"
        )
        assert (
            is_permission_allowed(
                session,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                stage_key="testing",
                tool_name="Bash",
                tool_input={"command": "npm run stage-scope"},
            )
            == "stage"
        )
        assert (
            is_permission_allowed(
                session,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                stage_key="planning",
                tool_name="Bash",
                tool_input={"command": "npm run stage-scope"},
            )
            is None
        )


def test_stage_allow_rule_does_not_apply_to_other_stages(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket).limit(1)).first()
        assert ticket is not None
        add_ticket_allow_rule(
            session,
            ticket.id,
            "Bash",
            {"command": "npm run stage-only"},
            stage_key="testing",
        )
        assert (
            is_permission_allowed(
                session,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                stage_key="testing",
                tool_name="Bash",
                tool_input={"command": "npm run stage-only"},
            )
            == "stage"
        )
        assert (
            is_permission_allowed(
                session,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                stage_key="planning",
                tool_name="Bash",
                tool_input={"command": "npm run stage-only"},
            )
            is None
        )
