"""Group memory records by what they already share, for the knowledge browser.

Measured on loregarden (2026-09-28): 33 records, 0 recorded relations. Agents
almost never call `create_memory_relation`, so a map drawn from recorded edges
alone is a scatter of unconnected dots — and a reader asking "what else do we
know about this?" gets nothing, although the answer is sitting in the records:
three learnings written on one ticket, five on tickets under one milestone.

These groups come from facts each record carries (its ticket, that ticket's
milestone, its tags). They are returned beside the recorded relations, never
merged into them, so the browser can draw them differently and a guess never
passes for an assertion.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from loregarden.models.domain import InferredGroupKind, MemoryNodeType, Ticket, Workspace
from loregarden.services import ticket_ids
from pydantic import BaseModel
from sqlmodel import Session, select

#: A tag on more than this share of the window says nothing about any one record
#: (`learning`, the workspace slug). Never below `_MIN_GENERIC_COUNT` records, so
#: a small window does not discard every tag.
_GENERIC_TAG_SHARE = 0.4
_MIN_GENERIC_COUNT = 4


class InferredGroup(BaseModel):
    kind: InferredGroupKind
    #: Stable identity within one response: the ticket ref, milestone id or tag.
    key: str
    #: What a person reads: the ticket or milestone title, or the tag.
    label: str
    node_ids: list[str]


class GroupableNode(BaseModel):
    """The two facts grouping needs from a graph node."""

    id: str
    ticket_id: str
    tags: list[str]


def _groups_of(
    kind: InferredGroupKind, members: dict[str, list[str]], labels: dict[str, str]
) -> list[InferredGroup]:
    return [
        InferredGroup(kind=kind, key=key, label=labels.get(key, key), node_ids=sorted(ids))
        for key, ids in sorted(members.items())
        if len(ids) >= 2
    ]


def _resolve_tickets(
    session: Session, refs: Iterable[str], workspace_id: str | None
) -> dict[str, Ticket]:
    resolved: dict[str, Ticket] = {}
    for ref in refs:
        ticket = session.get(Ticket, ref) or ticket_ids.resolve(
            session, ref, workspace_id=workspace_id
        )
        if ticket is not None:
            resolved[ref] = ticket
    return resolved


def _by_ticket_and_milestone(
    session: Session, nodes: list[GroupableNode], workspace_id: str | None
) -> list[InferredGroup]:
    by_ref: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.ticket_id:
            by_ref[node.ticket_id].append(node.id)
    tickets = _resolve_tickets(session, by_ref, workspace_id)

    by_milestone: dict[str, list[str]] = defaultdict(list)
    milestone_refs: dict[str, set[str]] = defaultdict(set)
    milestone_titles: dict[str, str] = {}
    for ref, ticket in tickets.items():
        milestone = ticket_ids.ancestor_milestone(session, ticket)
        if milestone is None:
            continue
        by_milestone[milestone.id].extend(by_ref[ref])
        milestone_refs[milestone.id].add(ref)
        milestone_titles[milestone.id] = milestone.title
    # A milestone reached through a single ticket says nothing the ticket group
    # does not already say.
    spanning = {mid: ids for mid, ids in by_milestone.items() if len(milestone_refs[mid]) >= 2}

    ticket_titles = {
        ref: f"{ticket.external_id} — {ticket.title}" for ref, ticket in tickets.items()
    }
    return _groups_of(InferredGroupKind.SAME_TICKET, by_ref, ticket_titles) + _groups_of(
        InferredGroupKind.SAME_MILESTONE, spanning, milestone_titles
    )


def _by_tag(nodes: list[GroupableNode], workspace_slug: str) -> list[InferredGroup]:
    ignored = {workspace_slug, *(node_type.value for node_type in MemoryNodeType)}
    by_tag: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for tag in set(node.tags) - ignored:
            by_tag[tag].append(node.id)
    ceiling = max(_MIN_GENERIC_COUNT, int(len(nodes) * _GENERIC_TAG_SHARE))
    specific = {tag: ids for tag, ids in by_tag.items() if len(ids) <= ceiling}
    return _groups_of(InferredGroupKind.SHARED_TAG, specific, {})


def inferred_groups(
    session: Session, nodes: list[GroupableNode], *, workspace_slug: str
) -> list[InferredGroup]:
    """Every group of two or more `nodes` sharing a ticket, milestone, or specific tag."""
    if len(nodes) < 2:
        return []
    workspace_id = session.exec(
        select(Workspace.id).where(Workspace.slug == workspace_slug)
    ).first()
    return _by_ticket_and_milestone(session, nodes, workspace_id) + _by_tag(nodes, workspace_slug)
