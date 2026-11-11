"""Where a finding goes when no ticket covers its premise: "Adopt from <source>".

A survey lands most findings on the ticket whose premise they answer. A component
or pattern we do not have yet has no such ticket, and filing one meant choosing a
parent the pass had no reason to know. Pass 6 on beluga recorded its command
palette "rather than forced", and the components the operator went looking for
were never filed at all (2026-11-06). So a new ticket may name no parent: it is
filed under one feature per source, "Adopt from <source>", inside one milestone
per workspace. Both are found again by tag, and made the first time they are
needed, so a pass never stops for want of a home and the operator reviews what
one source offered in one place.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_tags import load_tags, serialize_tags
from sqlmodel import Session, col, select

#: The milestone every "Adopt from" feature in a workspace sits under.
FINDINGS_HOME_TAG = "reference-survey-findings"
#: One feature per source, holding what it offered that no ticket covered.
ADOPT_TAG = "survey-adopt"

FINDINGS_HOME_TITLE = "Adopt from other projects"
FINDINGS_HOME_DESCRIPTION = (
    "What reference surveys found in other projects that no ticket here covered. Each "
    "feature under this milestone holds one source's findings; each child is one thing "
    "worth adopting, with its prior art. Triage them: promote, merge or close."
)

_OPEN = (TicketState.DONE, TicketState.WONT_DO)


def _tagged(session: Session, workspace_id: str, tag: str) -> list[Ticket]:
    rows = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace_id,
            col(Ticket.tags_json).contains(tag),
            col(Ticket.state).not_in(_OPEN),
        )
    ).all()
    return [row for row in rows if tag in load_tags(row.tags_json)]


def _tag(session: Session, ticket: Ticket, tag: str) -> Ticket:
    ticket.tags_json = serialize_tags([*load_tags(ticket.tags_json), tag])
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def findings_home(session: Session, workspace: Workspace) -> Ticket:
    """The workspace's milestone for adoptable findings, made the first time it is needed."""
    found = _tagged(session, workspace.id, FINDINGS_HOME_TAG)
    if found:
        return min(found, key=lambda ticket: ticket.created_at)
    home = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=FINDINGS_HOME_TITLE,
        work_item_type=WorkItemType.MILESTONE,
        description=FINDINGS_HOME_DESCRIPTION,
        priority=3,
        created_by="reference-survey",
    )
    return _tag(session, home, FINDINGS_HOME_TAG)


def area_tag(label: str) -> str:
    """The tag a ticket filed for a project area carries: `area:<workspace>/<slug>`."""
    return f"area:{label}"


def tag_ticket(session: Session, ticket: Ticket, tag: str) -> Ticket:
    """Add a tag (once) and commit."""
    if tag in load_tags(ticket.tags_json):
        return ticket
    return _tag(session, ticket, tag)


def adoption_parent(
    session: Session, workspace: Workspace, source: SurveySource, home: Ticket | None = None
) -> Ticket:
    """The "Adopt from <source>" feature, under the findings home; made when absent.

    `home` replaces the workspace's findings home: a project area's home ticket,
    so what one source offered an area is reviewed where that area's work lives.
    Found by its tag, its parent and the source URL its description opens with, so
    two sources with one name never share a feature.
    """
    home = home or findings_home(session, workspace)
    opening = f"Findings from {source.url}"
    for feature in _tagged(session, workspace.id, ADOPT_TAG):
        if feature.parent_ticket_id == home.id and feature.description.startswith(opening):
            return feature
    feature = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=f"Adopt from {source.name}",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=home.id,
        description=(
            f"{opening} that no existing ticket covered. Each child is one thing worth "
            "adopting, with the prior-art block that names the file and commit read."
        ),
        priority=3,
        created_by="reference-survey",
    )
    return _tag(session, feature, ADOPT_TAG)


def adoption_type(requested: WorkItemType) -> WorkItemType:
    """What a parentless new ticket is filed as under the feature: a bug stays a bug,
    anything else is a capability, the smallest type a feature holds."""
    return requested if requested is WorkItemType.BUG else WorkItemType.CAPABILITY
