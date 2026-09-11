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
    # The budget, not the whole command: this line broke once when `-x` was added
    # ahead of `-q`, which says nothing about whether the run claims every core.
    assert '-n "$TEST_WORKERS"' in server

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


def test_a_loaded_box_does_not_throttle_the_run_into_never_finishing(tmp_path: Path):
    """The regression this file exists to prevent, learned the expensive way.

    An earlier version collapsed to 2 workers whenever load exceeded the core
    count. But the pre-push suite falls back to the FULL ~3,700 tests whenever a
    change cannot be mapped through the import graph - which includes editing
    these very scripts - and that run took 27:47 at one worker per core. At 2
    workers on a loaded box it did not finish: two pushes were killed by their
    own 30-minute bounds, and because lefthook buffers a command's output until
    it exits, the symptom looked exactly like a deadlock.

    Load must not change the worker count. `nice` is the instrument for not
    starving the gateway; throttling throughput as well made a working hook
    unusable.

    NOTE the PATH: it keeps /usr/sbin. The test this replaced dropped it, so
    `sysctl` was not found, core detection fell back to its default of 4, and
    the budget came out 2 for that reason rather than the one asserted. It
    passed while testing nothing, and would have kept passing after the feature
    it named was deleted.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uptime = fake_bin / "uptime"
    uptime.write_text(
        "#!/bin/sh\necho '12:00 up 3 days, 5 users, load averages: 158.00 120.00 90.00'\n"
    )
    uptime.chmod(0o755)

    real_cores = int(
        subprocess.run(
            ["bash", "-c", "sysctl -n hw.ncpu 2>/dev/null || nproc"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    loaded = int(_budget({"PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"}))

    # Asserted ABSOLUTELY, against this machine's cores — not by comparing a
    # "loaded" reading to an "idle" one. The machine running these tests is
    # itself usually loaded, so both readings collapse together and the
    # comparison passes while the regression is present. The first version of
    # this assertion did exactly that, and a mutation that reinstated the
    # collapse slipped past it.
    assert loaded == max(2, real_cores // 2), (
        f"budget {loaded} on {real_cores} cores under simulated load 158 — "
        "load must not change the worker count, or a full-suite fallback runs for hours"
    )


def test_core_detection_actually_works_here(tmp_path: Path):
    """Guards the trap the replaced test fell into.

    If `sysctl`/`nproc` cannot be reached, `_tw_cores` returns 4 and every budget
    assertion silently becomes an assertion about that fallback instead of about
    this machine. Pin that the real core count is being read.
    """
    real_cores = int(
        subprocess.run(
            ["bash", "-c", "sysctl -n hw.ncpu 2>/dev/null || nproc"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert int(_budget({})) == max(2, real_cores // 2), (
        "the budget is not being computed from this machine's real core count"
    )
