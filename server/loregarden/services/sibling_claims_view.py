"""The ticket page's answer to "which open siblings will collide with this ticket?".

`path_claims.sibling_overlaps` already finds them for the prompt and the park;
this shapes the same answer for a person: each sibling, the files it shares
with this ticket and how it claims them, and whether the two are already
ordered, so the page can offer to order the ones that are not.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, TicketState
from loregarden.models.domain.path_claim_enums import PathClaimSource
from loregarden.services.path_claims import sibling_overlaps, ticket_claims
from loregarden.services.sibling_overlap_park import already_ordered
from pydantic import BaseModel
from sqlmodel import Session


class SharedPath(BaseModel):
    path: str
    #: How the sibling claims it: it has changed it, declared it, or planned it.
    source: PathClaimSource


class SiblingClaim(BaseModel):
    ticket_id: str
    external_id: str
    title: str
    state: TicketState
    #: Either ticket already waits for the other, so they cannot run side by side.
    ordered: bool
    paths: list[SharedPath]


class SiblingClaimsView(BaseModel):
    #: How many files this ticket claims; 0 means there is nothing to compare yet.
    claimed_paths: int
    siblings: list[SiblingClaim]


#: Strongest claim first, so a file a sibling has already changed leads.
_SOURCE_ORDER = {
    PathClaimSource.OBSERVED: 0,
    PathClaimSource.DECLARED: 1,
    PathClaimSource.PLANNED: 2,
}


def sibling_claims_view(session: Session, ticket: Ticket) -> SiblingClaimsView:
    claimed = ticket_claims(session, ticket)
    if not claimed:
        return SiblingClaimsView(claimed_paths=0, siblings=[])
    siblings = [
        SiblingClaim(
            ticket_id=overlap.sibling.id,
            external_id=overlap.sibling.external_id,
            title=overlap.sibling.title,
            state=overlap.sibling.state,
            ordered=already_ordered(session, ticket.id, overlap.sibling.id),
            paths=[
                SharedPath(path=claim.path, source=claim.source)
                for claim in sorted(overlap.shared, key=lambda c: (_SOURCE_ORDER[c.source], c.path))
            ],
        )
        for overlap in sibling_overlaps(session, ticket)
    ]
    # Unordered siblings lead: they are the ones that can still collide.
    siblings.sort(key=lambda s: (s.ordered, s.external_id))
    return SiblingClaimsView(claimed_paths=len(claimed), siblings=siblings)
