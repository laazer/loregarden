"""The receipt a destructive cleanup is gated on, read from the record we keep.

Every destructive act in this tree used to be reached from an *observation of
shape*: the directory exists, the tree looks clean, the branch looks empty.
None of those says the work the checkout held actually finished — they say what
a crash looks like from the outside.

So removing a worktree has one more premise, consulted before any premise about
the shape of the tree: a recorded receipt that the work landed. The receipt is
the record two writers already keep — `tickets.landed_sha` / `landed_branch` —
**re-verified against the repository on every read**. That last clause is the
whole design: a receipt the database records and git cannot confirm is not a
receipt, which is what makes a forged completion fail to release ownership.

Three owner arms, chosen by a COLUMN READ on the row — `ticket_id`,
`chat_session_id`, or neither — never by the row's Python type, because all
three are the same class and a type switch would answer the same for all of
them:

* **ticket-owned**: the landing is on record, names this tree's target branch,
  contains the tree's live tip, and the target still contains the landing.
* **chat-owned**: everything the branch changed is already in base.
* **neither** (a fan-out attempt, a parallel-queue run): the tree's tip is
  already contained by whoever chose the work — the promoted attempt when the
  group has settled, and the base branch when it has not, which is the only
  honest reading of "nothing here will be lost".

Nothing is stored. The tree's tip is read live, so advancing a branch changes
the answer with nothing written to the row in between (plan D2), and
:func:`landing_on_record` is the single implementation of the match, shared
with the writer that records it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import (
    BaxterChatSession,
    StageFanoutAttempt,
    StageFanoutAttemptStatus,
    Ticket,
    Workspace,
    Worktree,
)
from loregarden.services.artifact_service import git_base_ref
from loregarden.services.branch_landed import branch_work_has_landed
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.git_merge_noco import is_ancestor, rev
from loregarden.services.target_branch import target_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_premises import KeepReason
from sqlmodel import Session, select

#: A receipt names one immutable object. `is_ancestor` takes a *revision*, and
#: `main`, `HEAD`, `@`, `refs/heads/main` and `main^{commit}` all resolve in a
#: real repository — so a column somebody typed a branch name into would answer
#: "landed" for whatever that branch is this afternoon. 40 hex characters, or
#: it is not a digest and not a receipt.
_DIGEST = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class Receipt:
    """Evidence that the work a checkout held finished, re-verified in git."""

    sha: str
    branch: str
    #: What was matched, in words. Both answers carry one so a caller reading
    #: the sentence does not have to ask which answer it has first — a type
    #: test at every report site is the `isinstance` dispatch AC2 forbids one
    #: question over.
    detail: str = ""


@dataclass(frozen=True)
class Unresolved:
    """No receipt — either because there is none, or because nobody could look.

    Not a bool and not `None`. A bool collapses "the work did not land" with
    "I could not find out", and this gate's whole argument is that those are
    different; `None` is worse, because `if not answer:` reads the same for
    both. An `Unresolved` is truthy for the same reason.
    """

    reason: KeepReason
    detail: str


def landing_on_record(repo_root: Path, ticket: Ticket, target: str, branch_sha: str) -> bool:
    """Whether the ticket already records the commit that landed this branch.

    `landed_sha` is the commit on the target that first contained the branch:
    the merge commit when a landing merged it. Re-landing an already-contained
    branch used to overwrite that with the branch tip, so the same ticket named
    two different commits depending on how often it was landed (928).

    Public, and the one implementation of the match: the writer checks it
    before recording, and the receipt gate checks it before deleting a
    checkout. Two copies would disagree the first time one of them was
    tightened — as this one was, with the digest bar below.

    Four conjuncts, and the fourth is the one with no obvious failure: a
    landing the target no longer reaches (a force-update, a rewind) is a real
    commit that still contains the tree, and the work it claims to have
    published is no longer anywhere anybody else reads.
    """
    return (
        bool(ticket.landed_sha)
        and bool(_DIGEST.match(ticket.landed_sha))
        and ticket.landed_branch == target
        and is_ancestor(repo_root, branch_sha, ticket.landed_sha)
        and is_ancestor(repo_root, ticket.landed_sha, f"refs/heads/{target}")
    )


def resolve_receipt(session: Session, worktree: Worktree) -> Receipt | Unresolved:
    """The receipt for this checkout's work, or why there is not one.

    Any arm whose git call could not answer resolves `Unresolved` with
    `UNREADABLE` rather than borrowing the verdict "it did not land": both
    refuse, and only one of them is true about what happened.
    """
    workspace = session.get(Workspace, worktree.workspace_id)
    if workspace is None:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail=f"worktree {worktree.id} records no workspace, so nothing can be verified",
        )
    repo_root = resolve_workspace_root(workspace)

    # The owner arm is a column read. Three rows of the same class take three
    # different arms, decided by which id the row carries.
    if worktree.ticket_id:
        return _ticket_receipt(session, repo_root, worktree, workspace)
    if worktree.chat_session_id:
        return _chat_receipt(session, repo_root, worktree)
    return _unowned_receipt(session, repo_root, worktree)


def _live_tip(repo_root: Path, worktree: Worktree) -> str:
    """This tree's tip right now, read from the repository and never from a row.

    The branch rather than the directory's HEAD: a branch ref is what keeps
    commits reachable once the checkout is gone, so it is the thing a receipt
    has to cover.
    """
    return rev(repo_root, f"refs/heads/{worktree.branch}") if worktree.branch else ""


def _ticket_receipt(
    session: Session, repo_root: Path, worktree: Worktree, workspace: Workspace
) -> Receipt | Unresolved:
    ticket = session.get(Ticket, worktree.ticket_id)
    if ticket is None:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail=f"worktree {worktree.id} names a ticket that is not there",
        )
    tip = _live_tip(repo_root, worktree) or rev(repo_root, resolve_ticket_branch(ticket))
    if not tip:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail=f"the tip of {worktree.branch or resolve_ticket_branch(ticket)} could not be read",
        )
    target = target_branch_name(session, ticket, workspace)
    if landing_on_record(repo_root, ticket, target, tip):
        return Receipt(
            sha=ticket.landed_sha,
            branch=target,
            detail=f"{ticket.external_id} landed {ticket.landed_sha[:12]} on {target}",
        )
    if _landing_the_target_still_holds(repo_root, ticket, target) and not is_ancestor(
        repo_root, tip, ticket.landed_sha
    ):
        # The landing is real, it is on the right target, and the target still
        # reaches it; the one conjunct that fails is that it does not cover
        # what is in this tree now. A different answer from "there is no
        # record", because the operator's next move is different: land again.
        return Unresolved(
            reason=KeepReason.RECEIPT_DOES_NOT_COVER,
            detail=(
                f"{ticket.external_id} landed {ticket.landed_sha[:12]} on {target}, which does "
                f"not contain {worktree.branch or 'this tree'} at {tip[:12]}"
            ),
        )
    return Unresolved(
        reason=KeepReason.NO_RECEIPT,
        detail=f"nothing on record says {ticket.external_id}'s work reached {target}",
    )


def _landing_the_target_still_holds(repo_root: Path, ticket: Ticket, target: str) -> bool:
    """Whether the recorded landing is a real commit the target still reaches.

    Only used to tell two refusals apart. A record naming a digest no ref
    contains, or naming another target, is NOT a landing at all — "nothing says
    this work finished" — and must not be reported as a landing that merely
    needs repeating, which is advice about a commit that does not exist.
    """
    return (
        bool(ticket.landed_sha)
        and bool(_DIGEST.match(ticket.landed_sha))
        and ticket.landed_branch == target
        and bool(rev(repo_root, ticket.landed_sha))
        and is_ancestor(repo_root, ticket.landed_sha, f"refs/heads/{target}")
    )


def _chat_receipt(session: Session, repo_root: Path, worktree: Worktree) -> Receipt | Unresolved:
    chat_session = session.get(BaxterChatSession, worktree.chat_session_id)
    if chat_session is None:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail=f"worktree {worktree.id} names a chat thread that is not there",
        )
    base = git_base_ref(repo_root)
    if not base:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail=f"{repo_root} has no base ref, so nothing can be shown to have landed",
        )
    if not worktree.branch:
        return Unresolved(
            reason=KeepReason.NO_RECEIPT,
            detail=f"worktree {worktree.id} records no branch, so its work cannot be traced",
        )
    if branch_work_has_landed(repo_root, base, worktree.branch):
        return Receipt(
            sha=rev(repo_root, base),
            branch=base,
            detail=f"this thread's work on {worktree.branch} is already in {base}",
        )
    return Unresolved(
        reason=KeepReason.NO_RECEIPT,
        detail=f"this thread's work on {worktree.branch} is not yet in {base}",
    )


def _unowned_receipt(session: Session, repo_root: Path, worktree: Worktree) -> Receipt | Unresolved:
    """A tree no ticket and no thread owns: a fan-out attempt or a queue run.

    Its receipt is somebody else's choice of its work. The promoted attempt's
    tip is that chooser once the group has settled; until then — and for a
    group that was abandoned and never will settle — the base branch is, which
    answers "this tree holds nothing of its own" and refuses everything else.
    A raise here would end the sweep for every other tree, which is the one
    answer worse than a refusal.
    """
    tip = _live_tip(repo_root, worktree)
    if not tip:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail=f"the tip of {worktree.branch or 'this tree'} could not be read",
        )
    chooser, chooser_name = _chooser_tip(session, repo_root, worktree)
    if not chooser:
        return Unresolved(
            reason=KeepReason.UNREADABLE,
            detail="nothing this tree's work could have been chosen into could be read",
        )
    if is_ancestor(repo_root, tip, chooser):
        return Receipt(
            sha=chooser,
            branch=chooser_name,
            detail=f"{chooser_name} already contains {worktree.branch or 'this tree'}",
        )
    return Unresolved(
        reason=KeepReason.NO_RECEIPT,
        detail=(
            f"{worktree.branch or 'this tree'} holds commits {chooser_name} does not, "
            "so nobody has chosen its work"
        ),
    )


def _chooser_tip(session: Session, repo_root: Path, worktree: Worktree) -> tuple[str, str]:
    """The tip whose containment of this tree's work counts as having chosen it."""
    attempt = session.exec(
        select(StageFanoutAttempt).where(StageFanoutAttempt.worktree_id == worktree.id)
    ).first()
    if attempt is not None:
        keeper = session.exec(
            select(StageFanoutAttempt)
            .where(StageFanoutAttempt.group_id == attempt.group_id)
            .where(StageFanoutAttempt.status == StageFanoutAttemptStatus.PROMOTED)
        ).first()
        if keeper is not None and keeper.branch:
            return rev(repo_root, f"refs/heads/{keeper.branch}"), keeper.branch
    base = git_base_ref(repo_root)
    if not base:
        return "", ""
    return rev(repo_root, base), base
