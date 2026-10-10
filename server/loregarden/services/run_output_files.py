"""Where a detached agent writes, and how the control plane reads it back.

The server used to hold the agent's pipe: `stdout=PIPE` means the *server* is
the reader, so when the server goes the pipe has no reader and everything the
agent says from that moment is lost. Here the agent writes its own stdout,
stderr and exit code into three files, and a tailer — in this process or in the
one that replaces it after a restart — reads them back.

Three details in here are load-bearing rather than incidental:

* **The wrapper must end `exit "$rc"`.** Ending at `printf %s "$rc" > rc` makes
  `printf` the last command in the list, so `sh` exits with *printf's* status —
  0 whenever the write succeeded — and every print-mode run would report
  SUCCEEDED, crashed agents included.
* **The file stem carries a run-id suffix.** `run_code` is
  `"run_" + secrets.token_hex(3)` with no uniqueness constraint, and `.rc` is
  what decides a reattached run's status. A shared stem would let a reattaching
  server read a *dead* run's exit code and settle a *live* run from it, then
  commit the whole working tree on the strength of it.
* **`readline() is None` never means "the writer finished".** On a regular file
  EOF is the normal state of a file being appended to.

`RunOutputTail` deliberately does NOT live beside `SubprocessLineReader`. That
reader returns `None` for both "nothing ready" and "writer closed" — on a pipe
those really are one event — and on an empty read it flushes a buffered partial
*as a terminated line*. On a regular file that splits a `stream_event` JSON
object in half. Housing a file tailer next to it is how someone later reuses
the wrong one.
"""

from __future__ import annotations

import logging
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import AgentRun, AgentTransport
from loregarden.services.run_lease import SUPERVISED

logger = logging.getLogger(__name__)

#: How much is read from the file per syscall. A single line can be far larger
#: than this — the largest `log` artifact in the live database is 320,619 bytes
#: and one `stream_event` carrying a tool result is plausibly a megabyte — so
#: `readline` keeps reading until it finds a newline rather than emitting when
#: a buffer fills.
_CHUNK_BYTES = 65536

#: The one place the tmux session prefix is spelled.
_SESSION_PREFIX = "lg-"


def run_file_stem(run_code: str, run_id: str) -> str:
    """The collision-free name every per-run file and session is built from."""
    return f"{run_code}-{run_id[:8]}"


@dataclass(frozen=True)
class RunOutputPaths:
    """The files one detached run owns. `out`/`err` are never merged — see
    `wrap_for_files`. `prompt` is the run's stdin, written by this process
    *before* the spawn and read by the child through a `<` redirect.

    The prompt is a file rather than a pipe because a pipe needs a writer that
    outlives the spawn, and on the tmux transport there is no pipe to write to
    at all: a pane's stdin is the pane tty, so an agent reading stdin would wait
    for a human who is not there. One file is what makes both transports deliver
    the same prompt, and it removes the pipe-buffer ordering race on the FILE
    transport as a side effect.
    """

    out: Path
    err: Path
    rc: Path
    prompt: Path
    env: Path


def paths_for(run_code: str, run_id: str) -> RunOutputPaths:
    root = Path(settings.run_log_dir)
    stem = run_file_stem(run_code, run_id)
    return RunOutputPaths(
        out=root / f"{stem}.out",
        err=root / f"{stem}.err",
        rc=root / f"{stem}.rc",
        prompt=root / f"{stem}.prompt",
        env=root / f"{stem}.env",
    )


def ensure_dir() -> None:
    """Create `run_log_dir`. Called before any spawn: a shell redirect cannot
    create its own parent, and the failure reads as the agent refusing to start."""
    Path(settings.run_log_dir).mkdir(parents=True, exist_ok=True)


def tmux_session_name(stem: str) -> str:
    return f"{_SESSION_PREFIX}{stem}"


