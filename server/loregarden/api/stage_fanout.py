"""HTTP surface for running a stage N times and keeping one result.

Four verbs, because a fan-out has exactly four moments: launch it, look at it,
promote one attempt, or decline them all. There is no "cancel one attempt" —
the comparison is the product, and a partial set is not one.

Launching blocks until every attempt finishes. That is deliberate: there is
nothing to show until they are all in, and the caller is a human who just asked
for N agent runs.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from loregarden.db.session import get_session
from loregarden.models.domain import StageFanoutGroup, Ticket
from loregarden.services import stage_fanout_groups as groups
from loregarden.services.stage_fanout_service import (
    MAX_ATTEMPTS,
    FanoutError,
    attempt_diffs,
    attempt_file_diff,
    decline_fanout,
    launch_fanout,
    promote_attempt,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tickets/{ticket_id}/fanout", tags=["stage-fanout"])


class LaunchFanoutRequest(BaseModel):
    stage_key: str
    attempt_count: int = Field(default=2, ge=2, le=MAX_ATTEMPTS)
    #: Override the stage's configured agent, for a deliberately different take.
    agent_id: str = ""
    skill_name: str = ""
    auto_approve: bool = False


class DeclineFanoutRequest(BaseModel):
    reason: str = ""


def _ticket(session: Session, ticket_id: str) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.post("")
def launch(
    ticket_id: str = Path(...),
    body: LaunchFanoutRequest = Body(...),
    session: Session = Depends(get_session),
) -> dict:
    """Run the stage `attempt_count` times, each in its own worktree."""
    ticket = _ticket(session, ticket_id)
    try:
        return launch_fanout(
            session,
            ticket,
            body.stage_key,
            body.attempt_count,
            agent_id=body.agent_id,
            skill_name=body.skill_name,
            auto_approve=body.auto_approve,
        )
    except FanoutError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def list_groups(
    ticket_id: str = Path(...),
    session: Session = Depends(get_session),
) -> dict:
    """This ticket's fan-outs, newest first, with the unsettled one called out.

    The review surface needs to know whether there is a decision outstanding
    before it offers to start another one.
    """
    _ticket(session, ticket_id)
    rows = session.exec(
        select(StageFanoutGroup)
        .where(StageFanoutGroup.ticket_id == ticket_id)
        .order_by(StageFanoutGroup.created_at.desc())
    ).all()
    items = [groups.serialize_group(session, row.id) for row in rows]
    open_group = next((item for item in items if item["outcome"] == "pending"), None)
    if open_group is not None:
        open_group["diffs"] = attempt_diffs(session, open_group["id"])
    return {"groups": items, "open_group_id": open_group["id"] if open_group else None}


@router.get("/{group_id}")
def read(
    ticket_id: str = Path(...),
    group_id: str = Path(...),
    session: Session = Depends(get_session),
) -> dict:
    """The group, its attempts, and each attempt's diff against the shared base."""
    _ticket(session, ticket_id)
    try:
        group = groups.serialize_group(session, group_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if group["ticket_id"] != ticket_id:
        raise HTTPException(404, "That fan-out belongs to another ticket")
    group["diffs"] = attempt_diffs(session, group_id)
    return group


@router.get("/{group_id}/attempts/{attempt_id}/file")
def read_file_diff(
    ticket_id: str = Path(...),
    group_id: str = Path(...),
    attempt_id: str = Path(...),
    path: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    """One file's patch from one attempt, fetched when the reader opens it."""
    _ticket(session, ticket_id)
    try:
        return attempt_file_diff(session, group_id, attempt_id, path)
    except FanoutError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{group_id}/promote/{attempt_id}")
def promote(
    ticket_id: str = Path(...),
    group_id: str = Path(...),
    attempt_id: str = Path(...),
    session: Session = Depends(get_session),
) -> dict:
    """Merge this attempt into the ticket's branch; discard the others."""
    _ticket(session, ticket_id)
    try:
        return promote_attempt(session, group_id, attempt_id)
    except FanoutError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{group_id}/decline")
def decline(
    ticket_id: str = Path(...),
    group_id: str = Path(...),
    body: DeclineFanoutRequest = Body(default_factory=DeclineFanoutRequest),
    session: Session = Depends(get_session),
) -> dict:
    """Keep none of them and put the stage back where it was."""
    _ticket(session, ticket_id)
    try:
        return decline_fanout(session, group_id, body.reason)
    except FanoutError as exc:
        raise HTTPException(409, str(exc)) from exc
