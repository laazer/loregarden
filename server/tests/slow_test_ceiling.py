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
45819701261, 45818593089, 45817899729; 15,700 distinct test ids), taking each
test's slowest setup+call across the runs: p50 0.10 s, p90 0.85 s, p99 4.01 s,
p99.9 9.89 s; 88 tests over 5 s. 5 s sits just above p99, so a typical test has
about 6x headroom over p90 and the tests above it are the tail this is meant to
stop growing.

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
        self._call_seconds: dict[str, float] = {}
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
        yield
        self._call_seconds[item.nodeid] = time.perf_counter() - started

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo
    ) -> Generator[None, None, None]:
        outcome = yield
        if call.when != "call":
            return
        self._current = None
        report = outcome.get_result()
        seconds = self._fixture_seconds.pop(item.nodeid, 0.0) + self._call_seconds.pop(
            item.nodeid, 0.0
        )
        if not report.passed or not self._applies(item):
            return
        factor = load_factor()
        ceiling = CEILING_SECONDS * factor
        if seconds <= ceiling:
            return
        message = over_ceiling_message(item.nodeid, seconds, ceiling, factor)
        if self._fail:
            report.outcome = "failed"
            report.longrepr = message
        else:
            report.user_properties.append((_REPORTED, message))

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
    config.pluginmanager.register(
        SlowTestCeiling(inherited, enforce=not stepping, fail=env.get(_CI_ENV) == "true"),
        "slow_test_ceiling",
    )