def attach_command(run: AgentRun) -> str:
    """The command an operator pastes to watch this run, or "" when there is none.

    Composed here rather than in the client: the client cannot know the session
    name, and `""` is how it knows to render no control at all instead of a
    command that would fail. A FILE-transport run has no session; a settled run's
    session is gone with it.
    """
    if run.agent_transport is not AgentTransport.TMUX:
        return ""
    if run.status not in SUPERVISED:
        return ""
    session = tmux_session_name(run_file_stem(run.run_code, run.id))
    return f"tmux attach -t {session}"


def write_prompt_file(paths: RunOutputPaths, prompt: str) -> None:
    """Put this run's stdin on disk, before the spawn that reads it.

    Raises rather than degrading: an agent that is handed no prompt does not
    fail, it *waits* — on the tmux transport until the hard cap, reported as a
    timeout. A prompt that cannot be written must stop the spawn instead.
    """
    paths.prompt.write_text(prompt, encoding="utf-8")


#: `.env` is created with these bits and no others, in one `os.open` rather
#: than a write followed by a `chmod`: the values in it are the run's
#: credentials, and a world-readable window — however short — is a window.
_ENV_FILE_MODE = 0o600


def write_env_file(paths: RunOutputPaths, env: dict[str, str]) -> None:
    """This run's environment, as a file only its owner can read.

    It exists for the tmux transport, which cannot be handed an environment the
    way `Popen(env=...)` can: a pane is forked by the tmux *server*, so the
    environment has to travel in the pane command. Spelling the values there
    puts every one of them — `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`,
    `GH_TOKEN` — into argv, where `ps` shows them to every user on the host for
    the length of the client call and `tmux list-panes -F
    '#{pane_start_command}'` shows them for the session's whole life. A file
    sourced by the pane's own shell keeps the environment exact and keeps the
    values off every process table.

    Each line is `name=<shlex-quoted value>`, so a value carrying a newline, a
    quote or a `$` survives `.`-sourcing verbatim. Raises rather than
    degrading: a pane that sourced half an environment would run under a
    half-built identity, which is the defect this whole file exists to rule out.
    """
    body = "".join(f"{name}={shlex.quote(value)}\n" for name, value in env.items())
    descriptor = os.open(paths.env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _ENV_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)


def wrap_for_files(
    argv: list[str], paths: RunOutputPaths, *, stdin_from_prompt: bool = False
) -> list[str]:
    """`argv`, redirected into `paths` by the child itself.

    Every argv element and every path is `shlex.quote`d: the stage prompt can be
    an argv element carrying newlines, and `run_log_dir` is an operator-settable
    path interpolated into a shell string, so both are injection surfaces.

    `stdin_from_prompt` redirects the child's stdin from `paths.prompt`, which
    `write_prompt_file` must already have written. That is what makes the prompt
    transport-blind: a tmux pane has no stdin but it does have a filesystem. It
    also gives the agent a real EOF, which a pane tty never produces.

    The trailing `exit "$rc"` is the whole reason this is three commands rather
    than two — see this module's docstring.
    """
    joined = " ".join(shlex.quote(element) for element in argv)
    out, err, rc = (shlex.quote(str(path)) for path in (paths.out, paths.err, paths.rc))
    stdin = f" < {shlex.quote(str(paths.prompt))}" if stdin_from_prompt else ""
    script = f'{joined}{stdin} > {out} 2> {err}; rc=$?; printf %s "$rc" > {rc}; exit "$rc"'
    return ["sh", "-c", script]


