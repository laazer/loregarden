"""Parse git diff output: added lines per file, for the organization policy gates.

Two callers with two notions of "what this change touched":

* **pre-commit** (lefthook) scopes to the index — `git diff --cached`.
* **an orchestration run** scopes to the working tree, because an agent's edits
  are uncommitted when a transition gate fires. Scoping that run to the index
  would find nothing staged and report a clean pass over unreviewed work.

`diff_scope` names which one; everything downstream consumes the same parsed
shape either way.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitScopeError(RuntimeError):
    """git could not answer what this run should examine.

    An unresolvable ``--base`` makes ``git diff`` exit 128 with nothing on
    stdout, which is byte-identical to a clean diff. Returning that empty string
    let a gate report a pass over a scope it never resolved, so the failure is
    raised instead and the gates turn it into a loud non-zero exit.
    """


def git_repo_root() -> Optional[Path]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return Path(top) if top else None


STAGED = "staged"
WORKTREE = "worktree"
BRANCH = "branch"
DIFF_SCOPES = (STAGED, WORKTREE, BRANCH)


def _scope_args(diff_scope: str, base_ref: str) -> List[str]:
    """git-diff selectors for each scope.

    ``worktree`` uses ``HEAD`` so it covers staged *and* unstaged edits — an
    agent leaves both, and a gate that saw only one half would pass work it
    never read. ``branch`` uses the merge-base form so a run is judged on what
    it added, not on whatever landed on the base branch meanwhile.
    """
    if diff_scope == WORKTREE:
        return ["HEAD"]
    if diff_scope == BRANCH:
        return [f"{base_ref}...HEAD"]
    return ["--cached"]


def _git(command: List[str], repo: Path) -> str:
    proc = subprocess.run(
        ["git", *command],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit status {proc.returncode}"
        raise GitScopeError(f"`git {' '.join(command)}` failed in {repo}: {detail}")
    return proc.stdout


def _run_git(args: List[str], repo: Path) -> str:
    return _git(["diff", *args], repo)


def git_diff_cached(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> str:
    return _run_git([*_scope_args(diff_scope, base_ref), "--no-color", "-U0"], repo)


def git_untracked_paths(repo: Path) -> List[str]:
    """Repo-relative paths git is not tracking yet, respecting .gitignore.

    A brand-new file is invisible to `git diff` until it is added. In pre-commit
    that is fine — nothing unstaged is being committed. In a gate it is not: an
    agent's new module is exactly the code that has never been reviewed, and
    scoping it out would report a clean pass over the only new file in the run.
    """
    out = _git(["ls-files", "--others", "--exclude-standard"], repo)
    return [line.strip() for line in out.splitlines() if line.strip()]


def git_has_head(repo: Path) -> bool:
    """False in a repository with no commits yet — an unborn HEAD.

    `git diff HEAD` cannot resolve there, but a brand-new workspace is not a
    scope the gate failed to resolve: every file in it is untracked, which the
    worktree scope already collects. Exit 1 from `rev-parse --verify` is that
    command's answer to the question, not a failure being swallowed.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def git_changed_paths(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> List[str]:
    """Repo-relative paths this diff touches, for callers given no explicit file list."""
    if diff_scope == WORKTREE and not git_has_head(repo):
        return sorted(set(git_untracked_paths(repo)))
    out = _run_git([*_scope_args(diff_scope, base_ref), "--name-only", "--diff-filter=ACMR"], repo)
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if diff_scope == WORKTREE:
        paths.extend(git_untracked_paths(repo))
    return sorted(set(paths))


