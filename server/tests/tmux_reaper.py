"""Reap the tmux servers of test runs that died before their teardown.

Every pytest process runs its tmux agents on a socket of its own,
`lg-pytest-<pid>` (conftest `_private_tmux_server`), and kills that server at
session teardown. A run killed before teardown — a worker taken down by a
timeout, a nested suite inside a land-verification tree whose parent killed its
process group — never gets there. Loregarden's spawn sets `exit-empty off`, which
production needs (see `agent_spawn`), so an orphaned server never exits on its
own: 72 of them were alive on 2026-11-09, some days old, 49 of them from nested
suites in land-verification trees.

The owner is named by the socket, so an orphan is decidable without guessing:
`lg-pytest-<pid>` (or `lg-pytest-<pid>-<suffix>`) whose pid no longer exists. A
socket whose owner is alive belongs to a running suite and is never touched; a
reused pid makes a dead owner look alive, which leaves that server for a later
sweep rather than killing anything that is not ours.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_SOCKET_PREFIX = "lg-pytest-"
_OWNED_SOCKET = re.compile(rf"^{re.escape(TEST_SOCKET_PREFIX)}(\d+)(?:-.+)?$")
#: What tmux says when nothing listens on the socket — the only case where the
#: file is safe to remove. Anything else is a live server that refused.
#: ("error connecting … (File name too long)" is not one of them: that socket was
#: never reached, so it may well be live.)
_NO_SERVER = re.compile(r"no server running|\((?:No such file or directory|Connection refused)\)")


@dataclass
class ReapReport:
    killed: list[str] = field(default_factory=list)
    stale_sockets_removed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def tmux_socket_dir() -> Path:
    """Where tmux puts `-L` sockets: `$TMUX_TMPDIR/tmux-<uid>`, `/tmp` by default."""
    return Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"


def owner_pid(socket_name: str) -> int | None:
    """The pytest pid a test socket names, or None for a socket that is not a test's."""
    match = _OWNED_SOCKET.match(socket_name)
    return int(match.group(1)) if match else None


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # someone else's process: alive, and not ours to judge
    return True


def reap_orphaned_test_servers(socket_dir: Path | None = None) -> ReapReport:
    """Kill every `lg-pytest-<pid>` server whose pid is gone, and drop dead sockets."""
    report = ReapReport()
    directory = socket_dir or tmux_socket_dir()
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:  # silent-ok: no socket directory means tmux never ran here
        return report
    for entry in entries:
        pid = owner_pid(entry.name)
        if pid is None or pid_exists(pid):
            continue
        try:
            killed = subprocess.run(
                ["tmux", "-S", str(entry), "kill-server"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            # A hung server must not error every test in the session; the next
            # sweep tries it again, and the warning below names it.
            report.failures.append(f"{entry.name}: kill-server timed out after 30s")
            continue
        if killed.returncode == 0:
            report.killed.append(entry.name)
            continue
        if not _NO_SERVER.search(killed.stderr):
            report.failures.append(f"{entry.name}: {killed.stderr.strip()}")
            continue
        # No server answered: the socket is a file its dead server left behind.
        try:
            entry.unlink()
            report.stale_sockets_removed.append(entry.name)
        except FileNotFoundError:  # silent-ok: another worker's sweep removed it first
            continue
        except OSError as exc:
            report.failures.append(f"{entry.name}: {exc}")
    if report.failures:
        logger.warning("Could not reap orphaned test tmux sockets: %s", report.failures)
    return report
