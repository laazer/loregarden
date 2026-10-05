"""Single chokepoint for shelling out to git, and to `gh`, which runs git itself.

Git exports GIT_DIR — and, depending on the command, GIT_WORK_TREE and
GIT_INDEX_FILE — into the environment of hooks and of anything they spawn. Those
variables bind the child process to *that* repository and **override `cwd`**, so
a service that runs `git -C /some/workspace status` from inside a hook silently
operates on the repo the hook fired for. That is the exact failure the pre-push
suite hit: tests building throwaway repos in `tmp_path` inherited a worktree's
GIT_DIR and died on `git add .` with exit 128.

`.lefthook/scripts/hook-noninteractive.sh` unsets those vars at the hook layer,
which fixes pushes. It does nothing for the server running under any other
parent that has them set, so every git invocation in the server goes through
here and the child never inherits the binding.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Variables that rebind git to a different repository, index, object store, or
# pathspec root. GIT_DIR/GIT_WORK_TREE are the ones that caused the worktree
# breakage; the rest travel with them out of a hook and would point an otherwise
# scrubbed child back at the wrong repo state.
GIT_LOCATION_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_COUNT",
)

#: Ad-hoc config git reads from `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` pairs,
#: counted by `GIT_CONFIG_COUNT`. One pair setting `core.attributesFile` can mark
#: sources `-diff`, which empties a diff while `--name-only` still lists the file
#: — the environment reaching the hole a committed `.gitattributes` opens.
GIT_CONFIG_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def scrubbed_git_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """`env` (default: the ambient environment) minus git's repo bindings and config.

    Exposed separately because tools that shell out to git themselves — `gh`, for
    one — inherit the same bindings and need the same treatment.
    """
    base = dict(os.environ if env is None else env)
    for name in GIT_LOCATION_ENV_VARS:
        base.pop(name, None)
    for name in [n for n in base if n.startswith(GIT_CONFIG_ENV_PREFIXES)]:
        base.pop(name, None)
    return base


def run_git(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    index_file: Path | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run `git *args` with the repo-binding env vars removed.

    A thin passthrough otherwise: `check`, `capture_output`, `text`, and
    `timeout` mean what they mean to `subprocess.run`, so call sites keep their
    own semantics (some want bytes, some want a non-raising non-zero exit).

    *index_file* is the one binding a caller may set, and only by naming it
    here: an inherited GIT_INDEX_FILE is scrubbed because nobody chose it, while
    a scratch index is how a tree is snapshotted without touching the real one.
    """
    child_env = scrubbed_git_env(env)
    if index_file is not None:
        child_env["GIT_INDEX_FILE"] = str(index_file)
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=child_env,
        **kwargs,
    )


#: Which `gh` `run_gh` spawns, overriding PATH lookup — the same convention as
#: the agent CLIs' `LOREGARDEN_*_BIN`. The test suite points it at a stub.
GH_BINARY_ENV = "LOREGARDEN_GH_BIN"


def gh_binary() -> str:
    """The `gh` to spawn: `LOREGARDEN_GH_BIN` when set, otherwise `gh` from PATH."""
    return (os.environ.get(GH_BINARY_ENV) or "").strip() or "gh"


#: The `gh` account loregarden acts as, instead of whichever account is active on this
#: machine. `gh` acts as its *active* account, and on a machine signed in to
#: several that is a setting anything else can flip with `gh auth switch` — one
#: here was active as an account with read-only access to every workspace repo,
#: so PR creation, merges and issue writes failed or 404'd while `git push`,
#: which goes over SSH, kept working. Machine-local, so it belongs in `.env`
#: (gitignored, sourced by `scripts/dev-server.sh`), never a committed profile.
GH_USER_ENV = "LOREGARDEN_GH_USER"

#: `gh auth token` reads the local keyring; it does not touch the network.
GH_TOKEN_LOOKUP_TIMEOUT_SECONDS = 15

#: `gh`'s documented exit code for "authentication required".
GH_AUTH_REQUIRED_EXIT = 4

#: Tokens by account. `gh` OAuth tokens do not rotate on their own, and a lookup
#: forks `gh` — once per process is enough. Failures are not cached, so fixing
#: the login takes effect on the next call.
_gh_tokens: dict[str, str] = {}


class GhAccountUnavailable(RuntimeError):
    """The configured `gh` account has no token this machine can produce."""


