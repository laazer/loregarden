#!/usr/bin/env python3
"""PreToolUse/Bash: refuse `gh pr create` for work already on an integration branch.

A ticket inside an orchestrated tree lands on the tree's integration branch
(`integration/<root>`), which reaches the base as one PR when the root
completes. A PR opened by hand for the ticket's own branch carries the same
commits a second time: lg-durable-remote-336 was landed on
`integration/lg-durable-remote-335` and also opened as PR #555, so when `main`
moved, the same conflict had to be resolved on both. The app's own Open PR
already refuses this (`github_pr_service._refuse_tree_member`); this covers the
agent that runs `gh` directly.

Denies when the head shares history with any local `integration/*` branch
beyond the base: their merge-base is not on the base. That covers a branch
cut from an integration branch and one landed on it, including after later
commits (a containment test on the head alone missed 336 once it gained a
merge commit). A head that is itself an integration branch is the tree's PR
and passes. When the check cannot run, the command passes with a
note saying so.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

_GH_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")
_INTEGRATION = "integration/"


def _shared() -> ModuleType:
    """The UX precheck's command parsing (`_gh_args`, `_option`, `_workdir`, `_git`)."""
    path = Path(__file__).with_name("pr_ux_section_precheck.py")
    spec = importlib.util.spec_from_file_location("pr_ux_section_precheck", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ux = _shared()


def _head(workdir: Path, args: list) -> str:
    head = ux._option(args, "--head", "-H")
    if head:
        return head.rsplit(":", 1)[-1]  # owner:branch
    return ux._git(workdir, "rev-parse", "--abbrev-ref", "HEAD")


def _resolve(workdir: Path, *refs: str) -> str:
    for ref in refs:
        try:
            return ux._git(workdir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except ux.Unreadable:
            continue  # silent-ok: tries the next spelling; none resolving is handled by the caller
    return ""


def _on_base(workdir: Path, sha: str, bases: list) -> bool:
    for ref in bases:
        try:
            ux._git(workdir, "merge-base", "--is-ancestor", sha, ref)
        except ux.Unreadable:
            continue  # silent-ok: exit 1 means "not an ancestor"; the other spelling is tried
        return True
    return False


def _shares_work(workdir: Path, sha: str, branch_sha: str, bases: list) -> bool:
    """Whether ``sha`` and an integration tip have history in common past the base."""
    # `--all`: once both sides have taken the same base, the base tip is one
    # merge-base and the shared ticket commits are another; `git merge-base`
    # alone may answer with either.
    try:
        common = ux._git(workdir, "merge-base", "--all", sha, branch_sha).split()
    except ux.Unreadable:
        return False  # silent-ok: unrelated histories have no merge-base, so nothing is shared
    return any(not _on_base(workdir, commit, bases) for commit in common)


def decide(payload: dict):
    """("deny", reason), (None, note) or None, like the UX precheck."""
    command = (payload.get("tool_input") or {}).get("command") or ""
    match = _GH_PR_CREATE.search(command)
    if match is None:
        return None
    workdir = ux._workdir(command, match, Path(payload.get("cwd") or "."))
    try:
        args = ux._gh_args(command, match)
        head = _head(workdir, args)
        if head.startswith(_INTEGRATION):
            return None
        sha = _resolve(workdir, f"refs/heads/{head}", f"origin/{head}")
        if not sha:
            raise ux.Unreadable(f"branch {head!r} is not known locally")
        base = ux._option(args, "--base", "-B") or "main"
        bases = [ref for ref in (f"origin/{base}", base) if _resolve(workdir, ref)]
        if not bases:
            raise ux.Unreadable(f"base branch {base!r} is not known locally")
        if _on_base(workdir, sha, bases):
            return None  # nothing new on it; gh reports that itself
        holders = []
        for line in ux._git(
            workdir,
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            f"refs/heads/{_INTEGRATION}",
        ).splitlines():
            name, _, branch_sha = line.rpartition(" ")
            if name and _shares_work(workdir, sha, branch_sha, bases):
                holders.append(name)
    except (ux.Unreadable, ValueError) as exc:  # ValueError: shlex on unbalanced quotes
        return None, f"The duplicate-work precheck did not run ({exc})."
    if not holders:
        return None
    named = ", ".join(holders)
    return "deny", (
        f"{head} shares its work with {named}. That tree's work reaches {base} as one pull request "
        f"when its root completes; a PR for {head} would carry the same commits twice, and "
        f"every conflict with {base} would have to be resolved on both. Leave it to the tree. "
        f"If the tree should ship now, open the PR from {holders[0]} instead."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (
        json.JSONDecodeError
    ):  # silent-ok: _emit hands the agent a note that the check did not run
        ux._emit(None, "The duplicate-work precheck could not read its hook input.")
        return 0
    verdict = decide(payload)
    if verdict is not None:
        ux._emit(*verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
