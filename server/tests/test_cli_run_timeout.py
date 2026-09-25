"""Idle-vs-hard-cap timeout behavior of `executors.print_mode.run_print_mode`.

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
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.models.domain import RunStatus
from loregarden.services.run_errors import RunTimeoutKind
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
    """`db_session` is the DB the spawned run's process identity is recorded in;
    the fixture is what makes that write land somewhere."""
    return run_print_mode(
        invocation=_invocation(script),
        repo_root=Path.cwd(),
        timeout=timeout,
        streamer=_CollectingStreamer(),
        run_id="test-print-mode-run",
    )


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
    """A process that emits nothing is a hang: killed by the idle budget, not
    extended to the hard cap.

    Which budget fired is read off the raised exception. It used to be inferred
    from wall clock, and the two outcomes are only `timeout` and `timeout * 4`
    apart — three seconds at `timeout=1` — which process spawn, interpreter
    start and the reader loop's poll granularity eat under xdist. That test
    failed the pre-push suite at 3.46s, then again at 5.70s after the bound was
    widened once (lg-workflow-integrity-736). The reason is recorded now, so
    nothing here depends on how loaded the box is.
    """
    script = "import time\ntime.sleep(30)\n"
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        _run(db_session, script, timeout=1)

    assert excinfo.value.kind is RunTimeoutKind.IDLE


def test_chatty_runaway_is_bounded_by_the_hard_cap(db_session: Session):
    """A process that never stops printing never trips the idle budget, so only
    the absolute hard cap (timeout * 4 = 4s) stops it — and its partial output is
    preserved on the raised exception for the caller to keep."""
    script = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.write('x\\n'); sys.stdout.flush(); time.sleep(0.01)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        _run(db_session, script, timeout=1)

    assert excinfo.value.kind is RunTimeoutKind.HARD_CAP
    assert isinstance(excinfo.value.output, str)
    assert "x" in excinfo.value.output  # partial stdout carried on the exception
