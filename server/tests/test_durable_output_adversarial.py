"""Adversarial cases for the wrapper and the file tailer (spec S2-S3).

`test_run_output_files.py` establishes the contract. This module attacks it:
the inputs an agent really produces that the happy-path cases do not contain,
and the chunk boundaries a hand-written tailer gets wrong.

Four of these would pass every test in that module while being wrong:

* an argv element containing a NEWLINE. `shlex.quote` survives it; a wrapper
  built with manual escaping, or one that joins on `" "` after stripping, does
  not — and the element that carries newlines is the stage prompt (AC3).
* an argv element that is the EMPTY STRING. Unquoted it vanishes, every later
  positional argument shifts down one, and the agent is invoked with a
  different command line than the one recorded (AC3).
* a `.rc` written BEFORE the agent exits. `resupervise` reads `.rc` as the
  answer to "how did this end"; a wrapper that creates the file early lets a
  reattaching server settle a LIVE run (AC12).
* a single line longer than whatever chunk size the tailer reads. A tailer
  that returns what it has when its buffer fills splits a `stream_event` in
  half, which is the one thing AC4 exists to prevent — and the largest `log`
  artifact in the live database is 320,619 bytes, so long lines are the norm
  rather than a corner.

The fuzz case is deterministic: a fixed seed, so a failure reproduces exactly.
"""

from __future__ import annotations

import os
import random
import signal
import subprocess
import time
from pathlib import Path

import pytest
from loregarden.config import settings
from loregarden.services.run_output_files import (
    RunOutputTail,
    ensure_dir,
    paths_for,
    wrap_for_files,
)

RUN_ID = "0f8c1a2b-3d4e-5f60-7182-93a4b5c6d7e8"
RUN_CODE = "run_adv001"


@pytest.fixture(name="run_log_dir")
def run_log_dir_fixture(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "run-logs"
    monkeypatch.setattr(settings, "run_log_dir", target)
    ensure_dir()
    return target


def _sh(wrapped: list[str], **kwargs) -> subprocess.CompletedProcess:
    assert wrapped[:2] == ["sh", "-c"], "the wrapper is /bin/sh, not bash"
    return subprocess.run(wrapped, capture_output=True, **kwargs)


# --- AC3: argv mutations ------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("newline", "first line\nsecond line"),
        ("crlf", "windows\r\nending"),
        ("tab", "before\tafter"),
        ("single quote", "it's quoted"),
        ("double quote", 'say "this"'),
        ("backslash", r"a\b\\c"),
        ("dollar", "$HOME ${PATH} $(id) `id`"),
        ("semicolons", "; rm -rf / ; echo pwned"),
        ("pipe and redirect", "a | b > c 2>&1 < d"),
        ("glob", "*.py ?? [a-z]"),
        ("newline plus redirect", "line one\n> /tmp/pwned\n"),
        ("leading dash", "--flag=value"),
        ("unicode", "reévalué — \U0001f600"),
        ("nul-adjacent control chars", "bell\a vtab\v formfeed\f"),
    ],
)
def test_an_argv_element_reaches_the_agent_verbatim(label, value, run_log_dir):
    """AC3. Every element is shlex-quoted, so none of these is interpreted.

    The existing happy-path case bundles its metacharacters into one string
    with no newline in it. The newline rows are the ones that matter: a stage
    prompt passed as an argv element carries them, and `>` on a line of its own
    is a redirect the moment the quoting is wrong.
    """
    paths = paths_for(RUN_CODE, RUN_ID)
    argv = ["/bin/sh", "-c", 'printf %s "$1"', "sh", value]

    done = _sh(wrap_for_files(argv, paths))

    assert done.returncode == 0, f"{label}: the wrapper would not even run"
    # Bytes, not `read_text()`: text mode translates a CRLF the agent wrote
    # into a bare newline, so the comparison would pass on a wrapper that
    # mangled exactly the row this parametrisation added.
    assert paths.out.read_bytes() == value.encode("utf-8"), (
        f"{label}: the agent received something else"
    )


def test_an_empty_argv_element_still_occupies_its_position(run_log_dir):
    """AC3. An unquoted `""` disappears and shifts every later argument down.

    The agent is then invoked with a command line that is not the one the run
    recorded, and the symptom is a flag silently taking the wrong value rather
    than anything failing.
    """
    paths = paths_for(RUN_CODE, RUN_ID)
    argv = ["/bin/sh", "-c", 'printf "%s|" "$@"', "sh", "first", "", "third"]

    done = _sh(wrap_for_files(argv, paths))

    assert done.returncode == 0
    assert paths.out.read_text() == "first||third|", "an argument was dropped or reordered"


