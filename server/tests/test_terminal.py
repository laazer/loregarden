"""A shell attached to a websocket, and what guards it."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.services.terminal_session import TerminalSession


@pytest.fixture(autouse=True)
def plain_shell(request):
    """Run these against /bin/sh rather than the operator's login shell.

    `-l` is deliberate in production — without it nvm/pyenv are off PATH and
    commands that work in the operator's own window fail here. But it makes a
    test depend on whoever's dotfiles are installed: this machine's profile
    emits a Poetry warning and takes seconds to load. The mechanism under test
    is the pty, not the profile.
    """
    if request.node.get_closest_marker("real_shell_command"):
        yield
        return
    with patch("loregarden.services.terminal_session._shell_command", return_value=["/bin/sh"]):
        yield


def _drain(session: TerminalSession, needle: str, timeout: float = 5.0) -> str:
    """Read until `needle` shows up, or give up.

    A pty delivers output in arbitrary chunks, so a single read is not enough
    to see a command's result.
    """
    seen = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen += session.read().decode("utf-8", errors="replace")
        if needle in seen:
            break
    return seen


def test_the_shell_starts_in_the_workspace(tmp_path: Path):
    """The whole point of a workspace shell: `ls` shows the repo, not $HOME."""
    (tmp_path / "marker-file.txt").write_text("x")
    session = TerminalSession(tmp_path)
    try:
        session.write("ls\n")
        assert "marker-file.txt" in _drain(session, "marker-file.txt")
    finally:
        session.close()


def test_it_runs_a_command_and_returns_output(tmp_path: Path):
    session = TerminalSession(tmp_path)
    try:
        session.write("echo loregarden-terminal-works\n")
        assert "loregarden-terminal-works" in _drain(session, "loregarden-terminal-works")
    finally:
        session.close()


def test_closing_reaps_the_shell(tmp_path: Path):
    """An orphaned login shell per browser refresh adds up fast."""
    session = TerminalSession(tmp_path)
    assert session.alive

    session.close()
    assert not session.alive


def test_reading_a_finished_shell_reports_the_end(tmp_path: Path):
    session = TerminalSession(tmp_path)
    session.write("exit\n")
    _drain(session, "", timeout=1.0)
    time.sleep(0.3)

    # b"" is how the pump learns to stop; an exception here would leak the task.
    deadline = time.monotonic() + 3
    while session.alive and time.monotonic() < deadline:
        time.sleep(0.05)
    assert session.read() == b""
    session.close()


def test_resize_is_survivable_after_the_shell_exits(tmp_path: Path):
    """The browser sends a resize on layout changes, which can race the exit."""
    session = TerminalSession(tmp_path)
    session.close()

    session.resize(40, 100)  # must not raise
    session.write("still no crash")


def test_a_terminal_without_a_token_configured_is_allowed():
    """Refusing only the terminal would be theatre while the rest of the API
    is already open to any local process."""
    from unittest.mock import MagicMock

    from loregarden.api.terminal import _token_ok

    with patch("loregarden.api.terminal.settings") as cfg:
        cfg.api_token = ""
        assert _token_ok(MagicMock()) is True


def test_a_configured_token_is_enforced_on_the_websocket():
    """TokenAuthMiddleware extends BaseHTTPMiddleware and never sees a
    websocket scope, so without this check the terminal would be the one
    endpoint that ignores the API token — and it is a shell."""
    from unittest.mock import MagicMock

    from loregarden.api.terminal import _token_ok

    socket = MagicMock()
    socket.query_params = {"token": "wrong"}
    socket.headers = {}

    with patch("loregarden.api.terminal.settings") as cfg:
        cfg.api_token = "correct-horse"
        assert _token_ok(socket) is False

        socket.query_params = {"token": "correct-horse"}
        assert _token_ok(socket) is True


@pytest.mark.real_shell_command
def test_the_shell_is_a_login_shell():
    """Asserted on the argv rather than by running it: without `-l` the profile
    that puts nvm and pyenv on PATH never loads, and the terminal fails at
    commands that work in the operator's own window."""
    from loregarden.services.terminal_session import _shell_command

    with patch.dict("os.environ", {"SHELL": "/bin/zsh"}):
        assert _shell_command() == ["/bin/zsh", "-l"]
