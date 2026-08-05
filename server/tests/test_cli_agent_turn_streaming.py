"""The one-shot turn, made visible while it runs.

`run_cli_agent_turn` used to hand over everything the agent said at the moment
it stopped saying it. Given a sink it asks the CLI for NDJSON and reads stdout
as it arrives instead — the reply is unchanged either way, which is the
property most of these tests are about.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from loregarden.agents.cli_adapters import CliInvocation, build_triage_invocation
from loregarden.models.domain import Workspace
from loregarden.services import cli_agent_runner
from loregarden.services.chat_thinking import ChatTurnThinkingSink, read_chat_turn_thinking
from loregarden.services.triage_service import TRIAGE_CLI_PROFILE
from sqlmodel import Session


def _invocation_argv(tmp_path, monkeypatch, adapter: str, *, stream_json: bool) -> list[str]:
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", adapter)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("triage", encoding="utf-8")
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir(exist_ok=True)
    inv = build_triage_invocation(
        agent_id="triage",
        adapter=adapter,
        prompt="triage",
        prompt_file=prompt_file,
        skill_name="",
        workspace_root=workspace_root,
        workspace=Workspace(slug="test", name="Test"),
        stream_json=stream_json,
    )
    return inv.argv


def test_claude_asks_for_text_when_nobody_is_watching(tmp_path, monkeypatch):
    argv = _invocation_argv(tmp_path, monkeypatch, "claude", stream_json=False)
    assert argv[argv.index("--output-format") + 1] == "text"
    assert "--include-partial-messages" not in argv


def test_claude_asks_for_streamed_events_when_someone_is(tmp_path, monkeypatch):
    argv = _invocation_argv(tmp_path, monkeypatch, "claude", stream_json=True)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    # Both are required: `-p` rejects stream-json without --verbose, and without
    # partial messages a thinking block only lands once it is finished.
    assert "--verbose" in argv
    assert "--include-partial-messages" in argv


def test_cursor_asks_for_its_own_partial_output(tmp_path, monkeypatch):
    argv = _invocation_argv(tmp_path, monkeypatch, "cursor", stream_json=True)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--stream-partial-output" in argv


class FakeProc:
    """A CLI that emits its lines on demand, then exits."""

    def __init__(self, lines: list[str], *, returncode: int = 0, stderr: bytes = b"") -> None:
        self._remaining = list(lines)
        self.returncode = returncode
        self.stdin = None
        self.killed = False
        self.stdout = self
        self.stderr = io.BytesIO(stderr)

    # -- stdout, as SubprocessLineReader sees it (no fileno → readline path) --
    def readline(self) -> str:
        return self._remaining.pop(0) if self._remaining else ""

    def poll(self):
        return None if self._remaining else self.returncode

    def communicate(self, timeout=None):
        # Buffered path only: streaming turns read stderr directly after drain.
        if self.stdin is not None:
            # Mirror CPython: flushing a closed stdin raises ValueError.
            if getattr(self.stdin, "closed", False):
                raise ValueError("I/O operation on closed file.")
            self.stdin.flush()
        rest, self._remaining = "".join(self._remaining), []
        return (rest.encode("utf-8"), self.stderr.getvalue())

    def kill(self):
        self.killed = True


@pytest.fixture(name="stub_turn")
def stub_turn_fixture(tmp_path, monkeypatch):
    """Wire `run_cli_agent_turn` to a fake CLI in a throwaway workspace."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        cli_agent_runner,
        "resolve_workspace_root",
        lambda _workspace: Path(repo),
    )
    monkeypatch.setattr(cli_agent_runner, "get_agent", lambda _id: {"adapter": "claude"})
    monkeypatch.setattr(
        cli_agent_runner,
        "build_triage_invocation",
        lambda **kwargs: CliInvocation(argv=["fake"], cwd=str(repo)),
    )

    def run(lines: list[str], sink=None, **kwargs):
        proc = FakeProc(lines)
        monkeypatch.setattr(cli_agent_runner.subprocess, "Popen", lambda *a, **k: proc)
        reply = cli_agent_runner.run_cli_agent_turn(
            TRIAGE_CLI_PROFILE,
            workspace=Workspace(slug="test", name="Test"),
            prompt="do the thing",
            thinking_sink=sink,
            **kwargs,
        )
        return reply, proc

    return run


