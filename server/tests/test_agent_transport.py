"""How the agent is detached, and what the run record says about it (spec S8).

Durability is already `start_new_session=True` plus the output files. tmux buys
one thing those do not: an operator can `tmux attach` to a live agent. It is
also strictly *less* reliable — its server can die independently of both the
agent and loregarden — which is why it is sequenced last and is the first thing
to cut if the ticket shrinks.

Both transports run the IDENTICAL wrapper. That is what keeps the tailing loop,
the settlement path and the stop path transport-blind, and it is asserted here
rather than assumed, because a tmux-only branch anywhere downstream is how the
stop path gets a second way to be wrong.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.agents.executors.agent_spawn import (
    SpawnedAgent,
    resolve_transport,
    spawn_agent,
)
from loregarden.config import settings
from loregarden.db import versions
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migration_registry import import_submodules
from loregarden.models.domain import AgentTransport, DoctorCheck, DoctorStatus, Workspace
from loregarden.models.domain.process_enums import ProcessState
from loregarden.services import doctor
from loregarden.services.run_output_files import (
    paths_for,
    run_file_stem,
    tmux_session_name,
    wrap_for_files,
)
from sqlmodel import Session, select

#: The stem the pure-string assertions use. Anything that SPAWNS takes its
#: identity from `run_key` instead — a module-level one gives every tmux test in
#: the module one session name, and `tmux new-session -d -s <existing>` exits 1
#: under `pytest -n` (see `run_key_fixture`).
STEM = "run_tpt001-7c1d2e3f"


@pytest.fixture(name="run_key")
def run_key_fixture() -> tuple[str, str]:
    """(run_code, run_id), unique to this test. The tmux server is host-global,
    so AC22's collision-free stem is what keeps parallel tests off each other's
    sessions — the same property, for the same reason, as in production."""
    unique = uuid4().hex[:8]
    return f"run_{unique[:6]}", f"{unique}-4a5b-6c7d-8e9f-0a1b2c3d4e5f"


has_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


@pytest.fixture(name="run_log_dir", autouse=True)
def run_log_dir_fixture(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "run-logs"
    monkeypatch.setattr(settings, "run_log_dir", target)
    return target


@pytest.fixture(name="tmux_cleanup")
def tmux_cleanup_fixture(run_key):
    """Kill the session this test created, whatever the assertions did."""
    yield
    subprocess.run(
        ["tmux", "kill-session", "-t", tmux_session_name(run_file_stem(*run_key))],
        capture_output=True,
        check=False,
    )


def _invocation(script: str, *, stdin_prompt: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        argv=[sys.executable, "-u", "-c", script],
        cwd=None,
        stdin_prompt=stdin_prompt,
        interactive=False,
        adapter="local",
        env={},
    )


def _spawn(
    run_key: tuple[str, str],
    transport: AgentTransport,
    script: str,
    *,
    stdin_prompt: str | None = None,
) -> SpawnedAgent:
    run_code, run_id = run_key
    return spawn_agent(
        _invocation(script, stdin_prompt=stdin_prompt),
        Path.cwd(),
        run_id=run_id,
        run_code=run_code,
        transport=transport,
    )


def _wait_for_rc(run_key: tuple[str, str], timeout: float = 20.0) -> str:
    """Block until `.rc` holds an exit code — CONTENT, not just existence.

    `printf %s "$rc" > rc` creates the file and then writes it, so a poll on
    `exists()` alone can read the empty window in between and report `""` as
    this run's exit status. Under `pytest -n` that window is wide enough to hit.
    Nothing in production reads `.rc` that early: both settlement paths read it
    only once the wrapper is gone, and the wrapper writes it before it exits.
    """
    paths = paths_for(*run_key)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        recorded = paths.rc.read_text() if paths.rc.exists() else ""
        if recorded:
            return recorded
        time.sleep(0.05)
    raise AssertionError("the agent never recorded an exit code")


# --- AC19: the vocabulary ----------------------------------------------------


def test_the_transport_is_a_closed_enum_with_the_wire_words():
    """AC19. The member is FILE, not SETSID: server and client share one
    vocabulary, and the UI criteria pin the wire words to 'tmux' | 'file' | ''.

    The mechanism is still setsid plus a file redirect; the NAME is what an
    operator reads. A closed set is not a string here — the `py-organization`
    gate rejects that.
    """
    assert AgentTransport.TMUX.value == "tmux"
    assert AgentTransport.FILE.value == "file"
    assert {member.value for member in AgentTransport} == {"tmux", "file"}


def test_the_transport_enum_lives_beside_the_other_process_vocabularies():
    """S8. Beside `ProcessState` and `DetachedStopOutcome`, re-exported from
    `models.domain`, because they answer about the same object."""
    assert AgentTransport.__module__ == "loregarden.models.domain.process_enums"
    assert ProcessState.__module__ == AgentTransport.__module__

    import loregarden.models.domain as domain

    assert domain.AgentTransport is AgentTransport


# --- AC21: the config default ------------------------------------------------


def test_the_default_transport_setting_means_auto_detect():
    """AC21. `None` is "decide at spawn", which is not the same as FILE."""
    assert settings.agent_detach_transport is None


def test_auto_detect_chooses_tmux_when_tmux_is_installed():
    with mock.patch("shutil.which", return_value="/opt/homebrew/bin/tmux"):
        assert resolve_transport(None) is AgentTransport.TMUX


def test_auto_detect_falls_back_to_files_without_tmux():
    with mock.patch("shutil.which", return_value=None):
        assert resolve_transport(None) is AgentTransport.FILE


@pytest.mark.parametrize("pinned", list(AgentTransport))
def test_a_pinned_transport_is_honoured_over_what_is_installed(pinned):
    """AC18. `settings.agent_detach_transport` pinned to FILE must not be
    quietly upgraded because tmux happens to exist on the host."""
    with mock.patch("shutil.which", return_value="/usr/bin/tmux"):
        assert resolve_transport(pinned) is pinned


def test_the_doctor_reports_the_fallback_before_a_run_rather_than_after_one(
    db_session: Session, tmp_path
):
    """AC21. Otherwise the fallback is inferred from a column after the fact."""
    workspace = db_session.exec(select(Workspace)).first()
    assert workspace

    with mock.patch("shutil.which", return_value=None):
        findings = doctor.run_checks(
            db_session,
            workspace,
            Path(workspace.repo_path),
            checks=(DoctorCheck.AGENT_DETACH_TRANSPORT,),
        )

    assert len(findings) == 1
    assert findings[0].status is not DoctorStatus.PASS
    assert "tmux" in findings[0].finding


def test_the_doctor_is_quiet_when_tmux_is_present(db_session: Session):
    workspace = db_session.exec(select(Workspace)).first()
    assert workspace

    with mock.patch("shutil.which", return_value="/usr/bin/tmux"):
        findings = doctor.run_checks(
            db_session,
            workspace,
            Path(workspace.repo_path),
            checks=(DoctorCheck.AGENT_DETACH_TRANSPORT,),
        )

    assert findings[0].status is DoctorStatus.PASS


# --- AC18: both transports, one wrapper --------------------------------------


def test_the_file_transport_runs_the_agent_and_records_its_output(run_log_dir, run_key):
    """AC18. The dependency-free path, which is the one that must always work."""
    spawned = _spawn(run_key, AgentTransport.FILE, "print('from the file transport')")

    assert spawned.transport_used is AgentTransport.FILE
    assert spawned.pid > 0
    assert spawned.handle is not None, "the FILE transport owns a Popen handle"
    assert _wait_for_rc(run_key) == "0"
    assert paths_for(*run_key).out.read_text() == "from the file transport\n"


def test_the_file_transport_pid_is_a_session_leader(run_log_dir, run_key):
    """AC17. `run_detached_stop`'s second identity guard is
    `os.getpgid(recorded_pid) == recorded_pid`, and the whole stop path rests
    on it. The recorded pid is now the `sh` wrapper's."""
    spawned = _spawn(run_key, AgentTransport.FILE, "import time; time.sleep(5)")
    try:
        assert os.getpgid(spawned.pid) == spawned.pid
    finally:
        os.killpg(os.getpgid(spawned.pid), 15)


