"""`run_print_mode` over files instead of pipes (spec S2, S4; AC1, AC2, AC5, AC7, AC8).

The server no longer reads the agent's pipe. It spawns an `sh -c` wrapper that
redirects the agent's own stdout, stderr and exit code into three files, and
tails `.out` — so the output outlives the process that started the run.

Two behaviours here are the ones that break quietly:

* the loop's terminator. EOF on a regular file is the *normal* state of a file
  being appended to, so a loop that ends on `readline() is None` truncates the
  transcript — including the `<<<LOREGARDEN_STAGE_REPORT>>>` block the
  orchestrator routes on. It may only end on the process being gone (AC5).
* the status. It comes from `.rc`, because that is also what the reattached
  path reads, and the two paths must not disagree about what "succeeded"
  means (AC2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from loregarden.agents.executors import print_mode
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.config import settings
from loregarden.models.domain import RunStatus
from loregarden.services import subprocess_lines
from loregarden.services.run_log_stream import format_stream_payload
from loregarden.services.run_output_files import paths_for
from sqlmodel import Session

RUN_ID = "test-print-mode-detached"
RUN_CODE = "run_det001"


class _CollectingStreamer:
    """What the owner hands `run_print_mode`: a line sink and an offset slot."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.tail_offset = 0
        #: The offset observed at the moment each line was appended. S4 pins
        #: the ordering — the offset is set immediately AFTER the append — and
        #: S5 depends on it.
        self.offset_after_each: list[int] = []

    def append_stream_line(self, line: str) -> None:
        self.lines.append(line)
        self.offset_after_each.append(self.tail_offset)


