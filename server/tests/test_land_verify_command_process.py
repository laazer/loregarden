"""How a land verification command runs: its worker grant, and how a timeout ends it.

lg-milestone-that-2196's first landing ran the configured `pytest -n auto` for
1800s and was refused with an empty report. Two things made that worse than it
had to be, and these pin both:

- `-n auto` started one worker per core whatever capacity the check was
  granted, so the check added to the overload that made it slow.
- `subprocess.run` killed only `uv run` on timeout. pytest and its workers kept
  running under the retry, and everything the suite had printed was dropped.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from pathlib import Path
from unittest import mock

from loregarden.models.domain import DockerFootprint, GateOutcome
from loregarden.services import land_verification
from loregarden.services.docker_capacity import resolve_weights
from loregarden.services.land_verification import run_verify_command

SLOW_TREE = """
import subprocess, sys, time
print("reached the slow part", flush=True)
subprocess.Popen([sys.executable, "-c",
    "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)",
    sys.argv[1]])
time.sleep(60)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until(condition, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def test_pytest_n_auto_is_sized_by_the_grant(tmp_path: Path) -> None:
    probe = shlex.join(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['LOREGARDEN_CAPACITY_WORKERS'],"
            " os.environ['PYTEST_XDIST_AUTO_NUM_WORKERS'])",
        ]
    )
    cpus, _ = resolve_weights(DockerFootprint.STACK)

    outcome = run_verify_command(probe, tmp_path, footprint=DockerFootprint.STACK)

    assert outcome.outcome is GateOutcome.PASSED, outcome
    assert outcome.output.split() == [str(int(cpus))] * 2


def test_a_timeout_kills_the_whole_tree_and_keeps_what_it_printed(tmp_path: Path) -> None:
    script = tmp_path / "slow_tree.py"
    script.write_text(SLOW_TREE)
    pid_file = tmp_path / "grandchild.pid"
    command = shlex.join([sys.executable, str(script), str(pid_file)])

    # The grandchild sleeps twice as long as its parent, so a timeout that only
    # waits the tree out still finds it alive below, rather than passing slowly.
    with mock.patch.object(land_verification, "LAND_VERIFY_TIMEOUT_SECONDS", 3):
        outcome = run_verify_command(command, tmp_path, footprint=DockerFootprint.NONE)

    assert outcome.outcome is GateOutcome.UNAVAILABLE and not outcome.ok, outcome
    assert "timed out after 3s" in outcome.detail
    assert "reached the slow part" in outcome.output
    assert pid_file.exists(), "the grandchild never started, so nothing was tested"
    grandchild = int(pid_file.read_text())
    assert _wait_until(lambda: not _alive(grandchild)), (
        f"pid {grandchild} outlived the timeout that refused its land"
    )
