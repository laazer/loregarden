"""Retiring a ticket's worktree, on completion and after a crash.

Worktrees are cheap to create and easy to leak: the queue has leaked both slots
and directories before, and a server restart mid-run leaves a row saying
"active" for a tree nothing is working in. Two entry points close that:
`release_ticket_worktree` when a ticket reaches a terminal state, and
`reconcile_worktrees` at startup for everything the last process left behind.

Uncommitted work is never destroyed. Removing a checkout is only safe because
the branch's commits outlive it in the shared object store, and that argument
has three premises the destructive step now checks instead of assuming: the tree
holds no changes (tracked *or* untracked), HEAD reaches no commit that no ref
reaches, and the branch holds commits its base does not. The first and the last
ask whether anything *survives* removing the checkout; the middle one asks the
other question, of HEAD, because an interrupted rebase or a `git bisect` leaves
a commit no branch, tag or remote ref can reach while both branch-shaped
premises still hold. The external-harness path never commits at stage
transitions, so a ticket driven that way reaches its terminal state with the
first and last premises both false — an
observed case had 1164 uncommitted lines across seven files and an empty
`git log base..HEAD`. Any premise failing keeps the tree on disk, with a log
line naming which one.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    BaxterChatSession,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def _is_dirty(path: Path) -> bool:
    """True when the tree holds changes no commit has captured.

    `--untracked-files=all` is explicit rather than implied: untracked files are
    the majority of what an agent produces before its first commit (three of the
    seven files in the observed near-miss), and `status.showUntrackedFiles=no` in
    a repo's config would otherwise turn this guard into a rubber stamp.
    """
    status = run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        # An unreadable tree is not a tree we should be deleting.
        return True
    return bool(status.stdout.strip())


def _base_ref(path: Path, parent_branch: str) -> str | None:
    """The ref the ticket branch was cut from, as this checkout can name it.

    The remote-tracking ref is preferred: it is what a later PR would be opened
    against, so it is the ref that decides whether a commit is worth preserving.
    The local branch is the fallback for a repository with no remote.
    """
    for ref in (f"origin/{parent_branch}", parent_branch):
        probe = run_git(
            ["rev-parse", "--verify", "--quiet", ref],
            cwd=str(path),
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return ref
    return None


def _has_preserved_commits(path: Path, worktree: Worktree) -> bool:
    """True when the branch holds commits its base does not.

    Those commits are the entirety of what survives removing the checkout. The
    question is asked of the *branch*, not of the tree's HEAD, because a branch
    ref is what keeps commits reachable once the worktree is gone — work
    committed on a detached HEAD counts for nothing here, and should not.

    A base that cannot be resolved, or a `rev-list` that fails, answers False:
    an unprovable premise is not a satisfied one.
    """
    base = _base_ref(path, worktree.parent_branch)
    if base is None:
        return False
    counted = run_git(
        ["rev-list", "--count", f"{base}..{worktree.branch or 'HEAD'}"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    if counted.returncode != 0:
        return False
    total = counted.stdout.strip()
    return total.isdigit() and int(total) > 0


def _has_stranded_commits(path: Path) -> bool:
    """True when HEAD reaches a commit that no ref reaches.

    The counterpart of :func:`_has_preserved_commits`, and a different question:
    that one asks whether anything survives removing the checkout, which is a
    property of the *branch*. This one asks whether anything dies, which is a
    property of HEAD measured against every ref there is. Both must hold.

    `HEAD --not --branches --tags --remotes` is the only spelling that answers
    it. `--not --all` includes HEAD itself and so answers 0 for the very shape
    this is guarding against; dropping any of the three ref classes over-refuses
    a commit something still reaches. A remote-tracking ref counts as a ref:
    a commit reachable only from `origin/x` is published and genuinely outlives
    the checkout.

    Fails closed, like the premises beside it. A non-zero exit (an unborn HEAD
    answers 128, so does a ref pointing at a missing object) or a count that is
    not a number keeps the checkout: an unprovable premise is not a satisfied
    one.
    """
    counted = run_git(
        ["rev-list", "--count", "HEAD", "--not", "--branches", "--tags", "--remotes"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    if counted.returncode != 0:
        return True
    total = counted.stdout.strip()
    if not total.isdigit():
        return True
    return int(total) > 0


def _service_for(session: Session, workspace_id: str) -> WorktreeService | None:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        return None
    return WorktreeService(session, repo_path=str(resolve_workspace_root(workspace)))


class KeepReason(StrEnum):
    """Why a finished worktree's checkout stays on disk."""

    UNCOMMITTED = "uncommitted changes"
    STRANDED_HEAD = "a commit no branch or tag reaches"
    NOTHING_COMMITTED = "no commits beyond its base"


