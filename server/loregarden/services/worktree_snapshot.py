"""What one tree looked like at one moment, read with one git call.

A stage used to ask git the same questions several times over while it
dispatched and completed — `status` three times, `rev-parse HEAD` three times,
`symbolic-ref` once more — each service re-deriving what the previous one had
just been told (lg-build-verification-847). `git status --porcelain=v2 --branch`
answers HEAD, the checked-out branch and every dirty path in a single process,
so one read serves every caller that is looking at the same moment.

The hazard is the reason this is a value and not a cache. A tree changes under
a stage: the agent runs, a gate fixer rewrites files, a commit lands. A reading
taken before any of those and consulted after it is a stale answer that looks
exactly like a fresh one. So:

- `TreeSnapshot` is frozen, and `read_tree` is the only thing that fills one.
- It is passed explicitly, never looked up — there is no process-wide, keyed or
  timed store of snapshots to go stale behind anyone's back.
- A step that mutates the tree reads its own precondition and hands back a new
  snapshot; a snapshot is never carried across a mutation. The executor names
  the two moments it holds (`before` and after the agent) rather than reusing one.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loregarden.services.git_subprocess import run_git

logger = logging.getLogger(__name__)

#: `# branch.oid` on a repository with no commit yet.
_UNBORN_OID = "(initial)"
#: `# branch.head` when HEAD is detached.
_DETACHED_HEAD = "(detached)"


def git_reading(
    args: list[str], *, repo_root: Path, describing: str
) -> subprocess.CompletedProcess | None:
    """Run a read-only git command, or None if git could not answer.

    The single place the "None means I could not look" contract of
    `read_tree`, `working_tree_paths` and `paths_committed_since` is honoured,
    because the last two used to honour only HALF of it. They checked
    `returncode`, which covers a git that ran and failed — and missed the case
    where git never ran at all.

    `run_git` passes `cwd` straight to `subprocess.run`, which raises
    FileNotFoundError when the directory is gone. That escaped both functions as
    an exception rather than the None their callers are written to expect, and
    `record_run_evidence` is called with no try/except immediately before
    `complete_run` — so a removed worktree did not degrade to "changed paths not
    recorded", it abandoned the run mid-completion and left it RUNNING with
    nothing behind it (lg-workflow-integrity-713).

    NotADirectoryError and PermissionError are caught for the same reason: each
    is a way of not being able to look, and the caller cannot tell them apart
    from an empty tree anyway.
    """
    try:
        proc = run_git(args, cwd=repo_root, capture_output=True, text=True)
    except OSError as exc:
        logger.warning("could not run git %s in %s: %s", describing, repo_root, exc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "git %s failed in %s (exit %s): %s",
            describing,
            repo_root,
            proc.returncode,
            (proc.stderr or "").strip()[:400],
        )
        return None
    return proc


@dataclass(frozen=True)
class TreeSnapshot:
    """HEAD, branch and dirty paths of `repo_root`, as one `git status` saw them.

    `dirty_paths` is None when git could not answer — NOT an empty set, which
    means it looked and the tree was clean. `head_sha` and `branch` are "" when
    unknown, unborn (no commit yet) or detached respectively; a caller that must
    tell "unreadable" apart checks `readable`.
    """

    repo_root: Path
    head_sha: str
    branch: str
    dirty_paths: frozenset[str] | None

    @property
    def readable(self) -> bool:
        return self.dirty_paths is not None

    def bracket_paths(self) -> set[str]:
        """The dirty paths to bracket a run's edits with — none if unreadable.

        Treating "could not answer" as "nothing was dirty" is the conservative
        direction *for bracketing only*: the delta taken after the run then
        attributes everything it finds to the run rather than silently dropping
        paths, and the post-run read reports its own failure. Nowhere else may
        an unreadable tree read as a clean one.
        """
        return set(self.dirty_paths or ())


def read_tree(repo_root: Path) -> TreeSnapshot:
    """One `git status` of `repo_root`: HEAD, branch, and every dirty path.

    Untracked files are included (`--untracked-files=all`), and `-z` keeps paths
    with spaces or non-ASCII unquoted so they round-trip back into `git add`.
    `--no-ahead-behind` because the branch header would otherwise walk history
    to count commits against the upstream, which nothing here reads.
    """
    proc = git_reading(
        [
            "status",
            "--porcelain=v2",
            "--branch",
            "--no-ahead-behind",
            "-z",
            "--untracked-files=all",
        ],
        repo_root=repo_root,
        describing="status",
    )
    if proc is None:
        return TreeSnapshot(repo_root=repo_root, head_sha="", branch="", dirty_paths=None)
    return _parse_status_v2(repo_root, proc.stdout)


def _parse_status_v2(repo_root: Path, stdout: str) -> TreeSnapshot:
    """Parse `status --porcelain=v2 --branch -z`.

    Each entry kind puts its path after a fixed number of space-separated
    fields, so splitting with a field limit keeps spaces inside the path. A
    rename or copy (`2`) emits its source path as the following record, and the
    source is dirty too — the same set `--porcelain=v1` reported.
    """
    head_sha = ""
    branch = ""
    paths: set[str] = set()
    records = [record for record in stdout.split("\0") if record]
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if record.startswith("# branch.oid "):
            oid = record.removeprefix("# branch.oid ")
            head_sha = "" if oid == _UNBORN_OID else oid
        elif record.startswith("# branch.head "):
            name = record.removeprefix("# branch.head ")
            branch = "" if name == _DETACHED_HEAD else name
        elif record.startswith("1 "):
            paths.add(record.split(" ", 8)[8])
        elif record.startswith("2 "):
            paths.add(record.split(" ", 9)[9])
            if index < len(records):
                paths.add(records[index])
                index += 1
        elif record.startswith("u "):
            paths.add(record.split(" ", 10)[10])
        elif record.startswith("? "):
            paths.add(record[2:])
    return TreeSnapshot(
        repo_root=repo_root,
        head_sha=head_sha,
        branch=branch,
        dirty_paths=frozenset(paths),
    )