def test_an_argv_element_made_only_of_spaces_survives(run_log_dir):
    paths = paths_for(RUN_CODE, RUN_ID)
    argv = ["/bin/sh", "-c", 'printf "[%s]" "$1"', "sh", "   "]

    _sh(wrap_for_files(argv, paths))

    assert paths.out.read_text() == "[   ]"


@pytest.mark.parametrize(
    ("label", "directory"),
    [
        ("space", "run logs here"),
        ("single quote", "operator's logs"),
        ("dollar", "logs $HOME dir"),
        ("backtick", "logs `id` dir"),
        ("semicolon", "logs;rm -rf dir"),
        ("newline", "logs\nwith newline"),
    ],
)
def test_a_hostile_run_log_dir_is_quoted_too(label, directory, tmp_path, monkeypatch):
    """AC3. `run_log_dir` is an operator-configurable setting, and the three
    redirect targets are interpolated into a shell string — so the paths are
    as much an injection surface as the argv is."""
    monkeypatch.setattr(settings, "run_log_dir", tmp_path / directory)
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)

    done = _sh(wrap_for_files(["/bin/sh", "-c", 'printf "ok\\n"; printf "e\\n" >&2'], paths))

    assert done.returncode == 0, f"{label}: the redirect was not quoted"
    assert paths.out.read_text() == "ok\n"
    assert paths.err.read_text() == "e\n"
    assert paths.rc.read_text() == "0"


# --- AC2: the exit status, past the happy path --------------------------------


@pytest.mark.parametrize("code", [0, 1, 2, 17, 42, 126, 127, 255])
def test_every_exit_code_round_trips_through_the_file_and_the_handle(code, run_log_dir):
    """AC2. Both answers must agree for every code, not just for 0 and 17.

    `_print_mode_result` reads one and `resupervise` reads the other; a wrapper
    that clamped, offset or truncated either would make the live path and the
    reattached path disagree about what the same run did.
    """
    paths = paths_for(RUN_CODE, RUN_ID)

    done = _sh(wrap_for_files(["/bin/sh", "-c", f"exit {code}"], paths))

    assert done.returncode == code
    assert paths.rc.read_text() == str(code)


def test_an_agent_killed_by_a_signal_records_the_shell_status_for_it(run_log_dir):
    """AC2/AC12. The wrapper outlives the agent, so a signalled agent still
    records an exit code — 128+N — and settles FAILED rather than falling to
    the absent-`.rc` sentence. That matters because the two cases have
    different operator meanings: "the agent crashed" and "we never found out".
    """
    paths = paths_for(RUN_CODE, RUN_ID)

    done = _sh(wrap_for_files(["/bin/sh", "-c", "kill -9 $$"], paths))

    assert paths.rc.read_text() == str(128 + signal.SIGKILL)
    assert done.returncode == 128 + signal.SIGKILL
    assert int(paths.rc.read_text()) != 0, "a killed agent must not read as success"


