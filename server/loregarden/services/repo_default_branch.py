"""The branch a repository treats as its default, read from the repository.

A workspace whose orchestration profile does not name `git.base_branch` used to
get the schema's `"main"`, whatever the repository held. Loremaker's default is
`master`, so every tree there failed to cut its integration branch ("base branch
'main' does not exist"). The profile still wins when it names a base; this is
what a profile that says nothing means.

Read locally, in one `git for-each-ref`, and remembered per repository for
`CACHE_SECONDS`, because `base_branch` is asked several times on every stage
start and the per-stage git process count is measured: no network, no fetch.
A repository's default branch changes about never; a rename is picked up once
the entry expires. `refs/remotes/origin/HEAD` is the remote's own answer, but
only `clone` writes it, so a repository built with `init` + `remote add` falls
through to whichever of `main`/`master` exists.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from loregarden.services.git_subprocess import run_git

logger = logging.getLogger(__name__)

#: What a repository that answers nothing gets: the schema default before this
#: module existed, so an unreadable repository behaves exactly as it did.
FALLBACK_BRANCH = "main"

#: Local names a repository without `origin/HEAD` is checked for, in the order
#: that breaks a tie when both exist.
_CONVENTIONAL = ("main", "master")

_ORIGIN_HEAD = "refs/remotes/origin/HEAD"
_ORIGIN_PREFIX = "refs/remotes/origin/"
_HEADS = "refs/heads/"

#: How long a detected default branch is reused before git is asked again.
CACHE_SECONDS = 300.0

#: Repository path -> (branch, monotonic time it was read).
_cache: dict[str, tuple[str, float]] = {}

#: Repositories already warned about, so a fallback is reported once per
#: process rather than on every stage transition that asks.
_warned: set[str] = set()


def default_branch(repo_root: Path) -> str:
    """`detect_default_branch`, reused for `CACHE_SECONDS` per repository."""
    key = str(repo_root)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[1] < CACHE_SECONDS:
        return cached[0]
    branch = detect_default_branch(repo_root)
    _cache[key] = (branch, now)
    return branch


def detect_default_branch(repo_root: Path) -> str:
    """The default branch of ``repo_root``, as a local branch name.

    In order: the branch `origin/HEAD` points at, when a local branch of that
    name exists (the integration branch is cut from the local ref); otherwise
    the one of `main`/`master` that exists locally, `main` when both do.
    Anything else gets `FALLBACK_BRANCH` and a warning naming why, since the
    branch named will not exist and the caller's own error follows from it.
    """
    try:
        result = run_git(
            [
                "for-each-ref",
                "--format=%(refname)%09%(symref)",
                _ORIGIN_HEAD,
                *(f"{_HEADS}{name}" for name in _CONVENTIONAL),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # A missing directory or no git binary: the same unreadable repository
        # as a non-zero exit, and reported the same way by `_fallback`.
        return _fallback(repo_root, f"git could not run there: {exc}")
    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        return _fallback(repo_root, f"git could not list its refs: {reason}")

    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, _, target = line.partition("\t")
        refs[name] = target

    remote_default = refs.get(_ORIGIN_HEAD, "").removeprefix(_ORIGIN_PREFIX)
    if remote_default and _has_local_branch(repo_root, remote_default, refs):
        return remote_default

    local = [name for name in _CONVENTIONAL if f"{_HEADS}{name}" in refs]
    if local:
        return local[0]
    return _fallback(
        repo_root,
        "it has no origin/HEAD with a matching local branch, and neither "
        + " nor ".join(_CONVENTIONAL),
    )


def _has_local_branch(repo_root: Path, name: str, listed: dict[str, str]) -> bool:
    ref = f"{_HEADS}{name}"
    if ref in listed:
        return True
    probe = run_git(
        ["rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _fallback(repo_root: Path, why: str) -> str:
    key = str(repo_root)
    if key not in _warned:
        _warned.add(key)
        logger.warning(
            "Cannot tell the default branch of %s (%s); using %r. Set git.base_branch "
            "in the workspace's orchestration profile to name it.",
            repo_root,
            why,
            FALLBACK_BRANCH,
        )
    return FALLBACK_BRANCH
