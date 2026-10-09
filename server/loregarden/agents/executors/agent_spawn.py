"""Start a detached agent, on either transport, through one wrapper.

Lifted out of `print_mode` so that module keeps only the budget loop and the
result shaping. The two transports differ in exactly one thing — who the
wrapper's parent is — and they run the *identical* `sh -c` wrapper, which is
what keeps the tailing loop, the settlement path and the stop path
transport-blind. A tmux branch anywhere downstream would give the stop path a
second way to be wrong.

Survival is `start_new_session=True` plus the wrapper's own output files, both
of which the FILE transport has. tmux buys one thing neither does: an operator
can `tmux attach` to a live agent and watch it. It is also strictly *less*
reliable — its server can die independently of both the agent and loregarden,
and the honest consequence of that is a run with no `.rc`, settled FAILED
rather than assumed complete.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.cli_adapters import invocation_env
from loregarden.db import session as db_session_module
from loregarden.models.domain import AgentRun, AgentTransport
from loregarden.services.run_output_files import (
    RunOutputPaths,
    ensure_dir,
    paths_for,
    run_file_stem,
    tmux_session_name,
    wrap_for_files,
    write_env_file,
    write_prompt_file,
)
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpawnedAgent:
    """What the caller needs to supervise and to stop a detached agent.

    `pid` is the wrapper's, which is the session leader and the ancestor a
    `killpg` reaches the agent through. `handle` is None on tmux: tmux owns the
    process, so there is no `Popen` to hold and no child to reap.
    """

    pid: int
    transport_used: AgentTransport
    paths: RunOutputPaths
    handle: subprocess.Popen | None


def resolve_transport(pinned: AgentTransport | None) -> AgentTransport:
    """Which transport to use. `pinned` wins; None means auto-detect.

    A pinned FILE is never quietly upgraded because tmux happens to exist on the
    host — that setting is how an operator turns the less reliable transport off.
    """
    if pinned is not None:
        return pinned
    return AgentTransport.TMUX if shutil.which("tmux") else AgentTransport.FILE


def transport_line(transport: AgentTransport, stem: str) -> str:
    """The SYS line announcing how this run was spawned, in the house style.

    A line rather than a field somewhere: it sits in the feed at the point the
    spawn happened, next to the reattach markers, so a run that survived two
    restarts reads as a sequence rather than as one overwritten fact.
    """
    if transport is AgentTransport.TMUX:
        return f"transport · tmux (session {tmux_session_name(stem)})"
    return f"transport · {transport.value}"


def record_agent_transport(run_id: str, transport: AgentTransport) -> None:
    """Store how this run was detached, in its own short-lived session.

    Written at the same point the pid is recorded, and for the same reason: the
    session driving the run may sit in a transaction old enough that a reader on
    another connection — which is exactly who reattaches — would not see it.
    """
    try:
        with Session(db_session_module.engine) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return
            run.agent_transport = transport
            session.add(run)
            session.commit()
    except Exception:  # noqa: BLE001 — never fail a run over its own bookkeeping
        logger.warning("Could not record the transport for run %s", run_id, exc_info=True)


def spawn_agent(
    invocation,
    repo_root: Path,
    *,
    run_id: str,
    run_code: str,
    transport: AgentTransport,
) -> SpawnedAgent:
    """Start `invocation` detached, writing into this run's own three files."""
    ensure_dir()
    paths = paths_for(run_code, run_id)
    # Written before the spawn, on both transports: the wrapper redirects its
    # stdin from this file, so it has to exist by the time `sh` runs. A tmux
    # pane has no stdin but it does have a filesystem, which is the whole
    # reason the prompt travels as a file rather than through a pipe.
    prompt = invocation.stdin_prompt
    if prompt:
        write_prompt_file(paths, prompt)
    wrapped = wrap_for_files(invocation.argv, paths, stdin_from_prompt=bool(prompt))
    cwd = invocation.cwd or str(repo_root)
    env = invocation_env(invocation)
    if transport is AgentTransport.TMUX:
        # Only this transport needs the environment on disk: `Popen(env=...)`
        # hands it to the child directly, and a FILE-transport run should not
        # leave its credentials in a file nothing reads.
        write_env_file(paths, env)
        pid = _spawn_in_tmux(
            wrapped, cwd=cwd, env=env, paths=paths, stem=run_file_stem(run_code, run_id)
        )
        return SpawnedAgent(pid=pid, transport_used=transport, paths=paths, handle=None)
    proc = _spawn_detached(wrapped, cwd=cwd, env=env)
    return SpawnedAgent(pid=proc.pid, transport_used=transport, paths=paths, handle=proc)


