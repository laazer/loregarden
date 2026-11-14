"""Put a branch's own commits on its target, leaving behind history the target replaced.

A ticket branch cut from an integration branch carries that branch's history.
When the integration branch is published, it is squashed onto the base and
moved (#621), so the ticket branch now holds the old history and its target
holds a squash of it. Merging the target then compares from the base the two
diverged at, and every file both sides touched conflicts. lg-milestone-that-1424,
2026-11-13: 201 conflicted files against integration/lg-milestone-that-491, of
which its six commits of its own touched one.

Replaying those six onto the target asks only about the commits that are the
ticket's. Everything else on the branch is left behind, so a replay is safe
only when the target already holds all of it (`work_landed.landing_commit`).
When the target does not, the branch is the last place some landed work still
exists, and replaying would erase it. 1424's old history carries
lg-milestone-that-1316's toast work, which is on neither its target nor `main`.
That answer is a refusal naming the files, never a replay.

**Its own commits** are the non-merge commits on its first parents that are on
neither the target nor the base: a ticket's commits sit on that walk, and the
merges that refreshed it from its target do not count (as in
`landing_carried_work.carries_own_work`). Each is replayed with `merge-tree`
against its own first parent and `commit-tree`, keeping its author and message,
and nothing is checked out. A commit whose change the target already holds is
dropped. The caller moves the branch by compare-and-swap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from loregarden.services.git_merge_noco import merge_base, signs_commits
from loregarden.services.git_subprocess import run_git
from loregarden.services.work_landed import landing_commit

#: How many files a refusal names before it counts the rest.
_NAMED_FILES = 3


@dataclass(frozen=True)
class Replay:
    """The replayed tip in ``sha``, or why there is none in ``detail``.

    ``conflicted_files`` is set when one of the commits does not apply to the target.
    """

    sha: str = ""
    detail: str = ""
    replayed: int = 0
    conflicted_files: tuple[str, ...] = ()


def replay_own_commits(repo_root: Path, branch_sha: str, target_sha: str, base: str) -> Replay:
    """``branch_sha``'s own commits written onto ``target_sha``. No ref moves.

    Raises `RuntimeError` when git cannot answer, which is not a refusal: the
    caller says so and goes on to its next answer.
    """
    own = _own_commits(repo_root, branch_sha, target_sha, base)
    if not own:
        return Replay(detail="it has no commits of its own to replay")
    left_behind = _left_behind(repo_root, branch_sha, own[0])
    for commit in left_behind:
        if not landing_commit(repo_root, commit, target_sha):
            return Replay(
                detail=(
                    f"its history below its own commits ({commit[:12]}) holds work its target "
                    f"lacks, in {_lacking(repo_root, commit, target_sha)}; replaying would "
                    "erase the last copy"
                )
            )
    onto = target_sha
    replayed = 0
    for commit in own:
        tree, conflicts, words = _pick_tree(repo_root, commit, onto)
        if conflicts:
            return Replay(
                detail=f"{commit[:12]} does not apply to its target: {', '.join(conflicts)}",
                conflicted_files=conflicts,
            )
        if tree == _git_out(repo_root, "rev-parse", f"{onto}^{{tree}}"):
            continue
        onto = _commit_like(repo_root, commit, tree, onto)
        replayed += 1
    if not replayed:
        return Replay(detail="its target already holds every commit of its own")
    return Replay(sha=onto, replayed=replayed)


def _own_commits(repo_root: Path, branch_sha: str, target_sha: str, base: str) -> list[str]:
    """Oldest first: first-parent, non-merge, on neither the target nor the base."""
    excluded = [target_sha]
    for ref in (f"refs/heads/{base}", f"refs/remotes/origin/{base}"):
        if _git(repo_root, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            excluded.append(ref)
    listed = _git_out(
        repo_root,
        "rev-list",
        "--first-parent",
        "--no-merges",
        "--reverse",
        branch_sha,
        "--not",
        *excluded,
    )
    return listed.split()


def _left_behind(repo_root: Path, branch_sha: str, oldest_own: str) -> list[str]:
    """What a replay leaves out: the commit below the oldest own commit, and the
    other parents of every merge on the first-parent walk above it."""
    cut = _git_out(repo_root, "rev-parse", f"{oldest_own}^")
    merges = _git_out(
        repo_root, "rev-list", "--first-parent", "--merges", "--parents", f"{cut}..{branch_sha}"
    )
    others = [parent for line in merges.splitlines() for parent in line.split()[2:]]
    return [cut, *dict.fromkeys(others)]


def _lacking(repo_root: Path, commit: str, target_sha: str) -> str:
    """The files ``commit`` changed since it diverged from the target, at content
    the target lacks, where the target has not touched them since either."""
    fork = merge_base(repo_root, commit, target_sha)
    if not fork:
        return "a history with no common ancestor"
    added = set(_names(repo_root, fork, commit))
    differ = set(_names(repo_root, commit, target_sha))
    moved_on = set(_names(repo_root, fork, target_sha))
    lacking = sorted((added & differ) - moved_on) or sorted(added & differ)
    named = lacking[:_NAMED_FILES]
    rest = len(lacking) - len(named)
    return ", ".join(named) + (f" and {rest} more" if rest else "") if named else "its tree"


def _pick_tree(repo_root: Path, commit: str, onto: str) -> tuple[str, tuple[str, ...], str]:
    """``commit``'s change against its first parent, applied to ``onto``: the
    tree, the conflicted paths (empty when clean), and git's words."""
    result = _git(
        repo_root,
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--merge-base",
        f"{commit}^",
        onto,
        commit,
    )
    lines = result.stdout.splitlines()
    if result.returncode == 0 and lines:
        return lines[0].strip(), (), ""
    if result.returncode == 1 and lines:
        files = []
        for line in lines[1:]:
            if not line.strip():
                break
            files.append(line.strip())
        return lines[0].strip(), tuple(files), result.stdout.strip()
    raise RuntimeError(
        f"git merge-tree could not replay {commit[:12]}: "
        f"{(result.stderr or result.stdout or 'no output').strip()}"
    )


def _commit_like(repo_root: Path, commit: str, tree: str, parent: str) -> str:
    """A commit of ``tree`` on ``parent`` with ``commit``'s author and message."""
    fields = _git_out(repo_root, "log", "-1", "--format=%an%x00%ae%x00%aI%x00%B", commit)
    name, email, date, message = fields.split("\x00", 3)
    sign = ["-S"] if signs_commits(repo_root) else []
    written = run_git(
        ["commit-tree", tree, *sign, "-p", parent, "-F", "-"],
        cwd=str(repo_root),
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": date,
        },
        input=f"{message.rstrip()}\n\n(cherry picked from commit {commit})\n",
        check=False,
        capture_output=True,
        text=True,
    )
    if written.returncode != 0:
        raise RuntimeError(
            f"git commit-tree failed replaying {commit[:12]}: "
            f"{(written.stderr or written.stdout).strip()}"
        )
    return written.stdout.strip()


def _names(repo_root: Path, old: str, new: str) -> list[str]:
    return _git_out(repo_root, "diff", "--name-only", "--no-renames", old, new).splitlines()


def _git(repo_root: Path, *args: str):
    return run_git(list(args), cwd=str(repo_root), check=False, capture_output=True, text=True)


def _git_out(repo_root: Path, *args: str) -> str:
    result = _git(repo_root, *args)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {args[0]} failed: {(result.stderr or result.stdout or 'no output').strip()}"
        )
    return result.stdout.strip()