class RemovalFailure(StrEnum):
    """Why a checkout every premise cleared is nonetheless still on disk.

    Separate from :class:`KeepReason`, which is a closed vocabulary of
    *premises*: nothing here is a premise that failed, and `_kept_summary`
    groups by premise. It exists so that "the removal was attempted and did not
    work" cannot be answered with the same value as "the removal happened".
    `WorktreeService.cleanup_worktree` returns False for a path outside the
    worktree base and for any exception out of `worktree remove`/`rmtree`, and
    a caller told None in that case would drop the row that owns a checkout
    still sitting on disk.
    """

    CLEANUP_FAILED = "its checkout could not be removed"


#: What `retire_worktree` answers when the checkout is still there: the premise
#: that refused, or the removal having failed. `None` means, and only means,
#: that the checkout is gone.
RetirementRefusal = KeepReason | RemovalFailure


def _keep_reason(worktree: Worktree) -> KeepReason | None:
    """The premise that fails for removing this checkout, or None when all hold.

    Ordered by what the operator can act on. Uncommitted work is destroyed
    outright, so it is said first; a stranded commit survives in the object
    store and only loses its last reference, and its reason names the recovery;
    an empty branch is last, because when a tree also holds one of the other
    two, its emptiness is a consequence rather than the cause.
    """
    path = Path(worktree.worktree_path) if worktree.worktree_path else None
    if path is None or not path.is_dir():
        return None
    if _is_dirty(path):
        return KeepReason.UNCOMMITTED
    if _has_stranded_commits(path):
        return KeepReason.STRANDED_HEAD
    if not _has_preserved_commits(path, worktree):
        return KeepReason.NOTHING_COMMITTED
    return None


def _reason_detail(reason: KeepReason, worktree: Worktree) -> str:
    """What this refusal is about, for one tree, in the words it is reported in.

    One home for the per-reason wording, read by both `retire_worktree`'s
    warning and `_kept_summary`'s grouped lines. A member with no detail of its
    own repeats its own value, never a neighbour's sentence.
    """
    if reason is KeepReason.UNCOMMITTED:
        return "the changes exist in this tree and nowhere else"
    if reason is KeepReason.STRANDED_HEAD:
        return (
            f"HEAD has moved off {worktree.branch or 'the branch'}; put a branch "
            "or a tag on the commit to keep it"
        )
    if reason is KeepReason.NOTHING_COMMITTED:
        return f"{worktree.branch or 'HEAD'} = {worktree.parent_branch}"
    return reason.value


def retire_worktree(
    session: Session, service: WorktreeService, worktree: Worktree
) -> RetirementRefusal | None:
    """Remove a worktree's checkout, unless doing so would lose something.

    Returns the premise that refused — or `RemovalFailure.CLEANUP_FAILED` when
    every premise held and the removal itself did not work — and None once the
    checkout is gone, which is the one thing None means. A failed removal
    answering None would tell three callers a checkout is gone while it is
    still on disk: the sweep would delete the published remote ref it had just
    kept, and `delete_chat_session` would drop the row that owns the
    directory.

    The answer is carried rather than collapsed into a bool because
    `chat_branch_sweep` reports to an operator *which* premise fired, and a
    sweep that says "uncommitted work" about a stranded commit sends them
    looking for edits that are not there.

    Public because `chat_branch_sweep` deletes on the strength of it unattended,
    and the refusals in `_keep_reason` are the whole safety argument for that. A
    sweep reaching in through an underscore is how a safety bar gets relaxed by
    someone who never saw the caller relying on it.

    The refusal is on `reason is not None`, never a branch per member: the
    fallthrough here is the destructive step, so a `KeepReason` this function
    has no case for would delete the checkout it was added to protect.
    """
    reason = _keep_reason(worktree)
    if reason is not None:
        logger.warning(
            "Worktree %s at %s kept on disk: %s (%s)",
            worktree.id,
            worktree.worktree_path,
            reason.value,
            _reason_detail(reason, worktree),
        )
        return reason
    if not service.cleanup_worktree(worktree):
        logger.warning(
            "Worktree %s at %s satisfied every premise but its removal failed; "
            "the checkout is still on disk and still owned",
            worktree.id,
            worktree.worktree_path,
        )
        return RemovalFailure.CLEANUP_FAILED
    return None