@pytest.fixture(name="run_log_dir")
def run_log_dir_fixture(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "run-logs"
    monkeypatch.setattr(settings, "run_log_dir", target)
    return target


def _invocation(script: str, *, stdin_prompt: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        argv=[sys.executable, "-u", "-c", script],
        cwd=None,
        stdin_prompt=stdin_prompt,
        interactive=False,
        adapter="local",
        env={},
    )


def _run(db_session: Session, script: str, *, timeout: int = 20, stdin_prompt=None, streamer=None):
    """`db_session` is where the spawned run's process identity is recorded."""
    return run_print_mode(
        invocation=_invocation(script, stdin_prompt=stdin_prompt),
        repo_root=Path.cwd(),
        timeout=timeout,
        streamer=streamer or _CollectingStreamer(),
        run_id=RUN_ID,
        run_code=RUN_CODE,
    )


# --- AC1/AC8: the child writes the files ------------------------------------


def test_the_agents_stdout_lands_in_the_out_file(db_session: Session, run_log_dir):
    """AC1. The bytes stdout used to carry are now in `.out`."""
    _run(db_session, "print('alpha')\nprint('beta')")

    assert paths_for(RUN_CODE, RUN_ID).out.read_text() == "alpha\nbeta\n"


def test_the_agents_stderr_lands_in_its_own_file(db_session: Session, run_log_dir):
    """AC1/AC8. Two files; `2>&1` would change what `failure_reason()` sees."""
    script = "import sys\nprint('out')\nsys.stderr.write('boom\\n')"
    _run(db_session, script)

    paths = paths_for(RUN_CODE, RUN_ID)
    assert paths.out.read_text() == "out\n"
    assert paths.err.read_text() == "boom\n"


def test_the_agents_exit_code_lands_in_the_rc_file(db_session: Session, run_log_dir):
    """AC1's third file, which is what the reattached path reads."""
    _run(db_session, "raise SystemExit(3)")

    assert paths_for(RUN_CODE, RUN_ID).rc.read_text() == "3"


def test_the_result_reads_stderr_from_the_err_file(db_session: Session, run_log_dir):
    """AC8. `proc.stderr` is DEVNULL now, so this is the only source left."""
    script = "import sys\nsys.stderr.write('the real reason\\n')\nraise SystemExit(1)"

    _stdout, stderr, status = _run(db_session, script)

    assert stderr.strip() == "the real reason"
    assert status is RunStatus.FAILED


def test_a_missing_err_file_reads_as_empty_rather_than_raising(db_session: Session, run_log_dir):
    """The agent may never write a byte to stderr; `.err` then may not exist."""
    _stdout, stderr, status = _run(db_session, "print('quiet')")

    assert stderr == ""
    assert status is RunStatus.SUCCEEDED


# --- AC2: the status ---------------------------------------------------------


def test_an_agent_that_exits_non_zero_fails_the_run(db_session: Session, run_log_dir):
    """AC2. The plan's wrapper would have reported this SUCCEEDED."""
    _stdout, _stderr, status = _run(db_session, "print('did some work')\nraise SystemExit(9)")

    assert status is RunStatus.FAILED
    assert paths_for(RUN_CODE, RUN_ID).rc.read_text() == "9"


def test_an_agent_that_exits_zero_succeeds(db_session: Session, run_log_dir):
    _stdout, _stderr, status = _run(db_session, "print('fine')")

    assert status is RunStatus.SUCCEEDED
    assert paths_for(RUN_CODE, RUN_ID).rc.read_text() == "0"


def test_the_rc_file_and_the_wrappers_own_exit_status_agree(db_session: Session, run_log_dir):
    """AC2. `.rc` is authoritative because the reattached path reads it — but on
    the live path `exit "$rc"` makes `proc.returncode` say the same thing, and a
    divergence here is the two settlement paths disagreeing about one run."""
    _run(db_session, "raise SystemExit(5)")

    rc_file = int(paths_for(RUN_CODE, RUN_ID).rc.read_text())
    assert rc_file == 5


# --- AC5: the loop terminator ------------------------------------------------


def test_the_last_line_of_the_transcript_survives(db_session: Session, run_log_dir):
    """AC5. A truncated tail loses the stage report, and the stage never routes."""
    report = '{"status": "pass", "confidence": 0.9, "reroute_to_stage": null}'
    script = (
        "print('working')\n"
        "print('<<<LOREGARDEN_STAGE_REPORT>>>')\n"
        f"print({report!r})\n"
        "print('<<<END_STAGE_REPORT>>>')\n"
    )
    streamer = _CollectingStreamer()

    stdout, _stderr, status = _run(db_session, script, streamer=streamer)

    assert status is RunStatus.SUCCEEDED
    assert stdout.splitlines()[-1] == "<<<END_STAGE_REPORT>>>"
    assert streamer.lines[-1].rstrip("\n") == "<<<END_STAGE_REPORT>>>"


def test_an_unterminated_final_line_survives_via_flush_partial(db_session: Session, run_log_dir):
    """AC5. The agent is killed, or simply never prints its newline."""
    script = "import sys\nsys.stdout.write('first\\n')\nsys.stdout.write('no trailing newline')"
    streamer = _CollectingStreamer()

    stdout, _stderr, _status = _run(db_session, script, streamer=streamer)

    assert [line.rstrip("\n") for line in streamer.lines] == ["first", "no trailing newline"]
    assert stdout.splitlines()[-1] == "no trailing newline"


def test_a_quiet_stretch_mid_run_does_not_end_the_loop(db_session: Session, run_log_dir):
    """AC5's root cause: a file at EOF looks exactly like a finished one.

    The agent says nothing for longer than one poll, then speaks again. A loop
    that read EOF as the end would return having lost everything after the gap.
    """
    script = "import time\nprint('before', flush=True)\ntime.sleep(1.5)\nprint('after', flush=True)"

    stdout, _stderr, status = _run(db_session, script, timeout=20)

    assert status is RunStatus.SUCCEEDED
    assert stdout.splitlines() == ["before", "after"]


def test_a_run_that_exits_immediately_does_not_hang(db_session: Session, run_log_dir):
    """The opposite failure of the one above: if EOF no longer ends the loop,
    something still has to. A process that is gone, with nothing left to drain,
    must return rather than run to its hard cap."""
    _stdout, _stderr, status = _run(db_session, "pass", timeout=20)

    assert status is RunStatus.SUCCEEDED


def test_a_run_whose_agent_writes_nothing_still_returns(db_session: Session, run_log_dir):
    """An empty `.out` is a legitimate transcript, not a reason to wait."""
    stdout, _stderr, status = _run(db_session, "raise SystemExit(0)", timeout=20)

    assert stdout == ""
    assert status is RunStatus.SUCCEEDED


# --- AC1: the spawn's shape --------------------------------------------------


def test_the_prompt_still_reaches_the_agent_on_stdin(db_session: Session, run_log_dir):
    """AC1/AC3. `sh -c` never reads stdin, so the fd is inherited at exec."""
    script = "import sys\nsys.stdout.write(sys.stdin.read())"

    stdout, _stderr, status = _run(db_session, script, stdin_prompt="the whole prompt\n")

    assert status is RunStatus.SUCCEEDED
    assert stdout.strip() == "the whole prompt"


def test_a_prompt_larger_than_the_pipe_buffer_still_arrives(db_session: Session, run_log_dir):
    """AC3. A real stage prompt is tens of kilobytes; the ordering is the risk."""
    prompt = "line of prompt text\n" * 20_000  # ~400KB
    script = "import sys\nsys.stdout.write(str(len(sys.stdin.read())))"

    stdout, _stderr, status = _run(db_session, script, stdin_prompt=prompt, timeout=60)

    assert status is RunStatus.SUCCEEDED
    assert int(stdout.strip()) == len(prompt)


# --- AC7: output parity ------------------------------------------------------


def test_a_stream_event_partial_reaches_the_streamer_intact(db_session: Session, run_log_dir):
    """AC7. Asserted on the PARSED event, not on raw bytes.

    A tailer that flushed a buffered partial as a terminated line would split
    this JSON object, and `format_stream_payload` would get something the agent
    never emitted.
    """
    payload = {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "a partial sentence"},
        },
    }
    script = f"import json\nprint(json.dumps({payload!r}))"
    streamer = _CollectingStreamer()

    _run(db_session, script, streamer=streamer)

    assert len(streamer.lines) == 1
    parsed = json.loads(streamer.lines[0])
    assert parsed == payload
    assert format_stream_payload(parsed) is not None


