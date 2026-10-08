"""Per-run output files, the `sh -c` wrapper, and the file tailer (spec S1-S3).

The server stops holding the agent's pipe. The agent writes its own stdout,
stderr and exit code to three files under `settings.run_log_dir`, and the
control plane tails them — which is what lets a *different* process pick the
run up after a restart.

Three things in here are the whole ticket's load-bearing details:

* the wrapper must end `exit "$rc"`. Without it `sh` exits with *printf's*
  status, which is 0 whenever the write succeeded, and `_print_mode_result`
  maps every print-mode run — including a crashed one — to SUCCEEDED (AC2).
* the file stem carries a run-id suffix. `run_code` is
  `"run_" + secrets.token_hex(3)` with no uniqueness constraint, and the `.rc`
  file is what decides a reattached run's status: a collision settles a LIVE
  run from a DEAD one's exit code (AC22).
* `readline() is None` must never mean "the writer finished". On a regular file
  EOF is the normal state, and a tailer that flushes a buffered partial as a
  terminated line corrupts exactly the `stream_event` partials AC7 protects
  (AC4).
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from loregarden.config import settings
from loregarden.models.domain import AgentRun, AgentTransport, RunStatus
from loregarden.services.run_output_files import (
    RunOutputPaths,
    RunOutputTail,
    attach_command,
    ensure_dir,
    paths_for,
    run_file_stem,
    tmux_session_name,
    wrap_for_files,
)

RUN_ID = "0f8c1a2b-3d4e-5f60-7182-93a4b5c6d7e8"
RUN_CODE = "run_abc123"


@pytest.fixture(name="run_log_dir")
def run_log_dir_fixture(tmp_path, monkeypatch) -> Path:
    """Point `run_log_dir` at a throwaway directory for the whole test."""
    target = tmp_path / "run-logs"
    monkeypatch.setattr(settings, "run_log_dir", target)
    return target


# --- S1: the stem, the paths, the session name, the attach command ----------


def test_the_run_log_dir_setting_resolves_against_the_repo_root():
    """AC21. Beside the other data dirs, not relative to whatever cwd a run had."""
    assert Path(settings.run_log_dir).is_absolute()
    assert Path(settings.run_log_dir).name == "run-logs"
    assert Path(settings.run_log_dir).parent == Path(settings.repo_root) / "data"


def test_the_file_stem_carries_a_run_id_suffix():
    """AC22. `run_code` is not unique; the stem has to be."""
    assert run_file_stem(RUN_CODE, RUN_ID) == f"{RUN_CODE}-{RUN_ID[:8]}"


def test_two_runs_sharing_a_run_code_do_not_share_a_stem():
    """The failure this prevents is not a mixed log.

    `.rc` decides a reattached run's status, so a shared stem lets a boot-time
    reattach read a DEAD run's exit code and settle a LIVE run from it — then
    commit the whole working tree on the strength of it. `run_allowlist`
    already accounts for 16 rows sharing one literal code.
    """
    other = "ffffffff-0000-0000-0000-000000000000"

    assert run_file_stem(RUN_CODE, RUN_ID) != run_file_stem(RUN_CODE, other)


def test_the_three_paths_share_one_stem_and_differ_only_by_suffix(run_log_dir):
    paths = paths_for(RUN_CODE, RUN_ID)
    stem = run_file_stem(RUN_CODE, RUN_ID)

    assert paths == RunOutputPaths(
        out=run_log_dir / f"{stem}.out",
        err=run_log_dir / f"{stem}.err",
        rc=run_log_dir / f"{stem}.rc",
    )


def test_ensure_dir_creates_the_run_log_dir(run_log_dir):
    """Called before any spawn: the child's redirect cannot create its parent."""
    assert not run_log_dir.exists()

    ensure_dir()

    assert run_log_dir.is_dir()


def test_ensure_dir_is_idempotent(run_log_dir):
    ensure_dir()
    ensure_dir()  # a second run on the same host must not raise

    assert run_log_dir.is_dir()


def test_the_tmux_session_name_is_the_stem_with_one_prefix():
    """AC22's second half: the `lg-` literal has exactly one home."""
    assert tmux_session_name(run_file_stem(RUN_CODE, RUN_ID)) == f"lg-{RUN_CODE}-{RUN_ID[:8]}"


def _run(transport: AgentTransport | None, status: RunStatus) -> AgentRun:
    return AgentRun(
        id=RUN_ID,
        run_code=RUN_CODE,
        ticket_id="t1",
        workspace_id="w1",
        agent_id="backend_implementer",
        stage_key="implement",
        status=status,
        agent_transport=transport,
    )


