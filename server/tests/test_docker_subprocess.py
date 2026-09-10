"""What the docker chokepoint refuses, and what it must not scrub.

Both halves are here because both are load-bearing and neither is obvious from
reading the call sites:

- The read-only allowlist is the *enforcement* of "this ledger never starts or
  stops containers". A comment asserting that is worth nothing; these tests pin
  that `docker compose up` cannot reach the daemon through this function, and
  that `compose` is not a gap between the two allowlists.
- `DOCKER_HOST` surviving is a deliberate divergence from `run_git`, whose whole
  purpose is removing location variables. A later refactor tidying the two into
  agreement would silently point the capacity ledger at a different daemon than
  the agents use, and nothing else in the suite would notice.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest
from loregarden.services.docker_subprocess import (
    DockerVerbRefused,
    assert_read_only,
    run_docker,
    scrubbed_docker_env,
)


@pytest.mark.parametrize(
    "args",
    [
        ["info", "--format", "{{json .}}"],
        ["ps", "--filter", "label=com.docker.compose.project=x"],
        ["inspect", "-f", "{{.State.Running}}", "abc"],
        ["version"],
        ["compose", "-p", "proj", "ps"],
        ["--context", "desktop", "ps"],
    ],
)
def test_read_verbs_are_allowed(args: list[str]) -> None:
    assert_read_only(args)


@pytest.mark.parametrize(
    "args",
    [
        ["compose", "up", "-d"],
        ["compose", "-p", "proj", "down"],
        ["compose"],
        ["run", "alpine"],
        ["kill", "abc"],
        ["rm", "-f", "abc"],
        ["system", "prune", "-f"],
        [],
    ],
)
def test_mutating_verbs_are_refused(args: list[str]) -> None:
    with pytest.raises(DockerVerbRefused):
        assert_read_only(args)


def test_run_docker_refuses_before_spawning_anything() -> None:
    """The refusal must precede the subprocess, not follow it."""
    with mock.patch("subprocess.run") as spawn:
        with pytest.raises(DockerVerbRefused):
            run_docker(["compose", "up"])
    spawn.assert_not_called()


def test_compose_project_bindings_are_scrubbed() -> None:
    env = {
        "COMPOSE_PROJECT_NAME": "someone-elses-stack",
        "COMPOSE_FILE": "/elsewhere/compose.yml",
        "COMPOSE_PROFILES": "dev",
        "PATH": "/usr/bin",
    }
    scrubbed = scrubbed_docker_env(env)
    assert "COMPOSE_PROJECT_NAME" not in scrubbed
    assert "COMPOSE_FILE" not in scrubbed
    assert "COMPOSE_PROFILES" not in scrubbed
    assert scrubbed["PATH"] == "/usr/bin"


def test_git_bindings_are_scrubbed_too() -> None:
    """Docker is invoked from processes that carry git's bindings; a compose
    file resolved from the wrong repository is the same defect one tool over."""
    scrubbed = scrubbed_docker_env({"GIT_DIR": "/somewhere/.git", "PATH": "/usr/bin"})
    assert "GIT_DIR" not in scrubbed


def test_daemon_selection_survives_scrubbing() -> None:
    """The divergence from `run_git`, pinned.

    The ledger must book the daemon the agents actually reach. Removing these
    would have it reconcile against a different Docker while every other test
    still passed.
    """
    env = {"DOCKER_HOST": "tcp://127.0.0.1:2375", "DOCKER_CONTEXT": "remote"}
    scrubbed = scrubbed_docker_env(env)
    assert scrubbed["DOCKER_HOST"] == "tcp://127.0.0.1:2375"
    assert scrubbed["DOCKER_CONTEXT"] == "remote"


def test_run_docker_builds_expected_argv_and_never_raises_on_exit_code() -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with mock.patch("subprocess.run", return_value=completed) as spawn:
        result = run_docker(["ps", "-q"], timeout=3.0)

    argv = spawn.call_args.args[0]
    assert argv[0] == "docker"
    assert argv[1:] == ["ps", "-q"]
    assert spawn.call_args.kwargs["check"] is False
    assert spawn.call_args.kwargs["timeout"] == 3.0
    assert result.returncode == 1


@pytest.mark.parametrize("args", [["--made-up", "ps"], ["compose", "--unknown", "ps"]])
def test_unrecognised_flags_are_refused_rather_than_guessed(args: list[str]) -> None:
    """The bug this rule exists for: `docker compose -p proj ps` parsed `proj`
    as the subcommand while `-p` was assumed boolean. A parser that guesses can
    be talked into reading a verb it was never given, so it refuses instead."""
    with pytest.raises(DockerVerbRefused):
        assert_read_only(args)


def test_compose_project_flag_does_not_shadow_the_subcommand() -> None:
    assert_read_only(["compose", "-p", "proj", "ps"])
    assert_read_only(["compose", "--project-name=proj", "ps"])
    with pytest.raises(DockerVerbRefused):
        assert_read_only(["compose", "-p", "proj", "down"])
