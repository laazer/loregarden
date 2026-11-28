"""Override warning fires only when a model pin was dropped. Spec R4.

A higher-tier harness pin with no role/stage model is silent. The old trigger
('declares adapter' / pin-the-ticket copy) is gone.
"""

from unittest import mock

import pytest
from loregarden.agents.cli_adapters import CliInvocation
from loregarden.agents.executors.dispatch_warnings import maybe_warn_adapter_override
from loregarden.models.domain import AgentRun, Workspace
from loregarden.services.cli_settings import adapter_override_warning, adapter_pin_source


@pytest.fixture(autouse=True)
def _no_env_adapter_pin(monkeypatch):
    """conftest pins LOREGARDEN_CLI_ADAPTER=local session-wide; the env tier would
    outrank the workspace and ticket tiers these tests are about."""
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)


def _warn(**overrides) -> str:
    args = {
        "agent_id": "learning",
        "selected_adapter": "cursor",
        "dropped_model": "",
    }
    return adapter_override_warning(**{**args, **overrides})


def test_silent_when_dropped_model_is_empty(monkeypatch):
    """R4.1 — workspace pins cursor, role has no model pin → silent."""
    assert _warn(dropped_model="") == ""
    assert _warn(dropped_model="", selected_adapter="local") == ""


def test_silent_when_pins_apply():
    """R4.1 — Claude pin on a Claude run is not a drop."""
    assert _warn(dropped_model="opus", selected_adapter="claude") == ""


def test_names_the_dropped_pin_and_selected_harness_only():
    """R4.2"""
    message = _warn(dropped_model="opus", selected_adapter="cursor")

    assert message == (
        "Agent 'learning' model pin 'opus' does not apply to cursor and was dropped."
    )


def test_does_not_tell_the_operator_to_pin_a_declared_cli():
    """R4.3"""
    message = _warn(dropped_model="opus", selected_adapter="cursor")

    assert "declares adapter" not in message
    assert "To run this agent as declared" not in message
    assert "Model:" not in message
    assert "own default" not in message


def test_pin_source_last_tier_is_process():
    """R6.1 — empty string is gone; inherit is process."""
    ws = Workspace(slug="ws", name="ws", cli_adapter="cursor")

    assert adapter_pin_source(workspace=ws) == "workspace"
    assert adapter_pin_source(workspace=ws, ticket_adapter="claude") == "ticket"
    assert adapter_pin_source(workspace=Workspace(slug="ws", name="ws")) == "process"
    with mock.patch.dict("os.environ", {"LOREGARDEN_CLI_ADAPTER": "local"}):
        assert adapter_pin_source(workspace=ws, ticket_adapter="claude") == "env"


def test_executor_writes_the_drop_warning_into_the_run_log():
    streamer = mock.Mock()
    run = AgentRun(run_code="r-1", ticket_id="t", workspace_id="w", agent_id="learning")

    maybe_warn_adapter_override(
        streamer=streamer,
        run=run,
        agent={"default_model": "opus"},
        stage_def=None,
        invocation=CliInvocation(argv=["cursor-agent"], adapter="cursor"),
    )

    streamer.append.assert_called_once()
    level, message = streamer.append.call_args.args
    assert level == "WARN"
    assert message == (
        "Agent 'learning' model pin 'opus' does not apply to cursor and was dropped."
    )


def test_executor_is_silent_when_the_role_has_no_model_pin():
    """R4.1 / R4.4 — trigger is the drop, not a higher-tier harness replacement."""
    streamer = mock.Mock()
    run = AgentRun(run_code="r-1", ticket_id="t", workspace_id="w", agent_id="learning")

    maybe_warn_adapter_override(
        streamer=streamer,
        run=run,
        agent={"default_model": ""},
        stage_def=None,
        invocation=CliInvocation(argv=["cursor-agent"], adapter="cursor"),
    )

    streamer.append.assert_not_called()


def test_maybe_warn_does_not_read_agent_adapter():
    """R4.4 — leftover agent['adapter'] must not become the trigger."""
    streamer = mock.Mock()
    run = AgentRun(run_code="r-1", ticket_id="t", workspace_id="w", agent_id="learning")

    maybe_warn_adapter_override(
        streamer=streamer,
        run=run,
        agent={"adapter": "claude", "default_model": ""},
        stage_def=None,
        invocation=CliInvocation(argv=["cursor-agent"], adapter="cursor"),
    )

    streamer.append.assert_not_called()
