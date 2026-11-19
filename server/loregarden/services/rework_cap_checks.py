"""A rework pause stands only on rounds that were charged to the budget.

A pause is raised for one of two reasons (`ReworkStopReason`): the target
stage's charged rounds passed `MAX_REWORK_REROUTES`, or the last two rounds
repeated one finding against one tree. A pending pause backed by neither was
raised on rounds that should not have counted, which is what free hand-offs
between sibling implementers used to be (`rework_routing`).
"""

from __future__ import annotations

from datetime import date

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    InvariantReentry,
    Ticket,
)
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    rework_is_stuck,
    rework_reroute_count,
)
from loregarden.services.rework_pause import rework_pause_target
from sqlmodel import Session, select


def pauses_on_uncharged_rounds(session: Session) -> list[Violation]:
    found: list[Violation] = []
    pending = session.exec(
        select(Approval).where(
            Approval.kind == ApprovalKind.REWORK_PAUSE,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).all()
    for approval in pending:
        target = rework_pause_target(approval.tool_input_json or "")
        ticket = session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
        if not target or ticket is None:
            continue
        charged = rework_reroute_count(session, ticket, target)
        if charged > MAX_REWORK_REROUTES or rework_is_stuck(session, ticket, target):
            continue
        found.append(
            Violation(
                subject_id=approval.id,
                ticket_id=ticket.id,
                entered_at=approval.created_at,
                detail=(
                    f"{ticket.external_id} is paused on '{target}' with {charged} charged "
                    f"round(s), at most {MAX_REWORK_REROUTES}, and its last two rounds differ"
                ),
            )
        )
    return found


REWORK_PAUSES_STAND_ON_CHARGED_ROUNDS = Invariant(
    name="rework_pauses_stand_on_charged_rounds",
    ticket="lg-code-knowledge-1084",
    statement=(
        "no pending rework pause has at most MAX_REWORK_REROUTES charged rounds on its "
        "target stage unless its last two rounds repeat one finding against one tree"
    ),
    detect=pauses_on_uncharged_rounds,
    # Report-only: resolving a pause re-dispatches the work, which is the
    # operator's call even when the pause should not have been raised.
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 19),
    prevented_by=(
        "server/tests/test_rework_routing.py"
        "::test_a_sibling_handoff_is_not_charged_to_the_rework_cap"
    ),
    unrepaired_action=(
        "Resolve the pause with Retry: its rounds were hand-offs between implementers, "
        "not rejections, so the budget it claims was spent never was"
    ),
)
