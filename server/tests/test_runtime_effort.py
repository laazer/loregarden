"""Reasoning-effort resolution and how each adapter carries it to its CLI."""

import json
import os
import threading
import time
from unittest import mock

import httpx
import pytest
from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.executors.lmstudio_runner import run_chat
from loregarden.models.domain import Ticket, Workspace, WorkspaceRuntimeSettings
from loregarden.models.domain.enums import DiscoveryStatus
from loregarden.services import cli_settings
from loregarden.services.cli_settings import (
    CURSOR_EFFORT_MODELS,
    apply_cursor_effort,
    resolve_effort_for_adapter,
    resolve_runtime_effective,
)
from loregarden.services.discovery_cache import ProbeAnswer, join_probes
from loregarden.services.runtime_options import runtime_options_payload
from sqlmodel import select


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
    assert "--stream" in inv.argv


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


def test_effective_names_the_agent_tier_when_nothing_pins_a_provider():
    """With no env/ticket/workspace pin each stage runs on its own agent's adapter;
    reporting the global setting claimed a provider ("local") no run used."""
    effective = resolve_runtime_effective(_workspace(cli_adapter="default"))

    assert effective["cli_adapter"] == ""
    assert effective["cli_adapter_source"] == "agent"
    assert effective["supports_model"] is False


def test_effective_credits_the_ticket_adapter_over_the_workspace_pin():
    ws = _workspace(cli_adapter="cursor", claude_model="claude-opus-5")
    ticket = WorkspaceRuntimeSettings(cli_adapter="claude", claude_effort="medium")

    effective = resolve_runtime_effective(ws, ticket_runtime=ticket)

    assert (effective["cli_adapter"], effective["cli_adapter_source"]) == ("claude", "ticket")
    assert (effective["model"], effective["model_source"]) == ("claude-opus-5", "workspace")
    assert (effective["effort"], effective["effort_source"]) == ("medium", "ticket")


def test_runtime_options_for_a_ticket_resolve_its_own_pin_and_workspace(client, db_session):
    """The ticket dialog asked with workspace=all and showed the global fallback,
    so a saved ticket pin never showed up as what the ticket's runs would use."""
    ticket = db_session.exec(select(Ticket)).first()
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.cli_adapter = "cursor"
    ticket.orchestration_runtime_json = json.dumps(
        {"cli_adapter": "claude", "claude_model": "claude-opus-5"}
    )
    db_session.add_all([workspace, ticket])
    db_session.commit()

    effective = client.get(
        f"/api/workspaces/runtime-options?workspace=all&ticket={ticket.id}"
    ).json()["effective"]

    assert (effective["cli_adapter"], effective["cli_adapter_source"]) == ("claude", "ticket")
    assert (effective["model"], effective["model_source"]) == ("claude-opus-5", "ticket")


def test_runtime_options_for_an_unknown_ticket_is_a_404(client):
    assert client.get("/api/workspaces/runtime-options?ticket=nope").status_code == 404


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
    assert body["codex_models"][0] == {"id": "", "label": "Default (Codex profile)"}
    # Live catalog from ``codex debug models`` — never the stale hard-coded gpt-5 pin.
    assert "gpt-5" not in {opt["id"] for opt in body["codex_models"]}


def test_runtime_options_surface_discovered_codex_models(client):
    from loregarden.services.codex_discovery import CodexModel

    fake = _probe_answer(
        [
            CodexModel(slug="gpt-5.5", display_name="GPT-5.5", priority=7),
            CodexModel(slug="gpt-5.6-sol", display_name="GPT-5.6-Sol", priority=1),
        ],
        target="/bin/codex",
    )
    with mock.patch(
        "loregarden.services.codex_discovery.list_codex_models",
        return_value=fake,
    ):
        body = client.get("/api/workspaces/runtime-options").json()

    assert body["codex_models"] == [
        {"id": "", "label": "Default (Codex profile)"},
        {"id": "gpt-5.5", "label": "GPT-5.5"},
        {"id": "gpt-5.6-sol", "label": "GPT-5.6-Sol"},
    ]


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


