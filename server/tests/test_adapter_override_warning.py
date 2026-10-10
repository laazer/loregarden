"""A provider pin above the agent replaces its adapter and drops its model pin.

That is deliberate, and invisible: a blobert Learning run declared claude, the
workspace pinned cursor, the claude model pin was discarded, and cursor-agent
ran with no --model on a capped "Auto" tier. The run log said nothing.
"""

from unittest import mock

import pytest
from loregarden.agents.cli_adapters import CliInvocation
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import AgentRun, Workspace, WorkspaceRuntimeSettings
from loregarden.services.cli_settings import adapter_override_warning, adapter_pin_source


@pytest.fixture(autouse=True)
def _no_env_adapter_pin(monkeypatch):
    """conftest pins LOREGARDEN_CLI_ADAPTER=local session-wide; the env tier would
    outrank the workspace and ticket tiers these tests are about."""
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)


def _warn(**overrides) -> str:
    args = {
        "agent_id": "learning",
        "agent_adapter": "claude",
        "selected_adapter": "cursor",
        "pin_source": "workspace",
        "dropped_model": "",
        "model": "",
    }
    return adapter_override_warning(**{**args, **overrides})


def test_names_the_declared_and_selected_adapter_and_the_tier():
    message = _warn()

    assert "'learning' declares adapter 'claude'" in message
    assert "workspace's provider setting pins 'cursor'" in message
    assert "cursor's own default model" in message


def test_names_the_model_pin_it_dropped():
    assert "'opus' does not apply to cursor and was dropped" in _warn(dropped_model="opus")


def test_names_the_model_the_run_uses_when_one_is_pinned():
    message = _warn(model="gpt-5")

    assert "Model: gpt-5." in message
    assert "own default" not in message


@pytest.mark.parametrize(
    ("agent_adapter", "selected_adapter", "pin_source"),
    [
        ("claude", "claude", "workspace"),  # got what it declared
        ("", "cursor", "workspace"),  # declares nothing
        ("default", "cursor", "workspace"),  # declares nothing
        ("claude", "local", ""),  # no tier pinned it (command override)
    ],
)
def test_silent_when_the_agent_was_not_overridden(agent_adapter, selected_adapter, pin_source):
    assert (
        _warn(agent_adapter=agent_adapter, selected_adapter=selected_adapter, pin_source=pin_source)
        == ""
    )


def test_pin_source_walks_env_then_ticket_then_workspace():
    ws = Workspace(slug="ws", name="ws", cli_adapter="cursor")

    assert adapter_pin_source(workspace=ws) == "workspace"
    assert adapter_pin_source(workspace=ws, ticket_adapter="claude") == "ticket"
    assert adapter_pin_source(workspace=Workspace(slug="ws", name="ws")) == ""
    with mock.patch.dict("os.environ", {"LOREGARDEN_CLI_ADAPTER": "local"}):
        assert adapter_pin_source(workspace=ws, ticket_adapter="claude") == "env"


def test_executor_writes_the_warning_into_the_run_log(db_session):
    streamer = mock.Mock()
    run = AgentRun(run_code="r-1", ticket_id="t", workspace_id="w", agent_id="learning")

    CliAgentExecutor(db_session)._maybe_warn_adapter_override(
        streamer=streamer,
        run=run,
        agent={"adapter": "claude", "default_model": "opus"},
        workspace=Workspace(slug="ws", name="ws", cli_adapter="cursor"),
        ticket_runtime=WorkspaceRuntimeSettings(),
        stage_def=None,
        invocation=CliInvocation(argv=["cursor-agent"], adapter="cursor"),
    )

    streamer.append.assert_called_once()
    level, message = streamer.append.call_args.args
    assert level == "WARN"
    assert "'opus' does not apply to cursor" in message
