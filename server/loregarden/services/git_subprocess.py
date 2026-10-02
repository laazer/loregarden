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

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

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

    `gh_token` acts as that account for this call only (`GH_TOKEN`), for a
    machine whose active `gh` account cannot write to the repository.
    """
    env = scrubbed_git_env()
    if gh_token:
        env["GH_TOKEN"] = gh_token
    return subprocess.run(
        [gh_binary(), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