def test_many_interleaved_stream_events_arrive_in_order_and_unsplit(
    db_session: Session, run_log_dir
):
    """AC7 at volume: the largest run in the live database carries 1,667 lines."""
    script = (
        "import json, sys\n"
        "for i in range(500):\n"
        "    sys.stdout.write(json.dumps({'type': 'stream_event', 'seq': i}) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    streamer = _CollectingStreamer()

    _run(db_session, script, timeout=60, streamer=streamer)

    seqs = [json.loads(line)["seq"] for line in streamer.lines]
    assert seqs == list(range(500))


def test_the_owner_records_the_tail_offset_after_every_appended_line(
    db_session: Session, run_log_dir
):
    """S4/AC9's ordering. The offset must be the position just PAST the line it
    was recorded with — the other way round and a restart re-ingests a line or
    skips one."""
    script = "print('aaa')\nprint('bbbb')"
    streamer = _CollectingStreamer()

    _run(db_session, script, streamer=streamer)

    assert streamer.offset_after_each == [len(b"aaa\n"), len(b"aaa\nbbbb\n")]
    assert streamer.tail_offset == len(b"aaa\nbbbb\n")


# --- AC6: the pipe reader is off this path -----------------------------------


def test_the_print_mode_module_does_not_import_the_pipe_line_reader():
    """AC6. Its `readline` conflates "nothing ready" with "writer closed" and
    flushes a buffered partial as a terminated line — on a regular file that
    corrupts exactly the partials the test above protects.

    Asserted on the module's own namespace rather than by monkeypatching the
    defining module: `print_mode` binds the name at import, so a patch there
    would be satisfied by a module that still calls the real class.
    """
    assert not hasattr(print_mode, "SubprocessLineReader")
    assert hasattr(subprocess_lines, "SubprocessLineReader"), "the pipe reader still exists"
