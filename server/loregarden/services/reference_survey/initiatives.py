"""Initiatives that follow a survey source.

Linking an initiative to a source makes the source's open tickets members of
the initiative (a planning relation: no ticket is re-parented, see
`services.initiative_membership`), now and as later passes touch more tickets.
Settled tickets stay out, so the initiative's plan is not padded with finished
work. Unlinking removes the memberships the link added, unless another linked
source added the same ticket too or a person did.

A ticket the initiative already covers (as a member, a child, or inside a
member's subtree) is skipped with the reason, never forced: the membership
rules are the initiative's, and they are reported rather than worked around.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import InitiativeMember, Ticket
from loregarden.models.domain.survey_tables import (
    SurveyInitiativeMember,
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
    SurveySourceInitiative,
)
from loregarden.services.initiative_membership import (
    InitiativeMembershipError,
    add_member,
    remove_member,
)
from loregarden.services.initiative_service import load_initiative
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.ticket_ids import resolve_one
from loregarden.services.ticket_state_service import RESOLVED_STATES
from pydantic import BaseModel
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: `InitiativeMember.added_by` for memberships a source link adds; anything else
#: was added by a person or another path, and unlinking never removes it.
LINK_ACTOR = "reference-survey"


class SkippedTicket(BaseModel):
    ticket: str
    reason: str


class LinkResult(BaseModel):
    initiative_id: str
    initiative: str
    added: list[str]
    skipped: list[SkippedTicket]
    settled_left_out: int


class UnlinkResult(BaseModel):
    initiative: str
    removed: list[str]
    kept: list[str]


class LinkedInitiative(BaseModel):
    id: str
    external_id: str
    title: str
    members_added: int


def _source(session: Session, source_id: str) -> SurveySource:
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    return source


def _initiative(session: Session, ref: str) -> Ticket:
    """An initiative by UUID (what the UI sends) or external id (what agents send)."""
    ticket = session.get(Ticket, ref) or resolve_one(session, ref)
    try:
        return load_initiative(session, ticket.id)
    except LookupError as exc:
        raise SurveyError(f"{ticket.external_id} is not an initiative: {exc}") from exc


def _source_tickets(
    session: Session, source_id: str, only: list[str] | None = None
) -> list[Ticket]:
    query = (
        select(Ticket)
        .join(SurveyPassTicket, col(SurveyPassTicket.ticket_id) == Ticket.id)
        .join(SurveyPass, col(SurveyPass.id) == SurveyPassTicket.pass_id)
        .where(SurveyPass.source_id == source_id)
    )
    if only is not None:
        query = query.where(col(Ticket.id).in_(only))
    return list({t.id: t for t in session.exec(query).all()}.values())


#: Deeper than any real hierarchy (initiative → milestone → feature → capability → task).
_MAX_DEPTH = 16


def _depth(session: Session, ticket: Ticket) -> int:
    depth, current = 0, ticket
    while current.parent_ticket_id and depth < _MAX_DEPTH:
        parent = session.get(Ticket, current.parent_ticket_id)
        if parent is None:
            break
        depth, current = depth + 1, parent
    return depth


def _join(
    session: Session, source: SurveySource, initiative: Ticket, tickets: list[Ticket]
) -> LinkResult:
    added: list[str] = []
    skipped: list[SkippedTicket] = []
    settled = 0
    # Shallowest first: a parent's membership brings its subtree, so a child that
    # joined first would block its parent ("contains … remove it first") and the
    # initiative would track the child alone instead of the whole piece of work.
    for ticket in sorted(tickets, key=lambda t: (_depth(session, t), t.external_id)):
        if ticket.state in RESOLVED_STATES:
            settled += 1
            continue
        if session.get(SurveyInitiativeMember, (source.id, initiative.id, ticket.id)) is not None:
            continue
        existing = session.get(InitiativeMember, (initiative.id, ticket.id))
        if existing is None:
            try:
                add_member(session, initiative.id, ticket.id, actor=LINK_ACTOR)
            except InitiativeMembershipError as exc:
                skipped.append(SkippedTicket(ticket=ticket.external_id, reason=str(exc)))
                continue
            added.append(ticket.external_id)
        elif existing.added_by != LINK_ACTOR:
            # A person tracked it already: it is theirs, so this link records nothing.
            continue
        # Added now, or by another linked source: either way this link holds it too.
        session.add(
            SurveyInitiativeMember(
                source_id=source.id, initiative_id=initiative.id, ticket_id=ticket.id
            )
        )
        session.commit()
    return LinkResult(
        initiative_id=initiative.id,
        initiative=initiative.external_id,
        added=added,
        skipped=skipped,
        settled_left_out=settled,
    )


def link_initiative(
    session: Session, source_id: str, initiative_ref: str, *, actor: str
) -> LinkResult:
    """Link an initiative to a source and bring the source's open tickets into it."""
    source = _source(session, source_id)
    initiative = _initiative(session, initiative_ref)
    if session.get(SurveySourceInitiative, (source.id, initiative.id)) is None:
        session.add(
            SurveySourceInitiative(
                source_id=source.id, initiative_id=initiative.id, linked_by=actor
            )
        )
        session.commit()
    return _join(session, source, initiative, _source_tickets(session, source.id))