@has_tmux
def test_the_tmux_transport_runs_the_agent_and_records_its_output(
    run_log_dir, run_key, tmux_cleanup
):
    """AC18. Same wrapper, same three files — only the parent differs."""
    spawned = _spawn(run_key, AgentTransport.TMUX, "print('from the tmux transport')")

    assert spawned.transport_used is AgentTransport.TMUX
    assert spawned.handle is None, "tmux owns the process; there is no Popen to hold"
    assert spawned.pid > 0
    assert _wait_for_rc(run_key) == "0"
    assert paths_for(*run_key).out.read_text() == "from the tmux transport\n"


@has_tmux
def test_tmux_ls_shows_the_runs_own_session(run_log_dir, run_key, tmux_cleanup):
    """AC18. This is what makes `attach_command` usable: the session is there."""
    _spawn(run_key, AgentTransport.TMUX, "import time; print('up', flush=True); time.sleep(5)")

    listed = subprocess.run(["tmux", "ls"], capture_output=True, text=True, check=False)

    assert tmux_session_name(run_file_stem(*run_key)) in listed.stdout
    assert tmux_session_name(run_file_stem(*run_key)) == f"lg-{run_file_stem(*run_key)}"


@has_tmux
def test_the_tmux_pane_pid_is_also_a_session_leader(run_log_dir, run_key, tmux_cleanup):
    """AC17, the half that could break the already-shipped group kill.

    tmux `setsid()`s each pane and the wrapper is the pane process, so this
    should hold. If it does not, the only sanctioned remedy is per-transport
    dispatch inside `run_detached_stop` — never a tmux branch in `print_mode`
    or `agent_spawn`.
    """
    spawned = _spawn(run_key, AgentTransport.TMUX, "import time; time.sleep(5)")

    assert os.getpgid(spawned.pid) == spawned.pid