def _thinking_line(text: str) -> str:
    return (
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": text},
                },
            }
        )
        + "\n"
    )


def _result_line(text: str) -> str:
    return json.dumps({"type": "result", "result": text}) + "\n"


def test_streamed_turn_returns_the_same_reply_it_always_did(stub_turn, db_session: Session):
    sink = ChatTurnThinkingSink("stream-1")
    reply, _proc = stub_turn(
        [_thinking_line("Weighing it up."), _result_line("Ship the fix.")],
        sink=sink,
    )
    sink.close()

    assert reply == "Ship the fix."


def test_streamed_turn_shows_its_reasoning_before_it_finishes(stub_turn, db_session: Session):
    sink = ChatTurnThinkingSink("stream-2")
    stub_turn([_thinking_line("Weighing it up."), _result_line("Ship the fix.")], sink=sink)
    sink.close()

    frame = read_chat_turn_thinking(db_session, "stream-2")
    assert frame["content"] == "Weighing it up."
    # The answer is the reply, not reasoning — it must not appear twice.
    assert "Ship the fix." not in frame["content"]


def test_a_plain_text_reply_still_works_with_a_sink_attached(stub_turn, db_session: Session):
    """A non-claude adapter ignores the stream request; the turn must not care."""
    sink = ChatTurnThinkingSink("stream-3")
    reply, _proc = stub_turn(["just some prose\n"], sink=sink)
    sink.close()

    assert reply == "just some prose"
    assert read_chat_turn_thinking(db_session, "stream-3")["content"] == ""


def test_an_unwatched_turn_is_left_on_the_buffered_path(stub_turn):
    reply, proc = stub_turn(["quiet reply\n"], sink=None)

    assert reply == "quiet reply"
    assert not proc.killed


def test_streamed_turn_survives_closed_stdin_prompt(tmp_path, monkeypatch, db_session: Session):
    """Codex writes the prompt on stdin then closes it — must not crash on drain."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        cli_agent_runner,
        "resolve_workspace_root",
        lambda _workspace: Path(repo),
    )
    monkeypatch.setattr(cli_agent_runner, "get_agent", lambda _id: {"adapter": "codex"})
    monkeypatch.setattr(
        cli_agent_runner,
        "build_triage_invocation",
        lambda **_kwargs: CliInvocation(
            argv=["fake"],
            cwd=str(repo),
            stdin_prompt="prompt on stdin",
            adapter="codex",
        ),
    )

    class FakeStdin:
        def __init__(self) -> None:
            self.closed = False
            self.written = b""

        def write(self, data: bytes) -> int:
            self.written += data
            return len(data)

        def close(self) -> None:
            self.closed = True

        def flush(self) -> None:
            if self.closed:
                raise ValueError("I/O operation on closed file.")

    class CodexProc(FakeProc):
        def __init__(self, lines: list[str]) -> None:
            super().__init__(lines)
            self.stdin = FakeStdin()

    proc = CodexProc([_result_line("Ship it.")])
    monkeypatch.setattr(cli_agent_runner.subprocess, "Popen", lambda *a, **k: proc)

    sink = ChatTurnThinkingSink("stream-stdin")
    reply = cli_agent_runner.run_cli_agent_turn(
        TRIAGE_CLI_PROFILE,
        workspace=Workspace(slug="test", name="Test"),
        prompt="do the thing",
        thinking_sink=sink,
    )
    sink.close()

    assert reply == "Ship it."
    assert proc.stdin is None