def test_runtime_options_holds_no_db_connection_while_discovery_runs(client, isolated_db):
    """Discovery must not run on a checked-out connection.

    ``opencode models`` can hang for its whole 45s budget, and the pool is
    fifteen connections for the entire process — requests, ``/ws/queue``
    snapshots, the reconciliation timer. Holding one across a probe is how every
    endpoint, ``/health`` included, began failing with ``QueuePool limit of size
    5 overflow 10 reached``.
    """
    checked_out: list[int] = []

    def record_then_answer():
        checked_out.append(isolated_db.pool.checkedout())
        return (
            [{"id": "", "label": "Default (OpenCode profile)"}],
            {"status": DiscoveryStatus.FRESH, "detail": "", "target": "opencode"},
        )

    # Patched where the payload reads it: `runtime_options` imports the three
    # discovery functions at module level (B5), so the discovery module's own
    # attribute is no longer the name the payload resolves.
    with mock.patch(
        "loregarden.services.runtime_options.opencode_model_options",
        side_effect=record_then_answer,
    ):
        response = client.get("/api/workspaces/runtime-options?workspace=loregarden")

    assert response.status_code == 200
    assert checked_out == [0], (
        f"discovery ran with {checked_out[0] if checked_out else '?'} connection(s) checked "
        "out; the request must hand its connection back before probing"
    )


# ---------------------------------------------------------------------------
# AC5 and the payload that makes a non-blocking probe legible.
# Criteria B5 (the move out of cli_settings), B6 (a cold request under 2s),
# B7 (the additive `model_discovery` block).
# ---------------------------------------------------------------------------


def _probe_answer(found, *, status=DiscoveryStatus.FRESH, detail="", target="probed"):
    return ProbeAnswer(found=list(found), status=status, detail=detail, target=target)


class _HangingProbe:
    """A CLI that never answers, which is the only honest test of a time budget.

    Not "absent" and not "refused": both of those fail fast and would pass B6
    against the unfixed code. This parks on an event for longer than every
    discovery timeout in the tree.
    """

    def __init__(self) -> None:
        self.release = threading.Event()
        #: Counted so a concurrency test can tell one probe per CLI from one per caller.
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        assert self.release.wait(timeout=90), "a probe stub was never released"
        raise AssertionError("the probe stub must never be allowed to answer")

    def finish(self) -> None:
        self.release.set()


@pytest.fixture(name="three_hanging_clis")
def three_hanging_clis_fixture():
    """All three discovery probes parked, caches cold.

    Patched at each module's own probe seam rather than at ``ProbeCache``, so
    the bounded join is what is under test and not a stub of it.
    """
    from loregarden.services import codex_discovery, lmstudio_discovery, opencode_discovery

    probe = _HangingProbe()
    for module in (opencode_discovery, codex_discovery, lmstudio_discovery):
        module.reset_model_cache()
    with (
        mock.patch.object(opencode_discovery, "_list_from_cli", probe),
        mock.patch.object(codex_discovery, "_discover", probe),
        mock.patch.object(lmstudio_discovery, "_probe", probe),
    ):
        try:
            yield probe
        finally:
            probe.finish()
            join_probes(timeout=10.0)
            for module in (opencode_discovery, codex_discovery, lmstudio_discovery):
                module.reset_model_cache()


