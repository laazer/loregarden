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
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_COUNT",
)

#: Ad-hoc config git reads from `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` pairs,
#: counted by `GIT_CONFIG_COUNT`. One such pair setting `core.attributesFile`
#: points every diff at attributes that can mark sources `-diff`, which empties
#: the gate's diff while `--name-only` still lists the file: the environment
#: reaching the same hole a committed `.gitattributes` opens.
GIT_CONFIG_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def scrubbed_git_env() -> Dict[str, str]:
    """The ambient environment minus git's repo bindings and injected config."""
    env = dict(os.environ)
    for name in GIT_LOCATION_ENV_VARS:
        env.pop(name, None)
    for name in [n for n in env if n.startswith(GIT_CONFIG_ENV_PREFIXES)]:
        env.pop(name, None)
    return env


class UnexaminableError(RuntimeError):
    """This run could not examine something it was asked to grade.

    **The invariant of every gate in this directory: a gate may not report
    success over anything it did not actually read.** Both ways of failing that
    live under this one type, and every gate turns it into the same loud
    non-zero exit — so a *third* way of not-reading a file inherits the
    behaviour instead of becoming the next silent pass.
    """


class GitScopeError(UnexaminableError):
    """git could not answer what this run should examine.

    An unresolvable ``--base`` makes ``git diff`` exit 128 with nothing on
    stdout, which is byte-identical to a clean diff. Returning that empty string
    let a gate report a pass over a scope it never resolved, so the failure is
    raised instead and the gates turn it into a loud non-zero exit.
    """


class UnexaminableFileError(UnexaminableError):
    """A file this run was told to grade but could not read or parse.

    Missing (a cone sparse-checkout, a ``skip-worktree`` entry whose file was
    removed), unreadable, or not UTF-8. Every one of those used to fall out of
    the read as ``None`` and be handled exactly like a file that parsed clean:
    ``examined 1 file(s)`` + ``checks passed.`` + exit 0, over a violation
    sitting in the commit. A file that cannot be examined is not clean — it is
    unexaminable, and the run has to say so and fail.
    """


#: git's C-quoting escapes, as `quote_c_style` writes them.
_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
_OCTAL_DIGITS = "01234567"