def _spawn_detached(wrapped: list[str], *, cwd: str, env: dict[str, str]) -> subprocess.Popen:
    """The dependency-free transport: the wrapper as a session leader of its own.

    `start_new_session` is what detaches it. Without it the agent is in this
    process's group, so a Ctrl-C, a reload, or anything else that signals the
    group takes a turn that may be minutes in — and backend edits *require* a
    reload to be picked up, so that happens by design rather than by accident.

    All three standard streams are DEVNULL here. A pipe makes the server the
    reader or the writer, so when the server goes the pipe has no counterpart
    and everything the agent says — or everything it was still waiting to be
    told — is lost; that is the defect this ticket removes. The wrapper's own
    redirects supply stdout, stderr and the prompt.
    """
    return subprocess.Popen(
        wrapped,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        bufsize=0,
        start_new_session=True,
    )


#: Where `env` and `sh` live on both supported hosts. Spelled absolutely on
#: purpose: the pane command is resolved by the tmux SERVER's PATH, and the
#: server's environment is the thing this prefix exists to stop mattering.
_ENV_BIN = "/usr/bin/env"
_SH_BIN = "/bin/sh"


def _under_env(paths: RunOutputPaths, wrapped: list[str]) -> list[str]:
    """`wrapped`, prefixed so the pane runs under exactly this run's environment.

    `subprocess.run(..., env=env)` hands the environment to the tmux CLIENT. The
    pane is forked by the tmux SERVER, whose environment was frozen by whichever
    `new-session` started it — so the FIRST spawn on a host works and every
    later one inherits the first one's environment for as long as that server
    lives. For a parallel stage that means lanes two and three run under lane
    one's run identity, MCP config, model and effort, and every MCP write they
    make claims lane one. It does not present as a crash: an expired OAuth token
    exits 0, so the lane reads as an empty SUCCEEDED run.

    `env -i` rather than tmux's own `-e K=V`: `invocation_env` STRIPS
    `RUN_IDENTITY_ENV_VARS` and `STATE_BINDING_ENV_VARS` out of the supervising
    process's environment precisely so a run's identity can only come from the
    invocation's overlay, and `-e` can only *add* — a stripped variable the
    server happens to carry would survive it. `-i` makes the pane's environment
    exactly the one computed here. It also has no tmux version floor, where `-e`
    needs 3.2.

    The values are SOURCED from `paths.env` rather than spelled in this list,
    because this list is argv: a pane command is readable by `ps` for the length
    of the client call and by `tmux list-panes -F '#{pane_start_command}'` for
    the session's whole life, and it carries the host's API tokens. `set -a`
    exports what the file defines and `exec "$@"` replaces the sourcing shell
    with the wrapper — so the pane process is still the wrapper, still the
    session leader whose group a `killpg` reaches the agent through.

    The pane removes the file itself, between sourcing it and `exec`. Nothing
    reads it after that point, and deleting it here rather than at settlement
    is what keeps the window it exists in a few milliseconds wide instead of the
    run's whole lifetime — the environment holds credentials, and the settlement
    sweep still covers the case where the pane died before it got this far. Not
    `&&`: a delete that fails must not stop the agent from starting.
    """
    quoted = shlex.quote(str(paths.env))
    source = f'set -a; . {quoted}; set +a; rm -f {quoted}; exec "$@"'
    return [_ENV_BIN, "-i", _SH_BIN, "-c", source, _SH_BIN, *wrapped]


def _spawn_in_tmux(
    wrapped: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    paths: RunOutputPaths,
    stem: str,
) -> int:
    """Run the wrapper as a detached tmux pane, and report the pane's pid.

    The pane process IS the wrapper, so the recorded pid is the same kind of pid
    the FILE transport records: a session leader whose group a `killpg` reaches
    the agent through. A tmux that cannot start the session raises rather than
    falling back — reporting FILE for a run nobody can attach to would be the
    column asserting something untrue.

    The pane command sources its own environment (`_under_env`) because `env=`
    below reaches only the tmux client. `cwd` does NOT need the same treatment:
    the client resolves it and hands the server the result as the session's
    start directory.
    """
    session = tmux_session_name(stem)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, *_under_env(paths, wrapped)],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )
    pane = subprocess.run(
        ["tmux", "display-message", "-p", "-t", session, "#{pane_pid}"],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(pane.stdout.strip())