def test_the_exit_code_file_does_not_exist_while_the_agent_is_still_running(run_log_dir):
    """The invariant `resupervise` rests on, and nothing else asserts it.

    `.rc` is the answer to "how did this end". A wrapper that creates the file
    up front — `touch`, a `tee`, an `exec` that pre-opens all three — lets a
    boot-time reattach read an exit code for a run that is still working, and
    settle a LIVE run from it. The live-output half is asserted in the same
    test because it is the other half of the premise: output has to be readable
    BEFORE the run ends, or there is nothing to tail.
    """
    paths = paths_for(RUN_CODE, RUN_ID)
    wrapped = wrap_for_files(
        ["/bin/sh", "-c", 'printf "early\\n"; sleep 3; printf "late\\n"'], paths
    )

    proc = subprocess.Popen(
        wrapped, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not paths.out.exists():
            time.sleep(0.02)
        while time.monotonic() < deadline and paths.out.read_text() == "":
            time.sleep(0.02)

        assert paths.out.read_text() == "early\n", "nothing was readable mid-run"
        assert proc.poll() is None, "the agent exited before the assertion — widen the sleep"
        assert not paths.rc.exists(), "an exit code was recorded for a run still in flight"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_wrapper_is_the_session_leader_so_a_group_kill_reaches_the_agent(run_log_dir):
    """AC17's precondition, at the wrapper level.

    `run_detached_stop` has `os.getpgid(pid) == pid` as its second identity
    guard and then signals the group. The recorded pid is now the `sh`
    wrapper's, so the agent has to be inside that same group — otherwise the
    stop path kills the shell and leaves the agent running.
    """
    paths = paths_for(RUN_CODE, RUN_ID)
    wrapped = wrap_for_files(
        ["/bin/sh", "-c", 'printf "%s\\n" "$$"; sleep 30'],
        paths,
    )

    proc = subprocess.Popen(
        wrapped, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    try:
        assert os.getpgid(proc.pid) == proc.pid, "the recorded pid is not a session leader"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if paths.out.exists() and paths.out.read_text().strip():
                break
            time.sleep(0.02)
        assert paths.out.exists() and paths.out.read_text().strip(), (
            "the agent never reported its own pid"
        )
        agent_pid = int(paths.out.read_text().strip())

        assert os.getpgid(agent_pid) == proc.pid, (
            "the agent is in a different process group than the recorded pid, "
            "so killpg(recorded) would not reach it"
        )
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


# --- AC4: the tailer, attacked -------------------------------------------------


def test_a_single_line_larger_than_any_read_buffer_arrives_whole(run_log_dir):
    """AC4. The largest `log` artifact in the live database is 320,619 bytes,
    and a single `stream_event` carrying a tool result is a plausible megabyte.
    A tailer that emits when its buffer fills splits it."""
    path = run_log_dir / "huge.out"
    line = "x" * (3 * 1024 * 1024) + "\n"
    path.write_text(line)
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) == line
    assert tail.offset == len(line.encode("utf-8"))
    assert tail.readline(timeout=0) is None


def test_a_carriage_return_does_not_terminate_a_line(run_log_dir):
    """S3.1 says `\\n`-terminated, and a CLI agent's progress spinner redraws
    with bare `\\r`. Treating one as a terminator would emit a line the agent
    never finished — the same corruption as flushing a partial."""
    path = run_log_dir / "spinner.out"
    path.write_bytes(b"10%\r50%\r100%\rdone\n")
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) == "10%\r50%\r100%\rdone\n"
    assert tail.readline(timeout=0) is None


def test_the_tail_returns_immediately_when_a_line_is_already_available(run_log_dir):
    """The timeout is how long to wait for nothing, not a delay on every read.

    Sleeping first would put the resupervise interval between every two lines
    of a 1,667-line transcript, which looks like a hung run rather than a slow
    one.
    """
    path = run_log_dir / "ready.out"
    path.write_text("one\ntwo\nthree\n")
    tail = RunOutputTail(path)

    started = time.monotonic()
    assert tail.readline(timeout=5) == "one\n"
    assert tail.readline(timeout=5) == "two\n"
    assert time.monotonic() - started < 1.0, "the tailer slept even though data was ready"


def test_invalid_utf8_does_not_raise_and_does_not_stall_the_lines_after_it(run_log_dir):
    """A CLI agent writes whatever a tool wrote — truncated multi-byte
    sequences and raw terminal escapes included. A strict decode raises from
    inside the live loop, and the run dies mid-stream for a reason that has
    nothing to do with the agent.
    """
    path = run_log_dir / "mojibake.out"
    path.write_bytes(b"\xff\xfe broken bytes\nvalid after the damage\n")
    tail = RunOutputTail(path)

    first = tail.readline(timeout=0)

    assert first is not None and first.endswith("\n")
    assert "broken bytes" in first
    assert tail.readline(timeout=0) == "valid after the damage\n"


def test_a_nul_byte_inside_a_line_is_carried_rather_than_truncating_it(run_log_dir):
    path = run_log_dir / "nul.out"
    path.write_bytes(b"before\x00after\n")
    tail = RunOutputTail(path)

    line = tail.readline(timeout=0)

    assert line is not None and line.endswith("\n")
    assert "before" in line and "after" in line


def test_a_file_of_only_newlines_yields_one_empty_line_each(run_log_dir):
    """Blank lines are content: a stage report block is delimited by them, and
    a tailer that skipped them would change what the orchestrator parses."""
    path = run_log_dir / "blanks.out"
    path.write_text("\n\n\n")
    tail = RunOutputTail(path)

    assert [tail.readline(timeout=0) for _ in range(3)] == ["\n", "\n", "\n"]
    assert tail.readline(timeout=0) is None
    assert tail.offset == 3


