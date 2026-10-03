"""Run one child command on behalf of a wrapper that must clean up after it.

A wrapper (`loregarden capacity run`, `loregarden instance run`) holds something
for exactly as long as its command runs — a capacity lease, a registry record —
and must give it back however the command ends. `SignalRelay` makes sure a
SIGTERM or SIGHUP reaches the command instead of killing the wrapper first.
"""

from __future__ import annotations

import signal
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any


class Terminated(BaseException):
    """SIGTERM/SIGHUP arrived before the command started.

    A BaseException, like KeyboardInterrupt, so no `except Exception` on the way
    out swallows it, and the wrapper's `finally` still gives back what it holds.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class SignalRelay:
    """Who a SIGTERM, SIGHUP or SIGINT is for, across the whole run.

    Installed before the wrapper takes what it holds, so there is no moment a
    signal kills this process without giving it back. Before the command starts,
    SIGTERM and SIGHUP raise `Terminated` (SIGINT raises KeyboardInterrupt as
    usual), which unwinds through the release. Once it runs they are forwarded to it, and
    this process waits for it to exit so the release still happens. SIGINT is
    not forwarded: Ctrl-C reaches the whole foreground process group, so the
    command already has it, and a second copy reads to pytest as "force quit".
    """

    _FORWARDED = (signal.SIGTERM, signal.SIGHUP)

    def __init__(self) -> None:
        self._child: subprocess.Popen | None = None
        self._previous: dict[int, Any] = {}

    def __enter__(self) -> SignalRelay:
        for sig in (*self._FORWARDED, signal.SIGINT):
            self._previous[sig] = signal.getsignal(sig)
        for sig in self._FORWARDED:
            signal.signal(sig, self._on_signal)
        return self

    def __exit__(self, *_exc: object) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)

    def _on_signal(self, number: int, _frame: object) -> None:
        if self._child is None:
            raise Terminated(number)
        self._child.send_signal(number)

    def run(self, command: Sequence[str], env: Mapping[str, str]) -> int:
        signal.signal(signal.SIGINT, lambda _number, _frame: None)
        self._child = subprocess.Popen(list(command), env=dict(env))  # noqa: S603 — the caller's command
        code = self._child.wait()
        return 128 - code if code < 0 else code