@has_tmux
def test_both_transports_run_the_identical_wrapper(run_log_dir, run_key):
    """AC18. One wrapper is what keeps S4-S6 transport-blind."""
    paths = paths_for(*run_key)
    wrapper = wrap_for_files(_invocation("print(1)").argv, paths)

    assert wrapper[:2] == ["sh", "-c"]
    assert 'exit "$rc"' in wrapper[2]
    assert str(paths.rc) in wrapper[2]


@has_tmux
def test_a_tmux_spawn_that_cannot_start_a_session_is_not_silently_a_file_run(run_log_dir, run_key):
    """A failure to detach must be visible. Reporting FILE for a run that
    nobody can attach to is the column asserting something untrue."""
    with (
        mock.patch(
            "loregarden.agents.executors.agent_spawn.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["tmux"]),
        ),
        pytest.raises(subprocess.SubprocessError),
    ):
        _spawn(run_key, AgentTransport.TMUX, "print('never runs')")


# --- AC1/AC3/AC18: the prompt arrives on EVERY transport ---------------------


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_every_transport_delivers_the_whole_prompt(transport, run_log_dir, run_key, tmux_cleanup):
    """AC1/AC3/AC18, parity rather than whatever this host happens to resolve.

    The tmux transport once ignored `stdin_prompt` entirely: a pane's stdin is
    the pane tty, so the agent read nothing, never saw EOF, and the run sat
    until the hard cap and was reported a timeout — on the auto-detect default
    of every host with tmux installed. Pinning each member in turn is what
    makes that a caught regression instead of a host-dependent one.

    The prompt is deliberately larger than any OS pipe buffer: that is the size
    a real stage prompt is, and it is the size the pipe-based delivery could
    not get right.
    """
    if transport is AgentTransport.TMUX and shutil.which("tmux") is None:
        pytest.skip("tmux is not installed on this host")
    prompt = "line of prompt text\n" * 20_000  # ~400KB
    script = "import sys\nsys.stdout.write(str(len(sys.stdin.read())))"

    spawned = _spawn(run_key, transport, script, stdin_prompt=prompt)

    assert spawned.transport_used is transport
    assert _wait_for_rc(run_key, timeout=60) == "0"
    paths = paths_for(*run_key)
    assert int(paths.out.read_text().strip()) == len(prompt)
    assert paths.prompt.read_text() == prompt


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_the_wrapper_redirects_stdin_only_when_there_is_a_prompt(transport, run_log_dir, run_key):
    """The redirect is per-run. A run with no prompt writes no prompt file, so
    an unconditional `< <prompt>` would fail the spawn with nothing to read."""
    paths = paths_for(*run_key)
    argv = _invocation("print(1)").argv

    assert str(paths.prompt) not in wrap_for_files(argv, paths)[2]
    assert str(paths.prompt) in wrap_for_files(argv, paths, stdin_from_prompt=True)[2]


# --- AC20: the migration -----------------------------------------------------

