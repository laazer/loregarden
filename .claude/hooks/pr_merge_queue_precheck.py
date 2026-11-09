#!/usr/bin/env python3
"""PreToolUse/Bash: refuse `gh pr merge` here; pull requests land through the merge queue.

`main` requires a branch to be up to date with it before merging, so a PR merged
with `gh pr merge --auto` goes stale every time another PR lands, and someone
updates it and waits for every check again. Of the 49 PRs merged to `main` in
the five days after loregarden's merge queue shipped (2026-11-03 to
2026-11-08), 7 went through it. The queue merges each PR onto `main`'s tip
itself, waits for the required checks on exactly that result, and lands it, so
nobody updates a branch by hand.

Denies every `gh pr merge` except `--disable-auto` (which cancels one) and
names the enqueue call for the PR, by number when the command names one. A
person can still merge from their own terminal; this hook only sees an agent's
Bash. When the command cannot be parsed, it passes with a note saying so.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

_GH_PR_MERGE = re.compile(r"\bgh\s+pr\s+merge\b")
_PR_URL = re.compile(r"/pull/(?P<number>\d+)")
#: The loregarden workspace's slug in its own database.
WORKSPACE_SLUG = "loregarden"
#: `gh pr merge` options that take a value, so their value is not the PR argument.
_VALUED = {
    "--body",
    "-b",
    "--body-file",
    "-F",
    "--subject",
    "-t",
    "--match-head-commit",
    "--author-email",
    "-A",
    "--repo",
    "-R",
}


def _shared() -> ModuleType:
    """The UX precheck's command parsing (`_gh_args`, `_emit`)."""
    path = Path(__file__).with_name("pr_ux_section_precheck.py")
    spec = importlib.util.spec_from_file_location("pr_ux_section_precheck", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ux = _shared()


def pr_number(args: list) -> str:
    """The PR the command names, as a number; "" for the current branch's or a branch name."""
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in _VALUED:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        if arg.isdigit():
            return arg
        found = _PR_URL.search(arg)
        return found.group("number") if found else ""
    return ""


def decide(payload: dict):
    """("deny", reason), (None, note) or None, like the UX precheck."""
    command = (payload.get("tool_input") or {}).get("command") or ""
    match = _GH_PR_MERGE.search(command)
    if match is None:
        return None
    try:
        args = ux._gh_args(command, match)
    except ValueError as exc:  # shlex on unbalanced quotes
        return None, f"The merge-queue precheck did not run ({exc})."
    if "--disable-auto" in args:
        return None
    number = pr_number(args) or "<the PR's number>"
    call = f"loregarden_enqueue_merge pr_number={number} workspace_slug={WORKSPACE_SLUG}"
    return "deny", (
        "Pull requests here land through loregarden's merge queue, not `gh pr merge`. The "
        "queue merges the PR onto main's tip, waits for the required checks on that result, "
        "and lands exactly it; nobody updates the branch by hand when main moves. Enqueue it "
        f"with the MCP tool `{call}`, or, without MCP tools, `./scripts/loregarden-cli.sh mcp "
        f"call {call}`. The queue then pushes its staged commit onto the PR's branch: stop "
        "pushing to it unless you mean the queue to pick that push up instead. It moves only "
        "while the loregarden server runs; read where it stands with "
        "`loregarden_merge_queue_status`. If the queue is down and this cannot wait, ask the "
        "user to merge it from their own terminal."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (
        json.JSONDecodeError
    ):  # silent-ok: _emit hands the agent a note that the check did not run
        ux._emit(None, "The merge-queue precheck could not read its hook input.")
        return 0
    verdict = decide(payload)
    if verdict is not None:
        ux._emit(*verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
