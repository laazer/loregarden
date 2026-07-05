from fastapi.testclient import TestClient
from sqlmodel import Session

from loregarden.agents.cli_adapters import build_triage_invocation
from loregarden.models.domain import Ticket, TriageMessage
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.triage_service import build_triage_prompt, resolve_triage_timeout


def test_resolve_triage_timeout_defaults_to_settings():
    assert resolve_triage_timeout({}) == 300
    assert resolve_triage_timeout({"timeout": 180}) == 180


def test_resolve_triage_timeout_env_override(monkeypatch):
    monkeypatch.setenv("LOREGARDEN_TRIAGE_TIMEOUT", "600")
    assert resolve_triage_timeout({"timeout": 120}) == 600


def test_build_triage_prompt_omits_full_mcp_module(client: TestClient):
    from loregarden.db.session import engine

    ticket_id = client.get("/api/tickets").json()[0]["id"]
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        assert ticket is not None
        prompt = build_triage_prompt(
            ticket,
            [TriageMessage(ticket_id=ticket.id, role="user", content="prior question")],
            "latest question",
            session=session,
        )
    assert "## Loregarden MCP module" not in prompt
    assert "## Loregarden MCP reference" in prompt
    assert "latest question" in prompt


def test_extract_triage_reply_plain_text():
    assert extract_triage_reply("Hello operator") == "Hello operator"


def test_extract_triage_reply_stream_json():
    stdout = "\n".join(
        [
            '{"type":"assistant","message":{"content":[{"type":"text","text":"First part"}]}}',
            '{"type":"result","result":"Final answer"}',
        ]
    )
    assert extract_triage_reply(stdout) == "Final answer"


def test_extract_triage_reply_stream_json_without_result():
    stdout = '{"type":"assistant","message":{"content":[{"type":"text","text":"Only assistant"}]}}'
    assert extract_triage_reply(stdout) == "Only assistant"


def test_build_triage_invocation_uses_print_mode_not_stream_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("context", encoding="utf-8")
    inv = build_triage_invocation(
        agent_id="triage",
        adapter="claude",
        prompt="context",
        prompt_file=prompt_file,
        skill_name="",
        workspace_root=tmp_path,
        workspace=None,
    )
    assert inv.interactive is False
    assert "stream-json" not in inv.argv
    assert "text" in inv.argv
    assert "--add-dir" not in inv.argv
    assert "haiku" in inv.argv
    assert "bypassPermissions" in inv.argv
