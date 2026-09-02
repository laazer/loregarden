"""LM Studio speaks MCP through the runner's tool loop (Q2 Path B)."""

import json

import httpx
from loregarden.agents.executors.lmstudio_runner import McpBridge, run_chat

MCP_URL = "http://mcp.test/mcp"
LM_URL = "http://lm.test/v1"


class FakeTransport(httpx.BaseTransport):
    """Stands in for both LM Studio and the Loregarden MCP endpoint."""

    def __init__(self, model_turns: list[dict], tools: list[dict] | None = None):
        self.model_turns = list(model_turns)
        self.tools = (
            tools
            if tools is not None
            else [
                {
                    "name": "loregarden_get_ticket",
                    "description": "Read a ticket",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "loregarden_block_ticket",
                    "description": "Block it",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
        )
        self.tool_calls: list[tuple[str, dict]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if str(request.url).startswith(MCP_URL):
            if body.get("method") == "tools/list":
                return httpx.Response(200, json={"result": {"tools": self.tools}})
            params = body.get("params") or {}
            self.tool_calls.append((params.get("name", ""), params.get("arguments") or {}))
            return httpx.Response(
                200, json={"result": {"content": [{"type": "text", "text": '{"ok": true}'}]}}
            )
        if str(request.url).endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen-test"}]})
        turn = self.model_turns.pop(0)
        return httpx.Response(200, json={"choices": [{"message": turn}]})


def _run(transport: FakeTransport, **kwargs) -> str:
    """The inner loop's TEXT.

    `_chat_with_tools` returns an `_IterationResult` since
    lg-workflow-integrity-163 — it has to report whether the model answered or
    the conversation ran out of rounds, because the outer fresh-context loop
    decides on that. These tests are about what the model said, so they unwrap
    it; `test_lmstudio_iterations.py` covers the finished/unfinished half.
    """
    client = httpx.Client(transport=transport)
    try:
        # run_chat opens its own client, so drive the pieces it composes.
        from loregarden.agents.executors.lmstudio_runner import _chat_with_tools

        bridge = McpBridge(
            client,
            MCP_URL,
            kwargs.get("run_id", "run-1"),
            kwargs.get("workspace_slug", "loregarden"),
        )
        tools = bridge.tools(kwargs.get("granted", []))
        return _chat_with_tools(
            client=client,
            base_url=LM_URL,
            model="qwen-test",
            prompt="do the thing",
            bridge=bridge,
            tools=tools,
        ).text
    finally:
        client.close()


def test_the_model_can_call_a_tool_and_answer_with_the_result():
    """Without this loop the model could only emit text — no workflow state."""
    transport = FakeTransport(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "loregarden_get_ticket", "arguments": "{}"}}
                ],
            },
            {"role": "assistant", "content": "ticket read, done"},
        ]
    )
    assert _run(transport) == "ticket read, done"
    assert transport.tool_calls[0][0] == "loregarden_get_ticket"


def test_run_id_is_filled_in_rather_than_left_to_the_model():
    """A local model that omits or invents run_id fails every run-scoped call."""
    transport = FakeTransport(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "loregarden_get_ticket", "arguments": "{}"}}
                ],
            },
            {"role": "assistant", "content": "done"},
        ]
    )
    _run(transport, run_id="run-42", workspace_slug="loregarden")
    _, args = transport.tool_calls[0]
    assert args["run_id"] == "run-42"
    assert args["workspace_slug"] == "loregarden"


def test_a_run_id_the_model_supplies_is_left_alone():
    transport = FakeTransport(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {
                            "name": "loregarden_get_ticket",
                            "arguments": '{"run_id": "explicit"}',
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "done"},
        ]
    )
    _run(transport, run_id="run-42")
    assert transport.tool_calls[0][1]["run_id"] == "explicit"


def test_only_granted_tools_are_advertised():
    """A small model handed every tool calls the wrong one."""
    transport = FakeTransport([{"role": "assistant", "content": "done"}])
    client = httpx.Client(transport=transport)
    bridge = McpBridge(client, MCP_URL, "run-1", "loregarden")
    names = [t["function"]["name"] for t in bridge.tools(["loregarden_get_ticket"])]
    client.close()
    assert names == ["loregarden_get_ticket"]


def test_malformed_arguments_do_not_kill_the_run():
    """Local models emit invalid JSON; the call should still be attempted."""
    transport = FakeTransport(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "loregarden_get_ticket", "arguments": "not json"},
                    }
                ],
            },
            {"role": "assistant", "content": "recovered"},
        ]
    )
    assert _run(transport) == "recovered"
    assert transport.tool_calls[0][1]["run_id"] == "run-1"


def test_a_failing_tool_is_reported_back_instead_of_raising():
    """The model can correct a bad call; killing the run loses the work done."""

    class Failing(FakeTransport):
        def handle_request(self, request):
            if str(request.url).startswith(MCP_URL) and b"tools/call" in (request.content or b""):
                raise httpx.ConnectError("mcp down")
            return super().handle_request(request)

    transport = Failing(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "loregarden_get_ticket", "arguments": "{}"}}
                ],
            },
            {"role": "assistant", "content": "carried on"},
        ]
    )
    assert _run(transport) == "carried on"


