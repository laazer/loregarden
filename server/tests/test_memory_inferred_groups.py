"""Memory records group by the ticket, milestone and tags they already share."""

from loregarden.models.domain import InferredGroupKind, Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.memory_inferred_groups import GroupableNode, inferred_groups
from sqlmodel import Session, select


def _workspace(db_session: Session) -> Workspace:
    return db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def _ticket(
    db_session: Session, external_id: str, kind: WorkItemType, parent: Ticket | None = None
) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=_workspace(db_session).id,
        title=f"Title {external_id}",
        state=TicketState.IN_PROGRESS,
        work_item_type=kind,
        parent_ticket_id=parent.id if parent else None,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _node(node_id: str, ticket_id: str = "", tags: list[str] | None = None) -> GroupableNode:
    return GroupableNode(id=node_id, ticket_id=ticket_id, tags=tags or [])


def _by_kind(groups, kind: InferredGroupKind) -> dict[str, list[str]]:
    return {g.label: g.node_ids for g in groups if g.kind is kind}


def test_records_from_one_ticket_group_under_its_title(db_session: Session):
    milestone = _ticket(db_session, "grp-ms-1", WorkItemType.MILESTONE)
    task = _ticket(db_session, "grp-task-2", WorkItemType.TASK, parent=milestone)

    groups = inferred_groups(
        db_session,
        [_node("a", task.external_id), _node("b", task.external_id), _node("c")],
        workspace_slug="loregarden",
    )

    assert _by_kind(groups, InferredGroupKind.SAME_TICKET) == {
        "grp-task-2 — Title grp-task-2": ["a", "b"]
    }
    # One ticket under the milestone: the milestone group would only repeat it.
    assert _by_kind(groups, InferredGroupKind.SAME_MILESTONE) == {}


def test_records_from_sibling_tickets_group_under_their_milestone(db_session: Session):
    milestone = _ticket(db_session, "grp-ms-3", WorkItemType.MILESTONE)
    first = _ticket(db_session, "grp-task-4", WorkItemType.TASK, parent=milestone)
    second = _ticket(db_session, "grp-task-5", WorkItemType.TASK, parent=milestone)

    groups = inferred_groups(
        db_session,
        [_node("a", first.external_id), _node("b", second.id)],
        workspace_slug="loregarden",
    )

    assert _by_kind(groups, InferredGroupKind.SAME_MILESTONE) == {"Title grp-ms-3": ["a", "b"]}


def test_generic_tags_are_ignored_and_specific_ones_group(db_session: Session):
    nodes = [
        _node(
            str(i), tags=["learning", "loregarden", "common"] + (["retry-budget"] if i < 2 else [])
        )
        for i in range(10)
    ]

    groups = inferred_groups(db_session, nodes, workspace_slug="loregarden")

    # `learning` is a node type, `loregarden` the workspace, `common` is on every record.
    assert _by_kind(groups, InferredGroupKind.SHARED_TAG) == {"retry-budget": ["0", "1"]}


def test_an_unresolvable_ticket_ref_still_groups_by_the_ref(db_session: Session):
    groups = inferred_groups(
        db_session, [_node("a", "gone-99"), _node("b", "gone-99")], workspace_slug="loregarden"
    )

    assert _by_kind(groups, InferredGroupKind.SAME_TICKET) == {"gone-99": ["a", "b"]}