def test_a_cold_runtime_options_request_answers_while_every_cli_hangs(client, three_hanging_clis):
    """B6 — the defect this ticket exists to close.

    Before the fix, ``ProbeCache.get`` ran the probe on the calling thread and
    ``runtime_options_payload`` called the three in sequence, so this request
    took opencode 45s + codex 12s + lmstudio 2x2s ~= 61s. The connection was
    already handed back, so the pool survived — the request did not.
    """
    started = time.monotonic()
    response = client.get("/api/workspaces/runtime-options?workspace=loregarden")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 2.0, f"a cold runtime-options request took {elapsed:.2f}s with three hung CLIs"

    body = response.json()
    assert set(body["model_discovery"]) == {"codex", "lmstudio", "opencode"}
    for provider, block in body["model_discovery"].items():
        assert block["status"] == "probing", f"{provider} should still be probing"
        assert block["detail"] == "", "a probing provider has nothing to report yet"
        assert block["target"], f"{provider} must name what it is probing"

    # Every list still holds its default sentinel, so the picker is never blank.
    assert body["codex_models"][0]["id"] == ""
    assert body["lmstudio_models"][0]["id"] == ""
    assert body["opencode_models"][0]["id"] == ""


def test_three_bounded_joins_fit_inside_the_budget(client, three_hanging_clis):
    """The arithmetic behind the 0.25s default: 3 x 0.25 = 0.75s against 2s.

    Measured as a second request on a now-warm *failure*-free cache: the probes
    are still in flight, so this is the "someone opened Settings while the first
    probe runs" path, which must be faster still.
    """
    client.get("/api/workspaces/runtime-options?workspace=loregarden")

    started = time.monotonic()
    response = client.get("/api/workspaces/runtime-options?workspace=loregarden")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 1.0, f"a second caller waited {elapsed:.2f}s behind three in-flight probes"


def test_runtime_options_payload_moved_out_of_cli_settings():
    """B5. The three function-local imports existed only to dodge a cycle."""
    assert not hasattr(cli_settings, "runtime_options_payload"), (
        "runtime_options_payload must live in services/runtime_options.py — leaving a copy "
        "or an alias in cli_settings keeps the cycle the move exists to remove"
    )
    # What cli_settings keeps.
    assert hasattr(cli_settings, "resolve_runtime_effective")
    assert hasattr(cli_settings, "CLAUDE_MODEL_OPTIONS")


def test_the_new_module_imports_discovery_at_module_level():
    """B5. `opencode_discovery` and `codex_discovery` import ADAPTER_BINARIES from
    `cli_settings`; the cycle that forced three function-local imports is gone
    once the payload no longer lives there, so they must be module attributes."""
    from loregarden.services import runtime_options

    for name in ("codex_model_options", "lmstudio_model_options", "opencode_model_options"):
        assert hasattr(runtime_options, name), f"{name} must be a module-level import"


def test_model_discovery_names_the_three_probed_providers_and_no_others(client):
    """B7. `claude` and `cursor` are static catalogs: they are never probed."""
    body = client.get("/api/workspaces/runtime-options?workspace=loregarden").json()

    discovery = body["model_discovery"]
    assert set(discovery) == {"codex", "lmstudio", "opencode"}
    assert "claude" not in discovery
    assert "cursor" not in discovery

    legal = {status.value for status in DiscoveryStatus}
    for provider, block in discovery.items():
        assert set(block) == {"status", "detail", "target"}, provider
        assert block["status"] in legal, f"{provider} reported {block['status']!r}"
        assert isinstance(block["detail"], str)
        assert len(block["detail"]) <= 200
        assert block["target"], f"{provider} must name the thing it probed"


def test_model_discovery_is_purely_additive(client):
    """B7. No existing key changes shape; a client that ignores the new one is fine."""
    body = client.get("/api/workspaces/runtime-options?workspace=loregarden").json()

    for key in (
        "cli_adapters",
        "claude_models",
        "cursor_models",
        "codex_models",
        "lmstudio_models",
        "opencode_models",
        "claude_efforts",
        "cursor_efforts",
        "lmstudio_efforts",
        "opencode_efforts",
    ):
        assert isinstance(body[key], list), key
        assert all(set(opt) >= {"id", "label"} for opt in body[key]), key
    assert isinstance(body["cursor_effort_models"], list)
    assert isinstance(body["effective"], dict)