def test_the_offset_covers_what_flush_partial_handed_out(run_log_dir):
    """Otherwise the bytes of the last line are read twice.

    `flush_partial` is only legal once the writer is gone, but the offset it
    leaves behind is persisted — and the orphan sweep, a post-mortem read, or
    a second reattach of a run whose row was never settled all start from it.
    """
    path = run_log_dir / "flushed.out"
    path.write_text("terminated\nunterminated tail")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "terminated\n"
    assert tail.readline(timeout=0) is None

    assert tail.flush_partial() == "unterminated tail\n"
    assert tail.offset == path.stat().st_size, (
        "the offset did not advance over the flushed remainder"
    )


def test_flush_partial_on_a_file_that_never_appeared_returns_nothing(run_log_dir):
    """The final drain runs even for a run that wrote nothing at all."""
    assert RunOutputTail(run_log_dir / "never-written.out").flush_partial() is None


def test_a_file_truncated_under_the_tailer_does_not_raise_or_emit_half_a_line(run_log_dir):
    """The orphan sweep deletes these files, and a `run_code` collision can
    recreate one shorter than the offset already consumed. Whatever the tailer
    does next, it may not raise on the live loop's path and may not emit
    something that was never a complete line."""
    path = run_log_dir / "truncated.out"
    path.write_text("alpha\nbeta\n")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "alpha\n"
    assert tail.readline(timeout=0) == "beta\n"

    path.write_text("z\n")

    for _ in range(3):
        line = tail.readline(timeout=0)
        assert line is None or line.endswith("\n")


def test_a_tail_over_a_real_subprocess_writer_never_splits_a_line(run_log_dir):
    """The bytewise test writes from this process, which hides the one thing a
    real run has: the OS deciding when a write becomes visible. 300 lines from
    a separate process, read while it writes."""
    path = run_log_dir / "subprocess.out"
    writer = subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            f'i=1; while [ "$i" -le 300 ]; do printf "line %s\\n" "$i"; i=$((i+1)); done '
            f"> {path!s}",
        ]
    )
    tail = RunOutputTail(path)
    collected: list[str] = []
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            exited = writer.poll() is not None
            line = tail.readline(timeout=0)
            if line is None:
                if exited:
                    break
                continue
            collected.append(line)
    finally:
        writer.wait(timeout=10)
    while (line := tail.readline(timeout=0)) is not None:
        collected.append(line)

    assert all(entry.endswith("\n") for entry in collected)
    assert collected == [f"line {index}\n" for index in range(1, 301)]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_randomised_append_boundaries_never_corrupt_the_stream(seed, run_log_dir):
    """AC4 as a fuzz, with a fixed seed per case so a failure reproduces.

    The named bytewise test covers the worst chunking. This covers the rest of
    the space — appends of 1..37 bytes, so a line boundary falls mid-chunk, at
    a chunk edge, and across several chunks — and asserts the two properties
    that matter together: every emitted line is terminated, and the
    concatenation of everything emitted equals the file byte for byte. Either
    alone can pass while the stream is wrong.
    """
    rng = random.Random(seed)
    alphabet = "abc \t{}\"':,.0123456789"
    source = "".join(
        "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120))) + "\n" for _ in range(120)
    )
    path = run_log_dir / f"fuzz-{seed}.out"
    path.write_bytes(b"")
    tail = RunOutputTail(path)
    emitted: list[str] = []

    data = source.encode("utf-8")
    cursor = 0
    with path.open("ab", buffering=0) as handle:
        while cursor < len(data):
            step = rng.randint(1, 37)
            handle.write(data[cursor : cursor + step])
            cursor += step
            while (line := tail.readline(timeout=0)) is not None:
                emitted.append(line)

    assert all(line.endswith("\n") for line in emitted), "a partial line was emitted"
    assert "".join(emitted) == source, "the stream was reordered, dropped or duplicated"
    assert tail.offset == len(data)
    assert tail.flush_partial() is None


def test_two_tailers_on_one_file_each_see_the_whole_stream(run_log_dir):
    """A restarted server can overlap the original's tailer for a beat, and the
    orphan sweep reads these files too. Reading must not consume."""
    path = run_log_dir / "shared.out"
    path.write_text("one\ntwo\n")
    first, second = RunOutputTail(path), RunOutputTail(path)

    assert first.readline(timeout=0) == "one\n"
    assert second.readline(timeout=0) == "one\n"
    assert first.readline(timeout=0) == "two\n"
    assert second.readline(timeout=0) == "two\n"
