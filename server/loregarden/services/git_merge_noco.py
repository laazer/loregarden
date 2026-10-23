"""Merge one branch into another with no working tree involved.

``merge-tree --write-tree`` computes the result, ``commit-tree`` makes the
merge commit, and a compare-and-swap ``update-ref`` moves the target. Nothing
is checked out, so it works while the primary checkout and any number of
worktrees hold other branches, and two callers racing on one target cannot
overwrite each other — the second sees the ref move and reports it.

Used to land a ticket on its integration branch (768) and to bring an
integration branch up to the base branch before a ticket is cut from it
(770). Requires git ≥ 2.38 for ``--write-tree``; the host runs 2.51.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from loregarden.services.git_subprocess import run_git

logger = logging.getLogger(__name__)


class VerifyVerdict(Protocol):
    """What a `merge_tips` verifier answers about a candidate merge commit.

    A structural type rather than an import of the verifier's own class: this
    module is git plumbing and a leaf, and the land verification that supplies
    the real verdict reaches the capacity ledger and the gate conventions.
    """

    @property
    def ok(self) -> bool: ...

    @property
    def detail(self) -> str:
        """Why it was refused, in the words the operator will be blocked with."""
        ...


#: Called with the candidate merge commit between `commit-tree` and `update-ref`.
Verify = Callable[[str], VerifyVerdict]


@dataclass(frozen=True)
class MergeOutcome:
    ok: bool
    #: The merge commit, or the source tip when nothing needed merging.
    sha: str = ""
    #: True when the source was already contained and nothing was written.
    already_contained: bool = False
    detail: str = ""
    conflicted_files: tuple[str, ...] = field(default_factory=tuple)
    #: Whether the commit *this call wrote* carries a signature. ``None`` means
    #: no commit was written — a failure, an ``already_contained`` short-circuit,
    #: or a preview. Tri-state on purpose: collapsing "nothing was written" into
    #: "what was written is unsigned" is the same answer for two different
    #: states, which is what let eight unsigned landings read as successes
    #: (lg-milestone-that-782).
    signed: bool | None = None

    @property
    def conflicted(self) -> bool:
        return bool(self.conflicted_files)


@dataclass(frozen=True)
class BranchTips:
    """Where some local branches pointed, read in one `for-each-ref` at one moment.

    Consumed by the step that moves one of them (`merge_tips`), never kept past
    it: the compare-and-swap that step ends with is what makes a reading taken
    a moment earlier safe to act on — a ref that moved since is refused, not
    overwritten (lg-build-verification-847).
    """

    shas: Mapping[str, str]
    #: git's own words when it could not list the refs at all. Every branch then
    #: reads as missing, which is what `rev` answered on the same failure.
    error: str = ""

    def of(self, branch: str) -> str:
        """The commit `branch` names, or "" when it does not exist."""
        return self.shas.get(branch, "")


def read_branch_tips(repo_root: Path, *branches: str) -> BranchTips:
    """The tips of `branches`, in one git call instead of one per branch.

    `for-each-ref` matches a pattern by whole path components, so
    `refs/heads/integration` would also list `refs/heads/integration/x`; only
    exact names are kept. A branch that does not exist is simply absent.
    """
    wanted = {f"refs/heads/{branch}": branch for branch in branches}
    result = _git(repo_root, "for-each-ref", "--format=%(refname) %(objectname)", *wanted)
    if result.returncode != 0:
        return BranchTips(
            shas={}, error=(result.stderr or result.stdout or "git for-each-ref failed").strip()
        )
    shas: dict[str, str] = {}
    for line in result.stdout.splitlines():
        refname, _, sha = line.rpartition(" ")
        if refname in wanted:
            shas[wanted[refname]] = sha
    return BranchTips(shas=shas)


def _git(repo_root: Path, *args: str):
    return run_git(list(args), cwd=str(repo_root), check=False, capture_output=True, text=True)


def rev(repo_root: Path, ref: str) -> str:
    """The commit ``ref`` names, or "" when it names nothing."""
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else ""


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    return _git(repo_root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def first_containing(repo_root: Path, commit: str, ref: str) -> str:
    """The first commit on ``ref`` that contains ``commit``, or "" when git cannot say.

    The git question behind what `landed_sha` means (928): the oldest commit
    reachable from ``ref`` that has ``commit`` as an ancestor. That is the
    merge commit when a merge brought the work in, and ``commit`` itself when
    ``ref`` fast-forwarded to it or ``ref`` *is* it.

    `rev-list --ancestry-path commit..ref` lists exactly the commits on a path
    from ``commit`` to ``ref``, oldest last. An empty list means one of three
    things, and they are not the same answer: ``commit`` is the tip of ``ref``,
    ``ref`` reached it with no descendant on the path, or ``ref`` does not
    contain it at all. The first two are answers; the third is a failure, and
    returning ``commit`` for it would record a landing at the branch tip — the
    defect this function exists to stop — with nothing saying so. So every
    route to "" logs at warning, and ``commit`` is never a fallback.
    """
    if not commit:
        # An empty left side makes `..ref` a valid range meaning `HEAD..ref`,
        # so git would answer plausibly for a commit nobody named.
        logger.warning("cannot say which commit on %s first contained an unnamed commit", ref)
        return ""
    listed = _git(repo_root, "rev-list", "--ancestry-path", f"{commit}..{ref}")
    if listed.returncode != 0:
        logger.warning(
            "git rev-list could not say which commit on %s first contained %s: %s",
            ref,
            commit,
            (listed.stderr or listed.stdout or "git rev-list failed").strip(),
        )
        return ""
    lines = listed.stdout.split()
    if lines:
        return lines[-1]
    if not is_ancestor(repo_root, commit, ref):
        logger.warning("%s does not contain %s, so no commit on it first contained it", ref, commit)
        return ""
    return rev(repo_root, commit)


def merge_base(repo_root: Path, one: str, other: str) -> str:
    """The best common ancestor, or "" for unrelated histories. Raises when git fails."""
    found = _git(repo_root, "merge-base", one, other)
    if found.returncode == 1:
        return ""
    if found.returncode != 0:
        raise RuntimeError(f"git merge-base failed: {(found.stderr or found.stdout).strip()}")
    return found.stdout.strip()


def signs_commits(repo_root: Path) -> bool:
    """Whether this repository signs its commits (`commit.gpgsign`).

    `git commit` reads that setting; `git commit-tree` does not — it signs
    only with an explicit `-S`. The first eight landings this module made
    were unsigned in a repository whose branch rules require signatures, and
    the integration branch could not be merged (lg-milestone-that-782).
    """
    result = _git(repo_root, "config", "--get", "--type=bool", "commit.gpgsign")
    return result.returncode == 0 and result.stdout.strip() == "true"


def describe_signing(signed: bool | None) -> str:
    """How ``MergeOutcome.signed`` reads in a log line about a written commit.

    Branches on the three states rather than on truthiness: ``None`` is not
    "unsigned", and a caller that read it as one would describe a landing it
    did not make.
    """
    if signed is True:
        return "signed"
    if signed is False:
        return "unsigned"
    return "no commit written"


def _merge_tree(repo_root: Path, target: str, source: str) -> tuple[str, tuple[str, ...], str]:
    """The merged tree, the conflicted paths (empty when clean), and git's own words.

    Exit 0: the tree on the first line. Exit 1: the tree, then the conflicted
    paths up to a blank line. Higher: git could not merge at all — reported as
    a failure with no tree, never as "no conflicts".
    """
    result = _git(repo_root, "merge-tree", "--write-tree", "--name-only", target, source)
    lines = result.stdout.splitlines()
    if result.returncode == 0:
        return lines[0].strip() if lines else "", (), ""
    if result.returncode == 1 and lines:
        files: list[str] = []
        for line in lines[1:]:
            if not line.strip():
                break
            files.append(line.strip())
        return lines[0].strip(), tuple(files), result.stdout.strip()
    return "", (), (result.stderr or result.stdout or "git merge-tree failed").strip()


def squash_onto(repo_root: Path, base_sha: str, tip_sha: str, *, message: str) -> MergeOutcome:
    """One commit on ``base_sha`` holding ``tip_sha`` merged into it. No ref moves.

    What a tree publishes (958): the integration branch's net change as a single
    commit whose only parent is the base tip, signed where the repository signs.
    The branch's own history — which can carry unsigned commits a ticket branch
    brought back from before 782 — never reaches the base. The tree is the
    merge of the two tips, not the branch's own tree, so a branch behind the
    base does not revert what the base gained since; a conflict is refused, for
    the integration sweep's merge turn to resolve on the branch first.
    """
    tree, conflicts, words = _merge_tree(repo_root, base_sha, tip_sha)
    if conflicts:
        return MergeOutcome(ok=False, detail=words, conflicted_files=conflicts)
    if not tree:
        return MergeOutcome(ok=False, detail=words)
    signing = signs_commits(repo_root)
    sign = ["-S"] if signing else []
    committed = _git(repo_root, "commit-tree", tree, *sign, "-p", base_sha, "-m", message)
    if committed.returncode != 0:
        detail = (committed.stderr or committed.stdout or "git commit-tree failed").strip()
        return MergeOutcome(ok=False, detail=detail)
    return MergeOutcome(ok=True, sha=committed.stdout.strip(), signed=signing)


def merge_preview(repo_root: Path, target_sha: str, source_sha: str) -> MergeOutcome:
    """Whether ``source_sha`` would merge into ``target_sha``, writing nothing.

    The read-only half of `merge_tips`: same ancestry shortcut, same
    `merge-tree`, no commit and no ref update. ``ok`` with ``already_contained``
    means nothing to merge; ``ok`` alone means a clean merge is waiting.
    """
    if source_sha == target_sha or is_ancestor(repo_root, source_sha, target_sha):
        return MergeOutcome(ok=True, sha=source_sha, already_contained=True)
    tree, conflicts, words = _merge_tree(repo_root, target_sha, source_sha)
    if conflicts:
        return MergeOutcome(ok=False, detail=words, conflicted_files=conflicts)
    if not tree:
        return MergeOutcome(ok=False, detail=words)
    return MergeOutcome(ok=True)


def adds_nothing(repo_root: Path, base_sha: str, branch_sha: str) -> bool:
    """Whether merging ``branch_sha`` into ``base_sha`` would leave the base unchanged.

    True when everything the branch adds is already in the base by any route:
    ancestry, or a squash merge whose commit the branch never sees. Raises when
    git cannot compute the merge at all, which is not an answer either way.
    """
    if branch_sha == base_sha or is_ancestor(repo_root, branch_sha, base_sha):
        return True
    tree, conflicts, words = _merge_tree(repo_root, base_sha, branch_sha)
    if conflicts:
        return False
    if not tree:
        raise RuntimeError(f"git merge-tree failed for {branch_sha[:12]}: {words}")
    return tree == _tree_of(repo_root, base_sha)


#: How many base commits `superseding_commit` tries before giving up. Each try
#: is one `merge-tree`; past this the conflict is reported as it always was.
SUPERSEDE_SCAN_LIMIT = 200


def superseding_commit(repo_root: Path, branch_sha: str, base_sha: str) -> str:
    """A commit on ``base_sha``'s history that already holds everything
    ``branch_sha`` adds, or "" when there is none.

    A branch whose work reached the base through a squash merge shares no
    commit with it, so ancestry and `git cherry` both miss it the moment the
    squash differs by a line, and a later base edit to the same files then
    conflicts (lg-durable-remote-335: e93c2c19, squash-merged as 71eb856b,
    then #570 edited the test it added). The question asked instead is the
    one that matters: is there a base commit that merging the branch into
    would leave unchanged? Then nothing on the branch is missing from the
    base, whatever its commits look like.

    Only base commits the branch lacks and that touch a path the branch
    changed can answer yes, so those are the ones tried, oldest first.
    """
    merge_base = _git(repo_root, "merge-base", base_sha, branch_sha)
    if merge_base.returncode == 1:
        return ""  # unrelated histories: nothing shared, so nothing superseded
    if merge_base.returncode != 0:
        raise RuntimeError(
            f"git merge-base failed: {(merge_base.stderr or merge_base.stdout).strip()}"
        )
    changed = _git(
        repo_root, "diff", "--name-only", "--no-renames", merge_base.stdout.strip(), branch_sha
    )
    if changed.returncode != 0:
        raise RuntimeError(f"git diff failed: {(changed.stderr or changed.stdout).strip()}")
    paths = [line for line in changed.stdout.splitlines() if line]
    if not paths:
        return ""
    candidates = _git(
        repo_root,
        "rev-list",
        f"--max-count={SUPERSEDE_SCAN_LIMIT}",
        base_sha,
        f"^{branch_sha}",
        "--",
        *paths,
    )
    if candidates.returncode != 0:
        raise RuntimeError(
            f"git rev-list failed: {(candidates.stderr or candidates.stdout).strip()}"
        )
    for candidate in reversed(candidates.stdout.split()):
        tree, conflicts, _ = _merge_tree(repo_root, candidate, branch_sha)
        if tree and not conflicts and tree == _tree_of(repo_root, candidate):
            return candidate
    return ""


@dataclass(frozen=True)
class SquashTwin:
    """A commit on a branch and one on its target holding the same tree,
    neither an ancestor of the other side."""

    on_branch: str
    on_target: str


def squash_twin(repo_root: Path, branch_sha: str, target_sha: str) -> SquashTwin | None:
    """The newest branch commit the target lacks whose exact tree is also a
    target commit the branch lacks, or None when there is none.

    That pair is two copies of one snapshot with no shared history: what a
    branch holds after merging a squash of its target (`publish_tree` used to
    push one under the target's own name) instead of the target itself. Git
    cannot see they match, so every later merge of the target compares from
    an older base and conflicts in files both sides already agree on.
    """
    on_target = _trees(repo_root, target_sha, branch_sha)
    if not on_target:
        return None
    newest_by_tree: dict[str, str] = {}
    for sha, tree in on_target:
        newest_by_tree.setdefault(tree, sha)
    for sha, tree in _trees(repo_root, branch_sha, target_sha):
        twin = newest_by_tree.get(tree)
        if twin:
            return SquashTwin(on_branch=sha, on_target=twin)
    return None


def _trees(repo_root: Path, tip: str, exclude: str) -> list[tuple[str, str]]:
    """(commit, tree) for each commit on ``tip`` and not on ``exclude``, newest first."""
    listed = _git(repo_root, "log", "--format=%H %T", tip, f"^{exclude}")
    if listed.returncode != 0:
        raise RuntimeError(f"git log failed: {(listed.stderr or listed.stdout).strip()}")
    pairs: list[tuple[str, str]] = []
    for line in listed.stdout.splitlines():
        sha, _, tree = line.partition(" ")
        pairs.append((sha, tree))
    return pairs


def record_merged(
    repo_root: Path, *, branch: str, branch_sha: str, merged_sha: str, subject: str
) -> MergeOutcome:
    """Record ``merged_sha`` as merged into ``branch`` without changing a file.

    A merge commit whose tree is ``branch_sha``'s own (`git merge -s ours`),
    written with no checkout and moved by compare-and-swap. Only for a commit
    the branch already holds the content of; it is history, not a merge.
    """
    signing = signs_commits(repo_root)
    sign = ["-S"] if signing else []
    tree = _tree_of(repo_root, branch_sha)
    committed = _git(
        repo_root, "commit-tree", tree, *sign, "-p", branch_sha, "-p", merged_sha, "-m", subject
    )
    if committed.returncode != 0:
        detail = (committed.stderr or committed.stdout or "git commit-tree failed").strip()
        return MergeOutcome(ok=False, detail=detail)
    merge_sha = committed.stdout.strip()
    moved = _git(repo_root, "update-ref", f"refs/heads/{branch}", merge_sha, branch_sha)
    if moved.returncode != 0:
        detail = (moved.stderr or moved.stdout or "git update-ref refused").strip()
        return MergeOutcome(ok=False, detail=detail)
    return MergeOutcome(ok=True, sha=merge_sha, signed=signing)


def _tree_of(repo_root: Path, commit: str) -> str:
    result = _git(repo_root, "rev-parse", f"{commit}^{{tree}}")
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed for {commit}: {result.stderr.strip()}")
    return result.stdout.strip()


def merge_without_checkout(
    repo_root: Path,
    *,
    target: str,
    source: str,
    subject: str,
    verify: Verify | None = None,
) -> MergeOutcome:
    """Merge ``source`` into ``target`` (both local branch names) and move ``target``."""
    return merge_tips(
        repo_root,
        read_branch_tips(repo_root, target, source),
        target=target,
        source=source,
        subject=subject,
        verify=verify,
    )


def merge_tips(
    repo_root: Path,
    tips: BranchTips,
    *,
    target: str,
    source: str,
    subject: str,
    verify: Verify | None = None,
) -> MergeOutcome:
    """`merge_without_checkout` from tips the caller has just read.

    For a caller that already read both branches to decide whether to merge at
    all, so the merge does not ask git for them again. The final `update-ref`
    compares against ``tips``' target, so a target that moved after the reading
    is reported rather than overwritten.

    ``verify`` is called with the candidate merge commit, between the
    `commit-tree` that writes it and the compare-and-swap `update-ref` that
    publishes it, and a non-ok verdict refuses the merge. That gap is the whole
    point of the keyword: "do not publish a tree that fails" then holds by
    construction rather than by ordering discipline somewhere further out —
    the ref never moves, so no published branch ever holds the refused tree and
    nothing downstream has a window to stamp it. The candidate exists as a git
    object, so a verifier can cut a worktree at it.

    It is not consulted where there is nothing to publish: a source the target
    already contains writes no commit, and a conflict never reaches one.
    Omitted, this behaves exactly as it did before the keyword existed, which is
    what keeps `advance_tips`, `squash_onto` and `publish_tree` out of it
    (lg-milestone-that-1238 AC5).
    """
    source_sha = tips.of(source)
    target_sha = tips.of(target)
    if not source_sha:
        return MergeOutcome(ok=False, detail=tips.error or f"branch {source!r} does not exist")
    if not target_sha:
        return MergeOutcome(ok=False, detail=tips.error or f"branch {target!r} does not exist")
    # A commit is its own ancestor; equal tips need no `merge-base` to say so.
    if source_sha == target_sha or is_ancestor(repo_root, source_sha, target_sha):
        return MergeOutcome(ok=True, sha=source_sha, already_contained=True)

    tree, conflicts, words = _merge_tree(repo_root, target, source)
    if conflicts:
        return MergeOutcome(ok=False, detail=words, conflicted_files=conflicts)
    if not tree:
        return MergeOutcome(ok=False, detail=words)

    # One reading of `commit.gpgsign` decides the flag git gets and the answer
    # the caller is given; re-reading the commit back to check would be a second
    # git call on every landing, and would answer `N` on any checkout without
    # `gpg.ssh.allowedSignersFile`.
    signing = signs_commits(repo_root)
    sign = ["-S"] if signing else []
    committed = _git(
        repo_root, "commit-tree", tree, *sign, "-p", target_sha, "-p", source_sha, "-m", subject
    )
    if committed.returncode != 0:
        detail = (committed.stderr or committed.stdout or "git commit-tree failed").strip()
        return MergeOutcome(ok=False, detail=detail)
    merge_sha = committed.stdout.strip()

    if verify is not None:
        verdict = verify(merge_sha)
        if not verdict.ok:
            # The candidate stays an unreferenced object for git to garbage
            # collect. Nothing points at it, so there is nothing to undo.
            return MergeOutcome(ok=False, detail=verdict.detail)

    moved = _git(repo_root, "update-ref", f"refs/heads/{target}", merge_sha, target_sha)
    if moved.returncode != 0:
        # The old value did not match: something else moved the target since
        # we read it. Nothing was written; the caller re-reads and retries.
        detail = (moved.stderr or moved.stdout or "git update-ref refused").strip()
        return MergeOutcome(ok=False, detail=detail)
    # Only here, past the ref move: before it, no landing exists to describe.
    return MergeOutcome(ok=True, sha=merge_sha, signed=signing)


def advance_tips(
    repo_root: Path, tips: BranchTips, *, target: str, source: str, subject: str
) -> MergeOutcome:
    """`merge_tips`, except a target ``source`` already contains is fast-forwarded.

    For moving a branch onto work built on top of it, where a merge commit
    would only record that nothing diverged. Compare-and-swap like
    `merge_tips`: a target that moved after the reading is refused.
    """
    source_sha = tips.of(source)
    target_sha = tips.of(target)
    if not (source_sha and target_sha) or not is_ancestor(repo_root, target_sha, source_sha):
        return merge_tips(repo_root, tips, target=target, source=source, subject=subject)
    if source_sha == target_sha:
        return MergeOutcome(ok=True, sha=source_sha, already_contained=True)
    moved = _git(repo_root, "update-ref", f"refs/heads/{target}", source_sha, target_sha)
    if moved.returncode != 0:
        detail = (moved.stderr or moved.stdout or "git update-ref refused").strip()
        return MergeOutcome(ok=False, detail=detail)
    return MergeOutcome(ok=True, sha=source_sha)