def _kept_summary(kept: dict[KeepReason, list[Worktree]]) -> str:
    """One grouped block for every finished tree startup left on disk.

    A restart after a busy week kept a dozen trees, each as its own wall-width
    line; grouped by reason, the two that hold real work stand apart from the
    ten that merely hold nothing.
    """
    total = sum(len(trees) for trees in kept.values())
    lines = [f"Kept {total} finished ticket worktree(s) on disk:"]
    for reason in KeepReason:
        trees = kept.get(reason, [])
        if not trees:
            continue
        lines.append(f"  {reason.value} ({len(trees)}):")
        for tree in sorted(trees, key=lambda t: t.worktree_path or ""):
            lines.append(f"    {tree.worktree_path or tree.id}  [{_reason_detail(reason, tree)}]")
    return "\n".join(lines)


def release_ticket_worktree(session: Session, ticket: Ticket) -> bool:
    """Remove the worktree a finished ticket was using. Returns True if removed.

    The ticket's branch survives — its commits live in the shared repository's
    object store, so a PR opened later still has them. Only the checkout goes.
    That holds exactly as far as `retire_worktree` proves it does: a tree with
    local changes, a HEAD carrying a commit no ref reaches, or a branch carrying
    no commits of its own, is kept instead. So is one whose removal was tried
    and failed, which answers False here rather than claiming a checkout that
    is still on disk is gone.
    """
    if ticket.state not in StateMachine.TERMINAL_TICKET_STATES:
        return False

    service = _service_for(session, ticket.workspace_id)
    if not service:
        return False

    worktree = service.active_worktree_for_ticket(ticket.id)
    if not worktree:
        return False
    return retire_worktree(session, service, worktree) is None


def release_chat_worktree(session: Session, chat_session: BaxterChatSession) -> Worktree | None:
    """Remove the worktree a chat thread was using.

    Returns None when nothing is left holding the thread, and otherwise the
    worktree it *could not* release — a failure that carries its subject, so a
    caller can say which branch and which directory rather than "no".

    The conversational counterpart of :func:`release_ticket_worktree`, and
    subject to the same refusals: `retire_worktree` keeps a tree with
    uncommitted changes, a HEAD holding a commit no ref reaches, or a branch
    carrying no commits of its own, because removing any of them loses work. A
    removal that failed is returned here too — the thread still has a checkout,
    so the caller must not drop the row that owns it.
    """
    # Read the row before the service, so a workspace that cannot be resolved
    # still reports the tree it is leaving behind. Deriving it from the service
    # instead made that case return "nothing to release", and the checkout was
    # then orphaned on disk with the column set to NULL by the delete.
    worktree = session.exec(
        select(Worktree)
        .where(Worktree.chat_session_id == chat_session.id)
        .where(Worktree.state == WorktreeState.ACTIVE)
    ).first()
    if worktree is None:
        return None

    service = _service_for(session, chat_session.workspace_id)
    if service is None:
        logger.warning(
            "Chat session %s has no resolvable workspace; its worktree at %s stays put",
            chat_session.id,
            worktree.worktree_path,
        )
        return worktree
    return None if retire_worktree(session, service, worktree) is None else worktree


def reconcile_worktrees(session: Session) -> int:
    """Settle worktrees the last process left active. Returns how many changed.

    Three cases, and only the first two are this function's business: the
    directory is gone (the row is stale bookkeeping), or the ticket has since
    finished (nothing will ever use the tree again). A live tree for an
    unfinished ticket is left alone — the orchestration resume path picks it
    back up, and deleting it would throw away the work that survived the crash.
    """
    rows = session.exec(select(Worktree).where(Worktree.state == WorktreeState.ACTIVE)).all()
    settled = 0
    kept: dict[KeepReason, list[Worktree]] = {}
    for worktree in rows:
        service = _service_for(session, worktree.workspace_id)
        if not service:
            continue

        path = Path(worktree.worktree_path) if worktree.worktree_path else None
        if path is None or not path.is_dir():
            logger.info("Worktree %s is recorded active but gone from disk", worktree.id)
            service.cleanup_worktree(worktree)
            settled += 1
            continue

        ticket = session.get(Ticket, worktree.ticket_id) if worktree.ticket_id else None
        if not ticket or ticket.state not in StateMachine.TERMINAL_TICKET_STATES:
            continue
        reason = _keep_reason(worktree)
        if reason is not None:
            kept.setdefault(reason, []).append(worktree)
        elif service.cleanup_worktree(worktree):
            settled += 1

    if kept:
        logger.warning(_kept_summary(kept))
    if settled:
        logger.info("Reconciled %d orphaned worktree(s) at startup", settled)
    return settled
