"""Land a finished ticket: merge its branch into its target (lg-milestone-that-768).

The one step between "every stage passed" and "the next ticket can build on
this". It ran nowhere: `git_automation`'s publish chain had no caller on the
ticket path, so a ticket reached `done` with its branch local and unmerged,
and a dependent cut from the target found none of it.

The merge is made without a checkout — ``merge-tree`` for the result,
``commit-tree`` for the commit, ``update-ref`` to move the target — so it
works while the primary checkout and any number of worktrees hold other
branches, and the ref move is compare-and-swap so two landings racing on one
integration branch cannot overwrite each other.

Only integration branches are landed here. A ticket whose target is the base
branch is not merged locally: the base is what the primary checkout usually
has checked out, and moving it under a working tree leaves that tree showing
the landing in reverse. That leg is the publish chain's (771).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import (
    describe_signing,
    first_containing,
    is_ancestor,
    merge_preview,
    merge_without_checkout,
    rev,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.land_verification import land_verifier
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import (
    ensure_integration_branch,
    target_branch_name,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


class LandSkip(str, Enum):
    """Why an ok landing merged nothing."""

    #: The ticket never had a branch in the repository — nothing ran on it.
    NO_BRANCH = "no_branch"
    #: Every commit on the branch is already reachable from the target.
    ALREADY_LANDED = "already_landed"
    #: The target is the base branch; landing there is the publish chain's leg.
    BASE_TARGET = "base_target"
    #: The workspace has no git repository at its root — nothing ran in one.
    NO_REPOSITORY = "no_repository"


@dataclass(frozen=True)
class LandResult:
    ok: bool
    branch: str
    target: str
    landed_sha: str = ""
    detail: str = ""
    skipped: LandSkip | None = None
    conflicted_files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def conflicted(self) -> bool:
        return bool(self.conflicted_files)


def land_ticket(session: Session, ticket: Ticket, workspace: Workspace) -> LandResult:
    """Merge the ticket's branch into its target and record where it landed.

    Idempotent: a branch the target already contains merges nothing, and
    records the first commit on the target that contained it. A conflict is
    returned, never raised — the caller decides between the resolver and a
    block — and every other failure carries git's text so the ticket can be
    blocked with it.

    A land whose verification refuses is one of those failures: the merge
    commit is written and the target's ref never moves, so nothing here records
    anything and `landing` blocks the terminal stage with the verification's own
    words (lg-milestone-that-1238).
    """
    repo_root = resolve_workspace_root(workspace)
    branch = resolve_ticket_branch(ticket)
    base = resolve_orchestration_profile(workspace).git.base_branch
    target = target_branch_name(session, ticket, workspace)

    if not (repo_root / ".git").exists():
        # A run never starts in a workspace whose root is not a repository
        # (the executor refuses the dispatch), so a workflow finishing here
        # has no work in git to land. Skipped, and the event says so.
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.NO_REPOSITORY)
    if not rev(repo_root, f"refs/heads/{branch}"):
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.NO_BRANCH)
    if target == base:
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.BASE_TARGET)

    ensure_integration_branch(repo_root, target, base)

    failed = _refresh_branch(repo_root, branch, target)
    if failed is not None:
        return failed

    subject = f"Land {ticket.external_id or ticket.id[:8]}: {ticket.title}".strip()
    # The whole of the guard's wiring. Every line of its logic is in
    # `land_verification`; `merge_tips` calls it between the `commit-tree` and
    # the `update-ref`, so a refusal means this function never reaches
    # `_record` and `landed_sha` has nothing to be stamped from.
    verification = land_verifier(session, ticket, workspace)
    outcome = merge_without_checkout(
        repo_root, target=target, source=branch, subject=subject, verify=verification
    )
    if not outcome.ok:
        return LandResult(
            ok=False,
            branch=branch,
            target=target,
            detail=outcome.detail,
            conflicted_files=outcome.conflicted_files,
        )

    if outcome.already_contained:
        landed = _already_landed_at(repo_root, ticket, target, outcome.sha)
        if (ticket.landed_sha, ticket.landed_branch) != (landed, target):
            _record(session, ticket, landed, target)
        return LandResult(
            ok=True,
            branch=branch,
            target=target,
            landed_sha=landed,
            skipped=LandSkip.ALREADY_LANDED,
        )
    _record(session, ticket, outcome.sha, target)
    logger.info(
        "Landed %s (%s) on %s as %s (%s)",
        ticket.external_id,
        branch,
        target,
        outcome.sha[:12],
        describe_signing(outcome.signed),
    )
    return LandResult(
        ok=True,
        branch=branch,
        target=target,
        landed_sha=outcome.sha,
        # A workspace that configured no commands lands as it always did, and
        # the result says nobody checked it rather than letting that read as a
        # pass (AC20).
        detail=verification.note if verification else "",
    )


def landing_would_succeed(session: Session, ticket: Ticket, workspace: Workspace) -> bool:
    """Whether landing this ticket on its integration branch would now succeed.

    Read-only: the same preview `merge_tips` makes, with no commit and no ref
    move. Only an integration-branch landing answers True; the skips (no
    repository, no branch, a base target) are not landings this can vouch for,
    and a missing target is a landing that has not been set up.
    """
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return False
    target = target_branch_name(session, ticket, workspace)
    if target == resolve_orchestration_profile(workspace).git.base_branch:
        return False
    source_sha = rev(repo_root, f"refs/heads/{resolve_ticket_branch(ticket)}")
    target_sha = rev(repo_root, f"refs/heads/{target}")
    if not source_sha or not target_sha:
        return False
    return merge_preview(repo_root, target_sha, source_sha).ok


def _already_landed_at(repo_root: Path, ticket: Ticket, target: str, branch_sha: str) -> str:
    """Which commit an already-contained branch landed at (928).

    `landed_sha` is the *first* commit on `landed_branch` that contains the
    ticket's work, and that is a property of the git graph rather than of
    which code path observed it. So the answer is read from git, not from the
    tip `merge_tips` hands back for a branch it merged nothing for — that tip
    is the branch's own, and recording it is how 782 ended up naming two
    commits for one landing.

    A landing already on record is kept as it is (#578): re-reading it would
    be a second answer to a question already answered, and the guard is what
    stops a re-landing moving the sha. Otherwise git is asked, and its "could
    not say" falls back to the branch tip — the only thing left — having
    logged at warning inside `first_containing`, so a blind record never
    reads like a fast-forward landing.
    """
    if _landing_on_record(repo_root, ticket, target, branch_sha):
        return ticket.landed_sha
    return first_containing(repo_root, branch_sha, f"refs/heads/{target}") or branch_sha


def _landing_on_record(repo_root: Path, ticket: Ticket, target: str, branch_sha: str) -> bool:
    """Whether the ticket already records the commit that landed this branch.

    `landed_sha` is the commit on the target that first contained the branch:
    the merge commit when a landing merged it. Re-landing an already-contained
    branch used to overwrite that with the branch tip, so the same ticket
    named two different commits depending on how often it was landed (928).
    """
    return (
        bool(ticket.landed_sha)
        and ticket.landed_branch == target
        and is_ancestor(repo_root, branch_sha, ticket.landed_sha)
        and is_ancestor(repo_root, ticket.landed_sha, f"refs/heads/{target}")
    )


def _refresh_branch(repo_root: Path, branch: str, target: str) -> LandResult | None:
    """Bring `branch` up to `target` before it is merged back into it.

    Note the inversion against the land merge below: the target is merged
    *into* the ticket branch, with the same no-checkout plumbing, so a conflict
    with a target that moved after the gate passed is materialised on the
    branch the resolver is told to work on, and the tree that lands is the tree
    the ticket's own gate ran against (lg-milestone-that-801).

    `None` when the branch is ready to land; a failing `LandResult` otherwise —
    carrying the conflicted files when it was a conflict, so the caller's
    resolver leg takes it, and an empty tuple with git's words when it was
    anything else, so the caller blocks on it instead.
    """
    if is_ancestor(repo_root, branch, target):
        # Nothing to land is nothing to refresh. A branch the target already
        # contains must not take a merge commit here: that would leave it
        # un-landed again and the landing no longer idempotent.
        return None
    refresh = merge_without_checkout(
        repo_root,
        target=branch,
        source=target,
        subject=f"Refresh {branch} from {target} before landing",
    )
    if not refresh.ok:
        return LandResult(
            ok=False,
            branch=branch,
            target=target,
            detail=f"refreshing {branch} from {target} failed: {refresh.detail}",
            conflicted_files=refresh.conflicted_files,
        )
    if refresh.already_contained:
        return None
    stale = _align_worktrees(repo_root, branch)
    if stale:
        return LandResult(ok=False, branch=branch, target=target, detail=stale)
    return None


def _align_worktrees(repo_root: Path, branch: str) -> str:
    """Bring every worktree holding `branch` into line with the ref just moved.

    `update-ref` moves a ref that is checked out in a worktree without refusing
    or warning, which leaves that worktree staged-dirty in reverse — the files
    the refresh brought in read as staged deletions, and the orchestrator
    commits the whole working tree when a stage finishes. `read-tree -m -u`
    applies the move, keeps unrelated local work, and refuses rather than
    overwriting an untracked file at an incoming path.

    Returns git's own words when it could not be done, "" when it was. git is
    asked which worktrees hold the branch rather than the `Worktree` table,
    because git also sees the ones the table has lost track of.
    """
    listed = _git(repo_root, "worktree", "list", "--porcelain", "-z")
    if listed.returncode != 0:
        return f"refreshing {branch} could not list the worktrees holding it: {_words(listed)}"
    for path, attributes in _worktrees(listed.stdout):
        if f"branch refs/heads/{branch}" not in attributes:
            continue
        if any(attribute.split(" ", 1)[0] == "prunable" for attribute in attributes):
            # git keeps listing a worktree whose directory is gone, with its
            # branch. Running in it raises before git is reached, and pruning
            # it is not this step's to do, so it is left to `worktree prune`.
            continue
        try:
            done = _git(Path(path), "read-tree", "-m", "-u", "HEAD")
        except OSError as exc:
            return _stale(branch, path, str(exc))
        if done.returncode != 0:
            return _stale(branch, path, _words(done))
    return ""


def _worktrees(listed: str) -> Iterator[tuple[str, list[str]]]:
    """Each `worktree list --porcelain -z` record, as its path and attributes.

    `-z`, not newlines: `--porcelain` emits a worktree's path raw and escapes
    nothing, so a path containing a newline forges whatever attribute line it
    likes — including the `branch` line that decides which working tree is
    written to. Under `-z` every attribute is one NUL-terminated field and a
    record ends on an empty one, so such a newline stays inside the path.
    """
    path = ""
    attributes: list[str] = []
    for entry in listed.split("\0"):
        if not entry:
            if path:
                yield path, attributes
            path, attributes = "", []
        elif entry.startswith("worktree "):
            path = entry.removeprefix("worktree ")
        else:
            attributes.append(entry)
    if path:
        yield path, attributes


def _stale(branch: str, path: str, words: str) -> str:
    return (
        f"refreshing {branch} left the worktree at {path} stale "
        f"and it could not be brought into line: {words}"
    )


def _git(cwd: Path, *args: str):
    return run_git(list(args), cwd=str(cwd), check=False, capture_output=True, text=True)


def _words(done) -> str:
    return (done.stderr or done.stdout or "git failed with no output").strip()


def _record(session: Session, ticket: Ticket, sha: str, target: str) -> None:
    ticket.landed_sha = sha
    ticket.landed_branch = target
    session.add(ticket)
    session.commit()
