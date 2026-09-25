"""Run a CLI agent in print mode: spawn it, stream its lines, hold two deadlines.

Extracted from `CliAgentExecutor`, which had grown past the organization gate's
class size cap. The seam is natural: nothing here reads executor state. It is a
subprocess, a line reader and two clocks, and the caller supplies everything
else.

The two clocks are the point. `timeout` is an *idle* budget — the longest the
agent may go producing no output before it is presumed hung — and as long as it
keeps streaming it may run to an absolute ceiling of
`timeout * TIMEOUT_HARD_CAP_MULTIPLIER`. A long-but-progressing test run is not
killed mid-progress; a chatty runaway is still bounded. Which one fired is
recorded on the raised `RunTimeout`, because "it said nothing" and "it would not
stop" are opposite failures that read identically as elapsed seconds.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.cli_adapters import invocation_env
from loregarden.agents.executors.launch_gate import MAX_HOLD_SECONDS, acquire_launch_slot
from loregarden.models.domain import RunStatus
from loregarden.services.process_identity import record_process_identity
from loregarden.services.run_cancellation import cancel_requested
from loregarden.services.run_errors import (
    TIMEOUT_HARD_CAP_MULTIPLIER,
    RunTimeout,
    RunTimeoutKind,
)
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.subprocess_lines import SubprocessLineReader


def _spawn_print_process(invocation, repo_root: Path):
    """Open the CLI subprocess in its own session, and feed it any stdin prompt.

    `start_new_session` is what detaches it. Without it the agent is in this
    process's group, so a Ctrl-C, a reload, or anything else that signals the
    group takes a turn that may be minutes in — and backend edits *require* a
    reload to be picked up, so that happens by design rather than by accident.

    Detaching alone does not make the run recoverable; 470 is what reattaches
    to it. What this owes 470 is a pid it can trust, which is why the caller
    records an identity alongside the number.
    """
    proc = subprocess.Popen(
        invocation.argv,
        cwd=invocation.cwd or str(repo_root),
        env=invocation_env(invocation),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if invocation.stdin_prompt else None,
        bufsize=0,
        start_new_session=True,
    )
    if invocation.stdin_prompt and proc.stdin:
        proc.stdin.write(invocation.stdin_prompt.encode("utf-8"))
        proc.stdin.close()
    return proc


def _record_print_line(
    line: str,
    *,
    stdout_lines: list[str],
    launch_slot,
    streamer: RunLogStreamer,
) -> None:
    line = line.rstrip("\n")
    stdout_lines.append(line)
    # Output proves this process is past its credential read, so a
    # sibling lane may start authenticating now.
    launch_slot.release()
    streamer.append_stream_line(line)


def _drain_print_stdout(
    reader: SubprocessLineReader,
    *,
    stdout_lines: list[str],
    launch_slot,
    streamer: RunLogStreamer,
) -> None:
    """Empty the pipe after exit so trailing stage-report lines are not lost."""
    while True:
        leftover = reader.readline(timeout=0)
        if leftover is None:
            return
        _record_print_line(
            leftover,
            stdout_lines=stdout_lines,
            launch_slot=launch_slot,
            streamer=streamer,
        )


@dataclass
class _Budgets:
    """The two clocks a print-mode run is held to.

    Kept together because they are only meaningful against each other: the idle
    deadline moves every time the agent says something, the hard deadline never
    moves, and which of them ran out is the difference between a hang and a
    runaway.
    """

    timeout: int
    start: float
    idle_deadline: float
    hard_deadline: float

    @classmethod
    def starting_now(cls, timeout: int) -> _Budgets:
        now = time.time()
        return cls(
            timeout=timeout,
            start=now,
            idle_deadline=now + timeout,
            hard_deadline=now + timeout * TIMEOUT_HARD_CAP_MULTIPLIER,
        )

    def expired(self, now: float) -> RunTimeoutKind | None:
        """Which budget has run out, or None while both still hold."""
        if now >= self.idle_deadline:
            return RunTimeoutKind.IDLE
        if now >= self.hard_deadline:
            return RunTimeoutKind.HARD_CAP
        return None

    def saw_output(self) -> None:
        """Output is progress: extend the idle budget. The hard cap never moves."""
        self.idle_deadline = time.time() + self.timeout


def run_print_mode(
    *,
    invocation,
    repo_root: Path,
    timeout: int,
    streamer: RunLogStreamer,
    run_id: str,
) -> tuple[str, str, RunStatus]:
    launch_slot = acquire_launch_slot(invocation.adapter)
    try:
        proc = _spawn_print_process(invocation, repo_root)
    except BaseException:
        launch_slot.release()
        raise

    # Recorded together, and immediately: the identity is the process start
    # time, so it has to be read while this pid is still certainly ours. A
    # pid stored without one is a number a later process can wear.
    record_process_identity(run_id, proc.pid)

    stdout_lines: list[str] = []
    assert proc.stdout is not None
    reader = SubprocessLineReader(proc.stdout)
    budgets = _Budgets.starting_now(timeout)
    cancelled = False
    try:
        while True:
            now = time.time()
            expired = budgets.expired(now)
            if expired is not None:
                proc.kill()
                raise _timeout_expired(invocation.argv, budgets.start, stdout_lines, kind=expired)
            if cancel_requested(run_id):
                proc.kill()
                cancelled = True
                break
            exited = proc.poll() is not None
            # After exit, keep draining with a short poll so the last
            # buffered lines (e.g. a stage-report block) are not dropped
            # by a timeout=0 select race against the closing pipe.
            line = reader.readline(timeout=0.05 if exited else 0.5)
            if line is None:
                if exited:
                    _drain_print_stdout(
                        reader,
                        stdout_lines=stdout_lines,
                        launch_slot=launch_slot,
                        streamer=streamer,
                    )
                    break
                if now - budgets.start >= MAX_HOLD_SECONDS:
                    launch_slot.release()
                continue
            _record_print_line(
                line,
                stdout_lines=stdout_lines,
                launch_slot=launch_slot,
                streamer=streamer,
            )
            budgets.saw_output()
    finally:
        launch_slot.release()
        _reap(
            proc,
            budgets=budgets,
            argv=invocation.argv,
            stdout_lines=stdout_lines,
            cancelled=cancelled,
        )

    return _print_mode_result(proc, stdout_lines=stdout_lines, cancelled=cancelled)


def _reap(
    proc,
    *,
    budgets: _Budgets,
    argv,
    stdout_lines: list[str],
    cancelled: bool,
) -> None:
    """Make sure the process is gone before the caller reads its pipes.

    Reached on every exit from the loop, including the one that is already
    raising. A process still running here has outlived the hard cap, so it is
    killed and — unless the operator is the one who stopped it — reported as a
    hard-cap timeout rather than left to look like a clean exit.
    """
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=max(0.1, budgets.hard_deadline - time.time()))
    except subprocess.TimeoutExpired:
        proc.kill()
        if not cancelled:
            raise _timeout_expired(
                argv, budgets.start, stdout_lines, kind=RunTimeoutKind.HARD_CAP
            ) from None


def _print_mode_result(
    proc,
    *,
    stdout_lines: list[str],
    cancelled: bool,
) -> tuple[str, str, RunStatus]:
    """The finished run as the caller wants it: stdout, stderr and a status."""
    # Read before the cancelled branch: the pipe is drained either way, as it
    # was when this lived inline.
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    stdout = "\n".join(stdout_lines)
    if cancelled:
        return stdout, "Cancelled by operator", RunStatus.CANCELLED
    status = RunStatus.SUCCEEDED if proc.returncode == 0 else RunStatus.FAILED
    return stdout, stderr, status


def _timeout_expired(
    argv,
    start: float,
    stdout_lines: list[str],
    *,
    kind: RunTimeoutKind,
) -> RunTimeout:
    """A timeout carrying the real elapsed time, which budget fired, and
    whatever the agent streamed before it was killed, so the caller can
    report an accurate duration and preserve the partial output."""
    return RunTimeout(
        argv,
        int(time.time() - start),
        kind=kind,
        output="\n".join(stdout_lines),
    )
