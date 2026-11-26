"""pytest plugin: how long each test file has taken so far, for wall_limit.py.

Loaded by server-tests.sh as `-p pytest_file_durations`. When
$LOREGARDEN_FILE_DURATIONS_FILE names a path, the controller (under xdist it
sees every worker's reports) adds each report's duration to its file's total
and rewrites the path at most once a second as `{"files": {file: seconds},
"running": {test id: epoch seconds it started}}`. wall_limit.py reads it when it
stops a run at the hard limit, to name the slowest files so far and the tests
still running, which is where a hang is.

The path is taken out of `os.environ` at configure, so a pytest this suite
starts itself never writes over this run's totals.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_ENV = "LOREGARDEN_FILE_DURATIONS_FILE"
_MIN_INTERVAL = 1.0


class FileDurations:
    """Totals in memory, flushed by a background thread once a second when changed.

    A thread, not a write per report: a hung test's start is the last event the
    run ever sees, and a throttled write on that event would drop it.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._totals: dict[str, float] = {}
        self._running: dict[str, float] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._done = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
        self._flusher.start()

    def pytest_runtest_logstart(self, nodeid, location) -> None:
        with self._lock:
            self._running[nodeid] = time.time()
            self._dirty = True

    def pytest_runtest_logreport(self, report) -> None:
        name = report.nodeid.split("::")[0]
        with self._lock:
            self._totals[name] = self._totals.get(name, 0.0) + report.duration
            if report.when == "teardown":
                self._running.pop(report.nodeid, None)
            self._dirty = True

    def pytest_sessionfinish(self) -> None:
        self._done.set()
        self._flusher.join()
        self._write()

    def _flush_loop(self) -> None:
        while not self._done.wait(_MIN_INTERVAL):
            self._write()

    def _write(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            body = json.dumps({"files": self._totals, "running": self._running})
            self._dirty = False
        partial = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            partial.write_text(body, encoding="utf-8")
            os.replace(partial, self._path)
        except OSError:  # silent-ok: totals only name the slowest files if the run is stopped; wall_limit.py says when they are missing
            partial.unlink(missing_ok=True)


def pytest_configure(config) -> None:
    path = os.environ.pop(_ENV, "")
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(FileDurations(Path(path)), "file-durations")