def test_attach_command_names_the_session_of_a_live_tmux_run():
    """AC27. Composed server-side: the client cannot know the session name."""
    command = attach_command(_run(AgentTransport.TMUX, RunStatus.RUNNING))

    assert command == f"tmux attach -t lg-{RUN_CODE}-{RUN_ID[:8]}"


def test_attach_command_is_empty_for_a_file_transport_run():
    """There is no session to attach to, and '' is how the UI knows to show no control."""
    assert attach_command(_run(AgentTransport.FILE, RunStatus.RUNNING)) == ""


def test_attach_command_is_empty_when_no_transport_was_recorded():
    """AC28's server half: 1,449 existing rows have no transport and never will."""
    assert attach_command(_run(None, RunStatus.RUNNING)) == ""


@pytest.mark.parametrize(
    "status", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.QUEUED]
)
def test_attach_command_is_empty_for_a_run_that_is_not_supervised(status):
    """A settled run's session is gone; offering its attach command is a dead end."""
    assert attach_command(_run(AgentTransport.TMUX, status)) == ""


def test_attach_command_is_offered_while_a_run_waits_for_a_permission_answer():
    """The second member of `run_lease.SUPERVISED`, and the only row above that
    distinguishes `status in SUPERVISED` from `status is RunStatus.RUNNING`.

    An awaiting-permission run is a LIVE agent — its session exists and its pane
    is exactly what an operator wants to look at while deciding. Writing the
    predicate as an equality against RUNNING satisfies every other case in this
    module, so without this row the narrower implementation ships and the attach
    command disappears at the moment it is most useful.
    """
    command = attach_command(_run(AgentTransport.TMUX, RunStatus.AWAITING_PERMISSION))

    assert command == f"tmux attach -t lg-{RUN_CODE}-{RUN_ID[:8]}"


# --- S2: the wrapper --------------------------------------------------------


def _sh(wrapped: list[str], **kwargs) -> subprocess.CompletedProcess:
    assert wrapped[:2] == ["sh", "-c"], "the wrapper is /bin/sh, not bash"
    return subprocess.run(wrapped, capture_output=True, **kwargs)


def test_the_wrapper_sends_stdout_and_stderr_to_their_own_files(run_log_dir):
    """AC1. The CHILD writes the files; nothing here holds a pipe."""
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)
    argv = [
        "/bin/sh",
        "-c",
        'printf "on stdout\\n"; printf "on stderr\\n" >&2',
    ]

    done = _sh(wrap_for_files(argv, paths))

    assert done.stdout == b"" and done.stderr == b"", "the wrapper must not leak to its parent"
    assert paths.out.read_text() == "on stdout\n"
    assert paths.err.read_text() == "on stderr\n"


def test_the_wrapper_records_the_agents_exit_code(run_log_dir):
    """AC1's third file."""
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)

    _sh(wrap_for_files(["/bin/sh", "-c", "exit 17"], paths))

    assert paths.rc.read_text() == "17"


def test_the_wrapper_propagates_a_non_zero_exit_status(run_log_dir):
    """AC2, and the single highest-consequence assertion in this file.

    The plan's wrapper ended at `printf %s "$?" > rc`, so `sh` exited with
    *printf's* status. `_print_mode_result` reads `proc.returncode == 0` as
    SUCCEEDED, which would have made every print-mode run — crashed agents
    included — report success, commit, and route.
    """
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)

    done = _sh(wrap_for_files(["/bin/sh", "-c", "exit 17"], paths))

    assert done.returncode == 17, "sh exited with printf's status, not the agent's"
    assert paths.rc.read_text() == "17", "and the two must agree"


def test_the_wrapper_propagates_a_zero_exit_status(run_log_dir):
    """The control: the assertion above must not pass by always being non-zero."""
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)

    done = _sh(wrap_for_files(["/bin/sh", "-c", "exit 0"], paths))

    assert done.returncode == 0
    assert paths.rc.read_text() == "0"


def test_the_wrapper_quotes_an_argv_full_of_shell_metacharacters(run_log_dir):
    """AC3. Every element is shlex-quoted, so the agent receives it verbatim."""
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)
    hostile = "a b 'c' \"d\" $HOME `id` ;rm -rf / & |x #y \\z *"
    argv = ["/bin/sh", "-c", 'printf %s "$1"', "sh", hostile]

    done = _sh(wrap_for_files(argv, paths))

    assert done.returncode == 0
    assert paths.out.read_text() == hostile