def decode_git_path(token: str) -> str:
    """One git-printed path token as a real path.

    ``core.quotePath`` is on by default, so every porcelain command that prints
    paths — ``diff --name-only``, ``diff --numstat``, ``ls-files``, and the
    ``+++ b/`` header inside a diff — emits a path with a non-ASCII or control
    byte as a C-quoted literal: ``src/pkg/bäd.py`` arrives as
    ``"src/pkg/b\\303\\244d.py"``. Consumed raw, that literal is not a path: its
    suffix is ``.py"``, so the language filter drops it and the gate reports
    ``examined 0 file(s)``, exit 0, over a real committed violation — and where
    it was one file of several, the printed count was wrong too.

    Decoded here rather than sidestepped with ``-z`` because the ``+++ b/``
    header is inside diff *text* and has no NUL-delimited form: ``-z`` would fix
    three call sites and leave the fourth, which is how this class of bug keeps
    coming back. One decoder on every path boundary is one mechanism, and it
    also makes the quoting do its job — a newline inside a path stays escaped,
    so splitting git's output into lines remains correct.
    """
    if len(token) < 2 or not token.startswith('"') or not token.endswith('"'):
        return token
    body = token[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise GitScopeError(f"git printed a malformed quoted path: {token!r}")
        escape = body[index]
        if escape in _C_ESCAPES:
            out.append(_C_ESCAPES[escape])
            index += 1
            continue
        octal = body[index : index + 3]
        if len(octal) != 3 or any(digit not in _OCTAL_DIGITS for digit in octal):
            raise GitScopeError(f"git printed a malformed quoted path: {token!r}")
        out.append(int(octal, 8))
        index += 3
    return out.decode("utf-8", errors="surrogateescape")


def decoded_git_paths(out: str) -> List[str]:
    """Every path in a git command's path-per-line output, decoded.

    No ``.strip()``: git quotes anything that would make a line ambiguous, so
    what is left is literal — and stripping it corrupted a path with a trailing
    space into one that matches nothing.
    """
    return [decode_git_path(line) for line in out.splitlines() if line]


def read_source_text(path: Path) -> str:
    """The text of a file a gate is about to grade, or a loud failure.

    Never ``None``. The caller has no way to tell a ``None`` meaning "nothing
    wrong here" from one meaning "I never read it", and it took the first
    reading every time.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UnexaminableFileError(
            f"{path}: this run could not read it, so it cannot be reported clean "
            f"({type(exc).__name__}: {exc})"
        ) from exc


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
    return decoded_git_paths(out)


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
    paths = decoded_git_paths(out)
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


def git_rev_exists(repo: Path, ref: str) -> bool:
    """True when ``ref`` names a commit in ``repo``.

    Separates the two failures ``git merge-base`` reports with the same exit
    status: a ref that does not exist, and a ref that exists but shares no
    history with HEAD. They need opposite treatment, and collapsing them
    reported an orphan branch as an unresolvable trunk — a message that sent
    readers to trunk detection while every stage transition blocked.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{_validated_ref(ref)}^{{commit}}"],
        cwd=repo,
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def git_empty_tree(repo: Path) -> str:
    """The hash of the empty tree, as this repository's hash algorithm spells it.

    Diffing against it yields "everything", which is exactly the branch diff of
    a branch that shares no commit with its base. Computed rather than hardcoded
    because a sha256 repository names it differently.
    """
    return _git(["hash-object", "-t", "tree", os.devnull], repo).strip()


def git_merge_base(repo: Path, base_ref: str) -> Optional[str]:
    """The commit this branch forked from, or None when there is no such commit.

    None covers two cases the caller must tell apart — see `git_rev_exists`.
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
    if merge_base is None and git_rev_exists(repo, base_ref):
        # The ref resolves; the histories are disjoint (orphan branch,
        # re-initialised repo, shallow clone). There is no fork point to diff
        # from, but there is no unknown either — every commit on this branch is
        # unshared, so the branch diff *is* the whole tree. Refusing to run here
        # blocked every stage transition in such a workspace, under a message
        # about trunk detection that named the wrong cause.
        empty_tree = git_empty_tree(repo)
        return ResolvedScope(
            SINCE,
            empty_tree,
            git_changed_paths(repo, SINCE, empty_tree),
            f"whole branch and worktree (no common ancestor with {base_ref!r})",
        )
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
    return set(range(1, len(read_source_text(path).splitlines()) + 1))


def located_path(path: Path) -> Path:
    """``path`` with its *directory* resolved, but not the file itself.

    Resolving the whole path follows a symlinked source file out of the tree: a
    module linked into ``src/`` resolves to wherever it really lives, falls
    outside the source root, and is dropped — `examined 0`, exit 0, the vacuous
    pass again — and, for a file that survives that filter, stops matching the
    relpath git used in the diff, emptying its touched-line set. Resolving only
    the parent still normalises the symlinked prefix a checkout can sit behind
    (macOS ``/tmp`` -> ``/private/tmp``), which is why the resolve is there.
    """
    return path.parent.resolve() / path.name


def repo_relative_posix(path: Path, repo: Optional[Path]) -> str:
    """``path`` as git names it in a diff, or unchanged when it is outside ``repo``."""
    if repo is None:
        return path.as_posix()
    try:
        return located_path(path).relative_to(repo.resolve()).as_posix()
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
    most convincing form, so no gate performs any of these steps itself: each
    asks this object, and `resolve_gate_scope` is the only way to build one.

    The pieces it composes stay public and scope-parameterized — the pre-commit
    filters (`pylint_diff_filter`, `ruff_complexity_diff_filter`) call
    `git_diff_cached` and `parse_staged_additions` directly for the index, and
    the gate tests drive `resolve_scope` on its own. So this is a convention the
    three transition gates keep, not a wall the module enforces: a *new* gate
    could still assemble the steps by hand and get them wrong. Route new gates
    through here.
    """

    label: str
    repo: Optional[Path]
    scope: ResolvedScope
    files: List[Path]
    #: relpath -> line numbers this change added or modified, from the resolved diff.
    additions: Dict[str, Set[int]]
    #: relpaths git is not tracking; their whole contents count as touched.
    untracked: FrozenSet[str]
    #: line counts for the "don't make it worse" size checks, and the relpaths
    #: whose diff git suppressed — see `DiffNumstat`.
    numstat: DiffNumstat

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
        if rel in self.untracked or rel in self.numstat.undiffable:
            # Untracked: new in its entirety. Undiffable: git changed it but
            # would not say where, so there is no smaller honest answer than
            # the whole file. Returning the empty set instead is what let a
            # `.gitattributes` `-diff` entry pass every violation in the repo.
            return all_line_numbers(path)
        return self.additions.get(rel, set())

    def net_growing(self, path: Path) -> bool:
        """True when this change adds more lines to ``path`` than it removes.

        An undiffable file has no counts to compare; it is graded whole, so it
        is treated as growing rather than exempted from the size checks.
        """
        rel = repo_relative_posix(path, self.repo)
        if rel in self.numstat.undiffable:
            return True
        added, deleted = self.numstat.counts.get(rel, (0, 0))
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
    if diff_scope not in DIFF_SCOPES:
        # Coercing an unrecognised `--scope` to `staged` made a typo examine the
        # index — empty at a stage transition — and exit 0 over a committed
        # violation: the same vacuous pass, one argument over.
        raise GitScopeError(
            f"unknown scope {diff_scope!r}; expected one of {', '.join(DIFF_SCOPES)}"
        )
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
    numstat = DiffNumstat({}, frozenset())
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
        if line.startswith("+++ "):
            # The quoting wraps the *whole* operand, prefix included:
            # `+++ "b/src/pkg/b\303\244d.py"`. Matching on a literal `+++ b/`
            # therefore missed every non-ASCII path outright — the file got no
            # entry here, so its touched-line set came back empty and every
            # violation in it was filtered out of a passing run.
            name = decode_git_path(line[4:])
            current_file = name[2:] if name.startswith("b/") else None
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


@dataclass(frozen=True)
class DiffNumstat:
    """Per-file line counts for a diff, and the files git would not diff at all.

    ``git diff --numstat`` writes ``-\\t-\\tpath`` when it produced no textual
    diff for a file: a real binary, or — the reason this is a field and not a
    dropped line — a path a ``.gitattributes`` entry marks ``-diff``/``binary``.
    One committed ``*.py -diff`` emptied every ``-U0`` diff while ``--name-only``
    still listed the files, so each gate saw a plausible file count, an empty
    touched-line set for every file, and printed a pass. Discarding that marker
    was what made the suppression invisible, so it is carried instead.
    """

    counts: Dict[str, Tuple[int, int]]
    #: relpaths git reported as changed but refused to diff by line.
    undiffable: FrozenSet[str]


def git_diff_numstat(
    repo: Path, diff_scope: str = STAGED, base_ref: str = "main"
) -> DiffNumstat:
    """relpath -> (added_lines, deleted_lines) for this diff, plus the undiffable ones.

    The counts drive "don't make it worse" checks (e.g. file-length caps) that
    should fire on net growth, not on any touch to an already-oversized file —
    otherwise a pure cleanup/shrink of a long file would itself get blocked.
    """
    out = _run_git([*_scope_args(diff_scope, base_ref), "--numstat", "--"], repo)
    counts: Dict[str, Tuple[int, int]] = {}
    undiffable: Set[str] = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts[0], parts[1], decode_git_path(parts[2])
        if added == "-" or deleted == "-":
            undiffable.add(path)
            continue
        counts[path] = (int(added), int(deleted))
    return DiffNumstat(counts, frozenset(undiffable))


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
