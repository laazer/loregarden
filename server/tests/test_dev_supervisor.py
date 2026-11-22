"""The dev server reloads only into a build that imports, and says what happened.

A `uvicorn --reload` reload into a build that did not import (#932's lore-eden
bump, before the venv synced) killed the serving worker and left a dead port,
with the traceback on a terminal nobody could read. These run the real
supervisor, with a real uvicorn worker, over a small app the test rewrites.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from loregarden.dev_supervisor import LOG_FILE, ReloadState, Status, read_status

REPO = Path(__file__).resolve().parents[2]
RELOAD_SCRIPT = REPO / "scripts" / "reload-backend.sh"
TIMEOUT = 60.0

_APP = """
print("fakeapp {version} imported", flush=True)
{breakage}

async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = b"{version}"
    await send({{"type": "http.response.start", "status": 200, "headers": []}})
    await send({{"type": "http.response.body", "body": body}})
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class Server:
    root: Path
    port: int
    sentinel: Path
    state_dir: Path
    process: subprocess.Popen

    def write_app(self, version: str, *, broken: bool = False) -> None:
        breakage = 'raise RuntimeError("fakeapp cannot import")' if broken else ""
        (self.root / "fakeapp.py").write_text(
            _APP.format(version=version, breakage=breakage), encoding="utf-8"
        )

    def get(self) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/version", timeout=5) as response:
            return response.read().decode()

    def touch(self) -> int:
        # A distinct mtime even on a coarse filesystem clock.
        stamp = time.time_ns() + 1_000_000
        os.utime(self.sentinel, ns=(stamp, stamp))
        return self.sentinel.stat().st_mtime_ns

    def settled(self, *, after: int, state: ReloadState | None = None) -> Status:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            status = read_status(self.state_dir)
            if (
                status is not None
                and status.trigger >= after
                and status.state is not ReloadState.STARTING
                and (state is None or status.state is state)
            ):
                return status
            assert self.process.poll() is None, "the supervisor exited"
            time.sleep(0.2)
        raise AssertionError(
            f"no {state or 'settled'} status for trigger {after}: {read_status(self.state_dir)}"
        )

    def environ(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE"}}
        env["PYTHONPATH"] = str(self.root)
        # write_app swaps v1 for v2 (same size) within one second. A .pyc is
        # validated by source size and whole-second mtime, so with bytecode on
        # the next import loads v1 again. Pin it off rather than inherit it.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    sentinel = tmp_path / ".self-improve-restart"
    sentinel.touch()
    made = Server(tmp_path, _free_port(), sentinel, tmp_path / "state", process=None)  # type: ignore[arg-type]
    made.write_app("v1")
    made.process = subprocess.Popen(
        [
            sys.executable, "-m", "loregarden.dev_supervisor",
            "--app", "fakeapp:app",
            "--port", str(made.port),
            "--sentinel", str(sentinel),
            "--state-dir", str(made.state_dir),
            "--ready-timeout", "30",
            "--graceful-seconds", "1",
        ],
        cwd=tmp_path,
        env=made.environ(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        made.settled(after=0, state=ReloadState.OK)
        yield made
    finally:
        made.process.send_signal(signal.SIGTERM)
        try:
            made.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            made.process.kill()
            made.process.wait()


def test_a_build_that_does_not_import_is_refused_and_the_old_one_keeps_serving(server: Server):
    assert server.get() == "v1"
    server.write_app("v2", broken=True)

    status = server.settled(after=server.touch())

    assert status.state is ReloadState.REFUSED
    assert any("fakeapp cannot import" in line for line in status.log_tail)
    assert server.get() == "v1"


def test_a_build_that_imports_is_reloaded(server: Server):
    server.write_app("v2")

    status = server.settled(after=server.touch())

    assert status.state is ReloadState.OK
    assert server.get() == "v2"


def test_a_worker_that_dies_is_recorded_and_restarted(server: Server):
    status = read_status(server.state_dir)
    assert status is not None and status.worker_pid is not None
    trigger = status.trigger

    os.kill(status.worker_pid, signal.SIGKILL)

    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        now = read_status(server.state_dir)
        assert now is not None
        if now.state is ReloadState.OK and now.worker_pid not in {None, status.worker_pid}:
            break
        time.sleep(0.1)
    assert now.trigger == trigger
    assert now.state is ReloadState.OK, now
    assert server.get() == "v1"


def test_worker_output_reaches_the_log_file(server: Server):
    log = (server.state_dir / LOG_FILE).read_text(encoding="utf-8")

    assert "fakeapp v1 imported" in log


def _reload(server: Server) -> subprocess.CompletedProcess:
    env = server.environ()
    env.update(
        LOREGARDEN_RELOAD_SENTINEL=str(server.sentinel),
        LOREGARDEN_DEV_STATE_DIR=str(server.state_dir),
        LOREGARDEN_RELOAD_TIMEOUT="60",
    )
    return subprocess.run(
        ["bash", str(RELOAD_SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


def test_reload_script_exits_non_zero_with_the_traceback_when_refused(server: Server):
    server.write_app("v2", broken=True)

    result = _reload(server)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fakeapp cannot import" in result.stderr
    assert server.get() == "v1"


def test_reload_script_exits_zero_once_the_new_build_serves(server: Server):
    server.write_app("v2")

    result = _reload(server)

    assert result.returncode == 0, result.stdout + result.stderr
    assert server.get() == "v2"
