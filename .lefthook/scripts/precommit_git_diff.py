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
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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
#: Not a scope a caller may ask for: the resolved form of ``worktree`` once the
#: merge base is known. ``git diff <commit>`` compares that commit against the
#: working tree, so one diff carries the branch's commits *and* the edits an
#: agent has not committed yet.
SINCE = "since"
DIFF_SCOPES = (STAGED, WORKTREE, BRANCH)
#: Scopes whose file list must include untracked files: `git diff` never lists
#: them, and a module an agent just wrote is the least-reviewed code in a run.
_UNTRACKED_SCOPES = (WORKTREE, SINCE)


def _validated_ref(ref: str) -> str:
    """Refuse a ref git would read as an option.

    ``--base --output=/tmp/x`` reached ``git diff`` as a flag, wrote a file
    outside the repository, and returned a zero-file exit 0 — a scope nobody
    resolved reported as a clean gate.
    """
    if ref.startswith("-"):
        raise GitScopeError(f"base ref {ref!r} looks like an option, not a revision")
    return ref


def _scope_args(diff_scope: str, base_ref: str) -> List[str]:
    """git-diff selectors for each scope.

    ``worktree`` uses ``HEAD`` so it covers staged *and* unstaged edits — an
    agent leaves both, and a gate that saw only one half would pass work it
    never read. ``branch`` uses the merge-base form so a run is judged on what
    it added, not on whatever landed on the base branch meanwhile. ``since``
    takes an already-resolved commit and compares it against the working tree.
    """
    if diff_scope == WORKTREE:
        return ["HEAD"]
    if diff_scope == BRANCH:
        return [f"{_validated_ref(base_ref)}...HEAD"]
    if diff_scope == SINCE:
        return [_validated_ref(base_ref)]
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
    # The trailing `--` ends the revision list, so nothing derived from a ref can
    # be read as a pathspec.
    return _run_git([*_scope_args(diff_scope, base_ref), "--no-color", "-U0", "--"], repo)


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
    out = _run_git(
        [*_scope_args(diff_scope, base_ref), "--name-only", "--diff-filter=ACMR", "--"], repo
    )
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if diff_scope in _UNTRACKED_SCOPES:
        paths.extend(git_untracked_paths(repo))
    return sorted(set(paths))


def describe_scope(diff_scope: str, base_ref: str) -> str:
    """How a scope reads in the `examined N file(s) — …` line."""
    if diff_scope == WORKTREE:
        return "worktree changes vs HEAD"
    if diff_scope == BRANCH:
        return f"branch diff {base_ref}...HEAD"
    if diff_scope == SINCE:
        return f"worktree and branch changes since {base_ref}"
    return "staged changes"


@dataclass(frozen=True)
class ResolvedScope:
    """What a gate run actually ended up reading, and where it came from.

    ``diff_scope`` and ``base_ref`` are what every downstream diff must use —
    not what the caller asked for. A ``worktree`` request resolves to ``since``
    against the merge base, and re-diffing those files against ``HEAD`` would
    hand the committed ones an empty touched-line set, filtering out precisely
    the violations this scope went looking for.
    """

    diff_scope: str
    base_ref: str
    paths: List[str]
    description: str

    @property
    def includes_untracked(self) -> bool:
        return self.diff_scope in _UNTRACKED_SCOPES


