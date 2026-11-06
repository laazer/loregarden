"""Whether land verification already refused this landing at these tips (2284).

`land_ticket` verifies a candidate merge before it moves the target, and a
refusal blocks the ticket. The terminal-stage sweep then asked
`landing_would_succeed`, which previewed only the merge: a refused tree merges
cleanly, so the sweep re-landed it on the next pass, the suite refused it
again, and the ticket re-blocked. Of 114 refusals on the live database
(2026-11-06), 107 were re-runs on tips that had not moved, each holding a heavy
capacity lease for up to the 30-minute limit.

A landing is refused *at its tips*: the receipt's candidate merge has the
target's tip as its first parent and the branch's as its second. A new commit
on either side is a different landing and earns another attempt. A failing
suite does not change on its own, so `FAILED` stands until a tip moves.
`UNAVAILABLE` (a timeout, a command that could not start) is a fact about the
machine at the time, so it stands only for `UNAVAILABLE_RETRY_AFTER`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from loregarden.core.timestamps import as_utc
from loregarden.models.domain import Artifact, ArtifactKind, Ticket, TicketState, Workspace
from loregarden.models.domain.enums import GateOutcome, utcnow
from loregarden.services.git_merge_noco import rev
from loregarden.services.land_verification import RECEIPT_FAILED
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How long a "could not run" refusal stands before the same tips may be tried
#: again: long enough that the load which timed it out has usually moved on.
UNAVAILABLE_RETRY_AFTER = timedelta(hours=2)


class _Receipt(BaseModel):
    """The fields of a land verification receipt this reads."""

    model_config = ConfigDict(extra="ignore")

    status: str
    outcome: str = ""
    candidate_sha: str = ""


@dataclass(frozen=True)
class Refusal:
    """One refused land verification and the tips its candidate merged."""

    at: datetime
    outcome: GateOutcome
    target_sha: str
    source_sha: str

    def stands_at(self, now: datetime) -> bool:
        """Whether this refusal still answers for its tips at `now`."""
        if self.outcome == GateOutcome.UNAVAILABLE:
            return as_utc(now) - self.at < UNAVAILABLE_RETRY_AFTER
        return True


def candidate_tips(repo_root: Path, candidate: str) -> tuple[str, str] | None:
    """The (target, source) tips a candidate merge was made from, or None.

    None when the candidate is gone (nothing references a refused candidate,
    so `git gc` may prune it): logged, and read as "unknown", which lets the
    landing be tried again rather than blocking it on a commit nobody can
    inspect.
    """
    if not candidate:
        return None
    target, source = rev(repo_root, f"{candidate}^1"), rev(repo_root, f"{candidate}^2")
    if not target or not source:
        logger.warning(
            "land verification candidate %s has no two parents in %s; its refusal "
            "cannot be tied to tips, so the landing may be retried",
            candidate,
            repo_root,
        )
        return None
    return target, source


def recent_refusals(
    session: Session, ticket: Ticket, repo_root: Path, *, limit: int
) -> list[Refusal]:
    """The ticket's latest refused receipts, newest first, stopping at the first
    receipt that is not a refusal (or that cannot be read or tied to tips)."""
    rows = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.LAND_VERIFICATION)
        .order_by(col(Artifact.created_at).desc())
        .limit(limit)
    ).all()
    refusals: list[Refusal] = []
    for row in rows:
        refusal = _refusal(row, repo_root)
        if refusal is None:
            break
        refusals.append(refusal)
    return refusals


def _refusal(row: Artifact, repo_root: Path) -> Refusal | None:
    try:
        receipt = _Receipt.model_validate(json.loads(row.content_json))
    except (ValueError, ValidationError):
        logger.warning(
            "land verification receipt %s cannot be read; it is not counted as a refusal",
            row.id,
        )
        return None
    if receipt.status != RECEIPT_FAILED:
        return None
    try:
        outcome = GateOutcome(receipt.outcome)
    except ValueError:
        logger.warning(
            "land verification receipt %s has outcome %r; it is not counted as a refusal",
            row.id,
            receipt.outcome,
        )
        return None
    tips = candidate_tips(repo_root, receipt.candidate_sha)
    if tips is None:
        return None
    return Refusal(
        at=as_utc(row.created_at), outcome=outcome, target_sha=tips[0], source_sha=tips[1]
    )


def refused_at_tips(
    session: Session,
    ticket: Ticket,
    repo_root: Path,
    *,
    target_sha: str,
    source_sha: str,
    now: datetime | None = None,
) -> bool:
    """Whether the latest land verification refused exactly these tips, and
    that refusal still stands (see the module docstring)."""
    latest = recent_refusals(session, ticket, repo_root, limit=1)
    if not latest:
        return False
    refusal = latest[0]
    return (
        refusal.target_sha == target_sha
        and refusal.source_sha == source_sha
        and refusal.stands_at(now or utcnow())
    )


@dataclass(frozen=True)
class RetriedRefusal:
    """A landing verified again at the tips an earlier, still-standing refusal named."""

    ticket: Ticket
    retry: Refusal
    first: Refusal


def landings_retried_on_refused_tips(session: Session) -> Sequence[RetriedRefusal]:
    """Blocked tickets whose last two land verifications refused the same tips,
    the second while the first still stood.

    That is a landing retried with nothing changed, which the guard in
    `landing_would_succeed` exists to prevent.
    """
    blocked = session.exec(
        select(Ticket)
        .where(Ticket.state == TicketState.BLOCKED)
        .where(
            col(Ticket.id).in_(
                select(Artifact.ticket_id).where(Artifact.kind == ArtifactKind.LAND_VERIFICATION)
            )
        )
    ).all()
    retried: list[RetriedRefusal] = []
    for ticket in blocked:
        workspace = session.get(Workspace, ticket.workspace_id)
        if workspace is None:
            continue
        pair = recent_refusals(session, ticket, resolve_workspace_root(workspace), limit=2)
        if len(pair) < 2:
            continue
        retry, first = pair
        same_tips = (retry.target_sha, retry.source_sha) == (first.target_sha, first.source_sha)
        if same_tips and first.stands_at(retry.at):
            retried.append(RetriedRefusal(ticket=ticket, retry=retry, first=first))
    return retried
