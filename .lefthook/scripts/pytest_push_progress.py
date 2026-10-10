"""pytest plugin: count finished tests into the pre-push progress file.

Loaded by server-tests.sh as `-p pytest_push_progress`. When the run is held
under `loregarden capacity run`, $LOREGARDEN_PROGRESS_FILE names a file the
holder copies onto the lease (see server/loregarden/services/capacity_progress.py),
so the queue board and every push waiting behind this one can read "412/1830"
instead of "holding". Without that variable the plugin does nothing.

Under xdist only the controller writes: it sees every worker's reports and,
once collection finishes, how many tests there are. A test is counted at its
teardown report, which every test has, including one whose setup failed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

#: Writes per second, at most. The holder reads the file every couple of seconds.
_MIN_INTERVAL = 1.0


class _Progress:
    def __init__(self, path: Path, step: str) -> None:
        self.path = path
        self.step = step
        self.done = 0
        self.total: int | None = None
        self._written_at = 0.0
        self._warned = False

    def write(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._written_at < _MIN_INTERVAL:
            return
        self._written_at = now
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        body = {"step": self.step, "done": self.done, "total": self.total}
        try:
            tmp.write_text(json.dumps(body), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            # Progress is a view of the run; a view that fails must not fail
            # the tests. Said once, so the board's stale count is explained.
            if not self._warned:
                self._warned = True
                print(f"\npytest_push_progress: cannot write {self.path}: {exc}", flush=True)


#: The controller's counter; None in a worker, or when nothing asked for progress.
_STATE: _Progress | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _STATE
    raw = os.environ.get("LOREGARDEN_PROGRESS_FILE", "")
    if not raw or hasattr(config, "workerinput"):  # an xdist worker: the controller counts
        return
    _STATE = _Progress(Path(raw), os.environ.get("PUSH_PROGRESS_STEP") or "pytest")


def pytest_collection_finish(session: pytest.Session) -> None:
    if _STATE is not None and session.items:
        _STATE.total = len(session.items)
        _STATE.write(force=True)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids: list[str]) -> None:
    # Every worker collects the same tests; the first to finish says how many.
    if _STATE is not None and _STATE.total is None:
        _STATE.total = len(ids)
        _STATE.write(force=True)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _STATE is None or report.when != "teardown":
        return
    _STATE.done += 1
    _STATE.write()


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _STATE is not None:
        _STATE.write(force=True)
