"""`.lefthook/scripts/capacity-run.sh`: what a push does when the ledger cannot be used.

Run against a stand-in CLI (`LOREGARDEN_CAPACITY_CLI`) that fails a set number
of times before behaving like `capacity run`. The policy under test:

- a ledger failure is retried with backoff, and the command runs once it clears;
- the command failing is NOT a ledger failure — it is never retried;
- with no terminal, retries stop at the hard cap and the push fails; the cap
  counts from the first failure, never time spent queued before it;
- a line that stopped moving (exit 75) is not a ledger failure: it is not
  retried, and the message says the push was still queued, not that the ledger
  broke — with a terminal or without one;
- at a terminal, the person chooses: proceed unreserved, retry, or stop.
"""

from __future__ import annotations

import os
import pty
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / ".lefthook" / "scripts" / "capacity-run.sh"

#: Fails while its counter is below FAKE_FAILURES; then touches the started file
#: and runs the command after `--`, as `loregarden capacity run` does.
#: FAKE_STALLS makes its first calls exit 75 (the line stopped moving) instead;
#: FAKE_QUEUED_SECONDS makes each failing call wait that long first, as a call
#: that queued and then lost the ledger does. Its arguments go to `args`.
FAKE_CLI = """#!/usr/bin/env bash
count_file="$FAKE_STATE/calls"
calls=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$count_file"
echo "$*" >> "$FAKE_STATE/args"
if [ "$calls" -le "${FAKE_STALLS:-0}" ]; then
  echo "capacity: still queued for host capacity — 3 ahead, about 1800s" >&2
  exit 75
fi
if [ "$calls" -le "${FAKE_FAILURES:-0}" ]; then
  sleep "${FAKE_QUEUED_SECONDS:-0}"
  echo "OperationalError: database is locked" >&2
  exit 1
fi
started=""
while [ $# -gt 0 ] && [ "$1" != "--" ]; do
  [ "$1" = "--started-file" ] && started="$2"
  shift
done
shift
touch "$started"
"$@"
"""


@pytest.fixture(name="fake_cli")
def fake_cli_fixture(tmp_path) -> Path:
    cli = tmp_path / "fake-cli.sh"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    return cli


