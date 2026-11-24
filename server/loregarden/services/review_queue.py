"""The human review queue: every gate waiting on a person, grouped by build.

Answers "what needs me to look at it, and what do I run to look?". The inbox
already lists the same approvals, but one card at a time with nothing to open:
twelve "Resolve Playtest exit actions" cards for one milestone, each telling the
reviewer to find and run a build on their own.

Tickets in a tree land on their root's integration branch before their human
gate (``Ticket.landed_branch``), so one build of that branch holds every one of
them. Items are grouped by that ref; a ticket that has not landed anywhere is
its own group, built from its own branch.
"""

from __future__ import annotations

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    Workspace,
)
from loregarden.services.acceptance_criteria import load_checked, load_criteria
from loregarden.services.approval_views import approval_to_view
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.gate_checklist import resolve_review_files, review_scenes
from loregarden.services.review_config import (
    LaunchOffer,
    ReviewConfig,
    launch_offer,
    readable_config,
)
from loregarden.services.review_kit import TicketReviewKit, review_kits_for
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select


class ReviewTicket(BaseModel):
    id: str
    external_id: str
    title: str
    description: str
    revision: int
    acceptance_criteria: list[str]
    checked_criteria: list[str]


class ReviewItem(BaseModel):
    approval: dict
    ticket: ReviewTicket
    #: Scene files the ticket's branch changes; None when git could not say.
    scenes: list[str] | None
    #: What runs this ticket's changes; a build's tickets need not share a target.
    launch: LaunchOffer
    kit: TicketReviewKit


class ReviewBuild(BaseModel):
    """One ref to build and the gates a build of it lets a person resolve."""

    workspace_slug: str
    ref: str
    #: True when the ref is an integration branch several tickets landed on.
    combined: bool
    items: list[ReviewItem] = Field(default_factory=list)


class ReviewQueue(BaseModel):
    builds: list[ReviewBuild]
    total: int


def _build_ref(ticket: Ticket) -> tuple[str, bool]:
    if ticket.landed_sha and ticket.landed_branch:
        return ticket.landed_branch, True
    return resolve_ticket_branch(ticket), False


def _review_ticket(ticket: Ticket) -> ReviewTicket:
    criteria = load_criteria(ticket.acceptance_criteria_json)
    return ReviewTicket(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        description=ticket.description,
        revision=ticket.revision,
        acceptance_criteria=criteria,
        checked_criteria=load_checked(ticket.checked_criteria_json, criteria),
    )


def build_review_queue(session: Session, *, workspace_slug: str = "") -> ReviewQueue:
    """Pending human gates, grouped by the ref a reviewer builds to check them."""
    query = (
        select(Approval, Ticket, Workspace)
        .join(Ticket, col(Approval.ticket_id) == col(Ticket.id))
        .join(Workspace, col(Approval.workspace_id) == col(Workspace.id))
        .where(
            Approval.status == ApprovalStatus.PENDING,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
        .order_by(col(Approval.created_at).asc())
    )
    if workspace_slug:
        query = query.where(Workspace.slug == workspace_slug)
    rows = session.exec(query).all()

    kits = review_kits_for(session, [ticket.id for _, ticket, _ in rows])
    builds: dict[tuple[str, str], ReviewBuild] = {}
    configs: dict[str, ReviewConfig | None] = {}
    for approval, ticket, workspace in rows:
        ref, combined = _build_ref(ticket)
        build = builds.setdefault(
            (workspace.slug, ref),
            ReviewBuild(workspace_slug=workspace.slug, ref=ref, combined=combined),
        )
        if workspace.id not in configs:
            configs[workspace.id] = readable_config(workspace)
        files = resolve_review_files(session, ticket)
        build.items.append(
            ReviewItem(
                approval=approval_to_view(session, approval),
                ticket=_review_ticket(ticket),
                # Empty outside workspaces whose playable unit is a scene file.
                scenes=review_scenes(files),
                launch=launch_offer(configs[workspace.id], files),
                kit=kits.get(ticket.id, TicketReviewKit()),
            )
        )
    ordered = sorted(builds.values(), key=lambda b: (-len(b.items), b.workspace_slug, b.ref))
    return ReviewQueue(builds=ordered, total=len(rows))
