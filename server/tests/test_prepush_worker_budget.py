"""A pre-push hook must not take the whole machine.

`pytest -n auto` and a bare `npm test` size themselves to the core count, which
assumes an otherwise idle box. This box is not idle: it serves the MCP gateway
the agents talk to, and several agent sessions work in sibling worktrees at
once, each able to start its own hook.

Observed on 2026-09-09 — load average 158 on 10 cores, port 8000 accepting TCP
while /health returned nothing for 30s or more. Stage agents finished their work
and then timed out writing it back. A hook meant to protect the branch was
taking the control plane down.

These assert the budget rather than the wall-clock: a worker count is the thing
that regresses silently when someone restores `-n auto` for a faster local run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"
WORKERS_SH = SCRIPTS / "test-workers.sh"


def _budget(env: dict[str, str]) -> str:
    """The worker count the helper settles on, under a given environment."""
    result = subprocess.run(
        ["bash", "-c", f'source "{WORKERS_SH}"; printf "%s" "$TEST_WORKERS"'],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **env},
        check=True,
    )
    return result.stdout.strip()


def test_an_explicit_override_wins():
    """A machine whose operator knows better than this heuristic."""
    assert _budget({"LOREGARDEN_TEST_WORKERS": "3"}) == "3"


def test_the_default_leaves_cores_for_everything_else():
    """Never the whole box. The gateway, the editor and the agent processes all
    live here too."""
    workers = int(_budget({}))
    cores = int(
        subprocess.run(
            ["bash", "-c", "sysctl -n hw.ncpu 2>/dev/null || nproc"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert 2 <= workers <= max(2, cores // 2), (
        f"budget {workers} on {cores} cores — a pre-push run should not claim the machine"
    )


def test_neither_runner_asks_for_every_core():
    """The regression this guards. `-n auto` and a bare `npm test` are exactly
    what was there before, and both read as harmless in a diff."""
    server = (SCRIPTS / "server-tests.sh").read_text()
    client = (SCRIPTS / "client-tests.sh").read_text()

    assert "-n auto" not in server, "pytest is back to a worker per core"
    assert 'pytest -q -n "$TEST_WORKERS"' in server

    for line in client.splitlines():
        stripped = line.strip()
        if stripped.startswith('"${TEST_NICE[@]}" npm test'):
            assert "--maxWorkers=" in stripped, f"uncapped jest run: {stripped}"


def test_both_runners_source_the_same_budget():
    """One budget, not two that drift. The scripts already share
    `hook-noninteractive.sh` this way."""
    for name in ("server-tests.sh", "client-tests.sh"):
        assert "test-workers.sh" in (SCRIPTS / name).read_text(), (
            f"{name} does not source the shared worker budget"
        )


def test_the_test_run_yields_to_the_gateway():
    """`nice` is the half that matters when the box is oversubscribed anyway:
    the tests can wait, the control plane answering cannot."""
    assert "nice" in WORKERS_SH.read_text()
    for name in ("server-tests.sh", "client-tests.sh"):
        assert '"${TEST_NICE[@]}"' in (SCRIPTS / name).read_text(), (
            f"{name} runs its tests at normal priority"
        )


@pytest.mark.parametrize(
    "uptime_line",
    [
        "12:00  up 3 days, 20:15, 5 users, load averages: 158.00 120.00 90.00",
        "12:00:00 up 3 days, 20:15,  5 users,  load average: 158.00, 120.00, 90.00",
    ],
)
def test_a_saturated_box_collapses_to_the_floor(tmp_path: Path, uptime_line: str):
    """The case that actually hurt: a sibling worktree's hook is already running.
    Adding half a box of workers to a saturated box is how 158 happened.

    Parametrised over both `uptime` spellings — macOS says "load averages" with
    spaces, GNU says "load average" with commas — because a parser that silently
    reads one of them as zero would restore the old behaviour on that platform
    while every other assertion here still passed.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uptime = fake_bin / "uptime"
    uptime.write_text(f"#!/bin/sh\necho '{uptime_line}'\n")
    uptime.chmod(0o755)

    assert _budget({"PATH": f"{fake_bin}:/usr/bin:/bin"}) == "2"
