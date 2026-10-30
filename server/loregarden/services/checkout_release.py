"""Turning one filesystem path into one receipt-gate answer, in one place.

`retire_worktree` takes a `Worktree` row. Two callers hold a path instead: the
branch-triage surfaces an operator drives, and the checkout self-repair on the
live stage-dispatch path. Only the first had this decision, privately inside a
1150-line service — so the second ran `git worktree remove --force` on any path
git called a linked worktree and destroyed uncommitted work nothing could
regenerate (lg-external-harness-1452).

A move, not a redesign: `WorktreeRemoval`'s fields and `as_payload` are
unchanged, because three operator surfaces read them. What changed is that
there is now one definition of "may this checkout be removed", and both
callers ask it by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import RemovalOutcome, Worktree, WorktreeState
from loregarden.services import worktree_lifecycle
from loregarden.services.checkout_guard import checkout_removal_refusal
from loregarden.services.worktree_lifecycle import KeepReason, Retirement, retire_worktree
from sqlmodel import Session, select

# Bound, not imported: py-organization rejects `from ... import _name`, and AC1
# forbids promoting either helper. Tests patch these names on this module.
_REASON_CLAUSE = worktree_lifecycle._REASON_CLAUSE
_refused = worktree_lifecycle._refused
_service_for = worktree_lifecycle._service_for

#: How a checkout is actually retired, said once. Both the gate's own refusal
#: and the stage-dispatch self-repair's note print it, so an operator who was
#: told "nothing was deleted" is never left to find the supported route
#: themselves — and one spelling means the two cannot drift apart.
RETIREMENT_ROUTE = "Land the branch, or publish the tree, from the ticket that owns it."


@dataclass(frozen=True)
class WorktreeRemoval:
    """What happened to one checkout branch triage was asked to remove.

    A typed outcome rather than "the call did not raise". The three operator
    surfaces that reach this read `outcome` and nothing else to decide what to
    render: `deleted` is green, `refused` is amber and means nothing was lost,
    `parked` is the loud one and carries the only record of where the bytes
    went.

    `reason` is the gate's own member name as a snake_case key — a stable thing
    to group refusals on, where the rendered sentence is not — and is "" on a
    deletion. `detail` is the gate's sentence, which the surface prints.
    """

    outcome: RemovalOutcome
    path: str
    reason: str = ""
    detail: str = ""
    parked_path: str = ""

    def as_payload(self, branch: str) -> dict[str, object]:
        """The removal endpoint's body.

        Built here and not in the route because the route may not name this
        vocabulary: a bare `"refused"` or `"deleted"` written in the API layer
        has nothing behind it the day somebody types `"refuse"`, which is what
        the py-organization gate is for.
        """
        return {
            "branch": branch,
            "removed_path": self.path,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "detail": self.detail,
            "parked_path": self.parked_path,
        }


def _unregenerable_refusal(path: str) -> str | None:
    """What removing the checkout at `path` would lose that no receipt speaks for.

    `checkout_guard` asks the one question the receipt gate cannot. A receipt
    says the branch's work landed; it says nothing about an `.env`, a database
    or a key git was told to ignore, which `git status` never lists and which
    is exactly what somebody keeps in a worktree and nowhere else. It also
    names the repository's main checkout, and fails closed when git cannot say
    which checkout that is.

    Read-only, and a refusal only: an absence here licenses nothing, because
    the removal is still gated on the receipt (AC9). Machine -> Disk asks the
    same module, so the two surfaces cannot hold different bars for one
    directory.
    """
    return checkout_removal_refusal(Path(path))


def _reason_key(outcome: Retirement) -> str:
    """The gate's refusal as the key a client groups on, or "" for a deletion.

    The member's NAME, lowercased: `no_receipt`, `cleanup_failed`. Not the
    value, which is operator copy and changes when the copy is tightened, and
    not the rendered sentence, which is what grouping on prose would mean.
    """
    return "" if outcome.refusal is None else outcome.refusal.name.lower()


def _as_removal(path: str, outcome: Retirement) -> WorktreeRemoval:
    """One gate answer, in the shape the API and the three surfaces read."""
    if outcome.refusal is None:
        return WorktreeRemoval(outcome=RemovalOutcome.DELETED, path=path)
    return WorktreeRemoval(
        outcome=RemovalOutcome.PARKED if outcome.parked_path else RemovalOutcome.REFUSED,
        path=path,
        reason=_reason_key(outcome),
        # The member's own words in front of the gate's sentence. The `reason`
        # key beside it is what a client GROUPS on; this is what it renders,
        # and a reader of one line needs to know which premise fired before
        # they read what it did about it.
        detail=f"{outcome.refusal.value} — {outcome.detail}",
        parked_path=outcome.parked_path,
    )


def no_receipt(path: str) -> WorktreeRemoval:
    """The refusal for a checkout the `worktrees` table does not know about."""
    return WorktreeRemoval(
        outcome=RemovalOutcome.REFUSED,
        path=path,
        reason=KeepReason.NO_RECEIPT.name.lower(),
        detail=(
            f"Nothing was deleted: {path} has no worktrees row, so there is no record "
            f"of whose work it holds or whether that work finished. {RETIREMENT_ROUTE}"
        ),
    )


def checkout_refusal(session: Session, path: str) -> tuple[Worktree | None, WorktreeRemoval | None]:
    """The owner row for `path`, or the refusal that needs no removal attempted.

    A path git has registered and the `worktrees` table does not is a tree
    whose completion cannot be proved: there is no owner to resolve a receipt
    for, no claim to read and no row to record the refusal on. It is REPORTED,
    not performed — removing it anyway is the branch-triage half of exactly the
    bug this gate closes, and it is the half the three operator surfaces
    actually reach.

    `_unregenerable_refusal` is the second one, and the reason it is read here
    rather than at the removal: both are answers about one tree that a caller
    holding several of them can read before it destroys any of them.
    """
    row = session.exec(
        select(Worktree)
        .where(Worktree.worktree_path == path)
        .where(Worktree.state == WorktreeState.ACTIVE)
    ).first()
    if row is None:
        return None, no_receipt(path)
    lost = _unregenerable_refusal(path)
    if lost is not None:
        return row, WorktreeRemoval(
            outcome=RemovalOutcome.REFUSED,
            path=path,
            # The tree holds content that exists nowhere else, which is what
            # this member says; a second spelling of it would be a vocabulary
            # with nothing behind it.
            reason=KeepReason.UNCOMMITTED.name.lower(),
            detail=f"Nothing was deleted: {lost}",
        )
    return row, None


def retire_checkout(session: Session, row: Worktree, path: str) -> WorktreeRemoval:
    """The removal itself, through the receipt gate and nowhere else.

    Containment is judged against the workspace that cut this checkout, not
    against the caller's path. On the live dispatch path the caller is the
    ticket's own worktree, already under `.worktrees/`, and scoping the gate
    from that path names `<ws_parent>/.worktrees/.worktrees` as the base
    (lg-external-harness-1488).
    """
    service = _service_for(session, row.workspace_id)
    if service is None:
        outcome = _refused(
            session,
            row,
            KeepReason.WORKSPACE_GONE,
            _REASON_CLAUSE[KeepReason.WORKSPACE_GONE],
            parked_path="",
        )
        return _as_removal(path, outcome)
    return _as_removal(path, retire_worktree(session, service, row))


def release_checkout(session: Session, path: str) -> WorktreeRemoval:
    """Remove one checkout through the receipt gate, or report why not."""
    row, refusal = checkout_refusal(session, path)
    if refusal is not None:
        return refusal
    if row is None:  # unreachable: a missing row is a refusal above
        return no_receipt(path)
    return retire_checkout(session, row, path)
