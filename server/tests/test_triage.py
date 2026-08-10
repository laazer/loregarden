from fastapi.testclient import TestClient
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TriageMessage, Workspace
from sqlmodel import Session, select


def _ticket_id(client: TestClient, *, external_id: str | None = None) -> str:
    tickets = client.get("/api/tickets").json()
    if external_id:
        match = next((t for t in tickets if t["external_id"] == external_id), None)
        if match:
            return match["id"]
    return tickets[0]["id"]


def test_triage_snapshot_empty(client: TestClient):
    ticket_id = _ticket_id(client, external_id="01-bootstrap-fastapi-control-plane")
    res = client.get(f"/api/tickets/{ticket_id}/triage")
    assert res.status_code == 200
    body = res.json()
    assert body["pending_approvals"] == []
    assert body["messages"] == []
    assert body["runtime"]["cli_adapter"] == "default"


def test_triage_runtime_persists(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "lmstudio",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "http://127.0.0.1:1234/v1",
            "lmstudio_model": "test-model",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cli_adapter"] == "lmstudio"
    assert body["lmstudio_model"] == "test-model"

    snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assert snapshot["runtime"]["cli_adapter"] == "lmstudio"
    assert snapshot["runtime"]["lmstudio_model"] == "test-model"


def test_triage_runtime_rejects_invalid_adapter(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "not-valid",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    assert res.status_code == 400


def test_triage_invoke_uses_runtime_override(client: TestClient, monkeypatch):
    from loregarden.db.session import engine
    from loregarden.models.domain import Ticket
    from loregarden.services import cli_agent_runner
    from loregarden.services.triage_service import apply_triage_runtime_overrides

    ticket_id = _ticket_id(client)
    client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "lmstudio",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "http://127.0.0.1:9999/v1",
            "lmstudio_model": "override-model",
        },
    )

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        workspace = session.get(Workspace, ticket.workspace_id)
        effective = apply_triage_runtime_overrides(workspace, ticket)
        assert effective.cli_adapter == "lmstudio"
        assert effective.lmstudio_model == "override-model"
        assert effective.lmstudio_base_url == "http://127.0.0.1:9999/v1"

    captured: dict = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        from loregarden.agents.cli_adapters import _local_invocation

        return _local_invocation(
            agent_id=kwargs["agent_id"],
            skill_name=kwargs["skill_name"],
            prompt_file=kwargs["prompt_file"],
        )

    class FakeStdout:
        """The pipe a triage turn is now read from line by line."""

        def __init__(self, text: str) -> None:
            self._lines = text.splitlines(keepends=True)

        def readline(self) -> str:
            return self._lines.pop(0) if self._lines else ""

    class FakeProc:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = FakeStdout("runtime override ok")
            self.stderr = None

        def poll(self):
            return 0 if not self.stdout._lines else None

        def communicate(self, timeout=None):
            rest = "".join(self.stdout._lines)
            self.stdout._lines = []
            return (rest.encode("utf-8"), b"")

        def kill(self):
            return None

    monkeypatch.setattr(cli_agent_runner, "build_triage_invocation", fake_resolve)
    monkeypatch.setattr(cli_agent_runner.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setenv("LOREGARDEN_LMSTUDIO_STUB_RESPONSE", "runtime override ok")

    res = client.post(
        f"/api/tickets/{ticket_id}/triage/messages",
        json={"content": "Which model?"},
    )
    assert res.status_code == 202
    assert captured["workspace"].cli_adapter == "lmstudio"
    assert captured["workspace"].lmstudio_model == "override-model"

    snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assert snapshot["run_status"] == "idle"
    assert "runtime override ok" in snapshot["messages"][-1]["content"]


def test_triage_chat_stub(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Use pytest for backend tests.")
    ticket_id = _ticket_id(client)
    res = client.post(
        f"/api/tickets/{ticket_id}/triage/messages",
        json={"content": "What should I do next?"},
    )
    assert res.status_code == 202
    payload = res.json()
    assert payload["user_message"]["content"] == "What should I do next?"
    assert payload["status"] == "queued"

    snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assert len(snapshot["messages"]) == 2
    assert snapshot["run_status"] == "idle"
    assert "pytest" in snapshot["messages"][-1]["content"].lower()


def test_triage_messages_persist(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Acknowledged.")
    ticket_id = _ticket_id(client)
    client.post(f"/api/tickets/{ticket_id}/triage/messages", json={"content": "Hello triage"})

    from loregarden.db.session import engine

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        messages = session.exec(
            select(TriageMessage).where(TriageMessage.ticket_id == ticket.id)
        ).all()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"


def test_inbox_filter_by_ticket(client: TestClient):
    ticket_id = _ticket_id(client)
    res = client.get(f"/api/inbox/approvals?ticket_id={ticket_id}")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_triage_includes_workflow_gate_and_cli_approvals(client: TestClient):
    from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Ticket

    ticket_id = _ticket_id(client)

    from loregarden.db.session import engine

    with Session(engine) as session:
        db_ticket = session.get(Ticket, ticket_id)
        assert db_ticket
        session.add(
            Approval(
                ticket_id=ticket_id,
                workspace_id=db_ticket.workspace_id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Approve stage completion",
                stage_key="approval",
                impact="Human sign-off required",
                status=ApprovalStatus.PENDING,
            )
        )
        session.add(
            Approval(
                ticket_id=ticket_id,
                workspace_id=db_ticket.workspace_id,
                kind=ApprovalKind.CLI_PERMISSION,
                title="Allow Bash?",
                stage_key=db_ticket.workflow_stage_key,
                impact="Agent requested Bash",
                tool_name="Bash",
                status=ApprovalStatus.PENDING,
            )
        )
        session.commit()

    body = client.get(f"/api/tickets/{ticket_id}/triage").json()
    kinds = {item["kind"] for item in body["pending_approvals"]}
    assert "workflow_gate" in kinds
    assert "cli_permission" in kinds


def test_triage_includes_child_ticket_approvals(client: TestClient):
    from loregarden.db.session import engine
    from loregarden.models.domain import (
        Approval,
        ApprovalKind,
        ApprovalStatus,
        Ticket,
        WorkItemType,
    )

    with Session(engine) as session:
        parent = session.exec(select(Ticket).limit(1)).first()
        assert parent
        child = Ticket(
            external_id="triage-child-task",
            title="Child task for triage rollup",
            workspace_id=parent.workspace_id,
            parent_ticket_id=parent.id,
            work_item_type=WorkItemType.TASK,
        )
        session.add(child)
        session.commit()
        session.refresh(child)
        session.add(
            Approval(
                ticket_id=child.id,
                workspace_id=parent.workspace_id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Child stage sign-off",
                stage_key="approval",
                impact="Child needs approval",
                status=ApprovalStatus.PENDING,
            )
        )
        session.commit()
        parent_id = parent.id

    body = client.get(f"/api/tickets/{parent_id}/triage").json()
    titles = [item["title"] for item in body["pending_approvals"]]
    assert "Child stage sign-off" in titles


def _seed_active_run(
    client: TestClient, ticket_id: str, *, agent_id: str, status: RunStatus
) -> None:
    from loregarden.db.session import engine

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        session.add(
            AgentRun(
                run_code="run_active_test",
                ticket_id=ticket_id,
                workspace_id=ticket.workspace_id,
                agent_id=agent_id,
                stage_key=ticket.workflow_stage_key or "triage",
                status=status,
            )
        )
        session.commit()


def test_triage_concurrency_guard_rejects_overlapping_stage_run(client: TestClient):
    ticket_id = _ticket_id(client)
    _seed_active_run(client, ticket_id, agent_id="planner", status=RunStatus.RUNNING)

    res = client.post(f"/api/tickets/{ticket_id}/triage/messages", json={"content": "hello"})
    assert res.status_code == 409


def test_triage_concurrency_guard_rejects_overlapping_triage_run(client: TestClient):
    ticket_id = _ticket_id(client)
    _seed_active_run(client, ticket_id, agent_id="triage", status=RunStatus.AWAITING_PERMISSION)

    res = client.post(f"/api/tickets/{ticket_id}/triage/messages", json={"content": "hello"})
    assert res.status_code == 409


def test_orchestration_start_run_rejects_while_triage_active(client: TestClient, db_session):
    from loregarden.services.orchestration import OrchestrationService

    ticket_id = _ticket_id(client, external_id="01-bootstrap-fastapi-control-plane")
    _seed_active_run(client, ticket_id, agent_id="triage", status=RunStatus.RUNNING)

    ticket = db_session.get(Ticket, ticket_id)
    try:
        OrchestrationService(db_session).start_run(ticket)
        raised = False
    except ValueError as exc:
        raised = True
        assert "triage" in str(exc).lower()
    assert raised


def test_triage_run_excluded_from_list_runs(client: TestClient, db_session):
    from loregarden.services.run_service import RunService

    ticket_id = _ticket_id(client)
    _seed_active_run(client, ticket_id, agent_id="triage", status=RunStatus.RUNNING)

    svc = RunService(db_session)
    assert svc.list_runs(ticket_id=ticket_id) == []
    included = svc.list_runs(ticket_id=ticket_id, include_triage=True)
    assert len(included) == 1
    assert included[0].agent_id == "triage"


def test_start_triage_run_carries_auto_approve_flag(client: TestClient, db_session: Session):
    from datetime import datetime, timezone

    from loregarden.services.triage_run_service import start_triage_run

    ticket_id = _ticket_id(client)
    ticket = db_session.get(Ticket, ticket_id)
    assert ticket is not None

    _, run = start_triage_run(db_session, ticket, "fix the jump height", auto_approve=True)
    assert run.auto_approve is True

    run.status = RunStatus.SUCCEEDED
    run.finished_at = datetime.now(timezone.utc)
    db_session.add(run)
    db_session.commit()

    _, run_default = start_triage_run(db_session, ticket, "now check the landing")
    assert run_default.auto_approve is False


def test_triage_execute_intent_for_codex_uses_writable_oneshot(
    client: TestClient, db_session: Session, monkeypatch
):
    """Ticket triage must not hard-gate execute on adapter name == claude."""
    from loregarden.services import agent_turn_runner, triage_run_service
    from loregarden.services.agent_turn_runner import AgentTurnResult
    from loregarden.services.triage_run_service import TriageTurnExecutor, start_triage_run

    captured: dict[str, object] = {}

    def fake_turn(request):
        captured["intent"] = request.intent
        captured["adapter"] = request.adapter
        captured["prompt"] = request.prompt
        captured["ticket_id"] = request.ticket.id if request.ticket else None
        return AgentTurnResult(
            reply="updated the ticket via MCP",
            strategy="writable_oneshot",
            adapter="codex",
            run_id=request.run_id,
        )

    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(triage_run_service, "resolve_chat_adapter", lambda **_: "codex")
    monkeypatch.setattr(agent_turn_runner, "run_agent_turn", fake_turn)
    monkeypatch.setattr(triage_run_service, "run_agent_turn", fake_turn)

    ticket_id = _ticket_id(client)
    ticket = db_session.get(Ticket, ticket_id)
    assert ticket is not None
    _, run = start_triage_run(db_session, ticket, "update the ticket via MCP")
    TriageTurnExecutor(db_session).execute(run, ticket)

    assert captured["adapter"] == "codex"
    assert captured["intent"] == "execute"
    assert captured["ticket_id"] == ticket_id
    prompt = str(captured["prompt"])
    assert "real tool access" in prompt
    assert "advisory only" not in prompt
    assert "callable directly" in prompt


def test_triage_message_endpoint_accepts_auto_approve(
    client: TestClient, db_session: Session, monkeypatch
):
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "ok")
    ticket_id = _ticket_id(client)
    res = client.post(
        f"/api/tickets/{ticket_id}/triage/messages",
        json={"content": "auto approve please", "auto_approve": True},
    )
    assert res.status_code == 202
    run = db_session.get(AgentRun, res.json()["run_id"])
    assert run is not None
    assert run.auto_approve is True


def test_triage_async_send_returns_immediately_reply_via_poll(client: TestClient, monkeypatch):
    import time

    monkeypatch.delenv("LOREGARDEN_SYNC_RUNS", raising=False)
    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Async reply from Baxter.")

    ticket_id = _ticket_id(client)
    res = client.post(f"/api/tickets/{ticket_id}/triage/messages", json={"content": "async please"})
    assert res.status_code == 202
    payload = res.json()
    assert "assistant_message" not in payload
    assert payload["status"] == "queued"

    deadline = time.time() + 10
    snapshot = None
    while time.time() < deadline:
        snapshot = client.get(f"/api/tickets/{ticket_id}/triage").json()
        if snapshot["run_status"] == "idle" and len(snapshot["messages"]) == 2:
            break
        time.sleep(0.1)

    assert snapshot is not None
    assert snapshot["run_status"] == "idle"
    assert len(snapshot["messages"]) == 2
    assert "Async reply from Baxter." in snapshot["messages"][-1]["content"]


def test_triage_ignores_workspace_pipeline_adapter(
    client: TestClient, db_session: Session, monkeypatch
):
    """A chat rail keeps the permission bridge even on a codex workspace.

    The workspace `cli_adapter` is a choice about unattended pipeline runs. When
    it reached ticket triage too, the rail silently lost approvals, streamed
    thinking and steering, and could only answer.
    """
    from loregarden.services import agent_turn_runner, triage_run_service
    from loregarden.services.agent_turn_runner import AgentTurnResult
    from loregarden.services.triage_run_service import TriageTurnExecutor, start_triage_run

    captured: dict[str, object] = {}

    def fake_turn(request):
        captured["intent"] = request.intent
        captured["adapter"] = request.adapter
        captured["prompt"] = request.prompt
        return AgentTurnResult(
            reply="done", strategy="permission_bridge", adapter="claude", run_id=request.run_id
        )

    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)
    monkeypatch.setattr(agent_turn_runner, "run_agent_turn", fake_turn)
    monkeypatch.setattr(triage_run_service, "run_agent_turn", fake_turn)

    ticket_id = _ticket_id(client)
    ticket = db_session.get(Ticket, ticket_id)
    assert ticket is not None
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    workspace.cli_adapter = "codex"
    db_session.add(workspace)
    db_session.commit()

    _, run = start_triage_run(db_session, ticket, "why is this blocked?")
    TriageTurnExecutor(db_session).execute(run, ticket)

    assert captured["adapter"] == "claude"
    assert captured["intent"] == "execute"


def test_triage_honours_explicit_per_ticket_adapter_override(
    client: TestClient, db_session: Session, monkeypatch
):
    """Ignoring the workspace adapter must not ignore a deliberate override."""
    from loregarden.services import agent_turn_runner, triage_run_service
    from loregarden.services.agent_turn_runner import AgentTurnResult
    from loregarden.services.triage_run_service import TriageTurnExecutor, start_triage_run

    captured: dict[str, object] = {}

    def fake_turn(request):
        captured["adapter"] = request.adapter
        return AgentTurnResult(
            reply="done", strategy="writable_oneshot", adapter="codex", run_id=request.run_id
        )

    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)
    monkeypatch.setattr(agent_turn_runner, "run_agent_turn", fake_turn)
    monkeypatch.setattr(triage_run_service, "run_agent_turn", fake_turn)

    ticket_id = _ticket_id(client)
    res = client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "codex",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    assert res.status_code == 200

    ticket = db_session.get(Ticket, ticket_id)
    assert ticket is not None
    db_session.refresh(ticket)
    _, run = start_triage_run(db_session, ticket, "go")
    TriageTurnExecutor(db_session).execute(run, ticket)

    assert captured["adapter"] == "codex"


