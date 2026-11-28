"""Unit tests for the shared adapter/model resolution seam."""

from loregarden.models.domain import Workspace
from loregarden.services.cli_settings import (
    adapter_model_pins_apply,
    resolve_model_for_adapter,
    ticket_model_for_adapter,
)


def test_adapter_model_pins_apply_classifies_the_pin_not_the_agent():
    """R3 — empty/default is False (the landmine). Parameter is pin_adapter."""
    assert adapter_model_pins_apply(pin_adapter="claude", selected_adapter="claude")
    assert adapter_model_pins_apply(pin_adapter="cursor", selected_adapter="cursor")
    assert adapter_model_pins_apply(pin_adapter="codex", selected_adapter="codex")
    assert not adapter_model_pins_apply(pin_adapter="", selected_adapter="cursor")
    assert not adapter_model_pins_apply(pin_adapter="default", selected_adapter="claude")
    assert not adapter_model_pins_apply(pin_adapter="claude", selected_adapter="cursor")
    assert not adapter_model_pins_apply(pin_adapter="claude", selected_adapter="lmstudio")


def test_ticket_model_for_adapter_picks_matching_field():
    assert (
        ticket_model_for_adapter(
            "claude", claude_model="c", cursor_model="u", codex_model="x", lmstudio_model="l"
        )
        == "c"
    )
    assert (
        ticket_model_for_adapter(
            "cursor", claude_model="c", cursor_model="u", codex_model="x", lmstudio_model="l"
        )
        == "u"
    )
    assert (
        ticket_model_for_adapter(
            "codex", claude_model="c", cursor_model="u", codex_model="x", lmstudio_model="l"
        )
        == "x"
    )
    assert (
        ticket_model_for_adapter(
            "lmstudio", claude_model="c", cursor_model="u", codex_model="x", lmstudio_model="l"
        )
        == "l"
    )
    assert ticket_model_for_adapter("local", claude_model="c") == ""


def test_resolve_model_for_adapter_shared_precedence(monkeypatch):
    ws = Workspace(
        slug="t",
        name="t",
        claude_model="ws-claude",
        cursor_model="ws-cursor",
        codex_model="ws-codex",
        lmstudio_model="ws-local",
    )
    monkeypatch.delenv("LOREGARDEN_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("LOREGARDEN_CURSOR_MODEL", raising=False)
    monkeypatch.delenv("LOREGARDEN_CODEX_MODEL", raising=False)
    monkeypatch.delenv("LOREGARDEN_LMSTUDIO_MODEL", raising=False)

    assert resolve_model_for_adapter("claude", ws) == "ws-claude"
    assert resolve_model_for_adapter("cursor", ws) == "ws-cursor"
    assert resolve_model_for_adapter("codex", ws) == "ws-codex"
    assert resolve_model_for_adapter("lmstudio", ws) == "ws-local"
    assert resolve_model_for_adapter("local", ws) == ""

    assert resolve_model_for_adapter("cursor", ws, agent_model="agent-pin") == "agent-pin"
    assert (
        resolve_model_for_adapter(
            "lmstudio", ws, agent_model="a", stage_model="s", ticket_model="t"
        )
        == "t"
    )

    monkeypatch.setenv("LOREGARDEN_CURSOR_MODEL", "env-cursor")
    assert resolve_model_for_adapter("cursor", ws, ticket_model="ticket") == "env-cursor"
