#!/usr/bin/env python3
"""Whether the server suite already passed on exactly the tree a push put on main.

    tested_tree.py --repo OWNER/NAME --sha SHA [--output GITHUB_OUTPUT]

The merge queue tests a pull request merged onto main's tip and lands that tree,
so main's own run repeated it: 23 of 23 landings to 2026-11-25, 25 minutes each.

Says `reused=true` only when one merged pull request produced this commit, its
head has the same tree as this commit, and the suite's check passed on that
head. The head's tree is what its checks ran on: a pull request run tests the
head merged onto its base, and a head whose tree equals the landed one already
contained that base.

Anything else, GitHub not answering included, says `reused=false` with the
reason, and the suite runs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

#: The check that gathers the suite, and the app whose word for it counts.
SUITE_CHECK = "Server (Python)"
ACTIONS_APP = "github-actions"

#: GitHub associates a landed commit with its pull request a moment after the push.
ATTEMPTS = 4
WAIT_SECONDS = 10

Reader = Callable[[str], Any]


class CannotTell(Exception):
    """GitHub could not be asked, or did not answer in the shape it documents."""


def read_api(path: str) -> Any:
    # gh finds its repository through git, which GIT_DIR would point elsewhere.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    try:
        proc = subprocess.run(
            ["gh", "api", path], capture_output=True, text=True, timeout=60, check=False, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CannotTell(f"gh api {path}: {exc}") from exc
    if proc.returncode != 0:
        raise CannotTell(f"gh api {path}: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CannotTell(f"gh api {path}: {exc}") from exc


def landed_by(repo: str, sha: str, read: Reader, sleep: Callable[[float], None]) -> list[dict]:
    """The merged pull requests that produced `sha`, asked again while GitHub names none."""
    for attempt in range(ATTEMPTS):
        pulls = [
            pull
            for pull in read(f"repos/{repo}/commits/{sha}/pulls")
            if pull["merged_at"] and pull["merge_commit_sha"] == sha
        ]
        if pulls or attempt == ATTEMPTS - 1:
            return pulls
        sleep(WAIT_SECONDS)
    return []


def already_tested(
    repo: str, sha: str, read: Reader, sleep: Callable[[float], None] = time.sleep
) -> tuple[bool, str]:
    """Whether the suite passed on `sha`'s tree already, and why that is the answer."""
    try:
        pulls = landed_by(repo, sha, read, sleep)
        if len(pulls) != 1:
            return False, f"{len(pulls)} merged pull requests produced {sha[:12]}, not one"
        number, head = pulls[0]["number"], pulls[0]["head"]["sha"]
        landed, tested = (
            read(f"repos/{repo}/git/commits/{commit}")["tree"]["sha"] for commit in (sha, head)
        )
        if landed != tested:
            return False, f"#{number} was tested on tree {tested[:12]}, not {landed[:12]}"
        runs = read(
            f"repos/{repo}/commits/{head}/check-runs?check_name={quote(SUITE_CHECK)}&filter=latest"
        )["check_runs"]
        passed = [
            run
            for run in runs
            if run["app"]["slug"] == ACTIONS_APP and run["conclusion"] == "success"
        ]
    except CannotTell as exc:
        return False, str(exc)
    except (KeyError, TypeError) as exc:
        return False, f"unexpected answer from GitHub: {exc!r}"
    if not runs or len(passed) != len(runs):
        return False, f"{SUITE_CHECK} did not pass on #{number}'s head {head[:12]}"
    return True, f"{SUITE_CHECK} passed on this tree ({landed[:12]}) for #{number} at {head[:12]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reused, why = already_tested(args.repo, args.sha, read_api)
    print(f"::notice title=Server tests {'reused' if reused else 'run'}::{why}")
    if args.output is not None:
        with args.output.open("a", encoding="utf-8") as output:
            output.write(f"reused={'true' if reused else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
