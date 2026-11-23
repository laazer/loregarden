"""pytest plugin: a private basetemp for every pytest session mutmut starts.

mutmut runs one pytest session per mutant, several at once in forked workers.
Sessions sharing pytest's default temp root garbage-collect each other's
`pytest-N` directories and can die in `pytest_sessionfinish` after every test
passed (see .lefthook/scripts/server-tests.sh) — and a mutant whose session
exits non-zero counts as killed. So each session gets its own directory under
``LOREGARDEN_MUTMUT_BASETEMP_ROOT`` (set by mutmut_driver.py, which removes the
root afterwards). A given ``--basetemp`` also makes pytest skip that cleanup.

It also writes a session's first failing test to ``FAILURE_FILE`` in that
directory. mutmut runs pytest under a progress spinner that hides its output, so
without this a red clean baseline could only say "exited 1"; the driver reads
these files back when the baseline fails and names the test.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

ROOT_ENV = "LOREGARDEN_MUTMUT_BASETEMP_ROOT"
FAILURE_FILE = "first-failure.txt"
#: how much of a failure's traceback to keep: the end, where the assertion is
FAILURE_TAIL_CHARS = 4000

_session_dir: Path | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    root = os.environ.get(ROOT_ENV)
    if not root:
        raise pytest.UsageError(f"{ROOT_ENV} is not set; run through mutmut_driver.py")
    if config.option.basetemp is None:
        config.option.basetemp = tempfile.mkdtemp(prefix=f"s{os.getpid()}-", dir=root)
    global _session_dir
    _session_dir = Path(config.option.basetemp)


def _record_first_failure(report: pytest.TestReport | pytest.CollectReport, when: str) -> None:
    if not report.failed or _session_dir is None:
        return
    target = _session_dir.parent / f"{_session_dir.name}.{FAILURE_FILE}"
    if target.exists():
        return
    text = report.longreprtext[-FAILURE_TAIL_CHARS:]
    target.write_text(f"{report.nodeid} ({when})\n{text}\n", encoding="utf-8")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _record_first_failure(report, report.when)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    # An import error in the copy fails collection, not a test.
    _record_first_failure(report, "collection")
