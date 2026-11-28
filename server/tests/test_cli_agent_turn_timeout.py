"""A turn killed by its timeout hands back what the CLI wrote before the kill.

Both integration merge turns on lg-milestone-that-1216 (2026-10-19) were killed
at 900s, and the runner raised a bare `TimeoutError`: each run's stdout was
empty, so nothing showed that both had already resolved their conflicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.cli_adapters import CliInvocation
from loregarden.models.domain import Workspace
from loregarden.services import cli_agent_runner
from loregarden.services.chat_thinking import ChatTurnThinkingSink
from loregarden.services.cli_agent_runner import CliTurnTimeout
from loregarden.services.triage_service import TRIAGE_CLI_PROFILE
from sqlmodel import Session

SAID = json.dumps(
    {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Resolved shared.txt; running tests."}]},
    }
)


def _hanging_cli(first_line: str) -> list[str]:
    """A CLI that writes one line and then never finishes.

    A shell, not a Python interpreter: under a loaded full-suite run Python's
    start-up sometimes outlasted the 1s turn, which then timed out with nothing
    written. `sh` prints within milliseconds.
    """
    return ["/bin/sh", "-c", 'printf "%s\\n" "$1"; exec sleep 60', "sh", first_line]


TURN_TIMEOUT_SECONDS = 1


@pytest.fixture(name="hanging_turn")
def hanging_turn_fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(first_line: str, sink=None) -> CliTurnTimeout:
        invocation = CliInvocation(argv=_hanging_cli(first_line), cwd=str(repo))
        with (
            mock.patch.object(cli_agent_runner, "resolve_workspace_root", return_value=repo),
            mock.patch.object(cli_agent_runner, "get_agent", return_value={"timeout": 1}),
            mock.patch.object(cli_agent_runner, "build_triage_invocation", return_value=invocation),
            mock.patch.object(
                cli_agent_runner, "resolve_agent_timeout", return_value=TURN_TIMEOUT_SECONDS
            ),
        ):
            with pytest.raises(CliTurnTimeout) as raised:
                cli_agent_runner.run_cli_agent_turn(
                    TRIAGE_CLI_PROFILE,
                    workspace=Workspace(slug="test", name="Test"),
                    prompt="do the thing",
                    thinking_sink=sink,
                )
        return raised.value

    return run


def test_an_unwatched_turn_keeps_what_it_wrote(hanging_turn):
    timeout = hanging_turn("Resolved shared.txt; running tests.")

    assert f"timed out after {TURN_TIMEOUT_SECONDS}s" in str(timeout)
    assert timeout.output.strip() == "Resolved shared.txt; running tests."


def test_a_streamed_turn_keeps_what_it_wrote(hanging_turn, db_session: Session):
    sink = ChatTurnThinkingSink("timeout-1")
    timeout = hanging_turn(SAID, sink=sink)
    sink.close()

    assert json.loads(timeout.output) == json.loads(SAID)