MIGRATION_ID = "20261008_agent_run_transport"
#: Re-parented at the integration merge. AC20 wrote this as
#: `20261007_layout_by_question`, which was the registry tip when the spec was
#: authored; main then landed `20261008_handoff_notice_kind` on that same
#: parent. Two branches off one tip make the apply order between them id order
#: rather than the order the live database actually saw, and the registry warns
#: about exactly that. This column has not shipped, so it moves to the end of
#: the shipped chain; the migrations ahead of it already applied live.
PREDECESSOR = "20261009_sonnet_for_mechanical_lanes"


def test_the_transport_column_ships_as_a_named_module():
    """AC20. The numbered list is CLOSED; new migrations are named modules
    registered with `@migration(id, after=...)`. This corrects the plan's
    step-5 guidance, which predates the registry."""
    import_submodules(versions)
    registered = {m.id: m for m in versions.REGISTRY.registered()}

    assert MIGRATION_ID in registered, sorted(registered)
    assert registered[MIGRATION_ID].after == PREDECESSOR


def test_no_numbered_migration_id_is_claimed():
    """AC20. Appending here re-runs a migration against databases that already
    applied it under its old name."""
    assert MIGRATION_ID not in SHIPPED_MIGRATION_IDS
    assert SHIPPED_MIGRATION_IDS[-1] == "0151_ticket_criteria_checked"


def test_the_transport_column_is_nullable_and_is_not_backfilled(isolated_db):
    """AC20. NULL is the truthful value for the 1,449 rows that predate it —
    and AC28's em dash is what the UI renders for them."""
    with isolated_db.connect() as conn:
        columns = {
            row[1]: row for row in conn.exec_driver_sql("PRAGMA table_info(agent_runs)").all()
        }

    assert "agent_transport" in columns
    assert columns["agent_transport"][3] == 0, "notnull must be 0"
    assert columns["agent_transport"][4] is None, "no default, so nothing is asserted by it"


# --- S8: the run record ------------------------------------------------------


def test_the_spawn_announces_its_transport_as_a_sys_line(run_log_dir, run_key):
    """AC29. In the existing SYS house style: `noun · detail`."""
    from loregarden.agents.executors import agent_spawn

    assert agent_spawn.transport_line(AgentTransport.FILE, STEM) == "transport · file"
    assert agent_spawn.transport_line(AgentTransport.TMUX, STEM) == (
        f"transport · tmux (session lg-{STEM})"
    )


# --- AC10: the output outlives the reader -------------------------------------


def test_the_agent_keeps_writing_after_its_spawner_lets_go(run_log_dir, run_key):
    """AC10's mechanism, without killing the test runner.

    The defect this replaces: `stdout=PIPE` means the SERVER is the reader, so
    when the server goes the pipe has no reader and everything the agent says
    from that moment is lost. Here the agent writes to a file, the handle is
    dropped, and a tailer that never saw the spawn reads what came after —
    which is exactly what a restarted server does.
    """
    from loregarden.services.run_output_files import RunOutputTail

    script = (
        "import sys, time\n"
        "for i in range(6):\n"
        "    sys.stdout.write(f'line {i}\\n'); sys.stdout.flush(); time.sleep(0.15)\n"
    )
    spawned = _spawn(run_key, AgentTransport.FILE, script)
    paths = paths_for(*run_key)

    # Whatever has landed so far is what the "old" server ingested.
    first = RunOutputTail(paths.out)
    seen: list[str] = []
    deadline = time.monotonic() + 10
    while not seen and time.monotonic() < deadline:
        line = first.readline(timeout=0.1)
        if line is not None:
            seen.append(line)
    assert seen, "the agent never wrote anything"
    resume_at = first.offset

    # The spawner holds no reader at all: stdout and stderr are DEVNULL, so
    # there is no pipe whose absent reader could stall or lose the agent.
    if spawned.handle is not None:
        assert spawned.handle.stdout is None
        assert spawned.handle.stderr is None

    assert _wait_for_rc(run_key) == "0"
    later = RunOutputTail(paths.out, start_offset=resume_at)
    resumed = []
    while (line := later.readline(timeout=0)) is not None:
        resumed.append(line.rstrip("\n"))

    assert resumed, "nothing the agent said after the handle was dropped survived"
    assert resumed[-1] == "line 5"
    assert "line 0" not in resumed, "the resume duplicated what the first reader had"