class GhLogin(BaseModel):
    """One account in `gh auth status --json hosts`."""

    login: str
    active: bool
    #: `gh`'s own vocabulary: "success", "error", "timeout".
    state: str

    @property
    def signed_in(self) -> bool:
        """Whether `gh` could use this account's stored token."""
        return self.state == "success"  # py-org: allow-string — gh's vocabulary, not ours


class _GhAuthStatus(BaseModel):
    hosts: dict[str, list[GhLogin]]


def _spawn_gh(
    args: Sequence[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout: float | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [gh_binary(), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def configured_gh_user() -> str | None:
    """The account `LOREGARDEN_GH_USER` names, or None to act as the active one."""
    return os.environ.get(GH_USER_ENV, "").strip() or None


def gh_token_for_user(user: str) -> str:
    """The token `gh` holds for `user`, from its own keyring.

    Raises `GhAccountUnavailable` naming the account and `gh`'s reason — never
    falls back to the active account, which is the account this exists to avoid.
    """
    cached = _gh_tokens.get(user)
    if cached:
        return cached
    try:
        result = _spawn_gh(
            ["auth", "token", "--user", user],
            cwd=None,
            env=scrubbed_git_env(),
            timeout=GH_TOKEN_LOOKUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhAccountUnavailable(f"could not ask gh for {user!r}'s token: {exc}") from exc
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        reason = (result.stderr or result.stdout).strip() or f"gh exited {result.returncode}"
        raise GhAccountUnavailable(f"gh has no token for {user!r}: {reason}")
    _gh_tokens[user] = token
    return token


def configured_gh_token() -> str | None:
    """The token for `LOREGARDEN_GH_USER`, or None when it is unset.

    Raises `GhAccountUnavailable` when it is set and cannot be resolved.
    """
    user = configured_gh_user()
    return gh_token_for_user(user) if user else None


def gh_logins(*, timeout: float | None = GH_TOKEN_LOOKUP_TIMEOUT_SECONDS) -> list[GhLogin]:
    """The github.com accounts `gh` is signed in to, without reading any token.

    Asked with the ambient environment, not the configured account's token: a
    `GH_TOKEN` would make `gh` report that one account and hide the rest.
    Raises `GhAccountUnavailable` when `gh` cannot answer.
    """
    try:
        result = _spawn_gh(
            ["auth", "status", "--hostname", "github.com", "--json", "hosts"],
            cwd=None,
            env=scrubbed_git_env(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhAccountUnavailable(f"could not run gh auth status: {exc}") from exc
    if not result.stdout.strip():
        reason = result.stderr.strip() or f"gh exited {result.returncode}"
        raise GhAccountUnavailable(f"gh auth status failed: {reason}")
    try:
        status = _GhAuthStatus.model_validate_json(result.stdout)
    except ValidationError as exc:
        raise GhAccountUnavailable(f"gh auth status printed an unexpected shape: {exc}") from exc
    return status.hosts.get("github.com", [])


def run_gh(
    args: Sequence[str],
    *,
    cwd: Path,
    gh_token: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `gh *args` from `cwd`, with git's repo bindings scrubbed.

    The only place a `gh` argv is built. `gh` picks its target repository by
    shelling out to git, so an inherited GIT_DIR would aim it at whatever repo
    the parent was bound to — and `cwd` decides which worktree's branch it reads.

    Text output, captured; a non-zero exit is returned, not raised, so each
    caller decides what a failure means. `timeout` raises
    `subprocess.TimeoutExpired` as `subprocess.run` does, and a missing binary
    raises `FileNotFoundError`.

    `gh_token` acts as that account for this call only (`GH_TOKEN`). Without
    one, the call acts as `LOREGARDEN_GH_USER` when that is set. When it is set
    and cannot be resolved, the call is not made: it returns exit
    `GH_AUTH_REQUIRED_EXIT` with the reason on stderr, which every caller
    already surfaces as a `gh` failure. Running it as the active account instead
    would be the silent wrong-account call this setting exists to prevent.
    """
    env = scrubbed_git_env()
    if gh_token is None:
        try:
            gh_token = configured_gh_token()
        except GhAccountUnavailable as exc:
            logger.warning("gh %s not run: %s", " ".join(args[:2]), exc)
            return subprocess.CompletedProcess(
                args=[gh_binary(), *args],
                returncode=GH_AUTH_REQUIRED_EXIT,
                stdout="",
                stderr=f"{exc} (set by {GH_USER_ENV})",
            )
    if gh_token:
        env["GH_TOKEN"] = gh_token
    return _spawn_gh(args, cwd=cwd, env=env, timeout=timeout)
