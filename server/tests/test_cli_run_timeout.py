"""Idle-vs-hard-cap timeout behavior of `CliAgentExecutor._run_print_mode`.

A run's configured timeout is treated as an *idle* budget: a process that keeps
streaming output survives past it, up to an absolute hard cap
(`timeout * _TIMEOUT_HARD_CAP_MULTIPLIER`), so a long-but-progressing test run is
no longer killed mid-progress. A silent process is still killed at the idle
budget, exactly as the old fixed wall-clock deadline was — no regression for a
genuine hang.
"""

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import RunStatus
from sqlmodel import Session


class _CollectingStreamer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_stream_line(self, line: str) -> None:
        self.lines.append(line)


def _invocation(script: str) -> SimpleNamespace:
    return SimpleNamespace(
        argv=[sys.executable, "-u", "-c", script],
        cwd=None,
        stdin_prompt=None,
        interactive=False,
        adapter="local",
        env={},
    )


def _run(db_session: Session, script: str, timeout: int):
    executor = CliAgentExecutor(db_session)
    return executor._run_print_mode(
        invocation=_invocation(script),
        repo_root=Path.cwd(),
        timeout=timeout,
        streamer=_CollectingStreamer(),
        run_id="test-print-mode-run",
    )


def _spawn_overhead(db_session: Session) -> float:
    """What a run of this shape costs before any budget is involved.

    The timeout assertions below distinguish "killed at the idle budget" from
    "ran to the hard cap", and those are only `timeout` and `timeout * 4`
    apart — three seconds at `timeout=1`. Process spawn, interpreter start and
    the reader loop's poll granularity all sit on top of that, and under xdist
    they are not small: this test failed pre-push at 3.46s against a fixed
    `< 3`, where 3.46s is genuinely ambiguous between the two outcomes.

    So the margin is measured rather than guessed. A trivial run pays the same
    fixed cost as a killed one, on the same machine under the same load.
    """
    start = time.time()
    _run(db_session, "pass", timeout=1)
    return time.time() - start


def test_streaming_run_survives_past_the_idle_budget(db_session: Session):
    """Eight lines, one every 0.2s (~1.6s total): each resets the 1s idle budget,
    and the total stays under the 4s hard cap, so the run completes instead of
    being killed at 1s the way the old fixed deadline would have."""
    script = (
        "import time\nfor i in range(8):\n    print('tick', i, flush=True)\n    time.sleep(0.2)\n"
    )
    start = time.time()
    stdout, _stderr, status = _run(db_session, script, timeout=1)
    elapsed = time.time() - start

    assert status == RunStatus.SUCCEEDED
    assert "tick 7" in stdout
    assert elapsed > 1.0  # ran well past the idle budget without being killed


def test_silent_run_is_killed_at_the_idle_budget(db_session: Session):
    """A process that emits nothing is a hang: killed at the idle budget (~1s),
    not extended to the hard cap."""
    overhead = _spawn_overhead(db_session)

    script = "import time\ntime.sleep(30)\n"
    start = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        _run(db_session, script, timeout=1)
    elapsed = time.time() - start

    # Killed at the 1s idle budget lands near `overhead + 1`; running to the 4s
    # hard cap lands near `overhead + 4`. Halfway between separates them at any
    # load, where a fixed threshold only separated them on an idle machine.
    assert elapsed < overhead + 2.5, f"elapsed {elapsed:.2f}s, spawn overhead {overhead:.2f}s"


def test_chatty_runaway_is_bounded_by_the_hard_cap(db_session: Session):
    """A process that never stops printing never trips the idle budget, so only
    the absolute hard cap (timeout * 4 = 4s) stops it — and its partial output is
    preserved on the raised exception for the caller to keep."""
    script = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.write('x\\n'); sys.stdout.flush(); time.sleep(0.01)\n"
    )
    start = time.time()
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        _run(db_session, script, timeout=1)
    elapsed = time.time() - start

    assert 3.5 < elapsed < 8  # bounded near the hard cap, not running forever
    assert isinstance(excinfo.value.output, str)
    assert "x" in excinfo.value.output  # partial stdout carried on the exception
