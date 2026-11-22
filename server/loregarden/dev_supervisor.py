"""Run the dev server, and reload it only into a build that imports.

    python -m loregarden.dev_supervisor --host 127.0.0.1 --port 8000 \\
        --sentinel server/.self-improve-restart --state-dir data/dev-server

`uvicorn --reload` stops the old worker before it knows whether the new one can
start. When #932's lore-eden bump reached a checkout whose venv still had the
old lore-eden, the reload killed the serving worker, the new one died at import,
and the reloader sat on a dead port with the traceback printed to a terminal no
agent could read. Nothing reported it; the control plane was simply gone.

This supervisor replaces the reloader:

- It binds the port itself and hands the socket to each uvicorn worker
  (``--fd``), so connections wait in the backlog across a restart rather than
  being refused.
- When the sentinel is touched it first imports the app in a separate process.
  A build that does not import is **refused**: the serving worker keeps serving
  the previous build, and the refusal is recorded with its traceback.
- Otherwise it restarts the worker and waits for ``/health``. A worker that
  never answers is recorded as **failed**, and so is one that dies on its own,
  which is restarted with backoff until it has failed ``MAX_CRASH_RESTARTS``
  times in a row; then it waits for the next touch.
- Everything the worker prints goes to the terminal and to ``server.log`` in
  the state directory, and each outcome is written to ``status.json`` there.
  `scripts/reload-backend.sh` waits on that file, and the doctor's
  ``dev_server_reload`` check reports a refused or failed reload to every run.

Standard library only, and it imports nothing from the app: the process that
guards against a broken build must not be broken by one.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TextIO

STATUS_FILE = "status.json"
LOG_FILE = "server.log"
#: The log is rotated to `server.log.1` at startup once it passes this size.
LOG_ROTATE_BYTES = 20 * 1024 * 1024
#: Lines of worker output kept for a failure's `log_tail`.
TAIL_LINES = 60
#: Consecutive crashes restarted on their own before waiting for a touch.
MAX_CRASH_RESTARTS = 4
CRASH_BACKOFF_SECONDS = (1, 5, 15, 60)
POLL_SECONDS = 0.5
#: Bind addresses that are not themselves reachable; `/health` is probed on loopback.
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})


class ReloadState(StrEnum):
    """What the supervisor last did, as `status.json` records it."""

    #: The first worker is starting.
    STARTING = "starting"
    #: A worker is serving the build the latest trigger asked for.
    OK = "ok"
    #: The new build did not import; the previous worker is still serving it.
    REFUSED = "refused"
    #: A worker did not come up, or died; nothing may be serving.
    FAILED = "failed"


#: States a reload waiter stops on.
SETTLED_STATES = frozenset({ReloadState.OK, ReloadState.REFUSED, ReloadState.FAILED})


@dataclass
class Status:
    state: ReloadState
    #: The sentinel's mtime (ns) this outcome answers; a waiter matches on it.
    trigger: int
    detail: str
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    log_file: str = ""
    log_tail: list[str] = field(default_factory=list)
    supervisor_pid: int = field(default_factory=os.getpid)
    worker_pid: int | None = None


def read_status(state_dir: Path) -> Status | None:
    """The last recorded outcome, or None when no supervisor has written one.

    Raises `ValueError` for a file that exists but does not parse: an
    unreadable status is not the same answer as no status.
    """
    path = state_dir / STATUS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    raw["state"] = ReloadState(raw["state"])
    return Status(**raw)


def _write_status(state_dir: Path, status: Status) -> None:
    path = state_dir / STATUS_FILE
    scratch = path.with_suffix(".json.tmp")
    scratch.write_text(json.dumps(asdict(status), indent=2) + "\n", encoding="utf-8")
    os.replace(scratch, path)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except (
        FileNotFoundError
    ):  # silent-ok: no sentinel reads as never touched; creating it is a change the loop reloads on
        return 0


class Supervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.app: str = args.app
        self.import_module: str = args.app.split(":", 1)[0]
        self.probe_host = "127.0.0.1" if args.host in WILDCARD_HOSTS else args.host
        self.port: int = args.port
        self.sentinel = Path(args.sentinel)
        self.state_dir = Path(args.state_dir)
        self.ready_timeout: float = args.ready_timeout
        self.import_timeout: float = args.import_timeout
        self.graceful_seconds: int = args.graceful_seconds
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.state_dir / LOG_FILE
        self._rotate_log()
        self.log: TextIO = self.log_path.open("a", encoding="utf-8", buffering=1)
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self.lock = threading.Lock()
        self.sock = self._bind(args.host, args.port)
        self.worker: subprocess.Popen[str] | None = None
        self.stopping = False

    # -- output -------------------------------------------------------------

    def _rotate_log(self) -> None:
        if self.log_path.exists() and self.log_path.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(self.log_path, self.log_path.with_suffix(".log.1"))

    def _emit(self, line: str) -> None:
        with self.lock:
            sys.stdout.write(line)
            sys.stdout.flush()
            self.log.write(line)

    def say(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        self._emit(f"[dev-supervisor {stamp}] {message}\n")

    def _pump(self, worker: subprocess.Popen[str]) -> None:
        assert worker.stdout is not None
        for line in worker.stdout:
            self.tail.append(line.rstrip("\n"))
            self._emit(line)

    def record(self, state: ReloadState, trigger: int, detail: str, *, tail: list[str]) -> None:
        worker_pid = self.worker.pid if self.worker and self.worker.poll() is None else None
        _write_status(
            self.state_dir,
            Status(
                state=state,
                trigger=trigger,
                detail=detail,
                log_file=str(self.log_path),
                log_tail=tail,
                worker_pid=worker_pid,
            ),
        )

    # -- worker -------------------------------------------------------------

    @staticmethod
    def _bind(host: str, port: int) -> socket.socket:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(2048)
        sock.set_inheritable(True)
        return sock

    def start_worker(self) -> None:
        self.tail.clear()
        fd = self.sock.fileno()
        command = [
            sys.executable, "-m", "uvicorn", self.app,
            "--fd", str(fd),
            "--timeout-graceful-shutdown", str(self.graceful_seconds),
        ]  # fmt: skip
        worker = subprocess.Popen(
            command,
            pass_fds=(fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.worker = worker
        threading.Thread(target=self._pump, args=(worker,), daemon=True).start()
        self.say(f"worker {worker.pid} starting")

    def stop_worker(self) -> None:
        worker, self.worker = self.worker, None
        if worker is None or worker.poll() is not None:
            return
        worker.send_signal(signal.SIGTERM)
        try:
            worker.wait(timeout=self.graceful_seconds + 10)
        except subprocess.TimeoutExpired:
            self.say(f"worker {worker.pid} did not stop after SIGTERM; killing it")
            worker.kill()
            worker.wait()

    def wait_ready(self) -> bool:
        """True once `/health` answers 200; False if the worker exits or time runs out."""
        deadline = time.monotonic() + self.ready_timeout
        url = f"http://{self.probe_host}:{self.port}/health"
        while time.monotonic() < deadline:
            if self.worker is None or self.worker.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - fixed loopback URL
                    if response.status == 200:
                        return True
            except (
                urllib.error.URLError,
                OSError,
            ):  # silent-ok: not up yet; the deadline bounds the wait
                pass
            time.sleep(POLL_SECONDS)
        return False

    def _bring_up(self, trigger: int, reason: str) -> bool:
        self.start_worker()
        if self.wait_ready():
            self.record(ReloadState.OK, trigger, f"serving ({reason})", tail=[])
            self.say(f"worker {self.worker.pid if self.worker else '?'} is serving ({reason})")
            return True
        self._fail(trigger, f"the worker did not answer /health ({reason})")
        return False

    def _fail(self, trigger: int, detail: str) -> None:
        exit_code = self.worker.poll() if self.worker else None
        if exit_code is not None:
            detail = f"{detail}; it exited with {exit_code}"
        self.record(ReloadState.FAILED, trigger, detail, tail=list(self.tail))
        self.say(f"FAILED: {detail}. Nothing may be serving. Log: {self.log_path}")

    # -- reload -------------------------------------------------------------

    def import_check(self) -> tuple[bool, list[str]]:
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import {self.import_module}"],
                capture_output=True,
                text=True,
                timeout=self.import_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, [f"importing {self.import_module} took over {self.import_timeout:.0f}s"]
        lines = (result.stdout + result.stderr).splitlines()
        return result.returncode == 0, lines[-TAIL_LINES:]

    def reload(self, trigger: int) -> None:
        self.say(f"reload requested; importing {self.import_module} first")
        ok, output = self.import_check()
        if not ok:
            serving = self.worker is not None and self.worker.poll() is None
            still = "the previous build is still serving" if serving else "nothing is serving"
            self.record(
                ReloadState.REFUSED, trigger, f"the new build does not import; {still}", tail=output
            )
            for line in output:
                self._emit(f"  {line}\n")
            self.say(
                f"RELOAD REFUSED: the new build does not import; {still}. Log: {self.log_path}"
            )
            return
        self.stop_worker()
        self._bring_up(trigger, "reloaded")

    # -- loop ---------------------------------------------------------------

    def run(self) -> int:
        trigger = _mtime_ns(self.sentinel)
        self.record(ReloadState.STARTING, trigger, "starting", tail=[])
        self.say(
            f"serving on port {self.port}; reload by touching {self.sentinel}; log {self.log_path}"
        )
        crashes = 0 if self._bring_up(trigger, "started") else MAX_CRASH_RESTARTS
        while not self.stopping:
            time.sleep(POLL_SECONDS)
            current = _mtime_ns(self.sentinel)
            if current != trigger:
                trigger = current
                self.reload(trigger)
                crashes = 0
                continue
            if self.worker is not None and self.worker.poll() is not None:
                self._fail(trigger, "the worker died")
                self.worker = None
                if crashes < MAX_CRASH_RESTARTS:
                    delay = CRASH_BACKOFF_SECONDS[min(crashes, len(CRASH_BACKOFF_SECONDS) - 1)]
                    crashes += 1
                    self.say(f"restarting in {delay}s (crash {crashes} of {MAX_CRASH_RESTARTS})")
                    time.sleep(delay)
                    if self._bring_up(trigger, f"restarted after crash {crashes}"):
                        crashes = 0
                else:
                    self.say(f"not restarting again until {self.sentinel} is touched")
        self.stop_worker()
        return 0

    def shutdown(self, signum: int, _frame: object) -> None:
        self.stopping = True
        self.say(f"received signal {signum}; stopping")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m loregarden.dev_supervisor")
    parser.add_argument("--app", default="loregarden.main:app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--sentinel", required=True, help="File whose mtime change requests a reload."
    )
    parser.add_argument("--state-dir", required=True, help="Holds status.json and server.log.")
    parser.add_argument("--ready-timeout", type=float, default=180.0)
    parser.add_argument("--import-timeout", type=float, default=300.0)
    parser.add_argument("--graceful-seconds", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    supervisor = Supervisor(build_parser().parse_args(argv))
    signal.signal(signal.SIGTERM, supervisor.shutdown)
    signal.signal(signal.SIGINT, supervisor.shutdown)
    with contextlib.closing(supervisor.log):
        return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
