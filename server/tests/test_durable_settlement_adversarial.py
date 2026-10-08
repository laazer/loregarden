"""Adversarial cases for the live loop and for settlement (spec S4, S6).

`test_print_mode_detached.py` and `test_run_resupervise.py` establish the
contracts. This module attacks them, and three of the cases here are defects
the happy-path suites cannot see:

* **`proc.kill()` no longer reaches the agent.** The loop's cancel branch and
  its hard-cap branch both call `proc.kill()`, and after S2 that handle is the
  `sh` wrapper — not the agent. Killing the shell leaves the agent running,
  detached, still writing to `.out`, holding a worktree the orchestrator is
  about to commit. AC5 calls the cancel check "behaviourally unchanged", but
  the wrapper changes what the existing call *does*, and AC17 is the criterion
  it breaks: no orphan agent processes may remain.
* **Nothing asserts `resupervise` PERSISTS the offset it consumed.** Every
  existing test asserts it *reads* one. A resupervise that drains without
  writing `tail_offset` back passes all of them and re-ingests the entire
  transcript at the next restart — and AC29 is explicit that a 6,451-minute
  run can survive two.
* **Settlement is only proven once SEQUENTIALLY.** The trigger AC14 names is
  the original server coming back, which overlaps the two settlers rather than
  ordering them. The invariant has to hold under the interleaving, so it is
  asserted with both threads released from one barrier.

The adopted-run fixture and its helpers are imported from
`test_run_resupervise` rather than copied: two drifting definitions of "a run
as a boot-time reattach finds it" is how one module quietly stops testing the
same thing as the other.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.executors import print_mode
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.models.domain import AgentRun, Artifact, ProcessState, RunStatus
from loregarden.services import run_resupervise
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_output_files import RunOutputTail, paths_for
from sqlmodel import Session, select
from tests.test_print_mode_detached import _CollectingStreamer, _invocation
from tests.test_run_resupervise import (  # noqa: F401 -- fixtures, used by name
    NO_RC_STDERR,
    _gone,
    _log_lines,
    _report,
    _write_output,
    adopted_fixture,
    run_log_dir_fixture,
)

PRINT_RUN_ID = "test-print-mode-adversarial"
PRINT_RUN_CODE = "run_adv002"


def _print_run(script: str, *, timeout: int = 20, streamer=None):
    return run_print_mode(
        invocation=_invocation(script),
        repo_root=Path.cwd(),
        timeout=timeout,
        streamer=streamer or _CollectingStreamer(),
        run_id=PRINT_RUN_ID,
        run_code=PRINT_RUN_CODE,
    )


def _await_pid_file(path: Path, *, seconds: float = 15.0) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.02)
    raise AssertionError(f"the agent never recorded its pid in {path}")


def _gone_within(pid: int, *, seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False  # reparented and no longer ours to signal, but still alive
        time.sleep(0.05)
    return False


def _reap_orphan(pid: int) -> None:
    """Never leave a runaway agent behind, including when the test fails."""
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # silent-ok: cleanup of a process that is already gone is a no-op


# --- AC17/AC5: the loop's own kill must still reach the agent ------------------


_PID_AND_SPIN = (
    "import os, sys, time\n"
    "open({marker!r}, 'w').write(str(os.getpid()))\n"
    "sys.stdout.write('working\\n'); sys.stdout.flush()\n"
    "time.sleep(600)\n"
)


def test_a_cancelled_run_leaves_no_orphan_agent(db_session: Session, run_log_dir, tmp_path):
    """AC17, reached through the loop rather than through `run_detached_stop`.

    The cancel branch calls `proc.kill()`. Before this ticket that handle was
    the agent; after S2 it is the `sh` wrapper, and killing a shell does not
    kill what it is waiting on. The agent would keep running — detached, still
    appending to `.out`, still holding the worktree the orchestrator commits
    when the stage finishes — and nothing in the suite would say so, because
    the run's row goes CANCELLED either way.
    """
    marker = tmp_path / "agent.pid"
    script = _PID_AND_SPIN.format(marker=str(marker))

    with mock.patch.object(print_mode, "cancel_requested", return_value=True):
        _stdout, _stderr, status = _print_run(script, timeout=60)

    assert status is RunStatus.CANCELLED
    if not marker.exists():
        pytest.skip("the agent was cancelled before it recorded its pid")
    agent_pid = int(marker.read_text().strip())
    try:
        assert _gone_within(agent_pid), (
            "the agent outlived the cancel: proc.kill() reaped the sh wrapper and "
            "left the agent detached, writing into .out and holding the worktree"
        )
    finally:
        _reap_orphan(agent_pid)


def test_a_run_that_blows_its_hard_cap_leaves_no_orphan_agent(
    db_session: Session, run_log_dir, tmp_path
):
    """The same defect on the other branch. A timeout that kills only the
    wrapper reports a timeout to the operator while the agent keeps going, and
    the next thing to touch that worktree is a commit."""
    marker = tmp_path / "agent-timeout.pid"
    script = _PID_AND_SPIN.format(marker=str(marker))

    with pytest.raises(Exception):  # noqa: B017 -- RunTimeout; the point is the orphan
        _print_run(script, timeout=1)

    agent_pid = _await_pid_file(marker)
    try:
        assert _gone_within(agent_pid), (
            "the agent outlived the hard cap; only the sh wrapper was killed"
        )
    finally:
        _reap_orphan(agent_pid)


def test_output_written_before_a_cancel_is_still_reported(
    db_session: Session, run_log_dir, tmp_path
):
    """A cancelled run's partial transcript is the only record of what it did.

    Asserted alongside the orphan check because an implementation can satisfy
    one by sacrificing the other — killing the group before the final drain
    throws the output away.
    """
    marker = tmp_path / "agent-partial.pid"
    script = _PID_AND_SPIN.format(marker=str(marker))
    calls = {"n": 0}

    def cancel_after_first_look(_run_id: str) -> bool:
        calls["n"] += 1
        return calls["n"] > 12

    with mock.patch.object(print_mode, "cancel_requested", cancel_after_first_look):
        stdout, _stderr, status = _print_run(script, timeout=60)

    agent_pid = marker.read_text().strip() if marker.exists() else ""
    try:
        assert status is RunStatus.CANCELLED
        assert "working" in stdout, "the output the agent did produce was discarded"
    finally:
        if agent_pid:
            _reap_orphan(int(agent_pid))


# --- AC1/AC2: the live path's edges -------------------------------------------


def test_an_agent_killed_by_a_signal_fails_rather_than_succeeding(db_session: Session, run_log_dir):
    """AC2. A segfaulting or OOM-killed agent exits by signal, so `.rc` is
    128+N — and the one thing it must not be read as is success."""
    script = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)"

    _stdout, _stderr, status = _print_run(script)

    assert status is RunStatus.FAILED
    rc = paths_for(PRINT_RUN_CODE, PRINT_RUN_ID).rc
    assert rc.read_text() == str(128 + signal.SIGKILL)


def test_the_servers_own_streams_are_not_polluted_by_the_agent(
    db_session: Session, run_log_dir, capfd
):
    """AC1's `stdout=DEVNULL`/`stderr=DEVNULL`, asserted on what the parent
    sees. Inheriting the server's fds would interleave every agent's output
    into the control plane's own log — and, worse, keep a pipe alive, which is
    the defect this ticket removes."""
    script = (
        "import sys\n"
        "sys.stdout.write('AGENT-STDOUT-MARKER\\n')\n"
        "sys.stderr.write('AGENT-STDERR-MARKER\\n')\n"
    )

    _print_run(script)

    captured = capfd.readouterr()
    assert "AGENT-STDOUT-MARKER" not in captured.out
    assert "AGENT-STDOUT-MARKER" not in captured.err
    assert "AGENT-STDERR-MARKER" not in captured.err
    assert "AGENT-STDOUT-MARKER" in paths_for(PRINT_RUN_CODE, PRINT_RUN_ID).out.read_text()


def test_a_single_line_of_several_megabytes_survives_the_whole_path(
    db_session: Session, run_log_dir
):
    """AC7 at the size a tool result really reaches. A tailer that emits when
    its buffer fills would deliver this as several lines, and every one of
    them would be invalid JSON."""
    script = (
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'type': 'stream_event', 'text': 'y' * 2000000}) + '\\n')\n"
    )
    streamer = _CollectingStreamer()

    _print_run(script, timeout=60, streamer=streamer)

    assert len(streamer.lines) == 1, "the line was split"
    assert json.loads(streamer.lines[0])["text"] == "y" * 2_000_000


def test_invalid_utf8_on_stderr_does_not_raise_on_the_result_path(db_session: Session, run_log_dir):
    """S4 pins `errors="replace"` for the `.err` read, and this is why: a tool
    the agent shelled out to writes whatever bytes it likes to stderr, and a
    strict decode would raise AFTER the agent finished — turning a completed
    run into an exception with no status at all."""
    script = (
        "import sys\n"
        "sys.stderr.buffer.write(b'\\xff\\xfe broken on stderr\\n')\n"
        "sys.stderr.buffer.flush()\n"
        "raise SystemExit(3)\n"
    )

    _stdout, stderr, status = _print_run(script)

    assert status is RunStatus.FAILED
    assert "broken on stderr" in stderr


# --- AC12/AC29: the offset a SECOND restart resumes from ----------------------


def _log_artifact_content(db_session: Session, run: AgentRun) -> dict:
    artifact = db_session.exec(
        select(Artifact).where(Artifact.run_id == run.id, Artifact.kind == "log")
    ).first()
    assert artifact is not None, "no log artifact was written for the reattached run"
    return json.loads(artifact.content_json or "{}")


def test_resupervise_persists_the_offset_it_consumed(db_session: Session, adopted):
    """AC12/AC29, and the gap in the existing suite.

    Every other test asserts `resupervise` READS a stored offset. None asserts
    it writes one back. A resupervise that drains the file and never persists
    `tail_offset` passes all of them — and then the next restart of a run that
    is still going re-ingests the whole transcript from byte 0, which is the
    duplication AC12 forbids. AC29 names two restarts of one run explicitly.
    """
    transcript = "first\nsecond\nthird\n" + _report("pass")
    _write_output(adopted, out=transcript, rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    stored = _log_artifact_content(db_session, adopted).get("tail_offset")
    assert stored == len(transcript.encode("utf-8")), (
        "the offset consumed was not persisted, so a second restart replays the transcript"
    )
    resumed = RunOutputTail(paths_for(adopted.run_code, adopted.id).out, start_offset=stored or 0)
    assert resumed.readline(timeout=0) is None, "a second reader would re-emit lines"


def test_the_offset_advances_while_the_run_is_still_alive(db_session: Session, adopted):
    """The mid-flight half. An offset only written at settlement is no use to a
    restart that lands while the agent is still working — which is the only
    case this ticket exists for."""
    paths = paths_for(adopted.run_code, adopted.id)
    paths.out.write_text("beat one\n", encoding="utf-8")
    observed: list[int] = []

    def readings(*_args, **_kwargs) -> ProcessState:
        content = _log_artifact_content(db_session, adopted)
        observed.append(int(content.get("tail_offset") or 0))
        if len(observed) == 1:
            paths.out.write_text("beat one\nbeat two\n", encoding="utf-8")
            return ProcessState.ALIVE
        paths.rc.write_text("0", encoding="utf-8")
        return ProcessState.GONE

    with mock.patch.object(run_resupervise, "liveness", readings):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert observed and max(observed) > 0, (
        "the offset was never written during the run, only (if at all) at the end"
    )


# --- AC14: settle once, under the interleaving that actually causes it ---------


def test_two_overlapping_resupervisors_settle_the_run_exactly_once(db_session: Session, adopted):
    """AC14. The existing proof runs the two settlers in sequence; the trigger
    it names — the original server coming back — overlaps them.

    Two commits of the whole working tree into one checkout is the consequence,
    so the invariant is asserted against the interleaving rather than against
    one ordering: however the two threads race, `complete_run` happens once.
    """
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")
    original = OrchestrationService.complete_run
    calls: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=30)
    failures: list[BaseException] = []

    def counting(self, run, **kwargs):
        with lock:
            calls.append(run.id)
        return original(self, run, **kwargs)

    def settle() -> None:
        try:
            barrier.wait()
            run_resupervise.resupervise(adopted.id, interval_seconds=0)
        except BaseException as exc:  # noqa: BLE001 -- reported, not swallowed
            failures.append(exc)

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch.object(OrchestrationService, "complete_run", counting),
    ):
        threads = [threading.Thread(target=settle, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive(), "a resupervise thread never returned"

    assert len(calls) == 1, f"the run was settled {len(calls)} times (thread errors: {failures})"
    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.SUCCEEDED


def test_a_settlement_that_fails_is_not_reported_as_a_settlement(
    db_session: Session, adopted, caplog
):
    """A `complete_run` that raises must not leave the run looking finished.

    CLAUDE.md's rule, not a spec line: a swallow here is the exact shape the
    control plane's characteristic bug takes — the stage never routed, the
    worktree was never committed, and the row says SUCCEEDED. Either the
    failure propagates or it is logged where the default handler can see it.
    """
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch.object(
            OrchestrationService, "complete_run", side_effect=RuntimeError("routing exploded")
        ),
        caplog.at_level(logging.WARNING),
    ):
        propagated = False
        try:
            run_resupervise.resupervise(adopted.id, interval_seconds=0)
        except RuntimeError:
            propagated = True

    logged = any(record.levelno >= logging.WARNING for record in caplog.records)
    assert propagated or logged, "settlement failed and nothing above INFO said so"


# --- AC12: what `.rc` can actually contain ------------------------------------


@pytest.mark.parametrize(
    ("label", "value", "expected", "sentence"),
    [
        ("clean zero", "0", RunStatus.SUCCEEDED, False),
        ("padded zero", " 0\n", RunStatus.SUCCEEDED, False),
        ("double zero", "00", RunStatus.SUCCEEDED, False),
        ("one", "1", RunStatus.FAILED, False),
        ("sigkill", "137", RunStatus.FAILED, False),
        ("max byte", "255", RunStatus.FAILED, False),
        ("negative", "-1", RunStatus.FAILED, False),
        ("empty", "", RunStatus.FAILED, True),
        ("whitespace only", "   ", RunStatus.FAILED, True),
        ("not a number", "oops", RunStatus.FAILED, True),
        ("hex", "0x0", RunStatus.FAILED, True),
        ("float", "0.0", RunStatus.FAILED, True),
        ("scientific", "1e3", RunStatus.FAILED, True),
        ("absent", None, RunStatus.FAILED, True),
    ],
)
def test_the_exit_code_file_is_read_without_a_wrong_answer(
    label, value, expected, sentence, db_session: Session, adopted
):
    """AC12's three-way split, over the values the file can really hold.

    `0.0` and `0x0` are the dangerous rows: a reader that reached for `float()`
    or `int(x, 0)` to be forgiving would call them success, and a truncated
    write is exactly how they appear. Unparseable means "we never found out",
    which AC12 settles as FAILED with a sentence that says so — never as a
    zero.
    """
    _write_output(adopted, out="said things\n", rc=value)

    with mock.patch.object(run_resupervise, "liveness", _gone):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    settled = db_session.get(AgentRun, adopted.id)
    assert settled.status is expected, label
    if sentence:
        assert NO_RC_STDERR in (settled.stderr or ""), f"{label}: the operator was not told why"
    else:
        assert NO_RC_STDERR not in (settled.stderr or ""), (
            f"{label}: an exit code was recorded, so the no-exit-code sentence is false"
        )


# --- AC29/AC12: the marker, and output across readings that cannot answer -----


def test_the_resume_marker_is_written_once_however_many_beats_the_loop_takes(
    db_session: Session, adopted
):
    """AC29. One line per reattachment, appended before the first drain.

    Written inside the beat instead of before it, a run that lives through
    twenty beats gets twenty markers — and the operator reading the feed
    concludes the control plane restarted twenty times.
    """
    _write_output(adopted, out="work\n" + _report("pass"), rc="0")
    readings = iter(
        [ProcessState.ALIVE, ProcessState.ALIVE, ProcessState.UNKNOWN, ProcessState.GONE]
    )

    with mock.patch.object(run_resupervise, "liveness", side_effect=lambda *_: next(readings)):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    markers = [text for text in _log_lines(db_session, adopted) if text.startswith("reattached · ")]
    assert len(markers) == 1, f"{len(markers)} resume markers for one reattachment"


def test_output_written_during_readings_that_cannot_answer_is_not_lost(
    db_session: Session, adopted
):
    """lg-run-durability-862's rule says skip the beat on UNKNOWN. It must be
    the RENEWAL that is skipped, not the agent's output: an unanswered `ps`
    says nothing about what the agent wrote, and a transcript with a hole in it
    is worse than one that is merely late."""
    paths = paths_for(adopted.run_code, adopted.id)
    paths.out.write_text("before the unknown\n", encoding="utf-8")
    appended = {"n": 0}

    def readings(*_args, **_kwargs) -> ProcessState:
        appended["n"] += 1
        if appended["n"] == 1:
            paths.out.write_text("before the unknown\nduring the unknown\n", encoding="utf-8")
            return ProcessState.UNKNOWN
        if appended["n"] == 2:
            paths.out.write_text(
                "before the unknown\nduring the unknown\nafter the unknown\n" + _report("pass"),
                encoding="utf-8",
            )
            return ProcessState.UNKNOWN
        paths.rc.write_text("0", encoding="utf-8")
        return ProcessState.GONE

    with mock.patch.object(run_resupervise, "liveness", readings):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    texts = _log_lines(db_session, adopted)
    assert "before the unknown" in texts
    assert "during the unknown" in texts, "a line written during an UNKNOWN beat was dropped"
    assert "after the unknown" in texts


def test_a_stored_offset_past_the_end_of_the_file_still_settles_the_run(
    db_session: Session, adopted
):
    """AC22's failure mode, after the fact: a `run_code` collision, an orphan
    sweep, or a truncated write can leave the stored offset beyond what the
    file now holds. The run still has to settle — a reattach thread that
    raises here leaves the row RUNNING forever and the ticket's stage with it.
    """
    _write_output(adopted, out="short\n", rc="0")
    db_session.add(
        Artifact(
            ticket_id=adopted.ticket_id,
            run_id=adopted.id,
            kind="log",
            title=f"Run {adopted.run_code}",
            content_json=json.dumps({"live": None, "storage": "rows", "tail_offset": 10_000_000}),
        )
    )
    db_session.commit()

    with mock.patch.object(run_resupervise, "liveness", _gone):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.SUCCEEDED


def test_a_run_whose_output_file_appears_only_after_the_loop_starts_is_read(
    db_session: Session, adopted
):
    """The spawn and the reattach race: a run adopted at boot may not have
    written its first byte yet. A tailer constructed over a missing file has to
    pick it up, not conclude the run said nothing."""
    paths = paths_for(adopted.run_code, adopted.id)
    assert not paths.out.exists()
    beats = {"n": 0}

    def readings(*_args, **_kwargs) -> ProcessState:
        beats["n"] += 1
        if beats["n"] == 1:
            return ProcessState.ALIVE
        if beats["n"] == 2:
            paths.out.write_text("spoke at last\n" + _report("pass"), encoding="utf-8")
            return ProcessState.ALIVE
        paths.rc.write_text("0", encoding="utf-8")
        return ProcessState.GONE

    with mock.patch.object(run_resupervise, "liveness", readings):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert "spoke at last" in _log_lines(db_session, adopted)


def test_an_agent_that_only_ever_wrote_a_partial_line_still_reports_it(
    db_session: Session, adopted
):
    """The whole transcript with no terminator at all: an agent killed one line
    in. `flush_partial` is the only thing that recovers it, and a final drain
    that skips the call when nothing terminated was read loses the only
    evidence of what the run was doing."""
    _write_output(adopted, out="the only thing it ever said", rc="137")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert "the only thing it ever said" in _log_lines(db_session, adopted)
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.FAILED


def test_the_settled_run_keeps_its_transport_rather_than_being_rewritten(
    db_session: Session, adopted
):
    """AC13's shape, asserted on the row: an adopted run has
    `external_harness=None` and keeps it, and its transport is the record of
    how it was spawned — not something the settler gets to decide."""
    transport_before = adopted.agent_transport
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    settled = db_session.get(AgentRun, adopted.id)
    assert settled.agent_transport is transport_before
    assert settled.external_harness is None


def test_the_orphaned_output_files_are_gone_but_the_log_rows_are_not(db_session: Session, adopted):
    """S6.8 plus the reason it is safe: the files are deleted only because the
    transcript already lives in `run_log_lines`. Deleting them while the rows
    are still unwritten would destroy the only copy."""
    _write_output(adopted, out="the record\n" + _report("pass"), rc="0")
    paths = paths_for(adopted.run_code, adopted.id)

    with mock.patch.object(run_resupervise, "liveness", _gone):
        run_resupervise.resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert "the record" in _log_lines(db_session, adopted)
    assert not paths.out.exists() and not paths.rc.exists()


def test_a_subprocess_that_is_still_writing_is_not_settled_as_gone(db_session: Session, adopted):
    """The liveness reading is about the RECORDED pid, not about the file.

    A real agent that is quiet for a beat is indistinguishable, from the file
    alone, from one that has finished. If anything in the loop reaches for "the
    file stopped growing" as its terminator, this run settles while the agent
    is still working — and the orchestrator commits a half-finished tree.
    """
    paths = paths_for(adopted.run_code, adopted.id)
    paths.out.write_text("quiet for a while\n", encoding="utf-8")
    writer = subprocess.Popen(
        ["/bin/sh", "-c", f'sleep 1; printf "spoke again\\n" >> {str(paths.out)!r}']
    )
    beats = {"n": 0}

    def readings(*_args, **_kwargs) -> ProcessState:
        beats["n"] += 1
        if writer.poll() is None:
            return ProcessState.ALIVE
        paths.out.write_text(
            paths.out.read_text(encoding="utf-8") + _report("pass"), encoding="utf-8"
        )
        paths.rc.write_text("0", encoding="utf-8")
        return ProcessState.GONE

    try:
        with mock.patch.object(run_resupervise, "liveness", readings):
            run_resupervise.resupervise(adopted.id, interval_seconds=0.05)
    finally:
        writer.wait(timeout=30)

    db_session.expire_all()
    texts = _log_lines(db_session, adopted)
    assert "quiet for a while" in texts
    assert "spoke again" in texts, "the loop ended on the file going quiet, not on the pid"
