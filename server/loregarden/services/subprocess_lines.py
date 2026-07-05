"""Read subprocess stdout incrementally without block buffering delays."""

from __future__ import annotations

import os
import select


class SubprocessLineReader:
    """Accumulate bytes from a pipe and emit complete newline-delimited lines."""

    def __init__(self, stdout, *, encoding: str = "utf-8") -> None:
        self._stdout = stdout
        self._encoding = encoding
        self._buffer = ""
        self._use_fileno = hasattr(stdout, "fileno")

    def readline(self, *, timeout: float = 1.0) -> str | None:
        if "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            return line + "\n"

        if not self._use_fileno:
            line = self._stdout.readline()
            if not line:
                return None
            return line if line.endswith("\n") else line + "\n"

        fileno = self._stdout.fileno()
        ready, _, _ = select.select([fileno], [], [], timeout)
        if not ready:
            return None

        chunk = os.read(fileno, 8192)
        if not chunk:
            if self._buffer:
                rest, self._buffer = self._buffer, ""
                return rest + "\n"
            return None

        self._buffer += chunk.decode(self._encoding, errors="replace")
        if "\n" not in self._buffer:
            return None

        line, self._buffer = self._buffer.split("\n", 1)
        return line + "\n"
