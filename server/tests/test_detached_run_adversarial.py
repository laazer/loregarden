"""Adversarial cover for the detached-run path: the corners the green suite misses.

Written at `test-break` against commit 39abcc99, whose suite is green. Six tests
are RED over the three defects below -- four here and two appended to
`test_run_resupervise.py` -- and each is red over something the green suite
cannot see because of how it is set up rather than because of what it asserts:

1. `tests/test_agent_transport.py` spawns tmux panes and never starts a tmux
   SERVER first. `env=` is handed to the tmux *client*; the pane is forked by
   the tmux *server*, whose environment was fixed by whichever `new-session`
   started it. On a host with no server the client's env becomes the server's
   env and every assertion passes — which is exactly the host the existing
   tests were written on. The second lane of a parallel stage is the first run
   that meets a server it did not create, and it inherits lane one's identity.
   So the setup here starts a server with a DIFFERENT marker before spawning.

2. The reattached settlement path builds a SECOND `RunLogStreamer` for one run
   (`run_resupervise._finalize_log`) and `_hydrate()` clears `_stream_buffer`.
   `finalize` opens with `_flush_stream_buffer(force=True)`, so on the live path
   an unflushed partial becomes a log row and on the reattached path it is
   dropped. Every existing reattach test ends its `.out` with a line that
   forces a flush — a stage report, a plain line — so the buffer is always
   empty by the time the divergence would show.

3. The transport is resolved twice, and `cli.py` appends the SYS transport line
   BEFORE the spawn. A tmux spawn that raises therefore leaves a line in the
   feed asserting a session that never existed, and the line is composed from a
   second, independent reading of the host rather than from the transport
   `spawn_agent` actually used.

Every one of the six was proved satisfiable at `test-break`: a candidate remedy
per defect turned all six green (62 passed in these two modules) and was then
reverted, leaving the source byte-identical to 39abcc99. Two pieces of collateral
fell out and are the implementer's to expect, not to discover: deferring
`run_reattach`'s import removes the module-scope seam
`test_reattach_starts_resupervise_for_every_adopted_run` patches, and moving the
SYS transport line beside `record_agent_transport` produces 17 failures in
`test_print_mode_detached.py` from one cause -- `_CollectingStreamer` has no
`append`.

The rest are hardening: corners that pass today and are pinned so they cannot
regress silently — tmux's own quoting of a metacharacter argv, the tailer under
truncation and bare carriage returns, and `recorded_exit_status` over the shapes
a half-written `.rc` file actually takes.
"""

from __future__ import annotations

import json
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from loregarden.agents.executors.agent_spawn import SpawnedAgent, spawn_agent
from loregarden.config import settings
from loregarden.mcp.caller import ORCHESTRATED_ENV, RUN_ID_ENV
from loregarden.models.domain import AgentTransport
from loregarden.services.run_output_files import (
    RunOutputTail,
    paths_for,
    recorded_exit_status,
    run_file_stem,
    tmux_session_name,
    write_env_file,
)

has_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")

#: The marker a stale tmux server carries. Nothing this suite spawns may see it:
#: it stands in for the run id of the lane that happened to start the server.
POISON_RUN_ID = "a9f0c3d1-dead-4f00-9999-0000000000ff"