def test_an_unavailable_provider_carries_its_reason_into_the_payload(client):
    """B7/U7. The detail is server-side because the client cannot reconstruct it."""
    from loregarden.services import lmstudio_discovery

    lmstudio_discovery.reset_model_cache()
    try:
        with mock.patch.object(
            lmstudio_discovery.httpx,
            "Client",
            side_effect=httpx.ConnectError(
                "refused", request=httpx.Request("GET", "http://127.0.0.1:1234/v1/models")
            ),
        ):
            # The first request dispatches the probe; whether it beats the 0.25s
            # join is a scheduling question, so read the answer on the second
            # rather than racing it.
            client.get("/api/workspaces/runtime-options")
            join_probes(timeout=10.0)
            body = client.get("/api/workspaces/runtime-options").json()
    finally:
        join_probes(timeout=10.0)
        lmstudio_discovery.reset_model_cache()

    block = body["model_discovery"]["lmstudio"]
    assert block["status"] == DiscoveryStatus.UNAVAILABLE.value
    assert block["detail"] == (
        f"Could not reach LM Studio at {block['target']} — connection refused."
    )


def test_runtime_options_payload_includes_lmstudio_models():
    from loregarden.services.lmstudio_discovery import LmStudioChatModel

    with mock.patch(
        "loregarden.services.lmstudio_discovery.list_lmstudio_chat_models",
        return_value=_probe_answer(
            [LmStudioChatModel(id="local-a", loaded=True)],
            target="http://127.0.0.1:1234/v1",
        ),
    ):
        payload = runtime_options_payload()

    assert payload["lmstudio_models"][1]["id"] == "local-a"
    assert payload["lmstudio_models"][1]["label"] == "local-a · loaded"
    assert payload["model_discovery"]["lmstudio"]["status"] == DiscoveryStatus.FRESH


# ===========================================================================
# Adversarial coverage (test-break stage).
#
# B6 measures one cold request. The outage this ticket is named for needed
# twenty of them at once: the traceback named `get_active_runs` because that
# caller lost the race for the last connection, not because it was the one
# holding them. A fix that is only tested one caller at a time is tested in the
# one regime the bug never occurred in.
# ===========================================================================


def test_simultaneous_cold_requests_all_answer_and_share_one_probe_per_cli(
    client, three_hanging_clis
):
    """Eight callers, a cold cache, three CLIs that never answer.

    Weakness: single-flight that holds for sequential callers and not for
    simultaneous ones — which is the only regime that matters, because
    `AppLayout` fetches runtime-options at app load and seven call sites mount
    together when a modal opens over an already-loading page.

    Damage if broken: eight `opencode models` subprocesses per cold page load.
    Measured on this machine, that command costs 2.9s solo and 20.6-26.8s at
    twenty-way concurrency, so the contention the caller generates is the whole
    defect, and it reappears the moment the lock stops being per-key.
    """
    statuses: list[int] = []
    elapsed: list[float] = []
    barrier = threading.Barrier(8)

    def ask() -> None:
        barrier.wait(timeout=20)
        started = time.monotonic()
        response = client.get("/api/workspaces/runtime-options?workspace=loregarden")
        elapsed.append(time.monotonic() - started)
        statuses.append(response.status_code)

    threads = [threading.Thread(target=ask, daemon=True) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a request thread never returned"

    assert statuses == [200] * 8, f"not every caller was answered: {statuses}"
    # Each caller waits its own three bounded joins and nothing else — not a
    # queue behind whoever dispatched first.
    assert max(elapsed) < 3.0, f"the slowest of eight callers waited {max(elapsed):.2f}s"
    # One probe per CLI, not one per caller. The fixture's stub is shared by all
    # three modules, so three is the whole cold-cache budget.
    assert three_hanging_clis.calls <= 3, (
        f"{three_hanging_clis.calls} probes ran for eight concurrent requests; the per-key "
        "lock is no longer the single-flight bound"
    )
