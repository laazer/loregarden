"""A real shell, running in a workspace, attached to a websocket.

Deliberately a *general workspace shell* rather than a tty bound to an agent
run: an operator wants to look around, run a command, and read output, and
tying that to a run's lifetime would take the terminal away exactly when the
run ends and the questions start.

There is no sandbox. This spawns a login shell with the same privileges as the
process serving the API — it can do anything the operator can. The comp labels
this surface `pty · sandbox`; that label would be a lie, and the UI says
`pty · your shell` instead. Containment was considered and rejected rather than
forgotten: a cwd jail is trivially escaped by `cd`, and an allow-list of
commands makes a terminal that is not a terminal. What actually guards this is
the token check at the websocket, which the HTTP middleware cannot do.
"""

from __future__ import annotations

import fcntl
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
from pathlib import Path

logger = logging.getLogger(__name__)

#: Read size per pump. Large enough for a burst of build output, small enough
#: that an interactive keystroke echo is not held back waiting to fill it.
READ_CHUNK = 4096

DEFAULT_SHELL = "/bin/bash"


def _shell_command() -> list[str]:
    """The operator's login shell, so their aliases and prompt are present.

    `-l` matters more than it looks: without it the shell skips the profile
    that puts nvm, pyenv and friends on PATH, and the terminal would fail at
    commands that work in the operator's own window.
    """
    shell = os.environ.get("SHELL") or DEFAULT_SHELL
    return [shell, "-l"]


class TerminalSession:
    """One shell process and the pty it is talking through."""

    def __init__(self, cwd: Path) -> None:
        self.master_fd, slave_fd = pty.openpty()
        env = dict(os.environ)
        # Without TERM the shell assumes a dumb terminal and emits no colour or
        # cursor control, which xterm.js renders as a wall of plain text.
        env.setdefault("TERM", "xterm-256color")
        self.proc = subprocess.Popen(  # noqa: S603 - the operator's own shell
            _shell_command(),
            cwd=str(cwd),
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            # Its own session and controlling terminal, so job control works and
            # a signal aimed at the shell does not reach the API server.
            start_new_session=True,
        )
        # The child owns the slave now; holding it open here would keep the
        # master readable forever after the shell exits, hiding the exit.
        os.close(slave_fd)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def read(self) -> bytes:
        """Whatever the shell has produced, or b"" when it is finished."""
        try:
            return os.read(self.master_fd, READ_CHUNK)
        except OSError:
            # The pty closes with the shell; that is an exit, not an error.
            return b""

    def write(self, data: str) -> None:
        try:
            os.write(self.master_fd, data.encode("utf-8"))
        except OSError:
            logger.debug("Write to a closed terminal ignored")

    def resize(self, rows: int, cols: int) -> None:
        """Tell the pty its new size.

        Without this the shell keeps its initial 80x24 and wraps lines where
        the browser does not, so a resized window shows corrupted output.
        """
        try:
            size = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, size)
        except OSError:
            logger.debug("Resize on a closed terminal ignored")

    def close(self) -> None:
        """End the shell and release the pty.

        SIGHUP rather than SIGKILL: it is what closing a terminal window sends,
        so the shell runs its own cleanup and its children hear about it.
        """
        if self.alive:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGHUP)
            except (OSError, ProcessLookupError):
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            os.close(self.master_fd)
        except OSError:
            pass