@pytest.fixture(name="run_log_dir", autouse=True)
def run_log_dir_fixture(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "run-logs"
    monkeypatch.setattr(settings, "run_log_dir", target)
    return target


def _invocation(
    script: str,
    *,
    run_id: str,
    stdin_prompt: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> SimpleNamespace:
    """A print-mode invocation carrying this run's identity, as `cli.py` builds it.

    `invocation.env` is the overlay that matters: `invocation_env` STRIPS
    `RUN_IDENTITY_ENV_VARS` out of `os.environ` precisely so a run's identity can
    only come from here. Anything that drops the overlay therefore does not
    degrade to the supervising process's value — it leaves the agent with
    whatever the process it was forked from happened to carry.
    """
    return SimpleNamespace(
        argv=[sys.executable, "-u", "-c", script],
        cwd=None,
        stdin_prompt=stdin_prompt,
        interactive=False,
        adapter="local",
        env={RUN_ID_ENV: run_id, ORCHESTRATED_ENV: "1", **(extra_env or {})},
    )


def _spawn(
    transport: AgentTransport,
    script: str,
    *,
    run_id: str,
    run_code: str,
    stdin_prompt: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> SpawnedAgent:
    return spawn_agent(
        _invocation(script, run_id=run_id, stdin_prompt=stdin_prompt, extra_env=extra_env),
        Path.cwd(),
        run_id=run_id,
        run_code=run_code,
        transport=transport,
    )


def _kill_session(stem: str) -> None:
    subprocess.run(
        ["tmux", "kill-session", "-t", tmux_session_name(stem)],
        capture_output=True,
        check=False,
    )


def _wait_for_rc(run_code: str, run_id: str, timeout: float = 20.0) -> str:
    """Block until `.rc` holds an exit code — CONTENT, not just existence.

    `printf %s "$rc" > rc` creates the file and then writes it, so a poll on
    `exists()` alone can read the empty window between the two and report `""`
    as this run's exit status. Nothing in production reads `.rc` that early:
    both settlement paths read it only once the wrapper is gone, and the
    wrapper writes it before it exits.
    """
    paths = paths_for(run_code, run_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        recorded = paths.rc.read_text(encoding="utf-8") if paths.rc.exists() else ""
        if recorded:
            return recorded
        time.sleep(0.05)
    raise AssertionError(f"{run_code} never recorded an exit code")


#: Print this run's identity as the agent itself sees it, which is the only
#: reading that matters: every MCP write the agent makes is attributed from it.
ECHO_RUN_ID = f"import os;print(os.environ.get({RUN_ID_ENV!r}, '<absent>'))"


@pytest.fixture(name="stale_tmux_server")
def stale_tmux_server_fixture():
    """A tmux SERVER that already exists, carrying a run id that is not ours.

    This is the fixture the existing tmux tests do not have, and the whole
    reason they are green over a broken default path. `tmux new-session` starts
    a server only when none is running; the server's environment is then frozen
    from whichever client started it. So the first lane of a parallel stage sets
    the environment every later pane inherits.

    Deliberately NOT `tmux kill-server`: this machine runs real agents in real
    panes, and killing the server would kill them. Creating one more session is
    enough — after this fixture a server certainly exists, and it certainly does
    not carry the run id the test is about to spawn.

    The decoy's own name carries a uuid for the same reason every run file does:
    `tmux new-session -d -s <existing>` exits 1, and this repo runs its suite
    under `pytest -n` against one host-global tmux server, so a literal name is
    a collision between workers rather than a session.
    """
    decoy = f"lg-stale-server-decoy-{uuid4().hex[:8]}"
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", decoy, "sh", "-c", "sleep 300"],
        env={**{RUN_ID_ENV: POISON_RUN_ID}, "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp"},
        capture_output=True,
        check=True,
    )
    try:
        yield
    finally:
        subprocess.run(["tmux", "kill-session", "-t", decoy], capture_output=True, check=False)


# --- AC1 / AC18: the pane runs under THIS run's identity ---------------------


@has_tmux
def test_a_tmux_pane_carries_this_runs_env_and_not_an_older_servers(stale_tmux_server):
    """AC1 ("the spawn keeps `cwd` and `env`") and AC18 ("with tmux present a
    run must still complete correctly").

    RED at 39abcc99. `_spawn_in_tmux` passes `env=` to `subprocess.run(["tmux",
    "new-session", ...])`, which is the tmux CLIENT. The pane is forked by the
    tmux SERVER and inherits the server's environment. With a server already
    running, `invocation.env` — run identity, MCP config, model and effort — is
    dropped entirely.

    It does not present as a crash. An expired claude OAuth token exits 0, so a
    lane running under another lane's identity reads as an empty SUCCEEDED run.
    """
    run_id = str(uuid4())
    run_code = "run_env001"
    stem = run_file_stem(run_code, run_id)
    try:
        _spawn(AgentTransport.TMUX, ECHO_RUN_ID, run_id=run_id, run_code=run_code)
        assert _wait_for_rc(run_code, run_id) == "0"
        seen = paths_for(run_code, run_id).out.read_text(encoding="utf-8").strip()
    finally:
        _kill_session(stem)

    assert seen != POISON_RUN_ID, (
        "the pane ran under the tmux server's environment, so this run's agent "
        "is making MCP writes under another run's id"
    )
    assert seen == run_id, f"the pane saw {seen!r} rather than this run's id"


@has_tmux
def test_two_concurrent_tmux_lanes_do_not_share_one_run_identity(stale_tmux_server):
    """AC18. A parallel stage is the shape that meets this: three members, three
    run ids, one tmux server. This ticket's own `plan` stage has three.

    RED at 39abcc99, and red for the lanes rather than for the server: lane one
    starts while a server already exists, so *neither* lane sees its own id.
    """
    lanes = [(str(uuid4()), "run_lane01"), (str(uuid4()), "run_lane02")]
    stems = [run_file_stem(code, rid) for rid, code in lanes]
    try:
        for run_id, run_code in lanes:
            _spawn(AgentTransport.TMUX, ECHO_RUN_ID, run_id=run_id, run_code=run_code)
        seen = {}
        for run_id, run_code in lanes:
            assert _wait_for_rc(run_code, run_id) == "0"
            seen[run_code] = paths_for(run_code, run_id).out.read_text(encoding="utf-8").strip()
    finally:
        for stem in stems:
            _kill_session(stem)

    assert len(set(seen.values())) == len(lanes), (
        f"two lanes reported the same run identity: {seen}"
    )
    for run_id, run_code in lanes:
        assert seen[run_code] == run_id, f"{run_code} ran as {seen[run_code]!r}"


@has_tmux
def test_a_tmux_pane_runs_in_this_runs_cwd(stale_tmux_server, tmp_path):
    """The half of AC1 that the review found is NOT affected, pinned so a repair
    of the `env` half cannot quietly break it.

    `cwd` survives because the tmux client resolves it and passes the result to
    the server as the session's start directory. A remedy that moved the whole
    invocation into `env -i …` without carrying the directory would lose it.
    """
    run_id = str(uuid4())
    run_code = "run_cwd001"
    stem = run_file_stem(run_code, run_id)
    workdir = tmp_path / "agent-cwd"
    workdir.mkdir()
    invocation = _invocation("import os;print(os.getcwd())", run_id=run_id)
    invocation.cwd = str(workdir)
    try:
        spawn_agent(
            invocation, Path.cwd(), run_id=run_id, run_code=run_code, transport=AgentTransport.TMUX
        )
        assert _wait_for_rc(run_code, run_id) == "0"
        seen = paths_for(run_code, run_id).out.read_text(encoding="utf-8").strip()
    finally:
        _kill_session(stem)

    assert Path(seen).resolve() == workdir.resolve()


@has_tmux
def test_a_tmux_pane_receives_an_argv_full_of_shell_metacharacters(stale_tmux_server):
    """AC3 over the TMUX transport, which the existing quoting tests never use.

    The hazard: tmux takes `new-session [shell-command…]` as a LIST and joins it
    into one string for its own `/bin/sh`, so the wrapper is parsed twice —
    once by tmux's joiner and once by the pane's shell. tmux escapes while
    joining, so this passes today. Pinned because the obvious refactor — build
    one command string and hand tmux that — silently removes the escaping, and
    the failure would be a mangled wrapper rather than an error.
    """
    run_id = str(uuid4())
    run_code = "run_meta01"
    stem = run_file_stem(run_code, run_id)
    hostile = "a b 'c' \"d\" $HOME `id` ; echo PWNED | cat & #"
    invocation = _invocation("import sys;print(sys.argv[1])", run_id=run_id)
    invocation.argv = [sys.executable, "-u", "-c", "import sys;print(sys.argv[1])", hostile]
    try:
        spawn_agent(
            invocation, Path.cwd(), run_id=run_id, run_code=run_code, transport=AgentTransport.TMUX
        )
        assert _wait_for_rc(run_code, run_id) == "0"
        seen = paths_for(run_code, run_id).out.read_text(encoding="utf-8")
    finally:
        _kill_session(stem)

    # Exact equality is the whole assertion: the hostile string contains the
    # bytes a re-parse would act on (`;`, `|`, `&`, backticks, `$HOME`), so
    # getting it back verbatim is proof that nothing between tmux and the pane
    # treated them as syntax.
    assert seen == f"{hostile}\n", "the wrapper was re-parsed between tmux and the pane"


@has_tmux
def test_a_tmux_run_whose_log_dir_contains_a_space_still_records_its_output(
    stale_tmux_server, tmp_path, monkeypatch
):
    """AC3's path-quoting half over TMUX. Same double-parse exposure as above,
    and the one the existing test exercises only on the FILE transport."""
    monkeypatch.setattr(settings, "run_log_dir", tmp_path / "run logs with spaces")
    run_id = str(uuid4())
    run_code = "run_spc001"
    stem = run_file_stem(run_code, run_id)
    try:
        _spawn(AgentTransport.TMUX, "print('landed')", run_id=run_id, run_code=run_code)
        assert _wait_for_rc(run_code, run_id) == "0"
        seen = paths_for(run_code, run_id).out.read_text(encoding="utf-8")
    finally:
        _kill_session(stem)

    assert seen == "landed\n"


# --- The pane command is argv, and argv is public ----------------------------

#: A stand-in for the real thing. The live values are CLAUDE_CODE_OAUTH_TOKEN,
#: CURSOR_API_KEY, OPENAI_API_KEY and GH_TOKEN, every one of which
#: `invocation_env` passes through to the agent.
SECRET_ENV = "LOREGARDEN_TEST_FAKE_TOKEN"
SECRET_VALUE = "sk-test-5f3c9a-not-a-real-token"


@has_tmux
def test_a_tmux_pane_receives_its_secrets_without_putting_them_in_argv(stale_tmux_server):
    """The pane must run under THIS run's environment (the finding above) without
    that environment becoming readable by every user on the host.

    `_under_env` has to exist, because a pane inherits the tmux *server's*
    environment. But its first shape spelled the values into the pane command,
    which is argv: `ps` shows it for the length of the client call, and
    `tmux list-panes -F '#{pane_start_command}'` shows it for the session's
    whole life. The pre-change `Popen(env=...)` exposed nothing, so that would
    have been a regression introduced by the fix for one.

    Both halves are asserted together on purpose: an implementation can satisfy
    either one alone — by dropping the environment, or by spelling it in argv.
    """
    run_id = str(uuid4())
    run_code = "run_sec001"
    stem = run_file_stem(run_code, run_id)
    script = f"import os, time;print(os.environ.get({SECRET_ENV!r}, '<absent>'));time.sleep(3)"
    try:
        _spawn(
            AgentTransport.TMUX,
            script,
            run_id=run_id,
            run_code=run_code,
            extra_env={SECRET_ENV: SECRET_VALUE},
        )
        listed = subprocess.run(
            ["tmux", "list-panes", "-t", tmux_session_name(stem), "-F", "#{pane_start_command}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert _wait_for_rc(run_code, run_id) == "0"
        seen = paths_for(run_code, run_id).out.read_text(encoding="utf-8").strip()
    finally:
        _kill_session(stem)

    assert seen == SECRET_VALUE, f"the agent was handed {seen!r} instead of its token"
    assert not paths_for(run_code, run_id).env.exists(), (
        "the env file outlived the pane's sourcing of it; it holds this host's "
        "tokens and nothing reads it after `exec`"
    )
    assert SECRET_VALUE not in listed.stdout, (
        "the run's token is in the pane command, where `ps` and `tmux "
        "list-panes` show it to every user on the host"
    )


@has_tmux
def test_the_env_file_the_pane_sources_is_readable_only_by_its_owner():
    """The file that replaces argv must not be the same exposure on disk.

    `run_log_dir` is an ordinary directory under the repo, so a 0644 env file
    would hand the same tokens to anything that can read the tree — and a
    `write_text` followed by a `chmod` leaves a window where it is exactly
    that. Asserted on the mode bits rather than on the call shape.
    """
    paths = paths_for("run_mod001", str(uuid4()))
    paths.out.parent.mkdir(parents=True, exist_ok=True)

    write_env_file(paths, {SECRET_ENV: SECRET_VALUE})

    assert stat.S_IMODE(paths.env.stat().st_mode) == 0o600
    assert paths.env.read_text(encoding="utf-8").strip() == (
        f"{SECRET_ENV}={shlex.quote(SECRET_VALUE)}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        "with a space",
        'quote\'and"double"',
        "dollar $HOME and `id`",
        "newline\nand\ttab",
        "trailing\n",
        "",
    ],
)
def test_every_shape_of_env_value_survives_being_sourced(value, tmp_path, monkeypatch):
    """The env file is shell syntax, so every value in it is an injection
    surface and a quoting one. A `$`, a backtick or a newline that the pane's
    `.` re-parses is both a mangled environment and a way to run a command as
    the agent — so this asserts the value the POSIX shell reads back, not the
    bytes written.
    """
    monkeypatch.setattr(settings, "run_log_dir", tmp_path / "logs")
    paths = paths_for("run_qte001", str(uuid4()))
    paths.out.parent.mkdir(parents=True, exist_ok=True)

    write_env_file(paths, {SECRET_ENV: value, "SECOND": "intact"})
    readback = subprocess.run(
        ["/bin/sh", "-c", f'set -a; . {shlex.quote(str(paths.env))}; printf %s "${SECRET_ENV}"'],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert readback.stdout == value


# --- AC13 / S8: one authoritative transport, resolved once -------------------


def test_the_transport_is_resolved_once_by_whoever_performs_the_spawn():
    """AC13 ("introduces no new routing logic") and AC29's accuracy.

    `cli.py` resolves the transport independently and appends the SYS line
    BEFORE calling `run_print_mode`, which resolves it again. Two readings of
    one fact, and the announcement is made from the reading that did not spawn
    anything: a tmux spawn that raises leaves `transport · tmux (session
    lg-<stem>)` in the feed for a session that never existed.

    `spawn_agent` already returns `transport_used`, and `record_agent_transport`
    is already called with it — the line belongs beside that, where the
    authoritative answer is.
    """
    source = Path(__file__).parents[1] / "loregarden/agents/executors/cli.py"
    text = source.read_text(encoding="utf-8")

    assert "resolve_transport" not in text, (
        "cli.py resolves the transport a second time; the spawn's "
        "`transport_used` is the authoritative one"
    )
    assert "transport_line" not in text, (
        "cli.py announces a transport before the spawn that decides it; the "
        "line belongs beside record_agent_transport"
    )


def test_adopting_a_surviving_run_does_not_import_the_orchestrator():
    """AC11 ("`run_reattach` keeps only its predicates").

    The existing test asserts `not hasattr(run_reattach, "_watch")`, which
    passes whether or not the orchestrator came back with the import. S6 split
    `resupervise` out specifically to keep `OrchestrationService` off the
    boot-reaper path; `run_reattach` imports it at module scope, so it is back.

    Checked in a fresh interpreter, because anything this suite already imported
    would make `sys.modules` say yes for an unrelated reason.
    """
    probe = (
        "import sys;"
        "import loregarden.services.run_reattach;"
        "print('loregarden.services.orchestration' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[1],
    )

    assert result.stdout.strip() == "False", (
        "importing the boot reaper pulls in OrchestrationService, which is what "
        "the S6 split exists to prevent"
    )


# --- AC2: what a half-written .rc file actually looks like --------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("0\n", 0),
        ("  3  \n", 3),
        ("1", 1),
        ("130", 130),
        ("-1", -1),
        ("", None),
        ("\n", None),
        ("0.0", None),
        ("0x0", None),
        ("+0", 0),
        ("00", 0),
        ("ok", None),
        ("0 0", None),
        ("\x00", None),
        ("true", None),
    ],
)
def test_the_exit_code_reader_is_unforgiving_about_what_counts_as_zero(raw, expected, tmp_path):
    """AC2 and AC12. `0.0` and `0x0` are what a truncated or interrupted write
    looks like, and a reader reaching for `float()` or `int(x, 0)` would settle
    a crashed run as SUCCEEDED and commit the working tree on it.

    None is not a zero: the settling caller turns it into a FAILED run carrying
    the verbatim sentence AC12 pins.
    """
    path = tmp_path / "run.rc"
    path.write_text(raw, encoding="utf-8")

    assert recorded_exit_status(path) is expected or recorded_exit_status(path) == expected
    if expected is None:
        assert recorded_exit_status(path) is None


def test_a_directory_where_the_exit_code_should_be_is_not_read_as_success(tmp_path):
    """AC2. An OSError that is not FileNotFoundError must still mean "never
    found out" rather than falling through to a zero."""
    path = tmp_path / "run.rc"
    path.mkdir()

    assert recorded_exit_status(path) is None


# --- AC4: the tailer's corners ----------------------------------------------


def test_a_bare_carriage_return_is_not_a_line_terminator(tmp_path):
    """AC4. The contract says `\\n`-terminated lines only. A progress bar
    written with bare `\\r` is one unterminated line, and returning it as
    terminated would split a `stream_event` payload mid-JSON."""
    path = tmp_path / "out"
    path.write_bytes(b"10%\r20%\r30%\r")
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) is None
    assert tail.offset == 0, "the offset moved over text that was never handed out"
    assert tail.flush_partial() == "10%\r20%\r30%\r\n"


def test_crlf_is_one_line_and_keeps_its_carriage_return(tmp_path):
    """AC4/AC7. The tailer is a byte boundary reader, not a text normalizer:
    `append_stream_line` strips, so a CR that reached it changes nothing, while
    a tailer that rewrote bytes would make the file and the rows disagree."""
    path = tmp_path / "out"
    path.write_bytes(b"first\r\nsecond\r\n")
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) == "first\r\n"
    assert tail.readline(timeout=0) == "second\r\n"
    assert tail.offset == 15


def test_a_nul_byte_mid_line_does_not_stop_the_tailer(tmp_path):
    """AC4. An agent that shells out to a tool writing binary is not a reason
    for a live loop to raise — the run would die for something that has nothing
    to do with the agent."""
    path = tmp_path / "out"
    path.write_bytes(b"before\x00after\n")
    tail = RunOutputTail(path)

    line = tail.readline(timeout=0)
    assert line is not None
    assert line.endswith("\n")
    assert "before" in line and "after" in line


def test_a_file_truncated_under_the_tailer_yields_nothing_rather_than_garbage(tmp_path):
    """AC4. The orphan sweep and `_delete_output_files` both remove these files,
    so a tailer whose offset is past a shrunken EOF is a real state. It must
    read as "nothing available", never as a resynchronised mid-line fragment."""
    path = tmp_path / "out"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    tail = RunOutputTail(path)
    # Drained to empty first, deliberately: one `_read_chunk` pulls up to
    # _CHUNK_BYTES, so stopping mid-file would leave already-read bytes in the
    # buffer and the next `readline` would legitimately hand them out. The state
    # under test is an offset past a shrunken EOF, not an unread buffer.
    while tail.readline(timeout=0) is not None:
        pass
    at = tail.offset
    assert at == len("one\ntwo\nthree\n")

    path.write_text("x\n", encoding="utf-8")

    assert tail.readline(timeout=0) is None, (
        "the tailer resynchronised onto a file that had shrunk beneath it"
    )
    assert tail.offset == at, "the offset moved over a file that had shrunk beneath it"


def test_a_deleted_file_does_not_raise_mid_loop(tmp_path):
    """AC4 ("a missing file returns None rather than raising"), for the file
    going missing AFTER the handle was opened rather than before."""
    path = tmp_path / "out"
    path.write_text("one\n", encoding="utf-8")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "one\n"

    path.unlink()

    assert tail.readline(timeout=0) is None


def test_a_line_far_longer_than_one_read_chunk_arrives_in_one_piece(tmp_path):
    """AC4/AC7. A `stream_event` carrying a long assistant message is a single
    JSON line much larger than the read chunk. Splitting it would hand
    `append_stream_line` half an object, which it would log as a plain OUT line
    — a silently corrupted transcript rather than an error."""
    path = tmp_path / "out"
    payload = json.dumps({"type": "content_block_delta", "delta": {"text": "x" * 200_000}})
    path.write_text(payload + "\n", encoding="utf-8")
    tail = RunOutputTail(path)

    line = tail.readline(timeout=0)
    assert line is not None
    assert json.loads(line)["delta"]["text"] == "x" * 200_000
    assert tail.readline(timeout=0) is None


def test_flush_partial_is_idempotent_and_does_not_double_count_the_offset(tmp_path):
    """AC4/AC9. `_drain_to_end` calls it once, but the offset it leaves is
    persisted. A second call returning the same text would re-ingest the last
    line of every reattached run."""
    path = tmp_path / "out"
    path.write_text("tail with no newline", encoding="utf-8")
    tail = RunOutputTail(path)

    assert tail.readline(timeout=0) is None
    first = tail.flush_partial()
    assert first == "tail with no newline\n"
    after = tail.offset

    assert tail.flush_partial() is None
    assert tail.offset == after


def test_a_write_landing_between_a_none_and_the_next_read_is_not_lost(tmp_path):
    """AC4/AC5. `None` must mean "nothing complete right now", never "finished".
    The loop's correctness rests on the tailer picking up where it left off
    after an empty beat."""
    path = tmp_path / "out"
    path.write_text("one\n", encoding="utf-8")
    tail = RunOutputTail(path)
    assert tail.readline(timeout=0) == "one\n"
    assert tail.readline(timeout=0) is None

    with path.open("a", encoding="utf-8") as handle:
        handle.write("two\n")

    assert tail.readline(timeout=0) == "two\n"


def test_a_multibyte_character_split_across_the_newline_boundary_survives(tmp_path):
    """AC4/AC7. Byte-at-a-time arrival is the real shape of a pipe-less writer,
    and a tailer that decoded per chunk rather than per line would replace the
    halves of one character with two replacement characters."""
    path = tmp_path / "out"
    text = "naïve ☃ 🙂\n"
    raw = text.encode("utf-8")
    tail = RunOutputTail(path)
    seen: list[str] = []
    with path.open("wb") as handle:
        for index in range(len(raw)):
            handle.write(raw[index : index + 1])
            handle.flush()
            line = tail.readline(timeout=0)
            if line is not None:
                seen.append(line)

    assert seen == [text]
