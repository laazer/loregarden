"""Single chokepoint for shelling out to git.

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
)


def scrubbed_git_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """`env` (default: the ambient environment) minus git's repo bindings.

    Exposed separately because tools that shell out to git themselves — `gh`, for
    one — inherit the same bindings and need the same treatment.
    """
    base = dict(os.environ if env is None else env)
    for name in GIT_LOCATION_ENV_VARS:
        base.pop(name, None)
    return base


def run_git(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run `git *args` with the repo-binding env vars removed.

    A thin passthrough otherwise: `check`, `capture_output`, `text`, and
    `timeout` mean what they mean to `subprocess.run`, so call sites keep their
    own semantics (some want bytes, some want a non-raising non-zero exit).
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=scrubbed_git_env(env),
        **kwargs,
    )
