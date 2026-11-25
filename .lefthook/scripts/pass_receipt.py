#!/usr/bin/env python3
"""Which server test files have passed on exactly this code, so a push need not rerun them.

In the 3 days to 2026-11-24, 29 of 52 branches took a heavy lease more than once:
one ran its suite by hand for 41 minutes, again for 41, and its pre-push for 33.
A push that dies after its hooks (SSH idles out, exit 141) pays for the whole run
again. The second run of the same files on the same tree proves nothing.

`tests/conftest.py` registers `PassRecorder` in every pytest session. A session
that passed writes one receipt: the git tree it ran on and the test files it ran
in full. `pass_receipt.py covers` answers whether receipts for HEAD's tree name
every file it is given; `push-server-tests.sh` asks it before queueing.

A receipt is written only when the tree *is* what ran:

- nothing uncommitted or untracked, when the session starts and when it ends,
  and the same HEAD tree at both;
- no tracked file written in between, which is how an edit put back (a mutation
  run in the same worktree) would otherwise go unseen;
- the repository's own conftest and package, not a copy (`mutants/`);
- whole files: no `-k`, `-m`, `--deselect`, `--lf`, or `::` in an argument.

Receipts name the machine and Python they ran on and are read for a day. They
live in the git common directory, so worktrees share them and none is ever
committed. A landing does not read them: `land_verification` runs the suite itself.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

RECEIPTS = "loregarden-test-passes"
#: Long enough for a day of pushes; the tree says what ran, not what was installed.
MAX_AGE_SECONDS = 24 * 3600

_NARROWING_OPTIONS = ("keyword", "markexpr", "deselect", "lf", "stepwise", "collectonly")


def _git(repo: Path, *args: str) -> str:
    """Stripped stdout. The environment's own GIT_DIR is dropped: it beats `cwd`."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    done = subprocess.run(
        ["git", *args],  # py-git: allow - stdlib-only hook script
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def committed_tree(repo: Path) -> str:
    """HEAD's tree, or "" when the worktree holds anything HEAD does not."""
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        return ""
    return _git(repo, "rev-parse", "HEAD^{tree}")


def environment() -> str:
    return f"{platform.system()} {platform.machine()} python {platform.python_version()}"


def _store(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / RECEIPTS


def _written_since(repo: Path, moment: float) -> bool:
    """Whether any tracked file changed on disk, even if it was changed back.

    ctime, which a write moves and nothing but the kernel sets.
    """
    for name in _git(repo, "ls-files", "-z").split("\0"):
        path = repo / name
        if name and os.path.lexists(path) and path.lstat().st_ctime >= moment:
            return True
    return False


def record(repo: Path, tree: str, files: set[str]) -> Path:
    """Write one receipt, and drop the ones too old to be read."""
    store = _store(repo)
    store.mkdir(exist_ok=True)
    for old in store.glob("*.json"):
        if time.time() - old.stat().st_mtime > MAX_AGE_SECONDS:
            old.unlink(missing_ok=True)
    body = {"environment": environment(), "files": sorted(files)}
    receipt = store / f"{tree}-{uuid.uuid4().hex}.json"
    partial = receipt.with_suffix(".tmp")
    partial.write_text(json.dumps(body), encoding="utf-8")
    os.replace(partial, receipt)
    return receipt


def passed_files(repo: Path, tree: str) -> set[str]:
    """Every file a current receipt for `tree` names, `server/`-relative."""
    passed: set[str] = set()
    for receipt in _store(repo).glob(f"{tree}-*.json"):
        if time.time() - receipt.stat().st_mtime > MAX_AGE_SECONDS:
            continue
        body = json.loads(receipt.read_text(encoding="utf-8"))
        if body["environment"] == environment():
            passed.update(body["files"])
    return passed


class PassRecorder:
    """Pytest plugin for the controller of one session."""

    def __init__(self, repo: Path, tree: str) -> None:
        self._repo = repo
        self._tree = tree
        self._started = time.time()
        self._files: set[str] = set()

    def pytest_runtest_logreport(self, report) -> None:
        self._files.add(report.nodeid.split("::")[0])

    def pytest_sessionfinish(self, exitstatus) -> None:
        if exitstatus != 0 or not self._files:
            return
        if committed_tree(self._repo) != self._tree or _written_since(self._repo, self._started):
            return
        record(self._repo, self._tree, self._files)


def recorder_for(config, *, repo: Path, package: Path) -> PassRecorder | None:
    """The recorder for this session, or None when its pass would prove less than it says."""
    if hasattr(config, "workerinput"):
        return None
    if not package.is_relative_to(repo / "server") or "mutants" in package.parts:
        return None
    if config.rootpath != repo / "server":
        return None
    if any(config.getoption(name, None) for name in _NARROWING_OPTIONS):
        return None
    if any("::" in argument for argument in config.args):
        return None
    try:
        tree = committed_tree(repo)
    except (
        OSError,
        subprocess.CalledProcessError,
    ):  # silent-ok: no git here, so no receipt; the push runs the tests instead
        return None
    return PassRecorder(repo, tree) if tree else None


def _covers(repo: Path) -> int:
    wanted = {line.strip().removeprefix("server/") for line in sys.stdin if line.strip()}
    tree = committed_tree(repo)
    if not wanted or not tree:
        return 1
    missing = wanted - passed_files(repo, tree)
    if missing:
        print(f"{len(wanted) - len(missing)} of {len(wanted)} already passed on this tree")
        return 1
    print(f"all {len(wanted)} test file(s) already passed on tree {tree[:12]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="exit 0 when every file on stdin already passed")
    parser.add_argument("command", choices=["covers"])
    parser.add_argument("--repo", required=True, type=Path)
    return _covers(parser.parse_args().repo.resolve())


if __name__ == "__main__":
    sys.exit(main())
