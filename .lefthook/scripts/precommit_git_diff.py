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

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
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


#: A source file larger than this is not graded. No hand-written module comes
#: near it; a path that does is a device, a stream, or a mistake, and reading it
#: to the end is how a gate stops being a gate.
MAX_SOURCE_BYTES = 8 * 1024 * 1024


def read_source_text(path: Path, *, repo: Optional[Path]) -> str:
    """The text of a file a gate is about to grade, or a loud failure.

    Never ``None``. The caller has no way to tell a ``None`` meaning "nothing
    wrong here" from one meaning "I never read it", and it took the first
    reading every time.

    A graded path is checked before it is opened, because ``located_path``
    deliberately resolves only the parent: a symlinked module keeps the relpath
    git used, which is what makes it gradeable at all. The file itself is still
    a symlink, so opening it follows the link wherever it goes — ``src/x.py ->
    /dev/zero`` never returns and allocates until the host gives out, and a
    broken link raises out of the middle of the walk. Neither is a thing to
    report clean, and neither is a thing to read.

    ``repo`` is required and keyword-only, and ``None`` — "this run has no
    repository, so there is no boundary" — has to be passed on purpose. A
    default would let a caller that never passed one read exactly as before,
    and "nobody passed it" would be indistinguishable from "nothing to check".

    The boundary is crossed only when the *listed* path is inside ``repo`` and
    its target is not: that is a path presenting itself as a repository file
    while reading content the repository does not contain. A path the caller
    named outright makes no such claim — it scoped the run — which is the same
    reason `resolve_gate_scope` does not narrow an explicit list to the source
    root. Refusing those instead would refuse every fixture a gate is pointed
    at directly, which is an outage, not a stricter gate.
    """
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        # RuntimeError, not only OSError: `Path.resolve` raises it on a symlink
        # cycle (ELOOP). Catching OSError alone let one committed cycle anywhere
        # under the source root take down a gate with a traceback, even when
        # every listed file was clean — the catalog walk reaches it. The
        # TypeScript gate already handled this; this is the Python side matching.
        #
        # Same sentence as the read failure below, deliberately: a path that
        # cannot be resolved and one that cannot be opened are the same fact to
        # every caller, and a listed-but-absent file reaches this branch first
        # now that the target is checked before the open.
        raise UnexaminableFileError(
            f"{path}: this run could not read it, so it cannot be reported clean "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    if not real.is_file():
        raise UnexaminableFileError(
            f"{path}: not a regular file (resolves to {real}), so it cannot be graded "
            "and cannot be reported clean"
        )
    if repo is not None:
        # Both sides resolved. A checkout reached through a symlinked prefix —
        # macOS `/var` -> `/private/var`, every agent worktree under a linked
        # home — otherwise puts every file it owns outside its own root, and an
        # unresolved root does not merely refuse: it concludes the listed path
        # is not in the repository either and skips the check entirely.
        # `is_relative_to`, not `str.startswith`: `/w/repo` is a string prefix
        # of `/w/repo-vendor/x.py`, which is where vendored trees sit.
        root = repo.resolve()
        if located_path(path).is_relative_to(root) and not real.is_relative_to(root):
            raise UnexaminableFileError(
                f"{path}: resolves to {real}, outside the repository at {root}, so it "
                "cannot be graded and cannot be reported clean"
            )
    size = real.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise UnexaminableFileError(
            f"{path}: {size} bytes exceeds the {MAX_SOURCE_BYTES}-byte grading limit "
            f"(resolves to {real}), so it cannot be graded and cannot be reported clean"
        )
    try:
        # `real`, not `path`: every check above validated the resolved target,
        # and reading the unresolved path re-follows the link. If it is rewritten
        # in between, the bytes graded are not the bytes checked and the leak,
        # the hang and the size cap all come back. The TypeScript gate reads its
        # resolved path already.
        return real.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UnexaminableFileError(
            f"{path}: this run could not read it, so it cannot be reported clean "
            f"({type(exc).__name__}: {exc})"
        ) from exc


def parse_python_source(source: str, path: Path) -> ast.Module:
    """``ast.parse``, with its non-syntax failures routed to the one channel.

    A NUL byte in a ``.py`` makes ``ast.parse`` raise ``ValueError``, which is
    not a ``SyntaxError`` — so every gate's ``except SyntaxError`` missed it and
    the run died with a traceback out of the middle of the walk. That exits
    non-zero, so it was never a false pass, but it printed a stack trace instead
    of naming the file, and it bypassed the `UnexaminableError` channel that is
    supposed to be the single place a gate decides what to do about a file it
    could not examine. ``SyntaxError`` still propagates: each gate already has
    an answer for it.
    """
    try:
        return ast.parse(source, filename=str(path))
    except ValueError as exc:
        raise UnexaminableFileError(
            f"{path}: this run could not parse it, so it cannot be reported clean "
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


#: The base every gate falls back to when its caller named none. Only this
#: value is subject to trunk detection — see `effective_base_ref`.
DEFAULT_BASE_REF = "main"

#: Exit code for `--emit-scope-json` when the scope could not be resolved. Not
#: 1: the caller has to tell "this run could not determine what to examine"
#: apart from "this run graded files and found violations", which is the whole
#: point of `UnexaminableError` existing.
EXIT_UNEXAMINABLE = 3

#: Trunk names to try when `DEFAULT_BASE_REF` names nothing in a repository.
#: `origin/HEAD` is consulted first; these are the fallbacks for a checkout
#: with no remote, which is what an agent worktree and a test fixture are.
_TRUNK_REF_CANDIDATES = ("master", "trunk", "develop")


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
        # A diff carries the *content* of every file it touches, so one latin-1
        # byte anywhere in the change made `text=True` raise `UnicodeDecodeError`
        # out of `subprocess.run` and take the whole run down with a traceback,
        # before any gate could say which file it was. Undecodable bytes survive
        # as surrogates here; the file that actually holds them still fails
        # loudly and by name in `read_source_text`, which is where that decision
        # belongs.
        errors="surrogateescape",
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit status {proc.returncode}"
        raise GitScopeError(f"`git {' '.join(command)}` failed in {repo}: {detail}")
    return proc.stdout


def _run_git(args: List[str], repo: Path) -> str:
    return _git(["diff", *args], repo)


def unborn_worktree(repo: Path, diff_scope: str) -> bool:
    """Whether this scope has no ref to diff against, because nothing is committed.

    `git diff HEAD` cannot resolve in a repository with no commits, so every
    ref-based query below has to answer without one. `git_changed_paths` always
    did; the other four did not, which is what 553 filed: discovery found the
    untracked files, printed `examined 1 file(s)`, and the next call died with
    "cannot determine what to examine".

    Only the worktree scope is covered. `--cached` resolves fine against an
    unborn HEAD, and a caller that named `--base` or `--since` asked for a ref
    that has to exist — substituting emptiness there would grade against a base
    nobody chose, which is the failure this file exists to prevent.

    Every caller returns its own empty value rather than sharing one, because
    what "empty" means differs: no diff text, no added paths, no counts. They
    are safe to return only because an unborn repository has every path
    untracked, and `DiffScope.touched_lines` grades an untracked file whole.
    """
    return diff_scope == WORKTREE and not git_has_head(repo)


def git_diff_cached(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> str:
    """The unified diff this scope describes, or empty where there is none to take.

    The unborn-HEAD guard mirrors `git_changed_paths`, which has had it all
    along — the asymmetry is what 553 filed. Discovery would find the new files
    (they are untracked), print `examined 1 file(s)`, and then this call would
    die on `git diff HEAD` with "cannot determine what to examine": a gate that
    announces it read a file and then refuses to grade it.

    Returning no diff is safe *here specifically*, and only because of what the
    caller does with it: in a repository with no commits every path is
    untracked, and `DiffScope.touched_lines` already grades an untracked file
    in its entirety rather than scoping to changed lines. The empty diff
    narrows nothing. That distinction is the whole of 546 — an empty diff that
    *does* narrow is the vacuous pass this file exists to prevent — so if a
    scope is ever added where an unborn HEAD does not imply untracked, it must
    not reach this branch.
    """
    if unborn_worktree(repo, diff_scope):
        return ""
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
    if unborn_worktree(repo, diff_scope):
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


def git_origin_head(repo: Path) -> Optional[str]:
    """The trunk ``origin/HEAD`` names, e.g. ``origin/master``, or None.

    The remote's own answer rather than a guess, so it is asked before the
    candidate names below.
    """
    proc = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo,
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def effective_base_ref(repo: Path, base_ref: str) -> str:
    """``base_ref``, or this repository's actual trunk when nobody named one.

    Every gate defaults to `DEFAULT_BASE_REF` because most workspaces use it,
    not because a caller asked for it. In a repository whose trunk is
    ``master`` — git's default before 2.28, and still what a bare ``git init``
    produces wherever ``init.defaultBranch`` is unset, including CI images —
    that default resolves to nothing: the run degrades to worktree-vs-HEAD and
    then refuses to call itself a pass, so *every* stage transition fails over
    a trunk name rather than over any code. Detecting the trunk is the fix; the
    degraded path stays for the case where there is genuinely no trunk to find.

    Only the default is substituted. A caller that passed ``--base`` gets that
    ref or a loud failure — silently grading against a different base than the
    one asked for is the same class of bug the scope work exists to close.
    """
    if base_ref != DEFAULT_BASE_REF or git_rev_exists(repo, base_ref):
        return base_ref
    candidates = [ref for ref in (git_origin_head(repo), *_TRUNK_REF_CANDIDATES) if ref]
    for candidate in candidates:
        if candidate != base_ref and git_rev_exists(repo, candidate):
            return candidate
    return base_ref


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

    When ``base_ref`` still names nothing after `effective_base_ref` has looked
    for the repository's real trunk, there is no branch diff to union in: the
    run degrades to ``HEAD``, says so in the scope line, and is marked
    ``degraded`` so `resolve_gate_scope` can refuse to call it a pass if the
    narrower scope leaves this gate with nothing to grade.
    """
    base_ref = effective_base_ref(repo, base_ref)
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


def all_line_numbers(path: Path, *, repo: Optional[Path]) -> Set[int]:
    """Every line of ``path``, as a touched-line set.

    What a file with no diff to scope against is graded on: an untracked file
    is new in its entirety, so every line in it is part of this change.
    """
    return set(range(1, len(read_source_text(path, repo=repo).splitlines()) + 1))


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
            return all_line_numbers(path, repo=self.repo)
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


#: `git diff --raw` spells a gitlink — a submodule pointer — with this mode.
GITLINK_MODE = "160000"


def git_gitlink_paths(repo: Path, diff_scope: str, base_ref: str) -> List[str]:
    """Submodule paths this diff moved.

    ``--name-only`` lists the gitlink like any other path, every gate's language
    filter drops it (it is a directory), and the run prints ``examined 0
    file(s)`` and exits 0 — a change nobody graded, reported as a clean gate.
    """
    if unborn_worktree(repo, diff_scope):
        return []
    out = _run_git([*_scope_args(diff_scope, base_ref), "--raw", "--"], repo)
    found: List[str] = []
    for line in out.splitlines():
        if not line.startswith(":"):
            continue
        head, _, paths = line.partition("\t")
        if not paths:
            continue
        fields = head.lstrip(":").split()
        if len(fields) < 2 or GITLINK_MODE not in fields[:2]:
            continue
        found.append(decode_git_path(paths.split("\t")[-1]))
    return sorted(set(found))


def announce_ungraded_submodules(label: str, repo: Path, scope: ResolvedScope) -> None:
    """Say out loud that a submodule bump went ungraded.

    A gate cannot grade another repository's contents, and failing on every
    submodule pointer move would block transitions in any workspace that uses
    them for reasons that have nothing to do with code quality. So the choice
    here is to report rather than to fail — but *silently* exiting 0 over an
    unexamined change is the one option this ticket exists to remove, so the
    run names them.
    """
    gitlinks = git_gitlink_paths(repo, scope.diff_scope, scope.base_ref)
    if gitlinks:
        print(
            f"{label}: not examined — {len(gitlinks)} submodule(s) changed "
            f"({', '.join(gitlinks)}); gate their own repository there"
        )


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
    if discovered and repo is not None and git_has_head(repo):
        announce_ungraded_submodules(label, repo, scope)
    additions: Dict[str, Set[int]] = {}
    untracked: FrozenSet[str] = frozenset()
    numstat = DiffNumstat({}, frozenset())
    if repo is not None and files:
        diff = git_diff_cached(repo, scope.diff_scope, scope.base_ref)
        additions = {
            path: {ln for ln, _ in items} for path, items in parse_staged_additions(diff).items()
        }
        untracked = (
            frozenset(git_untracked_paths(repo)) if scope.includes_untracked else frozenset()
        )
        numstat = git_diff_numstat(repo, scope.diff_scope, scope.base_ref)
        # Asked only of the files this gate grades: a path outside them is
        # never looked up, and a deleted path (no `+++ b/`, real counts) would
        # otherwise be marked graded-whole and then fail to read.
        graded_rels = frozenset(repo_relative_posix(f, repo) for f in files) - untracked
        numstat = numstat.with_suppressed(
            suppressed_diff_paths(diff, numstat, graded_rels)
            | (graded_rels & frozenset(git_added_paths(repo, scope.diff_scope, scope.base_ref)))
        )
    return GateRun(
        label=label,
        repo=repo,
        scope=scope,
        files=files,
        additions=additions,
        untracked=untracked,
        numstat=numstat,
    )


def diff_header_path(line: str) -> Optional[str]:
    """The relpath a ``+++ `` header names, or None for ``/dev/null``.

    The quoting wraps the *whole* operand, prefix included:
    ``+++ "b/src/pkg/b\\303\\244d.py"``. Matching on a literal ``+++ b/``
    therefore missed every non-ASCII path outright — the file got no entry in
    the additions map, so its touched-line set came back empty and every
    violation in it was filtered out of a passing run.
    """
    name = decode_git_path(line[4:])
    return name[2:] if name.startswith("b/") else None


def suppressed_diff_paths(
    diff: str, numstat: DiffNumstat, candidates: Iterable[str]
) -> FrozenSet[str]:
    """Candidate relpaths whose diff did not describe the change git counted.

    This is the *mechanism* behind the ``.gitattributes`` hole rather than one
    of its spellings, and the question has moved twice as the spellings ran out.
    ``-diff``/``binary`` is the one git labels for us, with ``-\\t-`` in
    ``--numstat``. A ``diff=<driver>`` printing nothing, and a ``filter=``
    cleaning a file to empty, report real counts and no hunk. Asking "did the
    diff emit a header for this path" closed those — and a driver that prints
    the three header lines and exits walked straight through it (576).

    So the test is now against the thing the gate actually depends on: git says
    N lines were added, and the parser found M. If M is short of N the diff did
    not describe the change, whatever it printed. That subsumes every earlier
    spelling — no hunk and no header are both M=0 — and catches a *partial*
    suppression that the header rule and a plain "found nothing" test would both
    pass.

    Measured before choosing strict comparison over ``M == 0``: across the last
    40 commits of this repository, 215 paths, the parsed count equalled the
    numstat count every time and differed never. A false positive here grades a
    file whole, which is the safe direction, but a noisy one — so it is worth
    knowing the two agree on healthy diffs rather than assuming it.

    A mode-only ``chmod`` reports ``0\\t0`` and legitimately has no hunk, so
    ``0 < 0`` keeps it out. A rename's ``old => new`` operand never matches a
    candidate relpath, so it stays out too (a pre-existing gap in the size
    checks, not one this widens).
    """
    parsed = parse_staged_additions(diff)
    return frozenset(
        path
        for path in candidates
        if len(parsed.get(path, ())) < numstat.counts.get(path, (0, 0))[0]
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
            current_file = diff_header_path(line)
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

    def with_suppressed(self, paths: FrozenSet[str]) -> DiffNumstat:
        """The same counts, with `suppressed_diff_paths` folded into ``undiffable``.

        Both arrive at the same place because they mean the same thing
        downstream: git changed this file and would not say where, so the whole
        file is the only honest touched-line set.
        """
        return DiffNumstat(self.counts, self.undiffable | paths)


def git_added_paths(repo: Path, diff_scope: str = STAGED, base_ref: str = "main") -> List[str]:
    """Relpaths this scope *adds*, which are new in their entirety.

    Same standing as an untracked file, and graded the same way. Normally this
    changes nothing — every line of an added file shows up as a `+` line anyway
    — which is exactly why it is safe, and why it is worth asking separately: a
    ``filter=`` whose clean step empties the blob makes git commit the file with
    no content, so ``--numstat`` reports ``0\\t0`` and the diff carries no hunk,
    while the file on disk (the one the gate actually reads) is full of code.
    That pair is indistinguishable from a mode-only ``chmod`` by counts alone,
    so counts alone cannot decide it. "It is new, so all of it is new" can.
    """
    if unborn_worktree(repo, diff_scope):
        # Every path is untracked, which the caller already treats as whole-file.
        return []
    out = _run_git(
        [*_scope_args(diff_scope, base_ref), "--name-only", "--diff-filter=A", "--"], repo
    )
    return decoded_git_paths(out)


def git_diff_numstat(
    repo: Path, diff_scope: str = STAGED, base_ref: str = "main"
) -> DiffNumstat:
    """relpath -> (added_lines, deleted_lines) for this diff, plus the undiffable ones.

    The counts drive "don't make it worse" checks (e.g. file-length caps) that
    should fire on net growth, not on any touch to an already-oversized file —
    otherwise a pure cleanup/shrink of a long file would itself get blocked.

    Unborn HEAD is empty for the same reason `git_diff_cached` is: nothing is
    committed, so there is no "already" to be worse than, and every file is
    untracked and graded whole. Absent counts read as `(0, 0)` — a new file is
    judged on its own size rather than on growth it cannot have.
    """
    if unborn_worktree(repo, diff_scope):
        return DiffNumstat({}, frozenset())
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


# --------------------------------------------------------------------------- #
# `--emit-scope-json` — the scope, resolved once, for a gate in another language
# --------------------------------------------------------------------------- #
#
# About 560 lines of `ts_organization_check.cjs` were a hand-port of this module:
# the error classes, git-path decoding, env scrubbing, ref validation, scope
# resolution, untracked discovery, submodule announcement and diff-suppression
# detection. None of it was TypeScript-specific, and three of one review's nine
# defects were the two copies drifting apart. The conformance table catches that
# drift after the fact, on the repo states it enumerates; it does not remove the
# surface, and every fix still had to be written twice (580).
#
# So the `.cjs` asks this instead. It already shells out to git repeatedly; one
# more subprocess buys it a single implementation of scope policy.
#
# The language-specific half stays with the caller and is passed *in*: which
# suffixes that gate grades, and which source root it confines discovery to.
# That keeps the count honest — it is computed after the caller's own filter,
# by the same code that computes it for the Python gates — without this module
# needing to know what a TypeScript file is.


def _suffix_selector(
    suffixes: FrozenSet[str], select_root: Optional[Path]
) -> "GateFileSelector":
    """Build the caller's file filter from flags rather than from knowledge here.

    `select_root` is applied only when the run *discovered* its own candidates,
    matching every gate's existing behaviour: an explicitly named file was
    chosen by the caller (lefthook passes staged paths) and is not second-
    guessed, while a path the scope turned up is confined to the source root so
    a stray match elsewhere in the repo is not graded.
    """

    def select(repo: Optional[Path], candidates: List[Path], discovered: bool) -> List[Path]:
        chosen = []
        for candidate in candidates:
            if candidate.suffix not in suffixes:
                continue
            if discovered and select_root is not None:
                # `located_path`, not `resolve()`: resolving the whole path
                # follows a symlinked source out of the tree, drops it from the
                # filter, and reports `examined 0` over a file the read guard
                # was supposed to refuse. That is the vacuous pass this module's
                # own docstring warns about, and using `resolve()` here
                # reintroduced it (caught by the ts-org symlink tests).
                try:
                    located_path(candidate).relative_to(select_root)
                except ValueError:
                    continue
            chosen.append(candidate)
        return chosen

    return select


def emit_scope_json(argv: Optional[Sequence[str]] = None) -> int:
    """Print one JSON object describing what a gate run should examine.

    Everything a gate needs before it can apply a single rule: the resolved
    scope, the files that survived the caller's filter, which of them git is not
    tracking, which of them git changed but would not diff, and the line numbers
    the change touched in each.

    Two things are deliberately *not* decided here, because they are the
    caller's: which suffixes to grade, and what to do about the result. The
    caller also prints `notices` itself rather than this process writing to the
    terminal — stdout is the JSON channel, so the human-facing lines
    `resolve_gate_scope` emits are captured and handed back to be printed in
    the caller's own order.

    An unresolvable scope exits `EXIT_UNEXAMINABLE` with the reason on the JSON,
    not a traceback: the caller has its own sentence for "cannot determine what
    to examine" and needs the message, not a Python stack.
    """
    parser = argparse.ArgumentParser(
        prog="precommit_git_diff.py --emit-scope-json",
        description="Resolve a gate run's scope and print it as JSON.",
    )
    parser.add_argument("--emit-scope-json", action="store_true", required=True)
    parser.add_argument("--repo", default=None)
    # No `choices=`: an unknown scope is rejected by `resolve_gate_scope`, which
    # owns that rule and whose message says why it matters. Validating it twice
    # is how the two copies start disagreeing.
    parser.add_argument("--scope", dest="diff_scope", default=STAGED)
    parser.add_argument("--base", dest="base_ref", default=DEFAULT_BASE_REF)
    parser.add_argument("--label", default="gate")
    parser.add_argument(
        "--suffix",
        action="append",
        default=[],
        help="File suffix this gate grades, with the dot (repeatable).",
    )
    parser.add_argument(
        "--select-root",
        default=None,
        help="Confine discovered candidates to this directory.",
    )
    parser.add_argument(
        "--select-root-candidate",
        action="append",
        default=[],
        help=(
            "Repo-relative directory to confine discovered candidates to, first "
            "one that exists wins (repeatable). Unlike --select-root this is "
            "resolved against the repository root *this* process derived, so a "
            "caller that does not know the root yet can still express its own "
            "source-root policy."
        ),
    )
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    # `git_repo_root()` when unset, exactly as every Python gate does it. A
    # caller that resolves its own root from `cwd` gets a different answer than
    # the Python gates whenever it is not standing at the top level, and the
    # containment guard is then measured against the wrong tree — skipped
    # entirely for a file outside that root (594).
    repo = Path(args.repo).resolve() if args.repo else git_repo_root()
    select_root = Path(args.select_root).resolve() if args.select_root else None
    if select_root is None and repo is not None:
        for candidate in args.select_root_candidate:
            if (repo / candidate).is_dir():
                select_root = (repo / candidate).resolve()
                break
        else:
            select_root = repo if args.select_root_candidate else None
    selector = _suffix_selector(frozenset(args.suffix), select_root)

    # `resolve_gate_scope` prints the examined line (and any submodule notice)
    # as it goes. Captured rather than suppressed: the caller still has to show
    # them, and re-deriving the wording on the other side would reintroduce
    # exactly the duplication this entry point exists to delete.
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            run = resolve_gate_scope(
                label=args.label,
                repo=repo,
                diff_scope=args.diff_scope,
                base_ref=args.base_ref,
                # Same reason as the selector above: `resolve()` here hands
                # the caller the symlink's *target*, so a link pointing out of
                # the repository arrives as an ordinary file and the caller's
                # read guard has nothing left to refuse. The link's own path is
                # what git spells and what must be graded.
                explicit_files=[located_path(Path(f)) for f in args.files],
                select=selector,
            )
    except UnexaminableError as exc:
        json.dump({"error": str(exc), "notices": captured.getvalue().splitlines()}, sys.stdout)
        sys.stdout.write("\n")
        return EXIT_UNEXAMINABLE

    json.dump(
        {
            "notices": captured.getvalue().splitlines(),
            "scope": {
                "diff_scope": run.scope.diff_scope,
                "base_ref": run.scope.base_ref,
                "description": run.scope.description,
                "degraded": run.scope.degraded,
                "includes_untracked": run.scope.includes_untracked,
            },
            # The root every check downstream must measure against — the
            # caller does not re-derive it.
            "repo_root": str(repo) if repo is not None else None,
            "select_root": str(select_root) if select_root is not None else None,
            "files": [str(path) for path in run.files],
            "untracked": sorted(run.untracked),
            # Graded whole: git changed them but produced no usable diff.
            "undiffable": sorted(run.numstat.undiffable),
            "additions": {rel: sorted(lines) for rel, lines in run.additions.items()},
            # (added, deleted) per relpath — the caller's "is this file growing?"
            "counts": {rel: list(pair) for rel, pair in run.numstat.counts.items()},
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    # Importable as a library (every gate does) and runnable as this one entry
    # point. Guarded on the flag so a future second mode has to be added
    # deliberately rather than by accident.
    if "--emit-scope-json" in sys.argv[1:]:
        raise SystemExit(emit_scope_json())
    print(
        "precommit_git_diff.py is a library; its only CLI mode is --emit-scope-json",
        file=sys.stderr,
    )
    raise SystemExit(2)
