"""What a handoff claim is worth, and whether it still holds.

A checklist item carries a `certainty` its author wrote and an
`evidence_artifact_id` backing it. Neither says anything on its own once the code
moves: an evidence artifact proves something about the commit it was captured at,
and the stage reading the handoff is looking at a later one.

Staleness is therefore derived here rather than stored, and derived *beside* the
certainty rather than replacing it. AHP+ makes STALE a level of its own, which
throws away the thing you most want to know when you find one — whether the claim
that went stale was artifact-backed or was only ever the agent's word. A
USER_CONFIRMED claim can go stale exactly as a VERIFIED one can, and the two are
not equally worrying.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import Artifact, ClaimCertainty, Ticket
from loregarden.services.evidence import resolve_head_sha
from sqlmodel import Session, SQLModel, select

#: Certainties that count as proof for the met-counter. INFERRED does not: the
#: whole point is that an agent's own word stops satisfying a required item.
PROVEN_CERTAINTIES = frozenset({ClaimCertainty.VERIFIED, ClaimCertainty.USER_CONFIRMED})

#: Certainties that must name an evidence artifact. USER_CONFIRMED is absent —
#: a human's sign-off is the evidence, and there is no artifact to point at.
ARTIFACT_BACKED_CERTAINTIES = frozenset({ClaimCertainty.VERIFIED})


class ClaimStanding(SQLModel):
    """A checklist item's claim as it reads *now*, rather than as it was written."""

    certainty: ClaimCertainty = ClaimCertainty.INFERRED
    #: The evidence artifact was captured against an older commit than the tree
    #: currently holds, so it proves something about code that has since changed.
    #: Always False for a claim with no artifact — nothing to go stale.
    stale: bool = False

    @property
    def proves(self) -> bool:
        """Whether this claim still counts as proof of its item."""
        return self.certainty in PROVEN_CERTAINTIES and not self.stale


def certainty_of(item: dict[str, Any]) -> ClaimCertainty:
    """The level an item claims, defaulting to the weak one.

    An item written before certainties existed, or by an agent that omitted the
    field, is INFERRED. Defaulting the other way would silently promote every
    historical claim to proof.
    """
    raw = item.get("certainty")
    if not raw:
        return ClaimCertainty.INFERRED
    return ClaimCertainty(raw)


def standing_of(
    session: Session,
    ticket: Ticket,
    item: dict[str, Any],
    *,
    head_sha: str = "",
) -> ClaimStanding:
    """How an item's claim reads against the current tree.

    `head_sha` is accepted so a caller walking a whole checklist resolves the
    head once rather than shelling out to git per item.
    """
    certainty = certainty_of(item)
    artifact_id = str(item.get("evidence_artifact_id", ""))
    if certainty not in ARTIFACT_BACKED_CERTAINTIES or not artifact_id:
        return ClaimStanding(certainty=certainty)

    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        # A claim whose proof cannot be found is not proof. The write path
        # rejects this outright; a stored handoff can still reach it if the
        # artifact was deleted afterwards.
        return ClaimStanding(certainty=certainty, stale=True)

    current = head_sha or resolve_head_sha(session, ticket)
    # No sha on either side means there is nothing to compare, not that the
    # evidence is fresh — but calling that stale would flag every workspace whose
    # repo is not readable, so it reads as current and the boundary check is what
    # catches an unreadable tree.
    stale = bool(current and artifact.commit_sha and artifact.commit_sha != current)
    return ClaimStanding(certainty=certainty, stale=stale)


def unresolvable_evidence(
    session: Session, ticket: Ticket, checklist: list[dict[str, Any]]
) -> list[str]:
    """The item_keys claiming VERIFIED without an evidence artifact that exists.

    Returned rather than raised so the caller can report every bad item at once —
    an agent fixing them one exception at a time would burn a turn per item.
    """
    bad: list[str] = []
    for item in checklist:
        if certainty_of(item) not in ARTIFACT_BACKED_CERTAINTIES:
            continue
        artifact_id = str(item.get("evidence_artifact_id", ""))
        artifact = session.get(Artifact, artifact_id) if artifact_id else None
        if artifact is None or artifact.ticket_id != ticket.id:
            bad.append(str(item.get("item_key", "")))
    return bad


def evidence_artifact_ids(session: Session, ticket: Ticket) -> list[str]:
    """Evidence artifacts on this ticket, newest first — what an agent should be
    choosing between when it claims VERIFIED."""
    rows = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == "evidence")
        .order_by(Artifact.created_at.desc())
    ).all()
    return [row.id for row in rows]
