"""A test over the per-test ceiling fails, unless it says why it has to be slow.

The suite went from ~9 minutes to over an hour on one machine and nothing failed
along the way: each slow test arrived on its own, a few seconds at a time, and a
few seconds is never worth a red build. This makes it one. A test whose own time
passes ``CEILING_SECONDS`` fails with its measured time and the ceiling, unless:

* it carries ``@pytest.mark.slow(reason="...")``, a decision someone wrote down
  next to the test, with a reason that is not blank; or
* it is in ``tests/slow_test_ratchet.py``, the slow tests that were already there
  when this landed. That list may only shrink (``test_slow_test_ratchet.py``).

**What is timed.** The call, plus the setup of the test's *function-scoped*
fixtures. Those are the test's own cost: a fixture it asks for is paid on every
test that asks. Session- and module-scoped setup is not counted, since it lands
on whichever test happens to run first and would fail that one at random.
Teardown is not counted either (pytest has no hook that brackets one fixture's
finalizer); a slow teardown shows in the CI profile instead.

**The ceiling, and why 5 s.** Measured on 2026-11-26 from the CI server-suite
profiles of three runs after the test speedups (``profile-server-{1..4}`` of runs
45819701261, 45818593089, 45817899729; 15,484 distinct test ids), taking each
test's slowest setup+call across the runs: p50 0.07 s, p90 0.45 s, p99 2.27 s,
p99.9 7.57 s; 48 tests over 5 s. A typical test has 10x headroom over p90, and
the tests above it are the tail this is meant to stop growing.

**A breach fails only if it repeats.** One CI run's time for one test is not a
measurement of that test. In merge-queue run 45873876092 the shard medians sat
at 0.8-1.1x their baselines while the slowest 1% of tests ran 2.6-5.8x theirs:
``test_no_start_path_reads_the_profiles_stated_driver``, 1.9-2.0 s in every
profiled run, took 6.45 s. The cost is first-touch work (a module imported
lazily, a cold file cache, git under contention) landing on whichever test meets
it first, so it is neither the test's own nor the runner's, and neither the
load factor nor the run's median would have seen it. So a test over the ceiling
raises ``SlowTestCeilingExceeded`` and is run once more, warm, through
pytest-rerunfailures (``flaky(reruns=1, only_rerun=...)``, added to each item in
CI); it fails only if the rerun is over too. Only this error is rerun: every
other failure stays a hard failure, so no flaky assertion hides behind it.

**Where it fails: CI only.** Wall clock on a developer machine is noisy
(``lore_eden.testing.pytest_profile`` reports rather than fails for exactly this
reason), and this one routinely runs several suites at once. A gate that flakes
teaches everyone to re-run it. So the ceiling *fails* a test only where the noise
is lowest, a CI runner (``CI=true``, which GitHub Actions sets), and everywhere
else it *reports*: the same message, listed in the terminal summary, failing
nothing. CI runs every test on every PR, so nothing slow gets past it; the local
report is how you find out before you push. Even in CI the ceiling is scaled by
the run queue per core, ``CEILING_SECONDS * max(1, load1 / cpus)``, so a runner
oversubscribed by its own workers gets headroom and an idle one the plain 5 s.

A blank or missing ``reason`` fails everywhere: that is not a timing question.

Not enforced under ``--pdb``/``--trace`` (a person is stepping through it) or under
mutmut (``MUTANT_UNDER_TEST``), where the trampoline slows every call and a timing
failure would count as a killed mutant: a wrong oracle that reports green.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator, Iterable, Mapping

import pytest

#: Seconds of call + function-scoped fixture setup, idle. See the module docstring.
CEILING_SECONDS = 5.0

MARKER = "slow"
MARKER_HELP = (
    f"{MARKER}(reason=...): this test may exceed the {CEILING_SECONDS:g}s per-test "
    "ceiling (tests/slow_test_ceiling.py); the reason must say why"
)

_MUTMUT_ENV = "MUTANT_UNDER_TEST"
_CI_ENV = "CI"

#: The `user_properties` key a worker's report carries an over-ceiling message
#: under, so the controller can list it when the ceiling only reports.
_REPORTED = "slow_test_ceiling"

#: What a breach reruns on, matched by pytest-rerunfailures against
#: ``f"{type(exc).__name__}: {exc}"``.
_RERUN_ON = r"^SlowTestCeilingExceeded: "


class SlowTestCeilingExceeded(pytest.fail.Exception):
    """A test over the ceiling. A `pytest.fail` (printed as its message, no
    traceback into this plugin) with a name of its own, so the rerun matches it
    and nothing else."""

    def __init__(self, message: str) -> None:
        super().__init__(message, pytrace=False)


def load_factor(
    loadavg: Callable[[], tuple[float, float, float]] = os.getloadavg,
    cpu_count: Callable[[], int | None] = os.cpu_count,
) -> float:
    """How many runnable processes per core, floored at 1: the ceiling's multiplier."""
    cpus = cpu_count() or 1
    return max(1.0, loadavg()[0] / cpus)


