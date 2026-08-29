"""Shared adapter turn strategy map."""

from loregarden.services.agent_turn_runner import (
    adapter_capabilities,
    resolve_chat_intent,
    resolve_turn_strategy,
)


def test_adapter_capabilities_matrix():
    claude = adapter_capabilities("claude")
    assert claude.permission_bridge is True
    assert claude.inbox_approvals is True
    assert claude.plan_execute is True
    assert claude.steer is True

    for name in ("codex", "cursor", "lmstudio"):
        caps = adapter_capabilities(name)
        assert caps.permission_bridge is False
        assert caps.inbox_approvals is False
        assert caps.plan_execute is True


def test_resolve_turn_strategy_is_shared_across_adapters():
    assert resolve_turn_strategy("claude", "execute") == "permission_bridge"
    assert resolve_turn_strategy("claude", "advisory") == "advisory_oneshot"
    assert resolve_turn_strategy("codex", "execute") == "writable_oneshot"
    assert resolve_turn_strategy("codex", "advisory") == "advisory_oneshot"
    assert resolve_turn_strategy("cursor", "execute") == "writable_oneshot"
    assert resolve_turn_strategy("lmstudio", "execute") == "writable_oneshot"
    assert resolve_turn_strategy("local", "execute") == "advisory_oneshot"


def test_resolve_chat_intent_uses_capabilities_not_adapter_names():
    # Ticket-scoped surfaces: any execute-capable adapter acts.
    assert resolve_chat_intent("claude") == "execute"
    assert resolve_chat_intent("codex") == "execute"
    assert resolve_chat_intent("cursor") == "execute"
    assert resolve_chat_intent("lmstudio") == "execute"
    assert resolve_chat_intent("local") == "advisory"

    # Home chat used to keep oneshot adapters advisory until Run. The flag
    # still works for a surface that wants that; Home itself no longer sets it.
    assert resolve_chat_intent("claude", require_operator_run=True) == "execute"
    assert resolve_chat_intent("codex", require_operator_run=True) == "advisory"
    assert resolve_chat_intent("codex", require_operator_run=True, wants_execute=True) == "execute"
    assert resolve_chat_intent("local", require_operator_run=True, wants_execute=True) == "advisory"
