"""Idle-vs-hard-cap timeout behavior of `executors.print_mode.run_print_mode`.

A run's configured timeout is treated as an *idle* budget: a process that keeps
streaming output survives past it, up to an absolute hard cap
(`timeout * _TIMEOUT_HARD_CAP_MULTIPLIER`), so a long-but-progressing test run is
no longer killed mid-progress. A silent process is still killed at the idle
budget, exactly as the old fixed wall-clock deadline was — no regression for a
genuine hang.

Every budget here leaves seconds of slack between output and the idle deadline.
At `timeout=1` the chatty test failed the pre-push suite with IDLE at a load
average of ~30: the 10 ms writer was descheduled for over a second, which is
scheduling jitter on a loaded box, not silence (lg-build-verification-1066).
"""

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.agents.executors import print_mode
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.config import settings
from loregarden.models.domain import RunStatus
from loregarden.services.run_errors import RunTimeoutKind
from sqlmodel import Session


class _CollectingStreamer:
    def __init__(self) -> None:
        self.lines: list[str] = []
        #: Set by `run_print_mode` after every appended line (spec S4/S5).
        self.tail_offset = 0
        #: (tag, text, force) for every `append` — the SYS transport line.
        self.tagged: list[tuple[str, str, bool]] = []

    def append_stream_line(self, line: str) -> None:
        self.lines.append(line)

    def append(self, tag: str, text: str, *, force: bool = False) -> None:
        """The tagged-line sink `RunLogStreamer` also exposes.

        `run_print_mode` announces the transport it spawned on through this,
        beside `record_agent_transport`. Collected separately from the stream
        lines so this module's assertions still see only the agent's output.
        """
        self.tagged.append((tag, text, force))


@pytest.fixture(name="run_log_dir", autouse=True)
def run_log_dir_fixture(tmp_path, monkeypatch):
    """The agent writes its own output files now; keep them out of the repo."""
    monkeypatch.setattr(settings, "run_log_dir", tmp_path / "run-logs")
    return tmp_path / "run-logs"


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
        # Per call, not per module: `run_file_stem` is deterministic, so a
        # fixed pair gives every spawning test in this module one tmux session
        # name — and the second `tmux new-session -d -s <existing>` exits 1.
        # Under `pytest -n`, which is how this repo runs its suite, that is a
        # different failing subset every run.
        run_id=f"test-print-mode-run-{uuid4().hex[:8]}",
        run_code=f"run_{uuid4().hex[:6]}",
    )


def test_streaming_run_survives_past_the_idle_budget(db_session: Session):
    """Twenty lines, one every 0.2s (~4s total): each resets the 3s idle budget,
    and the total stays under the 12s hard cap, so the run completes instead of
    being killed at 3s the way the old fixed deadline would have. A stall short
    of 2.8s between two lines cannot trip the idle budget."""
    script = (
        "import time\nfor i in range(20):\n    print('tick', i, flush=True)\n    time.sleep(0.2)\n"
    )
    start = time.time()
    stdout, _stderr, status = _run(db_session, script, timeout=3)
    elapsed = time.time() - start

    assert status == RunStatus.SUCCEEDED
    assert "tick 19" in stdout
    assert elapsed > 3.0  # ran past the idle budget without being killed


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
    the absolute hard cap stops it — and its partial output is preserved on the
    raised exception for the caller to keep.

    A 4s idle budget, so only a stall of 4s trips it, and a 1.5x cap (6s) so the
    test does not wait out the production multiplier's 16s.
    """
    script = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.write('x\\n'); sys.stdout.flush(); time.sleep(0.01)\n"
    )
    with (
        mock.patch.object(print_mode, "TIMEOUT_HARD_CAP_MULTIPLIER", 1.5),
        pytest.raises(subprocess.TimeoutExpired) as excinfo,
    ):
        _run(db_session, script, timeout=4)

    assert excinfo.value.kind is RunTimeoutKind.HARD_CAP
    assert isinstance(excinfo.value.output, str)
    assert "x" in excinfo.value.output  # partial stdout carried on the exception
