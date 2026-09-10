"""Single chokepoint for shelling out to docker, and the only one there is.

Modelled on `git_subprocess.run_git`, and deliberately different from it in two
directions — the differences are the load-bearing part, so they are pinned by
tests rather than left to a later "consistency" refactor.

**`DOCKER_HOST` and `DOCKER_CONTEXT` are NOT scrubbed.** Git's location
variables are removed because they *mislead*: they rebind a child to a
repository the caller did not name. Docker's are the opposite — they are how a
process reaches a daemon at all. The capacity ledger must book the same daemon
the agents actually use, which is the ambient one, so scrubbing here would have
it reconcile against a Docker nobody is running containers on.

**The `COMPOSE_*` variables ARE scrubbed.** Those are the `GIT_DIR` failure mode
transplanted: `COMPOSE_PROJECT_NAME` and `COMPOSE_FILE` silently rebind
`docker compose ps` to a different project, so a liveness probe asking about
lease A's stack could be answered about lease B's. The base environment also has
git's bindings removed, because docker is invoked from processes (hooks, agent
runners) that carry them.

**Only read verbs are allowed.** This ledger reserves capacity; it never starts
or stops anything, and a human — not a sweep — takes a stack down. "It never
runs `docker compose down`" written in a docstring is worth nothing. The
allowlist below is worth something, and it refuses rather than warning.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

from loregarden.config import settings
from loregarden.services.git_subprocess import scrubbed_git_env

#: Variables that rebind docker compose to a different project or compose file.
#: Not `DOCKER_HOST`/`DOCKER_CONTEXT`: see the module docstring.
COMPOSE_LOCATION_ENV_VARS = (
    "COMPOSE_PROJECT_NAME",
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "COMPOSE_ENV_FILES",
    "COMPOSE_PATH_SEPARATOR",
)

#: Top-level docker verbs this process may run. Read-only, all of them.
READ_ONLY_DOCKER_VERBS = frozenset({"info", "ps", "inspect", "version"})

#: `docker compose <verb>` subcommands this process may run. `ps` answers the
#: liveness question for a lease that named a project; `config` resolves one.
READ_ONLY_COMPOSE_VERBS = frozenset({"ps", "config"})


#: Global flags that consume the token after them. A flag before the verb that
#: is in neither this set nor the boolean one below is refused rather than
#: guessed at — guessing is what let `docker compose -p proj ps` parse its own
#: project name as the subcommand.
DOCKER_GLOBAL_VALUE_FLAGS = frozenset(
    {
        "--context",
        "-c",
        "--host",
        "-H",
        "--log-level",
        "--config",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
    }
)
DOCKER_GLOBAL_BOOL_FLAGS = frozenset({"--debug", "-D", "--tls", "--tlsverify"})

COMPOSE_VALUE_FLAGS = frozenset(
    {
        "-p",
        "--project-name",
        "-f",
        "--file",
        "--profile",
        "--project-directory",
        "--env-file",
        "--ansi",
        "--progress",
        "--parallel",
    }
)
COMPOSE_BOOL_FLAGS = frozenset({"--dry-run", "--compatibility", "--no-ansi"})


class DockerVerbRefused(ValueError):
    """A caller asked for a docker verb that is not read-only, or one this
    function could not identify.

    Raised, not logged and skipped: a mutation reaching this function is a
    programming error, and the loud failure is the enforcement. An
    *unidentifiable* argv is refused for the same reason — a parser that guesses
    can be talked into reading a verb it was never given.
    """


def scrubbed_docker_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """`env` (default: the ambient environment) minus compose's project bindings.

    Layered on `scrubbed_git_env` because anything invoking docker from a hook
    carries git's bindings too, and a compose file resolved from the wrong
    repository is the same bug one tool over.
    """
    base = scrubbed_git_env(env)
    for name in COMPOSE_LOCATION_ENV_VARS:
        base.pop(name, None)
    return base


def _first_verb(
    args: Sequence[str], *, value_flags: frozenset[str], bool_flags: frozenset[str]
) -> str:
    """The first non-flag token, skipping only flags this function recognises.

    Fail-closed: an unrecognised flag raises rather than being assumed boolean.
    A `--flag=value` form carries its own value and consumes no token, so it
    needs no membership check.
    """
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if not token.startswith("-"):
            return token
        if "=" in token:
            continue
        if token in value_flags:
            skip_next = True
            continue
        if token in bool_flags:
            continue
        raise DockerVerbRefused(f"unrecognised docker flag {token!r}; refusing to guess the verb")
    return ""


def assert_read_only(args: Sequence[str]) -> None:
    """Refuse anything that could change what is running on this machine.

    Checks the compose subcommand as well as the top-level verb, because
    `compose` alone is in neither allowlist and `compose up` must not reach the
    daemon through a gap between the two checks.
    """
    verb = _first_verb(
        args, value_flags=DOCKER_GLOBAL_VALUE_FLAGS, bool_flags=DOCKER_GLOBAL_BOOL_FLAGS
    )
    if verb == "compose":
        tokens = list(args)
        rest = tokens[tokens.index("compose") + 1 :]
        sub = _first_verb(rest, value_flags=COMPOSE_VALUE_FLAGS, bool_flags=COMPOSE_BOOL_FLAGS)
        if sub in READ_ONLY_COMPOSE_VERBS:
            return
        raise DockerVerbRefused(
            f"docker compose {sub or '<none>'} is not read-only; "
            f"allowed: {sorted(READ_ONLY_COMPOSE_VERBS)}"
        )
    if verb not in READ_ONLY_DOCKER_VERBS:
        raise DockerVerbRefused(
            f"docker {verb or '<none>'} is not read-only; allowed: {sorted(READ_ONLY_DOCKER_VERBS)}"
        )


def run_docker(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a read-only `docker *args`, capturing text output.

    Explicit parameters rather than a `**kwargs` passthrough: every caller here
    wants the same thing — captured text, a timeout, and a non-raising non-zero
    exit — and a bag would let a future caller pass `check=True` and turn a dead
    daemon into an exception on the reserve path.

    Raises `DockerVerbRefused` for a non-read-only verb. Propagates
    `FileNotFoundError` and `subprocess.TimeoutExpired`; the probe layer above
    turns those into a `DockerProbeOutcome` rather than swallowing them.
    """
    assert_read_only(args)
    return subprocess.run(  # noqa: S603 — argv list, no shell; verbs are allowlisted above
        [settings.docker_binary, *args],
        env=scrubbed_docker_env(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=settings.docker_probe_timeout_seconds if timeout is None else timeout,
    )
