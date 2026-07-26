"""Parent-level integration-review tickets.

A feature/milestone whose children carry the work gets one childless
integration-review child, which runs its own workflow to check the pieces fit
together. It runs last because it *depends on* its siblings (see
TicketDependencyService). Review children are created on persisted tickets — as a
post-commit step in Ticket Studio, and by the standalone backfill — never as
draft items, so the scope preview and imports are left untouched.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, TicketDependency, WorkItemType, Workspace
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select

INTEGRATION_REVIEW_TITLE = "Integration review"
INTEGRATION_REVIEW_DESCRIPTION = (
    "Review that this work item's children integrate correctly as a whole. Added "
    "automatically; it depends on its siblings, so it runs last."
)

# The childless review item slots in as the valid non-bug child of its parent, so
# it runs its own workflow (an aggregator parent runs no stages of its own).
_REVIEW_CHILD_TYPE: dict[WorkItemType, WorkItemType] = {
    WorkItemType.MILESTONE: WorkItemType.FEATURE,
    WorkItemType.FEATURE: WorkItemType.CAPABILITY,
}


def review_child_type(parent_type: WorkItemType | None) -> WorkItemType | None:
    """The work-item type an integration-review child takes under ``parent_type``,
    or None for parents that don't get one (capability/task/bug, or no parent)."""
    if parent_type is None:
        return None
    return _REVIEW_CHILD_TYPE.get(parent_type)


def link_review_to_siblings(
    session: Session, review_ticket_id: str, *, created_by: str = ""
) -> list[TicketDependency]:
    """Make an integration-review ticket depend on each of its non-review siblings,
    so it runs after them. Idempotent (add_dependency dedupes)."""
    review = session.get(Ticket, review_ticket_id)
    if not review or not review.parent_ticket_id:
        return []
    siblings = session.exec(
        select(Ticket).where(
            Ticket.parent_ticket_id == review.parent_ticket_id,
            Ticket.id != review.id,
        )
    ).all()
    dep_svc = TicketDependencyService(session)
    edges: list[TicketDependency] = []
    for sibling in siblings:
        if sibling.is_integration_review:
            continue
        edges.append(dep_svc.add_dependency(review.id, sibling.id, created_by=created_by))
    return edges


def ensure_review_child(session: Session, parent: Ticket, *, created_by: str = "") -> str | None:
    """Ensure ``parent`` (a feature/milestone) has an integration-review child wired
    to depend on its real siblings. Creates one when the parent has real children
    and no review child yet; otherwise (re-)links an existing review child's
    dependencies. Idempotent. Returns a newly created review id, else None.
    """
    review_type = review_child_type(parent.work_item_type)
    if review_type is None or parent.is_integration_review:
        return None
    children = session.exec(select(Ticket).where(Ticket.parent_ticket_id == parent.id)).all()
    if not any(not c.is_integration_review for c in children):
        return None
    existing = [c for c in children if c.is_integration_review]
    if existing:
        for review in existing:
            link_review_to_siblings(session, review.id, created_by=created_by)
        return None
    workspace = session.get(Workspace, parent.workspace_id)
    if not workspace:
        return None
    review = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=INTEGRATION_REVIEW_TITLE,
        work_item_type=review_type,
        parent_ticket_id=parent.id,
        description=INTEGRATION_REVIEW_DESCRIPTION,
        is_integration_review=True,
    )
    link_review_to_siblings(session, review.id, created_by=created_by)
    return review.id


def ensure_reviews_for_tickets(
    session: Session, ticket_ids: list[str], *, created_by: str = ""
) -> list[str]:
    """Ensure an integration-review child for each given ticket that is a
    feature/milestone with children. Used as the Ticket Studio post-commit step."""
    created: list[str] = []
    for tid in ticket_ids:
        ticket = session.get(Ticket, tid)
        if ticket is None:
            continue
        new_id = ensure_review_child(session, ticket, created_by=created_by)
        if new_id:
            created.append(new_id)
    return created


def backfill_integration_reviews(
    session: Session, *, workspace_slug: str | None = None, created_by: str = "backfill"
) -> list[str]:
    """Give every existing feature/milestone parent that has real children — and no
    integration-review child yet — one, wired to depend on its siblings. Existing
    review children just get their sibling dependencies (re-)linked. Idempotent.
    Returns the ids of newly created review tickets.
    """
    parents = session.exec(
        select(Ticket).where(
            Ticket.work_item_type.in_([WorkItemType.FEATURE, WorkItemType.MILESTONE])
        )
    ).all()
    created: list[str] = []
    for parent in parents:
        if workspace_slug:
            ws = session.get(Workspace, parent.workspace_id)
            if not ws or ws.slug != workspace_slug:
                continue
        new_id = ensure_review_child(session, parent, created_by=created_by)
        if new_id:
            created.append(new_id)
    return created
