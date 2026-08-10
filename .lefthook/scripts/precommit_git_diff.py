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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


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


def _run_git(args: List[str], repo: Path) -> str:
    proc = subprocess.run(
        ["git", "diff", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def git_diff_cached(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> str:
    return _run_git([*_scope_args(diff_scope, base_ref), "--no-color", "-U0"], repo)


def git_untracked_paths(repo: Path) -> List[str]:
    """Repo-relative paths git is not tracking yet, respecting .gitignore.

    A brand-new file is invisible to `git diff` until it is added. In pre-commit
    that is fine — nothing unstaged is being committed. In a gate it is not: an
    agent's new module is exactly the code that has never been reviewed, and
    scoping it out would report a clean pass over the only new file in the run.
    """
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def git_changed_paths(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> List[str]:
    """Repo-relative paths this diff touches, for callers given no explicit file list."""
    out = _run_git([*_scope_args(diff_scope, base_ref), "--name-only", "--diff-filter=ACMR"], repo)
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if diff_scope == WORKTREE:
        paths.extend(git_untracked_paths(repo))
    return sorted(set(paths))


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
