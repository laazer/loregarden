"""Ask `ps` again, and skip rather than fail when it still will not answer.

Not a test module — a helper the pid tests import.

Every property those tests assert is about *what `ps` said*: a live process has
a start time, two processes do not share one, a matching stamp means the run's
own process is still there. None of them is about *whether `ps` answered within
`_PS_TIMEOUT_SECONDS`*. On 2026-09-07 a full suite ran for 1:32:32 against a
usual 42-48 minutes, `ps` did not answer in five seconds, `identify` returned
UNKNOWN, and `test_a_live_process_has_an_identity` failed asserting a property
that was never in question (lg-run-durability-679).

So the three functions here collapse an UNKNOWN reading into one of two
outcomes, and neither of them is a failed assertion: ask again, and if `ps`
still cannot say, skip the test with a reason naming the pid. A skipped test
reports honestly that the machine could not exercise the property; a failed one
claims the property is false.

The retry is an **attempt count, not a duration**. `_PS_TIMEOUT_SECONDS` is
neither read nor patched here, and nothing sleeps between attempts: a second
hand-set timeout floor in a test helper is a number nobody re-measures
(lg-milestone-that-717), and the load that causes this is machine-wide and
lasting, so a delay buys nothing measurable.

What a skip costs is only the cross-platform `ps -o lstart=` contract check,
which is exactly what it says it could not run. The ALIVE, GONE and UNKNOWN
branches of `identify` itself are covered by patched tests in
`test_process_identity.py` that spawn no subprocess and cannot skip.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import ProcessState
from loregarden.services.process_identity import IdentityReading, identify, liveness

#: Attempts, not seconds. Two, because one more `ps` costs at most one further
#: timeout on a site that was already going to fail. If two is ever shown not to
#: be enough, that is a new ticket carrying the measurement, not a tunable here.
_PS_ATTEMPTS = 2


def decided_reading(pid: int) -> IdentityReading:
    """An `identify` reading that came back — ALIVE or GONE, never UNKNOWN.

    Skips the calling test if every attempt is UNKNOWN. The caller then asserts
    its own expectation against a reading that exists.
    """
    for _ in range(_PS_ATTEMPTS):
        reading = identify(pid)
        if reading.state is not ProcessState.UNKNOWN:
            return reading
    pytest.skip(
        f"`ps` never answered about pid {pid} in {_PS_ATTEMPTS} attempts, so this machine "
        "could not tell whether that process is alive or gone — which is not the property "
        "this test asserts (lg-run-durability-679)"
    )


def stamp_of(pid: int) -> str:
    """The fingerprint of a pid that must be live. Never `""`.

    The setup form: tests that record an identity on a run need a real stamp,
    and `""` would reach `liveness` as GONE while the test asserts ALIVE.
    """
    reading = decided_reading(pid)
    assert reading.state is ProcessState.ALIVE, (
        f"pid {pid} was expected to be a live process; `ps` answered {reading.state}"
    )
    return reading.stamp


def decided_liveness(pid: int | None, identity: str | None) -> ProcessState:
    """A `liveness` answer that came back — ALIVE or GONE, never UNKNOWN.

    A separate retry from `decided_reading`: `liveness` performs its own fresh
    `identify`, so it can be UNKNOWN even when the setup reading answered.
    """
    for _ in range(_PS_ATTEMPTS):
        state = liveness(pid, identity)
        if state is not ProcessState.UNKNOWN:
            return state
    pytest.skip(
        f"`ps` never answered about pid {pid} in {_PS_ATTEMPTS} attempts, so this machine "
        "could not tell whether that run's process is still there — which is not the "
        "property this test asserts (lg-run-durability-679)"
    )
