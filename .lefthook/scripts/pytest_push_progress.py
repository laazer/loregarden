"""pytest plugin: what this run collected, and how far through it is.

Loaded by server-tests.sh as `-p pytest_push_progress`. It writes two files,
for two different readers, and the difference between them matters:

* **$LOREGARDEN_TEST_COUNT_FILE — always.** `{"count": N}`, the number this
  run's own collection found, which `judge_test_run.py` reads after the runner
  exits to decide whether the push verified anything. It is written from BOTH
  collection hooks because under `-n` only the workers reach
  `pytest_collection_finish`, and it fails CLOSED: a count that could not be
  written leaves no file, which the judge resolves as "could not run" rather
  than as a legitimate-looking zero.

  One recorded count means one recording process, and the pre-push script
  exports that path into an environment every nested runner inherits — this
  suite spawns pytest itself. So the path is taken out of `os.environ` at
  configure (nothing this process starts can see it) and the file is CLAIMED by
  creating it exclusively (the first collection to record owns the number; a
  later writer, in this process or any other, finds the path taken and keeps
  its hands off). Both halves are mechanism rather than convention: the first
  stops the inheritance, the second survives an environment rebuilt by hand.

* **$LOREGARDEN_PROGRESS_FILE — when a capacity holder named one.** The
  holder copies it onto the lease (see
  server/loregarden/services/capacity_progress.py), so the queue board and
  every push waiting behind this one read "412/1830" instead of "holding".
  Without that variable this half does nothing.

Neither number is ever read back out of pytest's own output or inferred from an
exit code. They come from this run's collection.

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

#: The count the pre-push judge reads. Written only here and by the jest
#: reporter: one recorded number, so the terminal and the board cannot disagree.
_COUNT_ENV = "LOREGARDEN_TEST_COUNT_FILE"
#: The queue board's file, same inheritance problem: a nested runner would
#: report its own progress over this run's.
_PROGRESS_ENV = "LOREGARDEN_PROGRESS_FILE"

#: Where this process records its count, read at configure. None when the path
#: was never named, which is every ad-hoc pytest run.
_COUNT_PATH: Path | None = None
#: This process has already recorded its count; collection happens once.
_COUNT_WRITTEN = False


def _take(name: str) -> str:
    """Read an inherited path and stop it being inherited again.

    Every subprocess this run starts builds its environment from `os.environ`,
    so a path left there is a path a nested runner writes over. Taking it out
    once, in the process that owns it, is the structural half of C-4: the
    variable cannot reach a child at all, rather than reaching one that is
    asked politely not to use it.
    """
    return os.environ.pop(name, "")


def _record_count(count: int) -> None:
    """Record what collection found, or say why it could not be recorded.

    Fail closed. The old progress half of this plugin warns once and carries on,
    which is right for a view of the run; it is wrong here, because a count that
    quietly failed to arrive is indistinguishable from a legitimate zero, and
    that collapse is the whole defect this sink exists to remove.
    """
    global _COUNT_WRITTEN
    path = _COUNT_PATH
    if path is None or _COUNT_WRITTEN:
        return
    try:
        # The claim and the write in one step: O_EXCL fails when the path is
        # already taken, so the first collection to record owns this run's
        # number and no later runner — nested, inherited, or racing — replaces
        # it. The payload is one short write, and the judge reads it only after
        # the runner has exited.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        print(
            f"\npytest_push_progress: {path} already holds a count, so this run's "
            f"{count} collected test(s) were not recorded there",
            flush=True,
        )
        return
    except OSError as exc:
        print(f"\npytest_push_progress: cannot record the test count in {path}: {exc}", flush=True)
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"count": count}))
    except OSError as exc:
        print(f"\npytest_push_progress: cannot record the test count in {path}: {exc}", flush=True)
        return
    _COUNT_WRITTEN = True


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
    """Take both paths out of the environment; the count file is claimed when
    it is recorded.

    Under `-n` the controller configures before execnet spawns its workers, so
    the workers inherit neither path and the controller's
    `pytest_xdist_node_collection_finished` is the one recorder. Serially this
    process is the only one. Either way exactly one process can write.
    """
    global _COUNT_PATH, _STATE
    raw_count = _take(_COUNT_ENV)
    _COUNT_PATH = Path(raw_count) if raw_count else None

    raw = _take(_PROGRESS_ENV)
    if not raw or hasattr(config, "workerinput"):  # an xdist worker: the controller counts
        return
    _STATE = _Progress(Path(raw), os.environ.get("PUSH_PROGRESS_STEP") or "pytest")


def pytest_collection_finish(session: pytest.Session) -> None:
    # Under xdist this fires in the workers, where _STATE is None. The count is
    # recorded regardless: it is the judge's only reading, and a sink that ran
    # only on the controller path would record nothing on every narrowed push.
    #
    # Except when collection itself errored. A file whose import raised collects
    # no tests, and recording that as a zero would let LOREGARDEN_ALLOW_NO_TESTS=1
    # wave a broken import through as a deliberately empty selection. An errored
    # collection has no count, which the judge resolves as "could not run".
    # (The xdist hook below cannot see this; there it reports as a zero, which is
    # still a failing push, just a less specific one.)
    if session.testsfailed:
        print(
            f"\npytest_push_progress: collection errored, so no test count was "
            f"recorded in {_COUNT_PATH}",
            flush=True,
        )
        return
    _record_count(len(session.items))
    if _STATE is not None and session.items:
        _STATE.total = len(session.items)
        _STATE.write(force=True)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids: list[str]) -> None:
    # Every worker collects the same tests; the first to finish says how many.
    # This is the controller's only sight of the count, so it records too.
    _record_count(len(ids))
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
