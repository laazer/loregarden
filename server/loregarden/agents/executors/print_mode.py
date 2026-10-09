"""Supervise a CLI agent in print mode: tail its output file, hold two deadlines.

The agent is no longer this process's pipe. `agent_spawn` starts an `sh -c`
wrapper that redirects the agent's own stdout, stderr and exit code into three
files; this module tails `.out`, holds the budgets, and shapes the result. The
output therefore outlives any reader — which is what lets the process that
replaces this one after a restart pick the same run up (`run_resupervise`).

Two things here break quietly if they are got wrong:

* **The loop's terminator.** EOF on a regular file is the *normal* state of a
  file being appended to, so a loop ending on `readline() is None` truncates
  the transcript — including the `<<<LOREGARDEN_STAGE_REPORT>>>` block the
  orchestrator routes on. It may only end on the wrapper being gone, after
  which it drains and flushes the unterminated remainder once.
* **The kill.** `proc.kill()` now reaches the `sh` wrapper, not the agent, and
  killing a shell does not kill what it is waiting on. A cancel or a hard cap
  that killed only the wrapper would leave the agent running, detached, still
  appending to `.out` and still holding the worktree the orchestrator is about
  to commit — while the row reads CANCELLED. So both branches signal the
  process *group*, which the wrapper leads.

The two clocks are otherwise unchanged. `timeout` is an *idle* budget — the
longest the agent may go producing no output before it is presumed hung — and
as long as it keeps streaming it may run to an absolute ceiling of
`timeout * TIMEOUT_HARD_CAP_MULTIPLIER`. Which one fired is recorded on the
raised `RunTimeout`, because "it said nothing" and "it would not stop" are
opposite failures that read identically as elapsed seconds.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.executors.agent_spawn import (
    SpawnedAgent,
    record_agent_transport,
    resolve_transport,
    spawn_agent,
    transport_line,
)
from loregarden.agents.executors.launch_gate import MAX_HOLD_SECONDS, acquire_launch_slot
from loregarden.config import settings
from loregarden.dot_line import SYS
from loregarden.models.domain import RunStatus
from loregarden.services.process_identity import record_process_identity
from loregarden.services.run_cancellation import cancel_requested
from loregarden.services.run_errors import (
    TIMEOUT_HARD_CAP_MULTIPLIER,
    RunTimeout,
    RunTimeoutKind,
)
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.run_output_files import (
    RunOutputPaths,
    RunOutputTail,
    paths_for,
    read_output_text,
    recorded_exit_status,
    run_file_stem,
)

logger = logging.getLogger(__name__)


def _record_print_line(
    line: str,
    *,
    stdout_lines: list[str],
    launch_slot,
    streamer: RunLogStreamer,
    tail: RunOutputTail,
) -> None:
    line = line.rstrip("\n")
    stdout_lines.append(line)
    # Output proves this process is past its credential read, so a
    # sibling lane may start authenticating now.
    launch_slot.release()
    # Set before the append, not after: `append_stream_line` can itself trigger
    # a persist, and the offset written in that transaction has to be the one
    # just PAST the row it is being written with. Set afterwards it lags by a
    # line, and the next restart re-ingests that line.
    streamer.tail_offset = tail.offset
    streamer.append_stream_line(line)


def _drain_print_stdout(
    tail: RunOutputTail,
    *,
    stdout_lines: list[str],
    launch_slot,
    streamer: RunLogStreamer,
) -> None:
    """Empty the file after exit, then recover any unterminated last line.

    `flush_partial` is legal here and only here: the wrapper has exited, so the
    file is complete and nothing is still writing. An agent killed mid-line, or
    one that simply never printed its newline, has that line as the only record
    of what it was doing.
    """
    while True:
        leftover = tail.readline(timeout=0)
        if leftover is None:
            break
        _record_print_line(
            leftover,
            stdout_lines=stdout_lines,
            launch_slot=launch_slot,
            streamer=streamer,
            tail=tail,
        )
    remainder = tail.flush_partial()
    if remainder is not None:
        _record_print_line(
            remainder,
            stdout_lines=stdout_lines,
            launch_slot=launch_slot,
            streamer=streamer,
            tail=tail,
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


def _wrapper_exited(spawned: SpawnedAgent) -> bool:
    """Whether the wrapper is gone, on either transport.

    The FILE transport's wrapper is this process's child, so `poll()` is both
    the test and the reap — `os.kill(pid, 0)` would succeed on a zombie and read
    a finished run as a live one. A tmux pane is not our child, so the pane's
    pid is asked directly and tmux does the reaping.
    """
    if spawned.handle is not None:
        return spawned.handle.poll() is not None
    try:
        os.kill(spawned.pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # A live process holds the pid and it is no longer ours to signal. Not a
        # death — reading it as one would settle a working run — but it should
        # not happen to a wrapper this process started, so it is reported rather
        # than absorbed into "still running".
        logger.warning(
            "pid %s is alive but no longer ours to signal; treating the run as still in "
            "flight, which will end at its hard cap if the pid was reused",
            spawned.pid,
        )
        return False
    return False


def _kill_agent_group(spawned: SpawnedAgent) -> None:
    """Signal the whole process group the wrapper leads.

    Not `handle.kill()`: that reaches the `sh` wrapper, and killing a shell does
    not kill what it is waiting on. The agent is a child of the wrapper, in the
    wrapper's group, so this is what reaches it — the same identity the detached
    stop path rests on.
    """
    try:
        os.killpg(os.getpgid(spawned.pid), signal.SIGKILL)
    except ProcessLookupError:
        return  # silent-ok: the group is already gone, which is the outcome asked for
    except OSError:
        logger.warning(
            "Could not signal the process group of pid %s; the agent may still be running",
            spawned.pid,
            exc_info=True,
        )
    if spawned.handle is not None:
        # Reap our own child so it does not linger as a zombie.
        try:
            spawned.handle.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("pid %s did not exit after a group SIGKILL", spawned.pid)


def run_print_mode(
    *,
    invocation,
    repo_root: Path,
    timeout: int,
    streamer: RunLogStreamer,
    run_id: str,
    run_code: str,
) -> tuple[str, str, RunStatus]:
    """Run `invocation` detached and return its stdout, stderr and status.

    `run_code` is taken explicitly rather than read off the streamer: the file
    stem is collision-critical — a shared stem lets a reattaching server settle
    a live run from a dead one's `.rc` — so the key's provenance belongs in the
    signature rather than in a collaborator's mutable attribute.
    """
    paths = paths_for(run_code, run_id)
    launch_slot = acquire_launch_slot(invocation.adapter)
    try:
        spawned = spawn_agent(
            invocation,
            repo_root,
            run_id=run_id,
            run_code=run_code,
            transport=resolve_transport(settings.agent_detach_transport),
        )
    except BaseException:
        launch_slot.release()
        raise

    # Recorded together, and immediately: the identity is the process start
    # time, so it has to be read while this pid is still certainly ours. A
    # pid stored without one is a number a later process can wear.
    record_process_identity(run_id, spawned.pid)
    record_agent_transport(run_id, spawned.transport_used)
    # Announced from `transport_used` rather than from a second reading of the
    # host, and AFTER the spawn rather than before it: a tmux spawn that raises
    # must not leave a line in the feed naming a session that never existed.
    streamer.append(
        SYS.name,
        transport_line(spawned.transport_used, run_file_stem(run_code, run_id)),
        force=True,
    )

    stdout_lines: list[str] = []
    tail = RunOutputTail(paths.out)
    budgets = _Budgets.starting_now(timeout)
    cancelled = False
    try:
        while True:
            now = time.time()
            expired = budgets.expired(now)
            if expired is not None:
                _kill_agent_group(spawned)
                raise _timeout_expired(invocation.argv, budgets.start, stdout_lines, kind=expired)
            if cancel_requested(run_id):
                _kill_agent_group(spawned)
                cancelled = True
                break
            exited = _wrapper_exited(spawned)
            # A short poll after exit: the file is complete, so there is nothing
            # to wait for, only the rest of it to read.
            line = tail.readline(timeout=0 if exited else 0.5)
            if line is None:
                # EOF is NOT the end. Only the wrapper being gone is, and the
                # drain below is what keeps the last lines of the transcript.
                if exited:
                    _drain_print_stdout(
                        tail,
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
                tail=tail,
            )
            budgets.saw_output()
    finally:
        launch_slot.release()
        _reap(
            spawned,
            budgets=budgets,
            argv=invocation.argv,
            stdout_lines=stdout_lines,
            cancelled=cancelled,
        )

    if cancelled:
        # The partial transcript is the only record of what a cancelled run did,
        # and the group is gone by now, so this drain cannot race a writer.
        _drain_print_stdout(
            tail,
            stdout_lines=stdout_lines,
            launch_slot=launch_slot,
            streamer=streamer,
        )

    return _print_mode_result(paths, stdout_lines=stdout_lines, cancelled=cancelled)


def _reap(
    spawned: SpawnedAgent,
    *,
    budgets: _Budgets,
    argv,
    stdout_lines: list[str],
    cancelled: bool,
) -> None:
    """Make sure the agent is gone before the caller reads its files.

    Reached on every exit from the loop, including the one that is already
    raising. A process still running here has outlived the hard cap, so its
    group is killed and — unless the operator is the one who stopped it — it is
    reported as a hard-cap timeout rather than left to look like a clean exit.
    """
    if _wrapper_exited(spawned):
        return
    if spawned.handle is not None:
        try:
            spawned.handle.wait(timeout=max(0.1, budgets.hard_deadline - time.time()))
            return
        except subprocess.TimeoutExpired:
            pass
    _kill_agent_group(spawned)
    if not cancelled:
        raise _timeout_expired(
            argv, budgets.start, stdout_lines, kind=RunTimeoutKind.HARD_CAP
        ) from None


def _print_mode_result(
    paths: RunOutputPaths,
    *,
    stdout_lines: list[str],
    cancelled: bool,
) -> tuple[str, str, RunStatus]:
    """The finished run as the caller wants it: stdout, stderr and a status.

    stderr comes from `.err` — `proc.stderr` is DEVNULL now, and merging the two
    streams would silently change what `run_completion.failure_reason()` sees.
    The status comes from `.rc`, because that is also what the reattached path
    reads and the two must not disagree about what one run did.
    """
    stderr = read_output_text(paths.err)
    stdout = "\n".join(stdout_lines)
    if cancelled:
        return stdout, "Cancelled by operator", RunStatus.CANCELLED
    status = RunStatus.SUCCEEDED if recorded_exit_status(paths.rc) == 0 else RunStatus.FAILED
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
