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
from dataclasses import dataclass
from pathlib import Path

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    BaxterChatSession,
    ProcessState,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.directory_release import (
    DirectoryReleaseFailed,
    DirectoryState,
    directory_state,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.process_identity import (
    BootIdentityUnavailable,
    ClaimIdentity,
    claim_is_live,
    parse_claim,
)
from loregarden.services.publish_ref import comparison_ref
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_premises import (
    KeepReason,
    RemovalFailure,
    RetirementRefusal,
)
from loregarden.services.worktree_receipt import Receipt, resolve_receipt
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select

__all__ = [
    "ChatWorktreeHold",
    "KeepReason",
    "RemovalFailure",
    "Retirement",
    "RetirementRefusal",
    "reconcile_worktrees",
    "release_chat_worktree",
    "release_ticket_worktree",
    "retire_worktree",
]

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
    An integration branch is the exception: the remote never holds its history,
    so its local ref is the branch (`publish_ref.comparison_ref`). The local
    branch is also the fallback for a repository with no remote.
    """
    for ref in dict.fromkeys((comparison_ref(parent_branch), parent_branch)):
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


def _rev(path: Path, ref: str) -> str:
    """The commit `ref` names in this checkout, or "" when it names nothing."""
    read = run_git(
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
    )
    return read.stdout.strip() if read.returncode == 0 else ""


def _has_preserved_commits(path: Path, worktree: Worktree) -> bool:
    """True when the branch names something other than its base.

    This used to count `base..branch` and require more than zero, and that
    reading cannot survive beside the receipt premise: a branch that has
    LANDED is contained by its base by definition, so `base..branch` is 0 for
    every tree the gate is otherwise ready to release, and every landed
    checkout would be kept forever. The count was always a proxy for "does
    anything survive removing this checkout", and with a receipt on record the
    answer to that is yes — the landing preserved it, on the target, which is
    what the receipt re-verified in git.

    So the premise asks what is left to ask: whether the branch names its base
    and nothing else, which is the tree where nothing was ever committed. The
    question is asked of the *branch*, not of the tree's HEAD, because a branch
    ref is what keeps commits reachable once the worktree is gone — work
    committed on a detached HEAD counts for nothing here, and should not.

    A base that cannot be resolved, or a ref that cannot be read, answers
    False: an unprovable premise is not a satisfied one.
    """
    base = _base_ref(path, worktree.parent_branch)
    if base is None:
        return False
    base_sha = _rev(path, base)
    branch_sha = _rev(path, worktree.branch or "HEAD")
    if not base_sha or not branch_sha:
        return False
    return base_sha != branch_sha


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


def _claim_state(worktree: Worktree) -> ProcessState | None:
    """Whether a live process still holds this checkout, or `None` for nobody.

    An EMPTY claim reads as "nobody claims this". That is the single deliberate
    fail-open in this change, and its reason is bounded: every `worktrees` row
    written before the claim columns existed carries no claim, and reading
    those as uncertainty would make every checkout existing at deploy time
    permanently unretirable — a worse outage than the one being fixed. The
    bound is that the claim is not the only premise: no receipt, no removal,
    whoever does or does not own the tree.

    A claim that is PRESENT and unreadable is a different row and is not
    granted that fail-open: three columns, one of them garbage, written by
    something that got part way. `parse_claim` answers `None` for both, so the
    columns decide which case this is.

    Raises `BootIdentityUnavailable` from `claim_is_live`, caught at the
    decision site below.
    """
    claim = parse_claim(
        claim_pid=worktree.claim_pid,
        claim_birth=worktree.claim_birth,
        claim_boot_id=worktree.claim_boot_id,
    )
    if claim is not None:
        state = claim_is_live(claim)
        if state is ProcessState.ALIVE and claim == ClaimIdentity.current():
            # OUR OWN claim, which is not somebody else working in the tree.
            # The claim is stamped by the process that cuts or reuses a tree,
            # and that is the same process that retires it when the ticket
            # finishes — so reading a self-claim as a live claimant would keep
            # every checkout this server ever cut, forever. What the claim
            # guards against is a DIFFERENT live process: another server, a
            # detached agent, a second checkout of the control plane.
            #
            # Read AFTER `claim_is_live`, never instead of it: that call is
            # where an unreadable boot stamp raises, and skipping it for our own
            # rows would make the machine that cannot say which boot it is on
            # answer "unclaimed" for exactly the trees it is working in.
            return None
        return state
    if worktree.claim_pid or worktree.claim_birth or worktree.claim_boot_id:
        # Written, and not readable. Uncertainty, not absence.
        raise _ClaimUnreadable(
            f"worktree {worktree.id} records a claim nothing can parse: "
            f"pid={worktree.claim_pid!r} birth={worktree.claim_birth!r} "
            f"boot={worktree.claim_boot_id!r}"
        )
    return None


class _ClaimUnreadable(RuntimeError):
    """A claim that is present and garbled. Private: it never leaves this module."""


def _keep_reason(session: Session, worktree: Worktree) -> KeepReason | None:
    """The premise that fails for removing this checkout, or None when all hold.

    The RECEIPT is asked first, before any premise about the shape of the tree.
    That is this ticket's subject: committed, clean, a branch ahead of its base
    and HEAD on the branch is an observation of *shape*, and shape is what a
    crash looks like from the outside. A tree that is also dirty reports the
    receipt, because an unlanded tree's dirt is not what is keeping it.

    Only asked about a checkout the caller has already OBSERVED. Whether there
    is a directory at all is not a premise about what the tree contains — with
    nothing on disk there is no destructive act to gate, and refusing there
    would pin every leaked row at ACTIVE forever — so that reading is taken
    once, by `_observed_state`, and is carried to the removal rather than
    re-taken here. Re-stating it as a stat of its own is what gave an absence
    read HERE the power to license a release of whatever the path held by the
    time the rename ran (AC9).

    The rest are ordered by what the operator can act on. Uncommitted work is
    destroyed outright, so it is said first; a stranded commit survives in the
    object store and only loses its last reference, and its reason names the
    recovery; an empty branch is last, because when a tree also holds one of
    the other two, its emptiness is a consequence rather than the cause.
    """
    path = Path(worktree.worktree_path)
    claimed = _claim_refusal(_claim_state(worktree))
    if claimed is not None:
        return claimed
    receipt = resolve_receipt(session, worktree)
    if not isinstance(receipt, Receipt):  # py-org: allow-isinstance - a two-member answer
        return receipt.reason
    if _is_dirty(path):
        return KeepReason.UNCOMMITTED
    if _has_stranded_commits(path):
        return KeepReason.STRANDED_HEAD
    if not _has_preserved_commits(path, worktree):
        return KeepReason.NOTHING_COMMITTED
    return None


#: What each answer about the claim means for the tree. A mapping over the
#: whole of `ProcessState` rather than a test for the one member somebody
#: thought of: `_claim_state` answering ALIVE used to be the only reading, so
#: UNKNOWN — which `liveness` returns when `ps` could not answer at all, and
#: whose own docstring says "none may read it as GONE" — fell through every
#: premise about the claim and reached the rename. GONE is the ONE answer that
#: releases a tree, and a member added later gets CLAIM_UNPROVEN by default,
#: because the fallthrough in this module is the destructive step (AC11).
_CLAIM_REFUSAL: dict[ProcessState, KeepReason | None] = {
    ProcessState.ALIVE: KeepReason.CLAIMANT_LIVE,
    ProcessState.UNKNOWN: KeepReason.CLAIM_UNPROVEN,
    ProcessState.GONE: None,
}


def checkout_loss(path: Path) -> KeepReason | None:
    """What removing the checkout at `path` would destroy, or None when nothing.

    The two premises of `_keep_reason` that are about the *tree*, for a caller
    that holds a path rather than a `Worktree` row (disk cleanup, which sees
    every repository's worktrees, tracked or not). `NOTHING_COMMITTED` is left
    out on purpose: it is a ticket's question ("did the agent produce
    anything?"), and an empty clean checkout loses nothing when it goes.
    """
    if _is_dirty(path):
        return KeepReason.UNCOMMITTED
    if _has_stranded_commits(path):
        return KeepReason.STRANDED_HEAD
    return None


def _claim_refusal(state: ProcessState | None) -> KeepReason | None:
    """The premise this claim reading refuses with, or `None` for nobody holding it.

    `None` in means an EMPTY claim — the deliberate fail-open `_claim_state`
    documents — and is not the same question as GONE, which is a claim that was
    read and whose process has exited. Both release; neither is uncertainty.
    """
    if state is None:
        return None
    return _CLAIM_REFUSAL.get(state, KeepReason.CLAIM_UNPROVEN)


#: The never-retryable counterpart of `RemovalFailure.CLEANUP_FAILED`: the
#: recorded path is not under the service's base, so nothing was attempted and
#: no boot will settle it. A separate clause rather than a reworded first one,
#: because "removing its checkout failed" invites the retry that cannot work
#: here.
REMOVAL_REFUSED_OUTSIDE_BASE = "its checkout is outside the worktrees folder"

#: The one place the seam's vocabulary and this module's are paired, read only
#: by `retire_worktree`. A mapping rather than a branch per member so a cause
#: added later has no clause and fails loudly instead of inheriting a sentence
#: written for the other one.
_FAILURE_CLAUSE: dict[CleanupRefusal, str] = {
    CleanupRefusal.REMOVE_RAISED: RemovalFailure.CLEANUP_FAILED.value,
    CleanupRefusal.OUTSIDE_BASE: REMOVAL_REFUSED_OUTSIDE_BASE,
}


def _failure_clause(cause: object | None) -> str:
    """The sentence a failed removal is reported with, for any cause at all.

    Looked up by membership and never indexed blind, for the reason
    `_clause_for` gives three functions down: a cause nobody wrote a clause for
    must still answer a REFUSAL. `DirectoryReleaseFailed.refusal` defaults to
    `None` — "left for the seam to stamp", per its own docstring — so an
    unstamped raise is representable without anybody writing a bug, and a third
    `CleanupRefusal` member is one line away. `_FAILURE_CLAUSE[None]` raised
    `KeyError` out of `_remove`, which is the one place AC14 says catches, and
    *loudly* on that path means ending the sweep with every remaining row
    unvisited — the thing AC11 forbids in the same breath as the inherited
    sentence.

    The fallback is `CLEANUP_FAILED`'s own wording rather than a neighbour's:
    it says the removal was attempted and did not happen, which is true of
    every cause that reaches here, and `_refusal_detail` still names the path
    that was not deleted and the one a parked copy went to (AC13).
    """
    if cause in _FAILURE_CLAUSE:
        return _FAILURE_CLAUSE[cause]
    return RemovalFailure.CLEANUP_FAILED.value


#: What each premise says to the operator, beyond naming itself. One home for
#: the per-reason wording, read by the returned `detail`, by `retire_worktree`'s
#: warning and by `_kept_summary`'s grouped lines. Each member's sentence is its
#: own and repeats no neighbour's: five reasons sharing one sentence is five
#: reasons nobody can tell apart, which is the state this gate replaces.
_REASON_CLAUSE: dict[KeepReason, str] = {
    KeepReason.UNCOMMITTED: "the changes exist in this tree and nowhere else",
    KeepReason.STRANDED_HEAD: (
        "HEAD has moved off the branch; put a branch or a tag on the commit to keep it"
    ),
    KeepReason.NOTHING_COMMITTED: "its branch holds nothing its base does not",
    KeepReason.NO_RECEIPT: (
        "nothing on record says its work finished — land the branch, or publish the tree, "
        "and the next sweep will take it"
    ),
    KeepReason.RECEIPT_DOES_NOT_COVER: (
        "the landing on record predates what is in this tree, so landing again is what clears it"
    ),
    KeepReason.CLAIM_UNPROVEN: "nobody could prove who holds it, so it is treated as held",
    KeepReason.UNREADABLE: "the filesystem would not say whether it is there",
    KeepReason.CLAIMANT_LIVE: "an agent is still working in it",
}


def _reason_detail(reason: KeepReason, worktree: Worktree) -> str:
    """The grouped boot summary's per-tree note. One line, no path."""
    clause = _REASON_CLAUSE.get(reason)
    if clause is None:
        return reason.value
    if reason is KeepReason.STRANDED_HEAD:
        return clause.replace("the branch", worktree.branch or "the branch", 1)
    if reason is KeepReason.NOTHING_COMMITTED:
        return f"{worktree.branch or 'HEAD'} = {worktree.parent_branch}"
    return clause


def _refusal_detail(
    refusal: RetirementRefusal, worktree: Worktree, *, parked_path: str, clause: str
) -> str:
    """The words a refusal is REPORTED in: what was not deleted, and where it is.

    Asserted on by callers and by tests, which is why it is built here and not
    in a log record: the operator reads the answer, not the source. Every
    refusal states that nothing was deleted and names the tree it kept; an
    outcome that parked a copy names that exact path and says the checkout is
    still owned, because that path is the whole recovery procedure.
    """
    path = worktree.worktree_path or worktree.id
    detail = f"Nothing was deleted: {path} is still owned — {clause}."
    if parked_path:
        detail = (
            f"Nothing was deleted: {path} is still owned, and its checkout is parked at "
            f"{parked_path} — {clause}."
        )
    del refusal
    return detail


@dataclass(frozen=True)
class Retirement:
    """What happened to a checkout, and if nothing did, what is still owed.

    `refusal is None` means — and means only — that the checkout is gone. The
    collapse this exists to prevent is between a premise that *chose* to keep
    the tree and a removal that was attempted and failed: both leave a
    directory on disk, and a `KeepReason | None` return said "removed" about a
    checkout a permission error had just refused to unlink
    (lg-external-harness-954). Which kind it was is the refusal's own type, so
    an answer cannot carry both and the ordering a caller would get wrong is
    unrepresentable rather than merely documented.

    Frozen, because this travels to a caller that decides whether to delete a
    branch: a mutable answer is one an intermediate layer can "normalize" into
    the success it was supposed to prevent.
    """

    #: `KeepReason` for a premise, `RemovalFailure` for a removal that failed,
    #: `None` for a checkout that is gone.
    refusal: RetirementRefusal | None
    #: Always True on a refusal, and this function's own decision rather than a
    #: coercion of whatever the seam answered.
    deleted_nothing: bool
    #: Where a failed removal left the checkout, or "".
    parked_path: str
    #: Words for a caller to print.
    detail: str
    #: The seam's own member for a failed removal, which `refusal` deliberately
    #: collapses into the single `CLEANUP_FAILED`. Additive and never the
    #: discriminator: a caller asking "is the checkout gone" reads `refusal`,
    #: and this is only how `delete_chat_session` picks a sentence for which of
    #: the two causes happened without parsing `detail`'s prose.
    cause: CleanupRefusal | None = None


@dataclass(frozen=True)
class ChatWorktreeHold:
    """A chat thread's checkout that stayed on disk, and what is holding it.

    `delete_chat_session` refuses the delete either way — the row is the only
    thing that owns the directory and no sweeper reaps a chat checkout — but
    the holds need different words. "Commit or discard it" cannot clear a
    permission error.

    Two fields carrying two DIFFERENT facts, which is what replaced the one
    bool that carried neither. `outcome` is the gate's own answer, and AC17
    needs three values from it: a premise refusal, a failed removal, a parked
    outcome. `cause` is the seam's own member, which the gate deliberately
    collapses into the single `CLEANUP_FAILED`, so without it the caller could
    not tell a path outside the base from an `rmtree` that raised except by
    parsing prose — and prose is the thing a wording tweak changes.
    """

    worktree: Worktree
    #: The gate's answer. Not defaulted: a hold with no outcome is the bool
    #: again, one field along.
    outcome: Retirement
    #: `None` when nothing was attempted — a premise chose to keep the tree, or
    #: the workspace could not be resolved.
    cause: CleanupRefusal | None = None


def _record_refusal(session: Session, worktree: Worktree, outcome: Retirement) -> None:
    """Write what this pass decided onto the row, replacing what the last one did.

    "Evidence records uncertainty; it never releases ownership" — both halves.
    A refusal nobody can see afterwards is a tree that sits on disk for a month
    with no answer to "why is this still here?", and the row is the only place
    that answer survives the sweep that produced it.

    Cleared, not appended, when the tree settles: a row that is gone from disk
    while still carrying "kept because: no receipt" is a record false about its
    own subject, and an operator reading the kept-tree list acts on it.
    """
    worktree.unresolved_reason = "" if outcome.refusal is None else outcome.detail
    worktree.parked_at = outcome.parked_path
    session.add(worktree)
    session.commit()


def _refused(
    session: Session,
    worktree: Worktree,
    refusal: RetirementRefusal,
    clause: str,
    *,
    parked_path: str,
    cause: CleanupRefusal | None = None,
) -> Retirement:
    """Build and record one refusal. The only place a `Retirement` refuses."""
    outcome = Retirement(
        refusal=refusal,
        deleted_nothing=True,
        parked_path=parked_path,
        detail=_refusal_detail(refusal, worktree, parked_path=parked_path, clause=clause),
        cause=cause,
    )
    _record_refusal(session, worktree, outcome)
    return outcome


def _clause_for(refusal: RetirementRefusal) -> str:
    """The sentence this refusal is reported with.

    A lookup, not a branch per member: the fallthrough in this module is the
    destructive step, so a member nobody wrote a clause for must still answer a
    refusal rather than fall through — it reports its own value, which is
    thinner than a written clause and is not a neighbour's sentence.
    """
    return _REASON_CLAUSE.get(refusal, refusal.value)


#: The two premises whose clause an operator needs the PROBE's own words for:
#: "nothing on record says this landed" is the rule, and which record was
#: consulted — or which git call could not be made — is the part that tells them
#: what to do next.
_RECEIPT_PREMISES = (KeepReason.NO_RECEIPT, KeepReason.RECEIPT_DOES_NOT_COVER)


def _premise_clause(session: Session, worktree: Worktree, refusal: RetirementRefusal) -> str:
    """The sentence this refusal is reported with, probe detail included.

    Re-asking `resolve_receipt` on the refusal path is deliberate and cheap
    where it happens: a tree that is being released asks once, and only a tree
    that is being KEPT pays for the second read — which is the one that has to
    explain itself to a person. The alternative was threading a second value
    out of `_keep_reason`, whose answer every caller and every test treats as
    one member.
    """
    clause = _clause_for(refusal)
    if refusal not in _RECEIPT_PREMISES:
        return clause
    answer = resolve_receipt(session, worktree)
    return f"{clause} ({answer.detail})" if answer.detail else clause


def _settle(session: Session, service: WorktreeService, worktree: Worktree) -> Retirement:
    """Remove the checkout, unless a premise refuses or the removal fails.

    The one place `DirectoryReleaseFailed` is caught, per row, so a sweep keeps
    sweeping: the seam raises rather than answering a value a caller can forget
    to read, and a raise that escaped here would end the pass and leave every
    worktree after this one unvisited.

    Builds the answer by BRANCHING. Nothing is coerced from what the seam
    returned — a `MagicMock`'s default answer is a truthy `Mock`, and
    `deleted_nothing=bool(not removed)` would pass every behavioural test here
    and silently become wrong the day the removal path answers something other
    than a bool.
    """
    try:
        observed = _observed_state(worktree)
    except OSError:
        return _unreadable(session, worktree)
    refusal = _premise_or_uncertainty(session, worktree, observed)
    if refusal is not None:
        return _refused(
            session, worktree, refusal, _premise_clause(session, worktree, refusal), parked_path=""
        )
    return _remove(session, service, worktree, observed=observed)


def _remove(
    session: Session,
    service: WorktreeService,
    worktree: Worktree,
    *,
    observed: DirectoryState,
) -> Retirement:
    """Hand the checkout to the seam, every premise having held.

    The one place `DirectoryReleaseFailed` is caught, and it is caught per row.

    `observed` is this pass's OWN reading of the recorded path, and it decides
    which half of the seam runs. An absence is not a licence: a row stays
    ACTIVE while its checkout is missing, `resolve_execution_root` reuses that
    row and recreates the checkout, and this sweep runs on a timer — so an
    ABSENT reading spent on a rename-then-delete deletes whatever a later run
    cut into the same path, having asked no premise about it (AC9). The row is
    still settled, and containment is still judged; only the destructive half
    is skipped.
    """
    try:
        if observed is DirectoryState.PRESENT:
            service.remove_checkout(worktree)
        else:
            service.settle_absent_checkout(worktree)
    except DirectoryReleaseFailed as failed:
        return _refused(
            session,
            worktree,
            RemovalFailure.CLEANUP_FAILED,
            _failure_clause(failed.refusal),
            parked_path=failed.parked_path,
            cause=failed.refusal,
        )
    # ORDERED AFTER the arm above, because `DirectoryReleaseFailed` subclasses
    # `OSError` and the reversed order would swallow every parked outcome into
    # a premise refusal that names no path. The seam reads the filesystem for
    # itself before releasing, and that read raises on any errno that is not
    # ENOENT/ENOTDIR — the same uncertainty about the same path the decision
    # site converts, so it answers the same way here rather than escaping
    # `_remove`, `_settle_one` and the sweep loop and ending the pass with
    # every remaining row unvisited (AC11).
    except OSError:
        return _unreadable(session, worktree)
    settled = Retirement(refusal=None, deleted_nothing=False, parked_path="", detail="")
    _record_refusal(session, worktree, settled)
    return settled


def _unreadable(session: Session, worktree: Worktree) -> Retirement:
    """Keep a tree the kernel would not describe, and record that it was asked.

    One home for the conversion, read by both entry points: a stat that will
    not answer is one step short of a delete, and the two callers must not
    drift into answering it differently.
    """
    logger.warning(
        "Worktree %s at %s: the filesystem would not say whether it is there",
        worktree.id,
        worktree.worktree_path,
        exc_info=True,
    )
    return _refused(
        session,
        worktree,
        KeepReason.UNREADABLE,
        _clause_for(KeepReason.UNREADABLE),
        parked_path="",
    )


def _premise_or_uncertainty(
    session: Session, worktree: Worktree, observed: DirectoryState
) -> KeepReason | None:
    """`_keep_reason`, with the two raises it is allowed to make converted here.

    Uncertainty is converted AT THE DECISION SITE and never inside
    `directory_release` or `process_identity`. Catching it there would mean
    picking a value to return, and every value available recreates the
    empty-string-compares-equal bug the throw prevents — `""` compares equal to
    an unstamped column, and `False` is what a delete was once licensed by.
    """
    if observed is DirectoryState.ABSENT:
        return None
    try:
        return _keep_reason(session, worktree)
    except (_ClaimUnreadable, BootIdentityUnavailable):
        logger.warning(
            "Worktree %s at %s: its claim could not be proved, so it stays claimed",
            worktree.id,
            worktree.worktree_path,
            exc_info=True,
        )
        return KeepReason.CLAIM_UNPROVEN
    except OSError:
        logger.warning(
            "Worktree %s at %s: the filesystem would not say whether it is there",
            worktree.id,
            worktree.worktree_path,
            exc_info=True,
        )
        return KeepReason.UNREADABLE


def retire_worktree(session: Session, service: WorktreeService, worktree: Worktree) -> Retirement:
    """Remove a worktree's checkout, unless doing so would lose something.

    Returns a :class:`Retirement`: `refusal is None` says the checkout is gone
    and says nothing else. A failed removal reported as a removal would tell
    three callers a checkout is gone while it is still on disk — the sweep would
    delete the published remote ref it had just kept, and `delete_chat_session`
    would drop the row that owns the directory.

    Public because `chat_branch_sweep` deletes on the strength of it unattended,
    and the premises are the whole safety argument for that. A sweep reaching in
    through an underscore is how a safety bar gets relaxed by someone who never
    saw the caller relying on it.

    The refusal is on `refusal is not None`, never a branch per member: the
    fallthrough here is the destructive step, so a premise this function has no
    case for would delete the checkout it was added to protect.

    One record per call, at `warning` — below that is reported nowhere.
    `reconcile_worktrees` reports for itself, because a boot settles every
    active row in one pass and needs the premises grouped and the two removal
    causes told apart.
    """
    outcome = _settle(session, service, worktree)
    if outcome.refusal is not None:
        logger.warning(
            "Worktree %s at %s kept on disk: %s (%s)",
            worktree.id,
            worktree.worktree_path,
            outcome.refusal.value,
            outcome.detail,
        )
    return outcome


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
    return retire_worktree(session, service, worktree).refusal is None


def release_chat_worktree(
    session: Session, chat_session: BaxterChatSession
) -> ChatWorktreeHold | None:
    """Remove the worktree a chat thread was using.

    Returns None when nothing is left holding the thread, and otherwise a
    :class:`ChatWorktreeHold` — a refusal that carries its subject, so a caller
    can say which branch and which directory rather than "no", and carries the
    gate's own answer plus the seam's cause, so it can say *why* rather than
    guessing. A bare `Worktree` could not tell a premise, a failed removal and
    a parked outcome apart, and the caller guessed "unpublished work" every
    time — which for a permission error is advice about a condition that may
    not exist.

    The conversational counterpart of :func:`release_ticket_worktree`, and
    subject to the same premises, the receipt among them.
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
        # Nothing was attempted — there was no service to attempt it with — so
        # this is a premise-shaped hold, and the caller keeps the wording
        # written for a tree the server chose to leave alone.
        return ChatWorktreeHold(
            worktree=worktree,
            outcome=_refused(
                session,
                worktree,
                KeepReason.UNREADABLE,
                "its workspace could not be resolved, so nothing could be attempted",
                parked_path="",
            ),
        )

    outcome = retire_worktree(session, service, worktree)
    if outcome.refusal is None:
        return None
    return ChatWorktreeHold(worktree=worktree, outcome=outcome, cause=outcome.cause)


def _report_failed_cleanup(
    worktree: Worktree, cause: CleanupRefusal | None, worktree_base: Path
) -> None:
    """Say that a row startup meant to settle did not settle, and which kind.

    One record per tree, not per run: a boot settles every active row in one
    pass, so a blanket line would name the trees that went as well.

    The single chooser between the two records. A retryable failure keeps the
    warning verbatim: the next boot may well clear it. A permanent one is its
    own record at `error`, naming the base the recorded path falls outside of
    and saying outright that no boot will settle it — because this one repeats
    on every boot, and a reader who met the retryable sentence twice would read
    "transient recurrence" about a fault only an operator can clear.
    """
    if cause is CleanupRefusal.OUTSIDE_BASE:
        logger.error(
            "Worktree %s records a checkout at %s, which is outside this workspace's "
            "worktrees folder %s; nothing was attempted and no boot will settle this "
            "row — fix the recorded path or the workspace's repo_path",
            worktree.id,
            worktree.worktree_path,
            worktree_base,
        )
        return
    logger.warning(
        "Worktree %s at %s could not be cleaned up at startup; the row stays active",
        worktree.id,
        worktree.worktree_path,
    )


def reconcile_worktrees(session: Session) -> int:
    """Settle worktrees the last process left active. Returns how many changed.

    Three cases, and only the first two are this function's business: the
    directory is gone (the row is stale bookkeeping), or the ticket has since
    finished (nothing will ever use the tree again). A live tree for an
    unfinished ticket is left alone — the orchestration resume path picks it
    back up, and deleting it would throw away the work that survived the crash.

    Every refusal is handled INSIDE the loop, per row. A stat the kernel
    refuses, a boot stamp that cannot be read and a release that failed are all
    raises, and any of them escaping would end the pass with every remaining
    worktree unvisited — silently, because an aborted sweep and a clean sweep
    both finish with no error.
    """
    rows = session.exec(select(Worktree).where(Worktree.state == WorktreeState.ACTIVE)).all()
    settled = 0
    kept: dict[KeepReason, list[Worktree]] = {}
    for worktree in rows:
        service = _service_for(session, worktree.workspace_id)
        if not service:
            continue

        try:
            observed = _observed_state(worktree)
        except OSError:
            kept.setdefault(KeepReason.UNREADABLE, []).append(worktree)
            _unreadable(session, worktree)
            continue

        if observed is DirectoryState.ABSENT:
            logger.info("Worktree %s is recorded active but gone from disk", worktree.id)
            settled += _settle_one(session, service, worktree, observed=observed)
            continue

        ticket = session.get(Ticket, worktree.ticket_id) if worktree.ticket_id else None
        if not ticket or ticket.state not in StateMachine.TERMINAL_TICKET_STATES:
            continue
        reason = _premise_or_uncertainty(session, worktree, observed)
        if reason is not None:
            kept.setdefault(reason, []).append(worktree)
            _refused(
                session,
                worktree,
                reason,
                _premise_clause(session, worktree, reason),
                parked_path="",
            )
            continue
        # `_remove` rather than `_settle`: the premises were just read, for the
        # grouped summary above, and asking the repository a second time per
        # row doubles a boot's git calls for an answer that cannot have changed.
        settled += _settle_one(session, service, worktree, observed=observed)

    if kept:
        logger.warning(_kept_summary(kept))
    if settled:
        logger.info("Reconciled %d orphaned worktree(s) at startup", settled)
    return settled


def _observed_state(worktree: Worktree) -> DirectoryState:
    """What is at the recorded path. Raises when the kernel will not say.

    Taken ONCE per pass and carried to the removal, rather than taken at the
    decision site and taken again by the seam: two readings of the same path
    are two chances to disagree, and the disagreement AC9 names is the one
    where the gate reads absent, concludes there is nothing to gate, and the
    seam then finds a checkout and releases it with no premise consulted.
    """
    if not worktree.worktree_path:
        return DirectoryState.ABSENT
    return directory_state(Path(worktree.worktree_path))


def _settle_one(
    session: Session,
    service: WorktreeService,
    worktree: Worktree,
    *,
    observed: DirectoryState,
) -> int:
    """Remove one checkout for the sweep. Returns 1 when the row moved.

    `settled` counts `None` and nothing else, per row: a pass that counted a
    refusal would make the boot count stop meaning "rows that moved", which is
    the leaked-row failure reported as its own fix.
    """
    outcome = _remove(session, service, worktree, observed=observed)
    if outcome.refusal is None:
        return 1
    _report_failed_cleanup(worktree, outcome.cause, service.worktree_base)
    return 0


def settle_row_whose_checkout_is_gone(session: Session, worktree: Worktree) -> bool:
    """Settle one ACTIVE row whose recorded checkout is not there. True if it moved.

    The same settle `reconcile_worktrees` runs for an ABSENT reading, exposed
    for the invariant that catches the state between boots
    (lg-external-harness-1452). Nothing is deleted on this path — an absence
    is not a licence — so a second definition of the settle would be a second
    place deciding what to do about one row.
    """
    service = _service_for(session, worktree.workspace_id)
    if service is None:
        logger.warning(
            "Worktree %s records a checkout at %s and its workspace is gone, so "
            "nothing can settle it",
            worktree.id,
            worktree.worktree_path,
        )
        return False
    return bool(_settle_one(session, service, worktree, observed=DirectoryState.ABSENT))
