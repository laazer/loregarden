"""`.lefthook/scripts/capacity-run.sh`: what a push does when the ledger cannot be used.

Run against a stand-in CLI (`LOREGARDEN_CAPACITY_CLI`) that fails a set number
of times before behaving like `capacity run`. The policy under test:

- a ledger failure is retried with backoff, and the command runs once it clears;
- the command failing is NOT a ledger failure — it is never retried;
- with no terminal, retries stop at the hard cap and the push fails;
- at a terminal, the person chooses: proceed unreserved, retry, or stop.
"""

from __future__ import annotations

import os
import pty
import subprocess
from pathlib import Path

import pytest
from loregarden.services.git_subprocess import run_git

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / ".lefthook" / "scripts" / "capacity-run.sh"

#: Fails while its counter is below FAKE_FAILURES; then touches the started file
#: and runs the command after `--`, as `loregarden capacity run` does. With
#: FAKE_CLI_CWD it first moves there, as the real CLI moves into server/.
FAKE_CLI = """#!/usr/bin/env bash
count_file="$FAKE_STATE/calls"
calls=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$count_file"
if [ "$calls" -le "${FAKE_FAILURES:-0}" ]; then
  echo "OperationalError: database is locked" >&2
  exit 1
fi
started=""
while [ $# -gt 0 ] && [ "$1" != "--" ]; do
  [ "$1" = "--started-file" ] && started="$2"
  [ "$1" = "--label" ] && printf '%s' "$2" > "$FAKE_STATE/label"
  shift
done
shift
touch "$started"
[ -n "${FAKE_CLI_CWD:-}" ] && cd "$FAKE_CLI_CWD"
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


def test_the_command_runs_where_the_hook_was_even_if_the_cli_moves(tmp_path, fake_cli) -> None:
    # lefthook names its scripts relative to the repository; the CLI runs from
    # its own server/ directory. The first capacity-gated push waited its turn
    # and then failed with "No such file or directory".
    elsewhere = tmp_path / "server"
    elsewhere.mkdir()
    (tmp_path / "script.sh").write_text("pwd > where\n")

    result = subprocess.run(
        _wrap("sh", "script.sh"),
        env=_env(tmp_path, fake_cli, FAKE_CLI_CWD=str(elsewhere)),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert Path((tmp_path / "where").read_text().strip()).resolve() == tmp_path.resolve()


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


def _at_terminal(tmp_path: Path, fake_cli: Path, answer: str, *command: str) -> int:
    """Run the wrapper with a pty on stdin, answering its prompt with `answer`."""
    controller, terminal = pty.openpty()
    process = subprocess.Popen(
        _wrap(*command),
        env=_env(tmp_path, fake_cli, FAKE_FAILURES="1", LOREGARDEN_CAPACITY_RETRIES="1"),
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
    ("worktree", "branch", "place"),
    [
        # A worktree named after its branch: the branch alone, said once.
        ("lg-x-e33a13", "claude/lg-x-e33a13", "claude/lg-x-e33a13"),
        # A worktree that says something the branch does not keeps both.
        (
            "hungry-solomon-46dbeb",
            "claude/adapter-21cc50",
            "hungry-solomon-46dbeb@claude/adapter-21cc50",
        ),
    ],
)
def test_the_label_names_the_branch_once(tmp_path, fake_cli, worktree, branch, place) -> None:
    repo = tmp_path / worktree
    repo.mkdir()
    run_git(["init", "-q", "-b", branch], cwd=repo, check=True)

    result = subprocess.run(
        _wrap("true"),
        env=_env(tmp_path, fake_cli),
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "label").read_text() == f"pre-push test · {place}"
