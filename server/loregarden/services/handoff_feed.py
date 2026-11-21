"""What each agent handed the next: the handoff artifacts, read back as a feed.

`handoff_store` writes one append-only row per transition. This reads them for a
person: who passed work to whom, on which ticket, and what the checklist said
went with it. Scoped to a ticket (its chain of handoffs), a workspace, or every
workspace, newest first.

A row whose document cannot be read is still listed, carrying the reason. The
feed is the record of who talked to whom, and dropping a row would show a gap in
that conversation as if nothing had been said.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from loregarden.models.domain import Artifact, ArtifactKind, ClaimCertainty, Ticket, Workspace
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlmodel import Session, select


class HandoffItemStatus(StrEnum):
    """Where one item of a handoff checklist stands, as its author reported it.

    The same four `handoff_writer.VALID_STATUSES` accepts on the way in; this is
    the read side's name for them.
    """

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    DEFERRED = "deferred"
    BLOCKED = "blocked"


#: The most a single read returns. A ticket's longest chain on the live
#: database is 32; a workspace has had over a thousand, of which the newest
#: are the ones anyone reads.
MAX_LIMIT = 200


class HandoffItem(BaseModel):
    """One line of a handoff's checklist."""

    item_key: str = ""
    item: str = ""
    required: bool = True
    status: HandoffItemStatus
    evidence: str = ""
    #: An omitted certainty reads as the weak claim, never as proof
    #: (`ClaimCertainty.INFERRED`'s own rule).
    certainty: ClaimCertainty = ClaimCertainty.INFERRED


class _HandoffBody(BaseModel):
    from_agent: str
    to_agent: str
    required_items_met: int | None = None
    total_required_items: int | None = None
    checklist: list[HandoffItem] = []
    note: str = ""


class _HandoffEnvelope(BaseModel):
    handoff: _HandoffBody


class HandoffEntry(BaseModel):
    """One handoff between two agents, as the feed lists it."""

    id: str
    ticket_id: str
    ticket_external_id: str
    ticket_title: str
    workspace_slug: str
    run_id: str | None
    created_at: datetime
    from_agent: str
    to_agent: str
    required_items_met: int | None
    total_required_items: int | None
    checklist: list[HandoffItem]
    note: str
    #: Why the stored document could not be read, or "" when it was. An
    #: unreadable row keeps its place in the feed with empty agents.
    unreadable: str


class HandoffFeed(BaseModel):
    items: list[HandoffEntry]
    #: Every handoff in scope, of which `items` is the newest `limit`.
    total: int


def _parse(content_json: str) -> tuple[_HandoffBody | None, str]:
    """The document's body, or None and why it could not be read.

    `loregarden_write_handoff` stores ``{"handoff": {...}}``. Two live rows were
    attached by hand through the generic artifact tool instead, as the bare body;
    those read too, since their agents and checklist are the same fields.
    """
    try:
        return _HandoffEnvelope.model_validate_json(content_json).handoff, ""
    except ValidationError as wrapped:
        try:
            return _HandoffBody.model_validate_json(content_json), ""
        except ValidationError:
            return None, f"not a handoff document: {wrapped.errors()[0]['msg']}"


def _entry(artifact: Artifact, ticket: Ticket, workspace_slug: str) -> HandoffEntry:
    body, unreadable = _parse(artifact.content_json)
    return HandoffEntry(
        id=artifact.id,
        ticket_id=ticket.id,
        ticket_external_id=ticket.external_id,
        ticket_title=ticket.title,
        workspace_slug=workspace_slug,
        run_id=artifact.run_id,
        created_at=artifact.created_at,
        from_agent=body.from_agent if body else "",
        to_agent=body.to_agent if body else "",
        required_items_met=body.required_items_met if body else None,
        total_required_items=body.total_required_items if body else None,
        checklist=body.checklist if body else [],
        note=body.note if body else "",
        unreadable=unreadable,
    )


def handoff_feed(
    session: Session,
    *,
    ticket_id: str | None = None,
    workspace: Workspace | None = None,
    limit: int = 50,
) -> HandoffFeed:
    """The newest `limit` handoffs on one ticket, in one workspace, or anywhere."""
    limit = max(1, min(limit, MAX_LIMIT))
    scope = [Artifact.kind == ArtifactKind.HANDOFF]
    if ticket_id is not None:
        scope.append(Artifact.ticket_id == ticket_id)
    if workspace is not None:
        scope.append(Ticket.workspace_id == workspace.id)

    total = session.exec(
        select(func.count())
        .select_from(Artifact)
        .join(Ticket, Ticket.id == Artifact.ticket_id)
        .join(Workspace, Workspace.id == Ticket.workspace_id)
        .where(*scope)
    ).one()
    rows = session.exec(
        select(Artifact, Ticket, Workspace.slug)
        .join(Ticket, Ticket.id == Artifact.ticket_id)
        .join(Workspace, Workspace.id == Ticket.workspace_id)
        .where(*scope)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(limit)
    ).all()
    return HandoffFeed(
        items=[_entry(artifact, ticket, slug) for artifact, ticket, slug in rows],
        total=total,
    )