def _env(tmp_path: Path, fake_cli: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOREGARDEN_")}
    env.update(
        {
            "LOREGARDEN_CAPACITY_CLI": str(fake_cli),
            "FAKE_STATE": str(tmp_path),
            "TMPDIR": str(tmp_path),
        }
    )
    env.update(extra)
    return env


def _calls(tmp_path: Path) -> int:
    counter = tmp_path / "calls"
    return int(counter.read_text()) if counter.exists() else 0


def _wrap(*command: str) -> list[str]:
    return ["bash", str(WRAPPER), "pre-push test", "--footprint", "heavy", "--", *command]


def test_a_ledger_failure_is_retried_and_the_command_runs_once_it_clears(
    tmp_path, fake_cli
) -> None:
    result = subprocess.run(
        _wrap("sh", "-c", "exit 3"),
        env=_env(tmp_path, fake_cli, FAKE_FAILURES="2"),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 3
    assert _calls(tmp_path) == 3


def test_a_failing_command_is_not_mistaken_for_a_ledger_failure(tmp_path, fake_cli) -> None:
    result = subprocess.run(
        _wrap("sh", "-c", "exit 1"),
        env=_env(tmp_path, fake_cli),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert _calls(tmp_path) == 1


def test_without_a_terminal_retries_stop_at_the_hard_cap(tmp_path, fake_cli) -> None:
    ran = tmp_path / "ran"
    result = subprocess.run(
        _wrap("touch", str(ran)),
        env=_env(
            tmp_path,
            fake_cli,
            FAKE_FAILURES="99",
            LOREGARDEN_CAPACITY_HARD_CAP_SECONDS="2",
        ),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert not ran.exists()
    assert _calls(tmp_path) >= 2
    assert "gave up after" in result.stderr


def test_off_runs_the_command_without_the_ledger(tmp_path, fake_cli) -> None:
    result = subprocess.run(
        _wrap("sh", "-c", "exit 0"),
        env=_env(tmp_path, fake_cli, LOREGARDEN_CAPACITY="off"),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0
    assert _calls(tmp_path) == 0
    assert "WITHOUT reserving" in result.stderr


def _without_terminal(tmp_path: Path, fake_cli: Path, *command: str, **extra: str):
    return subprocess.run(
        _wrap(*command),
        env=_env(tmp_path, fake_cli, **extra),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_stalled_line_fails_without_a_terminal_saying_it_was_still_queued(
    tmp_path, fake_cli
) -> None:
    ran = tmp_path / "ran"

    result = _without_terminal(tmp_path, fake_cli, "touch", str(ran), FAKE_STALLS="1")

    assert result.returncode == 75
    assert not ran.exists()
    assert _calls(tmp_path) == 1  # not retried: retrying rejoins at the back
    assert "still queued" in result.stderr
    assert "could not reserve" not in result.stderr
    assert "ledger error" not in result.stderr


def test_the_stall_timeout_is_passed_to_the_cli(tmp_path, fake_cli) -> None:
    result = _without_terminal(tmp_path, fake_cli, "true", LOREGARDEN_CAPACITY_STALL_SECONDS="1234")

    assert result.returncode == 0
    assert "--stall-timeout 1234 " in (tmp_path / "args").read_text()


def test_time_spent_queued_does_not_count_against_the_hard_cap(tmp_path, fake_cli) -> None:
    """Queued 3s, then the ledger failed once: a 2s cap on failures still retries."""
    ran = tmp_path / "ran"

    result = _without_terminal(
        tmp_path,
        fake_cli,
        "touch",
        str(ran),
        FAKE_FAILURES="1",
        FAKE_QUEUED_SECONDS="3",
        LOREGARDEN_CAPACITY_HARD_CAP_SECONDS="2",
    )

    assert result.returncode == 0
    assert ran.exists()
    assert _calls(tmp_path) == 2


def _at_terminal(tmp_path: Path, fake_cli: Path, answer: str, *command: str, **extra: str) -> int:
    """Run the wrapper with a pty on stdin, answering its prompt with `answer`."""
    controller, terminal = pty.openpty()
    process = subprocess.Popen(
        _wrap(*command),
        env=_env(
            tmp_path,
            fake_cli,
            **({"FAKE_FAILURES": "1", "LOREGARDEN_CAPACITY_RETRIES": "1"} | extra),
        ),
        cwd=tmp_path,
        stdin=terminal,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(terminal)
    try:
        os.write(controller, f"{answer}\n".encode())
        return process.wait(timeout=60)
    finally:
        os.close(controller)


@pytest.mark.parametrize(
    ("answer", "expected_code", "expected_calls", "ran"),
    [
        ("p", 0, 1, True),  # proceed without reserving: the CLI is not asked again
        ("r", 0, 2, True),  # retry: the second attempt succeeds and runs it
        ("x", 1, 1, False),  # anything else stops the push
    ],
)
def test_at_a_terminal_the_person_chooses(
    tmp_path, fake_cli, answer, expected_code, expected_calls, ran
) -> None:
    marker = tmp_path / "ran"

    code = _at_terminal(tmp_path, fake_cli, answer, "touch", str(marker))

    assert code == expected_code
    assert _calls(tmp_path) == expected_calls
    assert marker.exists() is ran


@pytest.mark.parametrize(
    ("answer", "expected_code", "expected_calls", "ran"),
    [
        ("p", 0, 1, True),  # proceed without reserving
        ("r", 0, 2, True),  # queue again: the second attempt is granted and runs it
        ("x", 75, 1, False),  # anything else stops, still saying it was queued
    ],
)
def test_at_a_terminal_a_stalled_line_lets_the_person_choose(
    tmp_path, fake_cli, answer, expected_code, expected_calls, ran
) -> None:
    marker = tmp_path / "ran"

    code = _at_terminal(
        tmp_path, fake_cli, answer, "touch", str(marker), FAKE_FAILURES="0", FAKE_STALLS="1"
    )

    assert code == expected_code
    assert _calls(tmp_path) == expected_calls
    assert marker.exists() is ran
