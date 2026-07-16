from loregarden.models.domain import (
    CompatibilityPosture,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.compatibility_posture import (
    coerce_posture,
    resolve_compatibility_posture,
)
from sqlmodel import Session


def _ws(db_session: Session, posture: str = "internal") -> Workspace:
    ws = Workspace(slug=f"ws-{posture}-{id(db_session)}", name="WS", compatibility_posture=posture)
    db_session.add(ws)
    db_session.commit()
    return ws


def _ticket(
    db_session: Session,
    ws: Workspace,
    *,
    wtype: WorkItemType,
    posture: str = "",
    parent: Ticket | None = None,
) -> Ticket:
    ticket = Ticket(
        external_id=f"{wtype.value}-{id(object())}",
        workspace_id=ws.id,
        title=wtype.value,
        state=TicketState.BACKLOG,
        work_item_type=wtype,
        parent_ticket_id=parent.id if parent else None,
        compatibility_posture=posture,
    )
    db_session.add(ticket)
    db_session.commit()
    return ticket


def test_coerce_posture_handles_blank_unknown_and_case():
    assert coerce_posture("") is None
    assert coerce_posture(None) is None
    # Unknown must degrade to inherit, never hard-fail an agent run.
    assert coerce_posture("bogus") is None
    assert coerce_posture("  GreenField ") is CompatibilityPosture.GREENFIELD


def test_ticket_posture_wins_over_ancestors_and_workspace(db_session: Session):
    ws = _ws(db_session, "public")
    milestone = _ticket(db_session, ws, wtype=WorkItemType.MILESTONE, posture="internal")
    feature = _ticket(db_session, ws, wtype=WorkItemType.FEATURE, parent=milestone)
    task = _ticket(
        db_session, ws, wtype=WorkItemType.CAPABILITY, parent=feature, posture="greenfield"
    )

    resolved = resolve_compatibility_posture(db_session, task, ws)
    assert resolved.posture is CompatibilityPosture.GREENFIELD
    assert "this capability" in resolved.source


def test_posture_inherits_from_milestone_through_the_parent_chain(db_session: Session):
    """Milestones are tickets, so milestone-level control falls out of the parent
    walk — this is the case the whole three-level design rests on."""
    ws = _ws(db_session, "public")
    milestone = _ticket(db_session, ws, wtype=WorkItemType.MILESTONE, posture="greenfield")
    feature = _ticket(db_session, ws, wtype=WorkItemType.FEATURE, parent=milestone)
    capability = _ticket(db_session, ws, wtype=WorkItemType.CAPABILITY, parent=feature)

    resolved = resolve_compatibility_posture(db_session, capability, ws)
    assert resolved.posture is CompatibilityPosture.GREENFIELD
    assert "inherited from milestone" in resolved.source


def test_nearest_ancestor_wins_over_more_distant_one(db_session: Session):
    ws = _ws(db_session, "public")
    milestone = _ticket(db_session, ws, wtype=WorkItemType.MILESTONE, posture="greenfield")
    feature = _ticket(
        db_session, ws, wtype=WorkItemType.FEATURE, parent=milestone, posture="internal"
    )
    capability = _ticket(db_session, ws, wtype=WorkItemType.CAPABILITY, parent=feature)

    resolved = resolve_compatibility_posture(db_session, capability, ws)
    assert resolved.posture is CompatibilityPosture.INTERNAL
    assert "inherited from feature" in resolved.source


def test_falls_back_to_workspace_default_when_nothing_overrides(db_session: Session):
    ws = _ws(db_session, "public")
    milestone = _ticket(db_session, ws, wtype=WorkItemType.MILESTONE)
    feature = _ticket(db_session, ws, wtype=WorkItemType.FEATURE, parent=milestone)

    resolved = resolve_compatibility_posture(db_session, feature, ws)
    assert resolved.posture is CompatibilityPosture.PUBLIC
    assert "workspace default" in resolved.source


def test_unknown_stored_value_degrades_to_inheritance(db_session: Session):
    ws = _ws(db_session, "greenfield")
    milestone = _ticket(db_session, ws, wtype=WorkItemType.MILESTONE, posture="nonsense")
    feature = _ticket(db_session, ws, wtype=WorkItemType.FEATURE, parent=milestone)

    resolved = resolve_compatibility_posture(db_session, feature, ws)
    assert resolved.posture is CompatibilityPosture.GREENFIELD
    assert "workspace default" in resolved.source


def test_resolution_survives_a_parent_cycle(db_session: Session):
    """Bad data must not hang an agent run."""
    ws = _ws(db_session, "internal")
    a = _ticket(db_session, ws, wtype=WorkItemType.MILESTONE)
    b = _ticket(db_session, ws, wtype=WorkItemType.FEATURE, parent=a)
    a.parent_ticket_id = b.id  # cycle
    db_session.add(a)
    db_session.commit()

    resolved = resolve_compatibility_posture(db_session, b, ws)
    assert resolved.posture is CompatibilityPosture.INTERNAL


def test_every_posture_has_an_agent_facing_contract_that_covers_tests():
    """The contract text IS the instruction the agent follows. Tests are the thing
    agents were treating as an untouchable legacy consumer, so each posture must say
    something explicit about them."""
    from loregarden.models.domain import COMPATIBILITY_POSTURE_CONTRACT

    for posture in CompatibilityPosture:
        contract = COMPATIBILITY_POSTURE_CONTRACT[posture]
        assert contract.strip()
        assert "test" in contract.lower(), f"{posture.value} contract must address tests"


def test_apply_compatibility_posture_validates_and_clears():
    from loregarden.services.compatibility_posture import apply_compatibility_posture

    ticket = Ticket(
        external_id="apply-test",
        workspace_id="ws",
        title="T",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
    )

    apply_compatibility_posture(ticket, " GreenField ")
    assert ticket.compatibility_posture == "greenfield"

    # "" clears the override so the ticket inherits again.
    apply_compatibility_posture(ticket, "")
    assert ticket.compatibility_posture == ""

    # A typo must be rejected, not silently ignored — the operator would otherwise
    # believe they had licensed a change the agent never sees.
    try:
        apply_compatibility_posture(ticket, "whatever")
    except ValueError as exc:
        assert "greenfield" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown posture")