def test_the_wrapper_quotes_a_path_containing_a_space(tmp_path, monkeypatch):
    """AC3. `run_log_dir` is operator-configurable, so a space is a real input."""
    monkeypatch.setattr(settings, "run_log_dir", tmp_path / "run logs here")
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)

    done = _sh(wrap_for_files(["/bin/sh", "-c", 'printf "ok\\n"'], paths))

    assert done.returncode == 0
    assert " " in str(paths.out.parent)
    assert paths.out.read_text() == "ok\n"


def test_stdout_and_stderr_are_never_merged(run_log_dir):
    """AC8. `2>&1` would silently change what `failure_reason()` sees."""
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)
    wrapped = wrap_for_files(["/bin/sh", "-c", 'printf "O\\n"; printf "E\\n" >&2'], paths)

    assert "2>&1" not in wrapped[2]
    _sh(wrapped)
    assert paths.out.read_text() == "O\n"
    assert paths.err.read_text() == "E\n"


def test_a_prompt_larger_than_the_pipe_buffer_reaches_the_agent(run_log_dir):
    """AC3's last clause.

    `print_mode` writes `stdin_prompt` and closes the pipe immediately, before
    `sh` has exec'd the agent. `sh -c` never reads stdin, so the fd is inherited
    at exec — but a prompt bigger than the OS pipe buffer blocks that write
    until the agent is reading, which is the ordering this asserts survives.
    """
    ensure_dir()
    paths = paths_for(RUN_CODE, RUN_ID)
    prompt = ("x" * 999 + "\n") * 2000  # ~2MB, far past any pipe buffer
    wrapped = wrap_for_files(["/bin/sh", "-c", "wc -c"], paths)

    proc = subprocess.Popen(wrapped, stdin=subprocess.PIPE, bufsize=0)

    def feed() -> None:
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    assert proc.wait(timeout=30) == 0
    writer.join(timeout=5)
    assert not writer.is_alive(), "the prompt write never completed"
    assert int(paths.out.read_text().strip()) == len(prompt.encode("utf-8"))


# --- S3: RunOutputTail ------------------------------------------------------


def test_the_tail_returns_nothing_for_a_file_that_does_not_exist_yet(run_log_dir):
    """AC4. The spawn and the first write race the tailer's construction."""
    ensure_dir()
    tail = RunOutputTail(run_log_dir / "nothing-here.out")

    assert tail.readline(timeout=0) is None
    assert tail.offset == 0


def test_the_tail_picks_the_file_up_once_it_appears(run_log_dir):
    ensure_dir()
    path = run_log_dir / "late.out"
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) is None

    path.write_text("first\n")

    assert tail.readline(timeout=0) == "first\n"


def test_the_tail_never_hands_out_half_a_line(run_log_dir):
    """AC4's named test: write the file ONE BYTE AT A TIME.

    This is risk #1 in the spec. A tailer that returns a buffered partial as a
    terminated line splits a `stream_event` JSON object in half, and the parsed
    event the UI gets is not the one the agent emitted.
    """
    ensure_dir()
    path = run_log_dir / "bytewise.out"
    path.write_bytes(b"")
    source = '{"type":"stream_event","delta":"hello"}\nsecond line\n'
    tail = RunOutputTail(path)

    emitted: list[str] = []
    with path.open("ab", buffering=0) as handle:
        for byte in source.encode("utf-8"):
            handle.write(bytes([byte]))
            while (line := tail.readline(timeout=0)) is not None:
                emitted.append(line)

    assert emitted == ['{"type":"stream_event","delta":"hello"}\n', "second line\n"]
    assert all(line.endswith("\n") for line in emitted)


def test_the_tail_does_not_report_eof_as_the_writer_finishing(run_log_dir):
    """AC4/AC5's root cause. EOF is the *normal* state of a file being appended to.

    `SubprocessLineReader` conflates "nothing ready" with "writer closed"
    because on a pipe those really are the same event. On a regular file they
    are not, and the live loop may only end on the process being gone.
    """
    ensure_dir()
    path = run_log_dir / "paused.out"
    path.write_text("one\n")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "one\n"

    assert tail.readline(timeout=0) is None  # idle, not finished
    path.write_text("one\ntwo\n")
    assert tail.readline(timeout=0) == "two\n"


def test_an_unterminated_trailing_line_is_not_returned_as_a_line(run_log_dir):
    """A live agent mid-line must not have that line closed for it."""
    ensure_dir()
    path = run_log_dir / "partial.out"
    path.write_text("complete\nincomp")
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) == "complete\n"
    assert tail.readline(timeout=0) is None
    assert tail.readline(timeout=0) is None