def git_merge_base(repo: Path, base_ref: str) -> Optional[str]:
    """The commit this branch forked from, or None when ``base_ref`` is unknown.

    Unknown is a real answer here, not a swallowed failure: workspaces the
    control plane drives do not all call their trunk ``main``, and every caller
    of this either reports the unresolved ref in the scope line or raises.
    """
    proc = subprocess.run(
        ["git", "merge-base", _validated_ref(base_ref), "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def resolve_scope(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> ResolvedScope:
    """Work out what a gate run should examine, and never let that be "nothing".

    ``worktree`` diffs against ``HEAD``. Any driver that commits the ticket
    worktree before settling a stage — which the external harness must, because
    the worktree-retire guard refuses to remove a tree holding uncommitted work
    — empties that diff. The gate then matched zero files and exited 0, so the
    commit that satisfied one safeguard silently disarmed the other.

    So ``worktree`` resolves to the merge base instead of ``HEAD``: one diff
    covering the branch's commits *and* whatever is still uncommitted. The two
    are not alternatives. Treating them as alternatives is what left the hole
    open after the first fix — the branch diff was consulted only when the
    *whole* tree was clean, so one stray untracked note, or a touch to any
    unrelated tracked file, put the committed change back out of view while the
    gate printed a plausible count and a pass.

    When ``base_ref`` names nothing (a workspace whose trunk is not ``main``),
    there is no branch diff to union in: the run degrades to ``HEAD`` and says
    so in the scope line, unless that leaves it with nothing at all — a run that
    can neither resolve its base nor find a local edit has not examined
    anything, and raises rather than reporting a pass.
    """
    if diff_scope != WORKTREE:
        return ResolvedScope(
            diff_scope,
            base_ref,
            git_changed_paths(repo, diff_scope, base_ref),
            describe_scope(diff_scope, base_ref),
        )
    if not git_has_head(repo):
        # Unborn HEAD: there is no commit to diff against and every file is
        # untracked, which `git_changed_paths` already collects.
        return ResolvedScope(
            WORKTREE,
            base_ref,
            git_changed_paths(repo, WORKTREE, base_ref),
            "worktree changes (no commits yet)",
        )
    merge_base = git_merge_base(repo, base_ref)
    if merge_base is None:
        paths = git_changed_paths(repo, WORKTREE, base_ref)
        if not paths:
            raise GitScopeError(
                f"base ref {base_ref!r} did not resolve and the worktree is clean: "
                "there is nothing this run could have examined"
            )
        return ResolvedScope(
            WORKTREE,
            base_ref,
            paths,
            f"worktree changes vs HEAD (base {base_ref!r} did not resolve; "
            "branch commits not examined)",
        )
    return ResolvedScope(
        SINCE,
        merge_base,
        git_changed_paths(repo, SINCE, merge_base),
        describe_scope(SINCE, base_ref),
    )


def examined_line(label: str, count: int, description: str) -> str:
    """The one line every gate run prints, pass or fail.

    A gate that printed nothing was indistinguishable from a gate that never
    ran, and "passed" over zero files read exactly like "passed" over reviewed
    ones. The count is what separates them.
    """
    return f"{label}: examined {count} file(s) — {description}"


#: Narrows a run's candidate paths to the files this particular gate grades:
#: language, source root, per-gate exemptions. Takes the repo (None outside a
#: git checkout), the candidates, and whether this run *discovered* them from a
#: diff rather than being handed them — a discovered list must be confined to
#: the repo's source root, mirroring the lefthook glob, while an explicit list
#: was already scoped by the caller and narrowing it again would silently drop
#: files that caller meant to have graded.
GateFileSelector = Callable[[Optional[Path], Sequence[Path], bool], List[Path]]


@dataclass(frozen=True)
class GateRun:
    """One gate invocation, already scoped, filtered, counted and announced.

    `resolve_scope` centralised the *decision* and left four steps to each gate:
    override the scope with the resolved one, take its description, filter to
    the files it grades, and print the count. Skipping any one of them
    reproduces the vacuous-gate bug — a gate that reports a pass over files it
    never read — so a gate does not get to perform them itself.
    """

    label: str
    repo: Optional[Path]
    scope: ResolvedScope
    files: List[Path]

    @property
    def diff_scope(self) -> str:
        return self.scope.diff_scope

    @property
    def base_ref(self) -> str:
        return self.scope.base_ref


def resolve_gate_scope(
    *,
    label: str,
    repo: Optional[Path],
    diff_scope: str,
    base_ref: str,
    explicit_files: Iterable[Path],
    select: GateFileSelector,
) -> GateRun:
    """Resolve, filter, count and announce — the whole preamble, once.

    An explicit file list (lefthook passes the staged files) means the caller
    already scoped the run, so only the gate's own filter applies; the scope
    line still names the scope that was asked for rather than assuming the
    index. Otherwise the diff decides, via `resolve_scope`.
    """
    candidates = list(explicit_files)
    discovered = not candidates and repo is not None
    if discovered:
        scope = resolve_scope(repo, diff_scope, base_ref)
        candidates = [repo / rel for rel in scope.paths]
    else:
        scope = ResolvedScope(diff_scope, base_ref, [], describe_scope(diff_scope, base_ref))
    files = select(repo, candidates, discovered)
    # Counted after filtering, always: the number has to be the number of files
    # the gate read, or it is one more thing that looks like a pass over work.
    print(examined_line(label, len(files), scope.description))
    return GateRun(label=label, repo=repo, scope=scope, files=files)


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
    out = _run_git([*_scope_args(diff_scope, base_ref), "--numstat", "--"], repo)
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
