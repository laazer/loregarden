"""Reasoning-effort resolution and how each adapter carries it to its CLI."""

import json
import os
from unittest import mock

import httpx
import pytest
from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.executors.lmstudio_runner import run_chat
from loregarden.models.domain import Workspace, WorkspaceRuntimeSettings
from loregarden.services import cli_settings
from loregarden.services.cli_settings import (
    CURSOR_EFFORT_MODELS,
    apply_cursor_effort,
    resolve_effort_for_adapter,
    resolve_runtime_effective,
)


@pytest.fixture(autouse=True)
def _workspace_picks_the_adapter(monkeypatch):
    """conftest pins LOREGARDEN_CLI_ADAPTER=local session-wide, and the env tier
    outranks everything — clear it so these tests exercise the workspace pin."""
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)


def _workspace(**kwargs) -> Workspace:
    return Workspace(slug="ws", name="ws", **kwargs)


def test_effort_precedence_env_beats_ticket_beats_workspace():
    ws = _workspace(claude_effort="medium")

    assert resolve_effort_for_adapter("claude", ws) == "medium"
    assert resolve_effort_for_adapter("claude", ws, ticket_effort="max") == "max"

    with mock.patch.dict("os.environ", {"LOREGARDEN_CLAUDE_EFFORT": "low"}):
        assert resolve_effort_for_adapter("claude", ws, ticket_effort="max") == "low"


def test_effort_is_per_adapter_not_shared():
    ws = _workspace(claude_effort="xhigh", cursor_effort="high")

    assert resolve_effort_for_adapter("cursor", ws) == "high"
    assert resolve_effort_for_adapter("lmstudio", ws) == ""


def test_adapter_without_effort_support_resolves_empty():
    assert resolve_effort_for_adapter("local", _workspace(claude_effort="max")) == ""


def test_level_outside_the_adapter_ladder_is_dropped():
    """`xhigh` is a Claude level; LM Studio's OpenAI-compatible field has no such
    value, and forwarding it would fail every request instead of one setting."""
    assert resolve_effort_for_adapter("lmstudio", _workspace(lmstudio_effort="xhigh")) == ""
    assert resolve_effort_for_adapter("claude", _workspace(claude_effort="xhigh")) == "xhigh"
    assert resolve_effort_for_adapter("claude", _workspace(claude_effort="bogus")) == ""


@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [
        ("claude-opus-4-8", "high", "claude-opus-4-8[effort=high]"),
        ("gpt-5", "high", "gpt-5"),  # not parameterized — brackets would break it
        ("claude-opus-4-8", "", "claude-opus-4-8"),
        ("", "high", ""),
        ("claude-opus-4-8[context=1m]", "high", "claude-opus-4-8[context=1m]"),
    ],
)
def test_apply_cursor_effort(model, effort, expected):
    assert apply_cursor_effort(model, effort) == expected


def test_cursor_effort_models_is_non_empty():
    """The bracket path is dead weight if no shipped option is parameterized."""
    assert CURSOR_EFFORT_MODELS


def test_claude_invocation_carries_effort_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CLAUDE_BIN", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
        workspace=_workspace(cli_adapter="claude", claude_effort="xhigh"),
    )

    assert "--effort" in inv.argv
    assert inv.argv[inv.argv.index("--effort") + 1] == "xhigh"


def test_ticket_effort_overrides_workspace_in_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CLAUDE_BIN", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
        workspace=_workspace(cli_adapter="claude", claude_effort="low"),
        ticket_claude_effort="max",
    )

    assert inv.argv[inv.argv.index("--effort") + 1] == "max"


def test_claude_invocation_omits_flag_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
    monkeypatch.setenv("LOREGARDEN_CLAUDE_BIN", "claude")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="claude",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
        workspace=_workspace(cli_adapter="claude"),
    )

    assert "--effort" not in inv.argv


def test_cursor_invocation_folds_effort_into_model_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CURSOR_BIN", "cursor-agent")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="cursor",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
        workspace=_workspace(
            cli_adapter="cursor", cursor_model="claude-opus-4-8", cursor_effort="high"
        ),
    )

    assert "--effort" not in inv.argv
    assert inv.argv[inv.argv.index("--model") + 1] == "claude-opus-4-8[effort=high]"


