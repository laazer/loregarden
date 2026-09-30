"""A supervised run names itself to the MCP server, so its memory writes carry `origin_ref`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.agents.cli_adapters import build_interactive_invocation, resolve_cli_invocation
from loregarden.mcp.caller import ORCHESTRATED_HEADER, RUN_ID_ENV, RUN_ID_HEADER
from loregarden.mcp.protocol import handle_message
from loregarden.models.domain import Workspace
from tests.factories import make_agent_run, make_workspace

WS = "kb"
RUN = "run-abc"


def _stage(tmp_path: Path, *, adapter: str, transport: str = "http", bypass: bool = False):
    env = {
        "LOREGARDEN_CLI_ADAPTER": adapter,
        "LOREGARDEN_MCP_TRANSPORT": transport,
        "LOREGARDEN_ALLOW_PERMISSION_BYPASS": "1" if bypass else "0",
    }
    with patch.dict(os.environ, env):
        return resolve_cli_invocation(
            agent_id="implementer",
            adapter=adapter,
            prompt="do the stage",
            prompt_file=tmp_path / "prompt.md",
            skill_name="implement",
            workspace_root=tmp_path,
            workspace=Workspace(slug="t", name="T"),
            run_id=RUN,
        )


def _claude_entry(argv: list[str]) -> dict:
    config = json.loads(argv[argv.index("--mcp-config") + 1])
    return config["mcpServers"]["loregarden"]


@pytest.mark.parametrize("bypass", [False, True], ids=["bridged", "print"])
def test_a_claude_stage_run_sends_its_run_id_as_a_header(tmp_path, bypass):
    entry = _claude_entry(_stage(tmp_path, adapter="claude", bypass=bypass).argv)
    assert entry["headers"] == {ORCHESTRATED_HEADER: "1", RUN_ID_HEADER: RUN}


def test_a_stdio_server_gets_the_run_id_in_its_environment(tmp_path):
    entry = _claude_entry(_stage(tmp_path, adapter="claude", transport="stdio").argv)
    assert entry["env"][RUN_ID_ENV] == RUN


def test_a_codex_stage_run_passes_the_run_id_to_its_server(tmp_path):
    argv = _stage(tmp_path, adapter="codex").argv
    assert f"mcp_servers.loregarden.env.{RUN_ID_ENV}={json.dumps(RUN)}" in argv


def test_an_opencode_stage_run_sends_its_run_id_as_a_header(tmp_path):
    invocation = _stage(tmp_path, adapter="opencode")
    entry = json.loads(invocation.env["OPENCODE_CONFIG_CONTENT"])["mcp"]["loregarden"]
    assert entry["headers"][RUN_ID_HEADER] == RUN


def test_a_chat_session_never_claims_a_run(tmp_path):
    invocation = build_interactive_invocation(
        adapter="claude",
        prompt_file=tmp_path / "prompt.md",
        workspace_root=tmp_path,
        orchestrated=False,
        run_id=RUN,
    )
    assert "headers" not in _claude_entry(invocation.argv)


# ---------------------------------------------------------------------------
# The server side
# ---------------------------------------------------------------------------


def _append(client, headers: dict[str, str]) -> dict:
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loregarden_append_learning",
                "arguments": {"ticket_id": "t", "workspace_slug": WS, "content": "A lesson."},
            },
        },
    )
    result = response.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])["graph"]


@pytest.fixture
def run_id(db_session) -> str:
    workspace = make_workspace(db_session, slug="run-identity")
    run = make_agent_run(db_session, workspace_id=workspace.id)
    db_session.commit()
    return run.id


def test_a_write_from_a_known_run_records_it_as_the_reference(client, run_id):
    node = _append(client, {ORCHESTRATED_HEADER: "1", RUN_ID_HEADER: run_id})
    assert (node["origin_kind"], node["origin_ref"]) == ("agent", run_id)


def test_an_unknown_run_id_is_not_recorded_and_is_logged(client, caplog):
    node = _append(client, {ORCHESTRATED_HEADER: "1", RUN_ID_HEADER: "no-such-run"})
    assert (node["origin_kind"], node["origin_ref"]) == ("agent", None)
    assert "no-such-run" in caplog.text


def test_a_run_id_without_the_orchestrated_flag_is_ignored(client, run_id):
    node = _append(client, {RUN_ID_HEADER: run_id})
    assert (node["origin_kind"], node["origin_ref"]) == (None, None)


def test_the_stdio_path_records_the_run_too(db_session, run_id):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "loregarden_upsert_memory",
            "arguments": {"title": "T", "body": "b", "workspace_slug": WS},
        },
    }
    result = handle_message(db_session, body, orchestrated=True, run_id=run_id)["result"]
    node = json.loads(result["content"][0]["text"])["graph"]
    assert node["origin_ref"] == run_id