def marker_error(item: pytest.Item) -> str | None:
    """Why this item's `slow` markers are invalid, if they are."""
    for mark in item.iter_markers(name=MARKER):
        reason = mark.kwargs.get("reason")
        if mark.args or set(mark.kwargs) - {"reason"}:
            return f"@pytest.mark.{MARKER} takes only reason=...: got {mark}"
        if (
            not isinstance(reason, str) or not reason.strip()
        ):  # py-org: allow-isinstance - a marker kwarg is an arbitrary user object
            return (
                f"@pytest.mark.{MARKER} needs a non-empty reason=... saying why this "
                "test cannot be faster"
            )
    return None


def over_ceiling_message(nodeid: str, seconds: float, ceiling: float, factor: float) -> str:
    scaled = f" ({CEILING_SECONDS:g}s x load factor {factor:.2f})" if factor > 1 else ""
    return (
        f"{nodeid} took {seconds:.2f}s (call + function-scoped fixture setup), over the "
        f"per-test ceiling of {ceiling:.2f}s{scaled}.\n"
        'Make it faster, or mark it @pytest.mark.slow(reason="why it cannot be"). '
        "Do not add it to tests/slow_test_ratchet.py: that list only shrinks."
    )


class SlowTestCeiling:
    """The pytest plugin. Registered by conftest.py's `pytest_configure`."""

    def __init__(self, inherited: Iterable[str], enforce: bool, fail: bool) -> None:
        self._inherited = frozenset(inherited)
        self._enforce = enforce
        self._fail = fail
        self._reported: list[str] = []
        self._fixture_seconds: dict[str, float] = {}
        #: Call + function-scoped setup, per test, from the call wrapper to the report.
        self._seconds: dict[str, float] = {}
        self._current: str | None = None

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> Generator[None, None, None]:
        self._current = item.nodeid
        self._fixture_seconds[item.nodeid] = 0.0
        error = marker_error(item)
        if error is not None:
            pytest.fail(error, pytrace=False)
        yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef) -> Generator[None, None, None]:
        started = time.perf_counter()
        yield
        if fixturedef.scope == "function" and self._current is not None:
            self._fixture_seconds[self._current] += time.perf_counter() - started

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        started = time.perf_counter()
        outcome = yield
        seconds = self._fixture_seconds.pop(item.nodeid, 0.0) + time.perf_counter() - started
        self._seconds[item.nodeid] = seconds
        if not self._fail or outcome.excinfo is not None or not self._applies(item):
            return
        factor = load_factor()
        ceiling = CEILING_SECONDS * factor
        if seconds > ceiling:
            outcome.force_exception(
                SlowTestCeilingExceeded(over_ceiling_message(item.nodeid, seconds, ceiling, factor))
            )

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """In CI, give every item one warm rerun for a breach and nothing else.
        A test with a `flaky` marker of its own keeps it as written."""
        if not self._fail:
            return
        for item in items:
            if item.get_closest_marker("flaky") is None:
                item.add_marker(pytest.mark.flaky(reruns=1, only_rerun=[_RERUN_ON]))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo
    ) -> Generator[None, None, None]:
        outcome = yield
        if call.when != "call":
            return
        self._current = None
        report = outcome.get_result()
        seconds = self._seconds.pop(item.nodeid, 0.0)
        # In CI the call wrapper already failed it; here only the local report.
        if self._fail or not report.passed or not self._applies(item):
            return
        factor = load_factor()
        ceiling = CEILING_SECONDS * factor
        if seconds > ceiling:
            report.user_properties.append(
                (_REPORTED, over_ceiling_message(item.nodeid, seconds, ceiling, factor))
            )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Collect reported (not failed) overruns, from this process or an xdist worker."""
        self._reported.extend(value for key, value in report.user_properties if key == _REPORTED)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if not self._reported:
            return
        terminalreporter.section("tests over the per-test ceiling (reported; CI fails these)")
        for message in self._reported:
            terminalreporter.write_line(message.splitlines()[0])

    def _applies(self, item: pytest.Item) -> bool:
        if not self._enforce or _MUTMUT_ENV in os.environ:
            return False
        if item.get_closest_marker(MARKER) is not None:
            return False
        return item.nodeid not in self._inherited


def register(
    config: pytest.Config, inherited: Iterable[str], environ: Mapping[str, str] | None = None
) -> None:
    """Declare the marker and install the plugin on this run."""
    env = os.environ if environ is None else environ
    config.addinivalue_line("markers", MARKER_HELP)
    stepping = bool(config.getoption("usepdb", False) or config.getoption("trace", False))
    fail = env.get(_CI_ENV) == "true"
    if fail and not config.pluginmanager.hasplugin("rerunfailures"):
        # Without the rerun, one noisy run of one test fails the build.
        raise pytest.UsageError(
            "the per-test ceiling fails tests in CI only through one warm rerun, and "
            "pytest-rerunfailures is not installed (or was disabled with -p no:rerunfailures)"
        )
    config.pluginmanager.register(
        SlowTestCeiling(inherited, enforce=not stepping, fail=fail), "slow_test_ceiling"
    )