def test_a_model_that_never_stops_calling_tools_is_cut_off():
    """Otherwise a loop runs until the stage timeout with nothing to show."""
    from loregarden.agents.executors.lmstudio_runner import MAX_TOOL_ROUNDS

    turns = [
        {
            "role": "assistant",
            "content": "still working",
            "tool_calls": [
                {"id": f"c{i}", "function": {"name": "loregarden_get_ticket", "arguments": "{}"}}
            ],
        }
        for i in range(MAX_TOOL_ROUNDS + 5)
    ]
    transport = FakeTransport(turns)
    _run(transport)
    assert len(transport.tool_calls) == MAX_TOOL_ROUNDS


def test_assistant_text_prefers_content_then_reasoning_json():
    from loregarden.agents.executors.lmstudio_runner import _assistant_text

    assert _assistant_text({"content": "hello"}) == "hello"
    assert _assistant_text({"content": "  ", "reasoning_content": "noise"}) == "noise"
    reasoning = 'Thinking...\n```json\n{"summary": "ok", "tickets": []}\n```\n'
    assert _assistant_text({"content": "", "reasoning_content": reasoning}) == (
        '{"summary": "ok", "tickets": []}'
    )
    assert (
        _assistant_text({"content": None, "reasoning_content": '{"summary": "x"}'})
        == '{"summary": "x"}'
    )


def test_stdout_heartbeat_emits_while_blocked(monkeypatch, capsys):
    """Idle print-mode budget needs a line during long non-stream completions."""
    import time

    from loregarden.agents.executors import lmstudio_runner as runner

    monkeypatch.setattr(runner, "_HEARTBEAT_SECONDS", 0.05)
    with runner._stdout_heartbeat("test"):
        time.sleep(0.12)
    out = capsys.readouterr().out
    assert "[WAIT] test" in out


def test_without_mcp_details_it_stays_a_plain_chat(monkeypatch):
    """Existing lmstudio use keeps working; tools are opt-in per run."""
    transport = FakeTransport([{"role": "assistant", "content": "plain answer"}])
    real_client = httpx.Client  # capture before patching, or the lambda recurses
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=transport))
    assert run_chat(prompt="hi", base_url=LM_URL, model="", stream=False) == "plain answer"
    assert transport.tool_calls == []


def test_the_invocation_carries_the_run_context(tmp_path, monkeypatch):
    """Wired wrong, the runner silently falls back to plain chat forever."""
    from loregarden.agents.cli_adapters import resolve_cli_invocation
    from loregarden.models.domain import Workspace

    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "lmstudio")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hi")
    workspace = Workspace(
        id="ws",
        slug="loregarden",
        name="LG",
        repo_path=str(tmp_path),
        cli_adapter="lmstudio",
        lmstudio_base_url="http://lm.test/v1",
    )
    invocation = resolve_cli_invocation(
        agent_id="backend_implementer",
        adapter="lmstudio",
        prompt="hi",
        prompt_file=prompt_file,
        skill_name="",
        workspace_root=tmp_path,
        workspace=workspace,
        run_id="run-9",
        workspace_slug="loregarden",
        granted_tools=["loregarden_get_ticket", "loregarden_complete_stage"],
    )
    argv = invocation.argv
    assert "--stream" in argv
    assert "--run-id" in argv and argv[argv.index("--run-id") + 1] == "run-9"
    assert "--mcp-url" in argv
    assert argv[argv.index("--tools") + 1] == "loregarden_get_ticket,loregarden_complete_stage"


def test_a_run_without_context_gets_no_tool_flags(tmp_path, monkeypatch):
    """Stage/triage builders omit MCP flags unless a run_id is supplied."""
    from loregarden.agents.cli_adapters import resolve_cli_invocation
    from loregarden.models.domain import Workspace

    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "lmstudio")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hi")
    workspace = Workspace(
        id="ws",
        slug="loregarden",
        name="LG",
        repo_path=str(tmp_path),
        cli_adapter="lmstudio",
        lmstudio_base_url="http://lm.test/v1",
    )
    invocation = resolve_cli_invocation(
        agent_id="backend_implementer",
        adapter="lmstudio",
        prompt="hi",
        prompt_file=prompt_file,
        skill_name="",
        workspace_root=tmp_path,
        workspace=workspace,
    )
    assert "--run-id" not in invocation.argv
    assert "--mcp-url" not in invocation.argv


def test_triage_lmstudio_invocation_carries_run_mcp(tmp_path, monkeypatch):
    """Baxter on LM Studio needs the same MCP wiring as stage runs — no native MCP."""
    from loregarden.agents.cli_adapters import build_triage_invocation
    from loregarden.models.domain import Workspace

    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "lmstudio")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("triage")
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    ws = Workspace(
        slug="test",
        name="Test",
        cli_adapter="lmstudio",
        lmstudio_base_url="http://lm.test/v1",
        lmstudio_model="local-chat",
    )
    inv = build_triage_invocation(
        agent_id="triage",
        adapter="claude",
        prompt="triage",
        prompt_file=prompt_file,
        skill_name="",
        workspace_root=workspace_root,
        workspace=ws,
        run_id="run-triage-1",
        workspace_slug="test",
        granted_tools=["loregarden_get_ticket"],
    )
    assert inv.adapter == "lmstudio"
    assert "--stream" in inv.argv
    assert inv.argv[inv.argv.index("--model") + 1] == "local-chat"
    assert inv.argv[inv.argv.index("--run-id") + 1] == "run-triage-1"
    assert "--mcp-url" in inv.argv
    assert inv.argv[inv.argv.index("--tools") + 1] == "loregarden_get_ticket"