def follow_new_tickets(session: Session, source_id: str, ticket_ids: list[str]) -> list[LinkResult]:
    """After a pass lands findings: the tickets it touched join every linked initiative.

    A refusal is reported per ticket and logged, never raised: the findings have
    already landed, and the initiative's own rules decide what it may hold.
    """
    links = session.exec(
        select(SurveySourceInitiative).where(SurveySourceInitiative.source_id == source_id)
    ).all()
    if not links:
        return []
    source = _source(session, source_id)
    tickets = _source_tickets(session, source_id, only=ticket_ids)
    results = []
    for link in links:
        result = _join(session, source, load_initiative(session, link.initiative_id), tickets)
        for skip in result.skipped:
            logger.warning(
                "Survey source %s: %s did not join %s: %s",
                source.url,
                skip.ticket,
                result.initiative,
                skip.reason,
            )
        results.append(result)
    return results


def unlink_initiative(session: Session, source_id: str, initiative_ref: str) -> UnlinkResult:
    """Remove the link and the memberships only it holds."""
    source = _source(session, source_id)
    initiative = _initiative(session, initiative_ref)
    link = session.get(SurveySourceInitiative, (source.id, initiative.id))
    if link is None:
        raise SurveyError(f"{initiative.external_id} is not linked to {source.name or source.url}")
    held = session.exec(
        select(SurveyInitiativeMember).where(
            SurveyInitiativeMember.source_id == source.id,
            SurveyInitiativeMember.initiative_id == initiative.id,
        )
    ).all()
    removed: list[str] = []
    kept: list[str] = []
    for row in held:
        ticket = session.get(Ticket, row.ticket_id)
        name = ticket.external_id if ticket else row.ticket_id
        others = session.exec(
            select(SurveyInitiativeMember.source_id).where(
                SurveyInitiativeMember.initiative_id == initiative.id,
                SurveyInitiativeMember.ticket_id == row.ticket_id,
                SurveyInitiativeMember.source_id != source.id,
            )
        ).first()
        session.delete(row)
        session.commit()
        member = session.get(InitiativeMember, (initiative.id, row.ticket_id))
        if others is None and member is not None and member.added_by == LINK_ACTOR:
            remove_member(session, initiative.id, row.ticket_id)
            removed.append(name)
        else:
            kept.append(name)
    session.delete(link)
    session.commit()
    return UnlinkResult(
        initiative=initiative.external_id, removed=sorted(removed), kept=sorted(kept)
    )


def linked_initiatives(session: Session, source_id: str) -> list[LinkedInitiative]:
    links = session.exec(
        select(SurveySourceInitiative, Ticket)
        .join(Ticket, col(Ticket.id) == SurveySourceInitiative.initiative_id)
        .where(SurveySourceInitiative.source_id == source_id)
        .order_by(col(SurveySourceInitiative.linked_at))
    ).all()
    views = []
    for link, initiative in links:
        held = session.exec(
            select(SurveyInitiativeMember.ticket_id).where(
                SurveyInitiativeMember.source_id == source_id,
                SurveyInitiativeMember.initiative_id == link.initiative_id,
            )
        ).all()
        views.append(
            LinkedInitiative(
                id=initiative.id,
                external_id=initiative.external_id,
                title=initiative.title,
                members_added=len(held),
            )
        )
    return views