def test_triage_snapshot_publishes_capability(client: TestClient, monkeypatch):
    """The panel can tell an advisory rail from an executing one before asking."""
    # conftest pins every test run to the `local` adapter; this is about what the
    # panel reports for a real one.
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)
    ticket_id = _ticket_id(client)
    body = client.get(f"/api/tickets/{ticket_id}/triage").json()

    assert body["chat_intent"] == "execute"
    assert body["adapter_capabilities"]["adapter"] == "claude"
    assert body["adapter_capabilities"]["permission_bridge"] is True

    res = client.patch(
        f"/api/tickets/{ticket_id}/triage/runtime",
        json={
            "cli_adapter": "lmstudio",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
        },
    )
    assert res.status_code == 200
    body = client.get(f"/api/tickets/{ticket_id}/triage").json()
    assert body["adapter_capabilities"]["adapter"] == "lmstudio"
    assert body["adapter_capabilities"]["permission_bridge"] is False


def test_triage_snapshot_reports_advisory_for_a_toolless_adapter(client: TestClient, monkeypatch):
    """An adapter with neither execution path must show as advisory, not execute."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "local")
    body = client.get(f"/api/tickets/{_ticket_id(client)}/triage").json()

    assert body["chat_intent"] == "advisory"
    assert body["adapter_capabilities"]["permission_bridge"] is False
    assert body["adapter_capabilities"]["plan_execute"] is False


def test_advisory_triage_prompt_forbids_narrating_tool_work(
    client: TestClient, db_session: Session
):
    """An advisory one-shot must not open with "I'll check X, then I'll do Y".

    That reply is the whole turn — nothing runs after it — so an announced plan
    is a promise the channel can never keep.
    """
    from loregarden.services.triage_service import build_triage_prompt

    ticket = db_session.get(Ticket, _ticket_id(client))
    assert ticket is not None
    prompt = build_triage_prompt(
        ticket,
        [],
        "fix the gate",
        session=db_session,
        interactive=False,
        advisory_reason="The selected lmstudio adapter cannot execute turns.",
    )

    assert "Do not announce work you are about to do" in prompt
    assert "The selected lmstudio adapter cannot execute turns." in prompt
    assert "real tool access" not in prompt
