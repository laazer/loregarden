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

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

#: Git exports these into hooks and everything they spawn, and they **override
#: `cwd`** — a gate invoked with `--repo <workspace>` from a context that has
#: GIT_DIR set reads the *other* repository, finds nothing, and reports a pass.
#: Same list, same reason, as `GIT_LOCATION_ENV_VARS` in
#: `loregarden.services.git_subprocess`; it is repeated rather than imported
#: because these scripts install into workspaces that have no such module.
GIT_LOCATION_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)


def scrubbed_git_env() -> Dict[str, str]:
    """The ambient environment minus git's repo bindings."""
    env = dict(os.environ)
    for name in GIT_LOCATION_ENV_VARS:
        env.pop(name, None)
    return env


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
            env=scrubbed_git_env(),
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
        env=scrubbed_git_env(),
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
        env=scrubbed_git_env(),
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
    #: True when ``base_ref`` did not resolve and this run fell back to a
    #: narrower scope than the caller asked for. The fallback still reads the
    #: uncommitted edits, so it is worth running — but it cannot see the
    #: branch's commits, so a run that then grades nothing has not examined the
    #: change at all and must not report a pass.
    degraded: bool = False

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
        env=scrubbed_git_env(),
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
    there is no branch diff to union in: the run degrades to ``HEAD``, says so
    in the scope line, and is marked ``degraded`` so `resolve_gate_scope` can
    refuse to call it a pass if the narrower scope leaves this gate with
    nothing to grade.
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
        return ResolvedScope(
            WORKTREE,
            base_ref,
            git_changed_paths(repo, WORKTREE, base_ref),
            f"worktree changes vs HEAD (base {base_ref!r} did not resolve; "
            "branch commits not examined)",
            degraded=True,
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


def all_line_numbers(path: Path) -> Set[int]:
    """Every line of ``path``, as a touched-line set.

    What a file with no diff to scope against is graded on: an untracked file
    is new in its entirety, so every line in it is part of this change.
    """
    try:
        return set(range(1, len(path.read_text(encoding="utf-8").splitlines()) + 1))
    except (OSError, UnicodeDecodeError):
        return set()


def repo_relative_posix(path: Path, repo: Optional[Path]) -> str:
    """``path`` as git names it in a diff, or unchanged when it is outside ``repo``."""
    if repo is None:
        return path.as_posix()
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass(frozen=True)
class GateRun:
    """One gate invocation: scoped, filtered, counted, announced, and diffed.

    `resolve_scope` centralised the *decision* and left the rest to each gate —
    override the requested scope with the resolved one, take its description,
    filter to the files this gate grades, print the count, then re-diff those
    files to find which of their lines the change touched. Every one of those
    steps has to use the *resolved* scope, and a gate that reaches for the
    requested one instead compiles, prints a credible file count, and reports a
    pass over an empty touched-line set. That is the vacuous-gate bug in its
    most convincing form, so a gate does not get to perform any of these steps
    itself: it asks this object.
    """

    label: str
    repo: Optional[Path]
    scope: ResolvedScope
    files: List[Path]
    #: relpath -> line numbers this change added or modified, from the resolved diff.
    additions: Dict[str, Set[int]]
    #: relpaths git is not tracking; their whole contents count as touched.
    untracked: FrozenSet[str]
    #: relpath -> (added, deleted), for "don't make it worse" size checks.
    numstat: Dict[str, Tuple[int, int]]

    @property
    def diff_scope(self) -> str:
        return self.scope.diff_scope

    @property
    def base_ref(self) -> str:
        return self.scope.base_ref

    def touched_lines(self, path: Path) -> Optional[Set[int]]:
        """Lines in ``path`` this run may report violations on.

        ``None`` means "no diff to scope against at all" — no repository, so
        the whole file is fair game; callers treat that as an unbounded set.
        """
        if self.repo is None:
            return None
        rel = repo_relative_posix(path, self.repo)
        if rel in self.untracked:
            return all_line_numbers(path)
        return self.additions.get(rel, set())

    def net_growing(self, path: Path) -> bool:
        """True when this change adds more lines to ``path`` than it removes."""
        added, deleted = self.numstat.get(repo_relative_posix(path, self.repo), (0, 0))
        return added > deleted


def resolve_gate_scope(
    *,
    label: str,
    repo: Optional[Path],
    diff_scope: str,
    base_ref: str,
    explicit_files: Iterable[Path],
    select: GateFileSelector,
) -> GateRun:
    """Resolve, filter, count, announce and diff — the whole preamble, once.

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
    if scope.degraded and not files:
        # The fallback is only tolerable while it still has something to grade.
        # With nothing left, this run read none of the branch's commits and none
        # of the working tree either: exiting 0 here would report a pass over a
        # scope that was never resolved, which is the bug, not the fallback.
        raise GitScopeError(
            f"base ref {scope.base_ref!r} did not resolve, so this run fell back to "
            "worktree changes vs HEAD and found nothing to grade: the branch's "
            "commits went unread"
        )
    # Counted after filtering, always: the number has to be the number of files
    # the gate read, or it is one more thing that looks like a pass over work.
    print(examined_line(label, len(files), scope.description))
    additions: Dict[str, Set[int]] = {}
    untracked: FrozenSet[str] = frozenset()
    numstat: Dict[str, Tuple[int, int]] = {}
    if repo is not None and files:
        diff = git_diff_cached(repo, scope.diff_scope, scope.base_ref)
        additions = {
            path: {ln for ln, _ in items} for path, items in parse_staged_additions(diff).items()
        }
        numstat = git_diff_numstat(repo, scope.diff_scope, scope.base_ref)
        if scope.includes_untracked:
            untracked = frozenset(git_untracked_paths(repo))
    return GateRun(
        label=label,
        repo=repo,
        scope=scope,
        files=files,
        additions=additions,
        untracked=untracked,
        numstat=numstat,
    )


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
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