def test_lmstudio_invocation_passes_effort_argument(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")

    inv = resolve_cli_invocation(
        agent_id="planner",
        adapter="lmstudio",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
        workspace=_workspace(
            cli_adapter="lmstudio", lmstudio_model="qwen3", lmstudio_effort="high"
        ),
    )

    assert inv.argv[inv.argv.index("--effort") + 1] == "high"


def test_effective_reports_resolved_values_and_their_source():
    ws = _workspace(cli_adapter="claude", claude_model="claude-opus-5", claude_effort="xhigh")

    effective = resolve_runtime_effective(ws)

    assert effective["cli_adapter"] == "claude"
    assert effective["cli_adapter_source"] == "workspace"
    assert effective["model"] == "claude-opus-5"
    assert effective["model_source"] == "workspace"
    assert effective["effort"] == "xhigh"
    assert effective["effort_source"] == "workspace"
    assert effective["supports_effort"] is True


def test_effective_falls_back_to_cli_default_when_nothing_is_pinned():
    effective = resolve_runtime_effective(_workspace(cli_adapter="claude"))

    assert effective["model"] == ""
    assert effective["model_source"] == "cli-default"
    assert effective["effort"] == ""
    assert effective["effort_source"] == "cli-default"


def test_effective_credits_the_ticket_tier_when_it_wins():
    ws = _workspace(cli_adapter="claude", claude_effort="low")
    ticket = WorkspaceRuntimeSettings(claude_effort="max")

    effective = resolve_runtime_effective(ws, ticket_runtime=ticket)

    assert effective["effort"] == "max"
    assert effective["effort_source"] == "ticket"


def test_effective_marks_local_adapter_as_taking_no_pins():
    effective = resolve_runtime_effective(_workspace(cli_adapter="local"))

    assert effective["supports_model"] is False
    assert effective["supports_effort"] is False


def test_runtime_options_expose_effort_catalogs_and_effective_block(client):
    payload = client.get("/api/workspaces/runtime-options?workspace=loregarden").json()

    assert [opt["id"] for opt in payload["claude_efforts"]] == [
        "",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert payload["cursor_effort_models"]
    assert "cli_adapter" in payload["effective"]


def test_runtime_options_advertise_only_live_claude_models(client):
    """Retired ids would fail every run they are pinned to."""
    ids = {
        opt["id"] for opt in client.get("/api/workspaces/runtime-options").json()["claude_models"]
    }

    assert "claude-opus-5" in ids
    assert "claude-sonnet-4-20250514" not in ids
    assert "claude-opus-4-20250514" not in ids


def test_runtime_options_include_codex_adapter_and_models(client):
    body = client.get("/api/workspaces/runtime-options").json()

    codex = next(opt for opt in body["cli_adapters"] if opt["id"] == "codex")
    assert codex["label"] == "Codex CLI"
    assert {"id": "gpt-5", "label": "GPT-5"} in body["codex_models"]


def test_runtime_options_flag_an_adapter_whose_cli_is_not_installed(client):
    with mock.patch.object(cli_settings.shutil, "which", return_value=None):
        body = client.get("/api/workspaces/runtime-options").json()

    by_id = {opt["id"]: opt for opt in body["cli_adapters"]}
    assert by_id["codex"]["available"] is False
    assert by_id["claude"]["available"] is False
    # Nothing local to spawn, so these can never be "missing".
    assert by_id["default"]["available"] is True
    assert by_id["lmstudio"]["available"] is True


def test_adapter_available_honours_a_binary_path_override(tmp_path):
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\n")
    with (
        mock.patch.object(cli_settings.shutil, "which", return_value=None),
        mock.patch.dict(os.environ, {"LOREGARDEN_CODEX_BIN": str(fake)}),
    ):
        assert cli_settings.adapter_available("codex") is True


def test_workspace_runtime_round_trips_codex_model(client):
    body = {
        "cli_adapter": "codex",
        "claude_model": "",
        "cursor_model": "",
        "codex_model": "gpt-5",
        "lmstudio_base_url": "",
        "lmstudio_model": "",
        "claude_effort": "",
        "cursor_effort": "",
        "lmstudio_effort": "",
    }
    assert client.patch("/api/workspaces/loregarden/runtime", json=body).status_code == 200

    saved = client.get("/api/workspaces/loregarden/runtime").json()
    assert saved["codex_model"] == "gpt-5"

    effective = client.get("/api/workspaces/runtime-options?workspace=loregarden").json()[
        "effective"
    ]
    assert effective["cli_adapter"] == "codex"
    assert effective["model"] == "gpt-5"
    assert effective["model_source"] == "workspace"
    assert not effective["supports_effort"]


def test_workspace_runtime_round_trips_effort(client):
    body = {
        "cli_adapter": "claude",
        "claude_model": "claude-opus-5",
        "cursor_model": "",
        "lmstudio_base_url": "",
        "lmstudio_model": "",
        "claude_effort": "xhigh",
        "cursor_effort": "",
        "lmstudio_effort": "",
    }
    assert client.patch("/api/workspaces/loregarden/runtime", json=body).status_code == 200

    saved = client.get("/api/workspaces/loregarden/runtime").json()
    assert saved["claude_effort"] == "xhigh"

    effective = client.get("/api/workspaces/runtime-options?workspace=loregarden").json()[
        "effective"
    ]
    assert effective["effort"] == "xhigh"
    assert effective["effort_source"] == "workspace"


def test_workspace_runtime_rejects_a_level_the_adapter_has_no_word_for(client):
    body = {
        "cli_adapter": "lmstudio",
        "claude_model": "",
        "cursor_model": "",
        "lmstudio_base_url": "",
        "lmstudio_model": "",
        "claude_effort": "",
        "cursor_effort": "",
        "lmstudio_effort": "xhigh",
    }
    response = client.patch("/api/workspaces/loregarden/runtime", json=body)

    assert response.status_code == 400
    assert "lmstudio_effort" in response.json()["detail"]


def test_ticket_runtime_rejects_an_unsupported_level(client):
    ticket_id = client.get("/api/tickets").json()[0]["id"]
    response = client.patch(
        f"/api/tickets/{ticket_id}/runtime",
        json={
            "cli_adapter": "claude",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
            "claude_effort": "turbo",
            "cursor_effort": "",
            "lmstudio_effort": "",
        },
    )

    assert response.status_code == 400
    assert "claude_effort" in response.json()["detail"]


def test_ticket_runtime_round_trips_a_supported_level(client):
    ticket_id = client.get("/api/tickets").json()[0]["id"]
    response = client.patch(
        f"/api/tickets/{ticket_id}/runtime",
        json={
            "cli_adapter": "claude",
            "claude_model": "",
            "cursor_model": "",
            "lmstudio_base_url": "",
            "lmstudio_model": "",
            "claude_effort": "max",
            "cursor_effort": "",
            "lmstudio_effort": "",
        },
    )

    assert response.status_code == 200
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["orchestration_runtime"]["claude_effort"] == "max"


class _RecordingTransport(httpx.BaseTransport):
    """Answers /chat/completions, optionally 400-ing any request carrying an effort."""

    def __init__(self, *, reject_effort: bool) -> None:
        self.reject_effort = reject_effort
        self.bodies: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.reject_effort and "reasoning_effort" in body:
            return httpx.Response(400, json={"error": "unknown field reasoning_effort"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "answer"}}]},
        )