@dataclass(frozen=True)
class ResolvedScope:
    """What a gate run actually ended up reading, and where it came from.

    ``diff_scope`` is the scope every downstream diff must use — not the one the
    caller asked for. When the worktree fallback fires, scoping the line-level
    diffs to ``HEAD`` would hand every file an empty touched-line set and filter
    out precisely the violations the fallback went looking for.
    """

    diff_scope: str
    base_ref: str
    paths: List[str]
    fell_back: bool

    def describe(self) -> str:
        if self.fell_back:
            return f"branch diff {self.base_ref}...HEAD (worktree is clean)"
        if self.diff_scope == WORKTREE:
            return "worktree changes vs HEAD"
        if self.diff_scope == BRANCH:
            return f"branch diff {self.base_ref}...HEAD"
        return "staged changes"


def resolve_scope(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> ResolvedScope:
    """Work out what a gate run should examine, and never let that be "nothing".

    ``worktree`` diffs against ``HEAD``. Any driver that commits the ticket
    worktree before settling a stage — which the external harness must, because
    the worktree-retire guard refuses to remove a tree holding uncommitted work
    — empties that diff. The gate then matched zero files and exited 0, so the
    commit that satisfied one safeguard silently disarmed the other.

    A clean worktree therefore falls back to the branch diff, which still holds
    the very commit that emptied the worktree diff. It is a fallback rather than
    a failure because a stage that legitimately changes no code (plan, review,
    spec) must not block the transition; when git itself cannot resolve the
    scope, `_git` raises instead and the run fails loudly.
    """
    paths = git_changed_paths(repo, diff_scope, base_ref)
    if diff_scope == WORKTREE and not paths and git_has_head(repo):
        return ResolvedScope(BRANCH, base_ref, git_changed_paths(repo, BRANCH, base_ref), True)
    return ResolvedScope(diff_scope, base_ref, paths, False)


def examined_line(label: str, count: int, description: str) -> str:
    """The one line every gate run prints, pass or fail.

    A gate that printed nothing was indistinguishable from a gate that never
    ran, and "passed" over zero files read exactly like "passed" over reviewed
    ones. The count is what separates them.
    """
    return f"{label}: examined {count} file(s) — {description}"


def parse_staged_additions(diff: str) -> Dict[str, List[Tuple[int, str]]]:
    """Map relpath (as in diff, posix) -> [(new_line_no, added_line_without_leading_plus)]."""
    result: Dict[str, List[Tuple[int, str]]] = {}
    current_file: Optional[str] = None
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            current_file = None
            i += 1
            continue
        if line.startswith("+++ b/"):
            name = line[6:].strip()
            current_file = None if name == "/dev/null" else name
            i += 1
            continue
        if line.startswith("@@ "):
            m = HUNK_HEADER_RE.match(line)
            i += 1
            if not m or current_file is None:
                continue
            new_line = int(m.group(3))
            while i < len(lines):
                l = lines[i]
                if l.startswith("@@") or l.startswith("diff --git"):
                    break
                if l.startswith("\\"):
                    i += 1
                    continue
                if not l:
                    i += 1
                    continue
                prefix = l[0]
                body = l[1:]
                if prefix == "+":
                    lst = result.setdefault(current_file, [])
                    lst.append((new_line, body))
                    new_line += 1
                elif prefix == " ":
                    new_line += 1
                elif prefix == "-":
                    pass
                i += 1
            continue
        i += 1
    return result


def git_diff_numstat(
    repo: Path, diff_scope: str = STAGED, base_ref: str = "main"
) -> Dict[str, Tuple[int, int]]:
    """Map relpath -> (added_lines, deleted_lines) for this diff.

    Used for "don't make it worse" checks (e.g. file-length caps) that should
    fire on net growth, not on any touch to an already-oversized file —
    otherwise a pure cleanup/shrink of a long file would itself get blocked.
    """
    out = _run_git([*_scope_args(diff_scope, base_ref), "--numstat"], repo)
    result: Dict[str, Tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            result[path] = (int(added), int(deleted))
        except ValueError:
            continue  # binary file ("-\t-\tpath")
    return result


def staged_file_text(repo: Path, relpath: str) -> Optional[str]:
    proc = subprocess.run(
        ["git", "show", f":0:{relpath}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