def test_flush_partial_recovers_the_last_line_of_a_file_with_no_newline(run_log_dir):
    """AC4/AC5. A stage report block is the last thing an agent writes."""
    ensure_dir()
    path = run_log_dir / "noeol.out"
    path.write_text("done\n<<<END_STAGE_REPORT>>>")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "done\n"
    assert tail.readline(timeout=0) is None

    assert tail.flush_partial() == "<<<END_STAGE_REPORT>>>\n"
    assert tail.flush_partial() is None, "the buffer is cleared, so it cannot be emitted twice"


def test_flush_partial_returns_nothing_when_there_is_no_remainder(run_log_dir):
    ensure_dir()
    path = run_log_dir / "clean.out"
    path.write_text("all\nterminated\n")
    tail = RunOutputTail(path)
    while tail.readline(timeout=0) is not None:
        pass

    assert tail.flush_partial() is None


def test_the_offset_is_always_a_resumable_line_boundary(run_log_dir):
    """AC4/AC9. The offset is what a restarted server resumes from."""
    ensure_dir()
    path = run_log_dir / "offsets.out"
    path.write_text("alpha\nbeta\ngamma\n")
    tail = RunOutputTail(path)

    assert tail.offset == 0
    assert tail.readline(timeout=0) == "alpha\n"
    assert tail.offset == len(b"alpha\n")
    assert tail.readline(timeout=0) == "beta\n"
    assert tail.offset == len(b"alpha\nbeta\n")


def test_the_offset_does_not_advance_over_a_buffered_partial(run_log_dir):
    """Otherwise a restart resumes past bytes nobody ever ingested."""
    ensure_dir()
    path = run_log_dir / "unflushed.out"
    path.write_text("alpha\nbet")
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) == "alpha\n"
    assert tail.readline(timeout=0) is None
    assert tail.offset == len(b"alpha\n")


def test_a_start_offset_resumes_without_duplicating_or_skipping(run_log_dir):
    """AC12's no-duplication/no-gap property, at the tailer level."""
    ensure_dir()
    path = run_log_dir / "resume.out"
    path.write_text("one\ntwo\n")
    first = RunOutputTail(path)
    assert first.readline(timeout=0) == "one\n"
    stored = first.offset

    path.write_text("one\ntwo\nthree\n")
    second = RunOutputTail(path, start_offset=stored)
    resumed = []
    while (line := second.readline(timeout=0)) is not None:
        resumed.append(line)

    assert resumed == ["two\n", "three\n"]


def test_a_start_offset_past_the_end_of_the_file_yields_nothing(run_log_dir):
    """A truncated or recreated file must not raise on the boot path."""
    ensure_dir()
    path = run_log_dir / "shrunk.out"
    path.write_text("tiny\n")
    tail = RunOutputTail(path, start_offset=10_000)

    assert tail.readline(timeout=0) is None


def test_the_tail_sleeps_its_timeout_at_eof_rather_than_spinning(run_log_dir):
    """S3 contract item 4. The resupervise beat is paced by this call."""
    ensure_dir()
    path = run_log_dir / "idle.out"
    path.write_text("")
    tail = RunOutputTail(path)

    started = time.monotonic()
    assert tail.readline(timeout=0.2) is None
    assert time.monotonic() - started >= 0.15


def test_utf8_split_across_two_writes_is_not_mangled(run_log_dir):
    """A multi-byte character can straddle any append boundary."""
    ensure_dir()
    path = run_log_dir / "utf8.out"
    path.write_bytes(b"")
    tail = RunOutputTail(path)
    payload = "reévalué — \U0001f600\n".encode("utf-8")

    with path.open("ab", buffering=0) as handle:
        for byte in payload:
            handle.write(bytes([byte]))
            assert tail.readline(timeout=0) in (None, payload.decode("utf-8"))

    assert tail.offset == len(payload)


def test_the_file_tailer_does_not_live_beside_the_pipe_reader():
    """AC6. Housing it next to `SubprocessLineReader` is how someone reuses the
    wrong one: that reader returns None for both "nothing ready" and "writer
    closed", and on an empty read flushes a buffered partial as a terminated
    line."""
    import loregarden.services.subprocess_lines as pipe_reader

    assert RunOutputTail.__module__ == "loregarden.services.run_output_files"
    assert not hasattr(pipe_reader, "RunOutputTail")


def test_the_tail_tolerates_a_file_whose_directory_is_gone(run_log_dir):
    """Orphan sweeps delete these files; a tailer must not raise over it."""
    ensure_dir()
    path = run_log_dir / "deleted.out"
    path.write_text("before\n")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "before\n"

    os.remove(path)

    assert tail.readline(timeout=0) is None