def _run_lmstudio(transport: _RecordingTransport, monkeypatch, effort: str) -> str:
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=transport))
    return run_chat(
        prompt="hi",
        base_url="http://lm.test/v1",
        model="qwen3",
        stream=False,
        effort=effort,
    )


def test_lmstudio_sends_reasoning_effort(monkeypatch):
    transport = _RecordingTransport(reject_effort=False)

    assert _run_lmstudio(transport, monkeypatch, "high") == "answer"
    assert transport.bodies[0]["reasoning_effort"] == "high"
    assert len(transport.bodies) == 1


def test_lmstudio_retries_without_effort_when_the_model_rejects_it(monkeypatch):
    """A model that doesn't know the field must not cost the whole run."""
    transport = _RecordingTransport(reject_effort=True)

    assert _run_lmstudio(transport, monkeypatch, "high") == "answer"
    assert len(transport.bodies) == 2
    assert "reasoning_effort" in transport.bodies[0]
    assert "reasoning_effort" not in transport.bodies[1]


def test_lmstudio_sends_no_effort_field_when_unpinned(monkeypatch):
    transport = _RecordingTransport(reject_effort=True)

    assert _run_lmstudio(transport, monkeypatch, "") == "answer"
    assert len(transport.bodies) == 1
    assert "reasoning_effort" not in transport.bodies[0]