def read_output_text(path: Path) -> str:
    """One of this run's output files as text, or "" when there is no such file.

    Shared by the live path and the reattached one so they cannot disagree about
    what a run's stderr was. `errors="replace"`: the agent writes whatever the
    tools it shelled out to wrote, and a strict decode would raise AFTER the
    agent finished — turning a completed run into an exception with no status.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # silent-ok: an agent that wrote nothing to this stream leaves no file,
        # and "" is the truthful reading of that.
        return ""
    except OSError:
        logger.warning(
            "Could not read %s; this run's output from it is reported empty", path, exc_info=True
        )
        return ""


def recorded_exit_status(path: Path) -> int | None:
    """The agent's exit code as the wrapper recorded it, or None for "never found out".

    Strict `int` of the stripped text, deliberately unforgiving: `0.0` and `0x0`
    are what a truncated write looks like, and a reader reaching for `float()`
    or `int(x, 0)` would call them success. None is not a zero — the settling
    caller turns it into a FAILED run that says so.

    Read through one function by both settlement paths, because the live path
    and the reattached path deciding "succeeded" differently for the same run is
    the divergence `exit "$rc"` exists to rule out.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # silent-ok: no `.rc` is the expected state of a run still in flight, and
        # of one whose wrapper died before writing it. Both mean "no exit code",
        # which is what None says.
        return None
    except OSError:
        logger.warning("Could not read the exit-code file %s", path, exc_info=True)
        return None
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Exit-code file %s holds %r, which is not an exit code", path, raw[:40])
        return None


class RunOutputTail:
    """Read complete lines out of a file something else is still appending to.

    The contract, all of it normative:

    1. `readline` returns exactly one ``"\\n"``-terminated line, or None.
    2. None means "nothing complete is available right now". It NEVER means the
       writer finished. A partial line stays buffered and is never returned as
       terminated.
    3. A missing file is not an error: None until it appears.
    4. At EOF it sleeps `timeout` and returns None, which is what paces a
       supervising loop's beat.
    5. `offset` is the byte position just past the last byte handed out, so it
       is a resumable boundary.
    6. `flush_partial` returns any buffered remainder as a line and clears it.
       Legal ONLY once the writer is known gone — it is how the last line of a
       file with no trailing newline survives, and calling it while the writer
       lives is exactly the corruption (2) exists to prevent.
    """

    def __init__(self, path: Path, *, start_offset: int = 0) -> None:
        self._path = path
        self._offset = start_offset
        self._buffer = b""
        self._handle = None

    @property
    def offset(self) -> int:
        """Bytes consumed: always a line boundary, except after `flush_partial`."""
        return self._offset

    def readline(self, *, timeout: float) -> str | None:
        line = self._take_line()
        if line is not None:
            return line
        while True:
            chunk = self._read_chunk()
            if not chunk:
                break
            self._buffer += chunk
            line = self._take_line()
            if line is not None:
                return line
        if timeout > 0:
            time.sleep(timeout)
        return None

    def flush_partial(self) -> str | None:
        """The buffered remainder as a line, once. None when there is none."""
        if not self._buffer:
            return None
        raw, self._buffer = self._buffer, b""
        self._offset += len(raw)
        return raw.decode("utf-8", errors="replace") + "\n"

    def _take_line(self) -> str | None:
        index = self._buffer.find(b"\n")
        if index < 0:
            return None
        raw = self._buffer[: index + 1]
        self._buffer = self._buffer[index + 1 :]
        self._offset += len(raw)
        # `errors="replace"` rather than strict: the agent writes whatever the
        # tools it shelled out to wrote, truncated multi-byte sequences
        # included, and a decode error raised from inside a live loop kills the
        # run for a reason that has nothing to do with the agent.
        return raw.decode("utf-8", errors="replace")

    def _read_chunk(self) -> bytes:
        """Whatever is past `offset + buffered`, or b"" when there is nothing.

        Seeks explicitly rather than relying on the handle's position, so a file
        truncated or deleted under the tailer yields b"" instead of raising —
        the orphan sweep deletes these files, and this runs on a live loop's
        path where an exception settles nothing.
        """
        if self._handle is None:
            try:
                self._handle = self._path.open("rb")
            except OSError:
                # Not cached: the spawn and the first write race this
                # construction, so the file appearing later is the normal case.
                return b""
        try:
            self._handle.seek(self._offset + len(self._buffer))
            return self._handle.read(_CHUNK_BYTES)
        except OSError:
            return b""
