"""`tests/tmux_reaper`: a test run killed before teardown leaves no tmux server behind.

72 `lg-pytest-<pid>` servers were alive on 2026-11-09, every one owned by a pytest
process that no longer existed. The properties:

- a server whose owning pid is gone is killed;
- a server whose owner is alive is left alone, whatever else is true of it;
- a socket file with no server behind it is removed, and one that names no test is never touched.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.tmux_reaper import owner_pid, pid_exists, reap_orphaned_test_servers

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


@pytest.fixture(name="socket_dir")
def socket_dir_fixture() -> Iterator[Path]:
    """A socket directory of the test's own, short enough for a unix socket path.

    macOS caps `sun_path` at 104 bytes, and pytest's `tmp_path` under
    `/var/folders/…` is past it, so tmux could not bind there at all.
    """
    directory = Path(tempfile.mkdtemp(prefix="lgt-", dir="/tmp"))
    yield directory
    for entry in directory.iterdir():
        subprocess.run(["tmux", "-S", str(entry), "kill-server"], capture_output=True, check=False)
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(name="dead_pid")
def dead_pid_fixture() -> int:
    child = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    child.wait()
    assert not pid_exists(child.pid), "precondition: the pid is free"
    return child.pid


def _start_server(socket_path: Path) -> None:
    subprocess.run(
        [
            "tmux",
            "-f",
            "/dev/null",
            "-S",
            str(socket_path),
            "set-option",
            "-g",
            "exit-empty",
            "off",
            ";",
            "new-session",
            "-d",
            "-s",
            "keeper",
            "sleep 300",
        ],
        capture_output=True,
        check=True,
    )


def _alive(socket_path: Path) -> bool:
    probe = subprocess.run(
        ["tmux", "-S", str(socket_path), "list-sessions"], capture_output=True, check=False
    )
    return probe.returncode == 0


def test_a_server_whose_test_run_died_is_killed(socket_dir, dead_pid) -> None:
    orphan = socket_dir / f"lg-pytest-{dead_pid}"
    _start_server(orphan)

    report = reap_orphaned_test_servers(socket_dir)

    assert report.killed == [orphan.name]
    assert not _alive(orphan)


def test_a_suffixed_socket_belongs_to_the_pid_it_names(socket_dir, dead_pid) -> None:
    orphan = socket_dir / f"lg-pytest-{dead_pid}-cwd-1a2b3c4d"
    _start_server(orphan)

    assert reap_orphaned_test_servers(socket_dir).killed == [orphan.name]


def test_a_live_runs_server_is_left_alone(socket_dir) -> None:
    ours = socket_dir / f"lg-pytest-{os.getpid()}"
    _start_server(ours)

    report = reap_orphaned_test_servers(socket_dir)

    assert (report.killed, report.stale_sockets_removed) == ([], [])
    assert _alive(ours)


def test_a_dead_runs_socket_with_no_server_is_removed(socket_dir, dead_pid) -> None:
    stale = socket_dir / f"lg-pytest-{dead_pid}"
    bound = socket.socket(socket.AF_UNIX)
    bound.bind(str(stale))
    bound.close()

    report = reap_orphaned_test_servers(socket_dir)

    assert report.stale_sockets_removed == [stale.name]
    assert not stale.exists()


def test_a_socket_that_names_no_test_is_never_touched(socket_dir) -> None:
    production = socket_dir / "loregarden"
    _start_server(production)

    reap_orphaned_test_servers(socket_dir)

    assert _alive(production)


@pytest.mark.parametrize(
    ("name", "pid"),
    [
        ("lg-pytest-123", 123),
        ("lg-pytest-123-cwd-ab", 123),
        ("lg-pytest-cwd-ab", None),
        ("loregarden", None),
        ("lg-pytest-", None),
    ],
)
def test_the_owner_is_read_from_the_socket_name(name, pid) -> None:
    assert owner_pid(name) == pid
