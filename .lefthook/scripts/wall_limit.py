#!/usr/bin/env python3
"""Run a command, and stop it at a hard wall-clock limit.

    wall_limit.py --seconds N [--elapsed S] [--durations FILE] -- command…

server-tests.sh runs pytest under it, on a push and in land verification. No
hook-driven server test run may pass 30 minutes (the operator's ceiling,
2026-11-25, after pre-push suites held capacity for 1-4 hours each): past that
the run is not a verification, it is a stuck lease. At the limit the command's
whole process group (pytest and its xdist workers) gets SIGTERM, then SIGKILL
after `GRACE_SECONDS`, and this exits `EXIT_TIMED_OUT` saying how long the run
took and which files took longest so far and which tests were still running (from
pytest_file_durations.py).

`--elapsed` is the time the caller had already spent, so the limit covers the
whole hook run, not only this command. SIGINT and SIGTERM sent to this process
are passed to the group, so an interrupted push leaves no workers behind.
Stdlib only: it runs under whichever python3 the hook finds.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

EXIT_TIMED_OUT = 124
GRACE_SECONDS = 30
SLOWEST = 5


def _recorded(durations: Path | None) -> dict:
    if durations is None or not durations.exists():
        return {}
    return json.loads(durations.read_text(encoding="utf-8"))


def slowest_files(durations: Path | None, count: int = SLOWEST) -> list[tuple[str, float]]:
    """The files that took longest so far, longest first; empty when none were recorded."""
    totals = _recorded(durations).get("files", {})
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:count]


def still_running(
    durations: Path | None, now: float, count: int = SLOWEST
) -> list[tuple[str, float]]:
    """Tests started and not finished, longest-running first, with seconds so far."""
    running = _recorded(durations).get("running", {})
    ages = [(test, now - started) for test, started in running.items()]
    return sorted(ages, key=lambda item: (-item[1], item[0]))[:count]


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:  # silent-ok: the group already exited, which is what this asks for
        pass


def _stop(process: subprocess.Popen) -> None:
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        process.wait()


def _report(limit: int, elapsed: float, durations: Path | None) -> None:
    print(
        f"wall-limit: STOPPED after {elapsed / 60:.1f} min ({elapsed:.0f}s); "
        f"no hook test run may pass {limit}s (LOREGARDEN_TEST_WALL_LIMIT_SECONDS)",
        file=sys.stderr,
    )
    slowest = slowest_files(durations)
    if not slowest:
        print("wall-limit: no per-file times were recorded before it stopped", file=sys.stderr)
    for name, seconds in slowest:
        print(f"wall-limit:   {seconds:7.0f}s  {name}", file=sys.stderr)
    running = still_running(durations, time.time())
    if running:
        print("wall-limit: still running when it stopped:", file=sys.stderr)
    for test, seconds in running:
        print(f"wall-limit:   {seconds:7.0f}s  {test}", file=sys.stderr)


def run(command: list[str], limit: int, already: float, durations: Path | None) -> int:
    started = time.monotonic() - already
    process = subprocess.Popen(command, start_new_session=True)

    def forward(signum, _frame) -> None:
        _signal_group(process, signum)

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    try:
        return process.wait(timeout=max(0.0, limit - already))
    except subprocess.TimeoutExpired:
        _stop(process)
        _report(limit, time.monotonic() - started, durations)
        return EXIT_TIMED_OUT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", required=True, type=int)
    parser.add_argument("--elapsed", type=int, default=0)
    parser.add_argument("--durations", type=Path, default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.seconds < 1:
        parser.error("needs --seconds N (positive) and a command after --")
    return run(command, args.seconds, args.elapsed, args.durations)


if __name__ == "__main__":
    sys.exit(main())
