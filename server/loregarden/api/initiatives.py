"""Cross-workspace reads for initiatives, and their schedules.

Ticket writes are the ticket endpoints': create with `work_item_type=initiative`
and no workspace, attach or detach a milestone with `PATCH /tickets/{id}`
`parent_ticket_id`, delete with `DELETE /tickets/{id}`. The board reads
`GET /tickets?ancestor_ticket_id=`.

What lives here beyond reads is the schedule: the plan (targets, order, mode),
the planner's proposals, and the planner conversation.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import (
    DEFAULT_SPRINT_DAYS,
    MAX_SPRINT_DAYS,
    MIN_SPRINT_DAYS,
    AutopilotUpdate,
    InitiativeMilestoneView,
    InitiativePlanUpdate,
    InitiativePlanView,
    InitiativeSuggestionSet,
    InitiativeView,
    PlannerTurnMode,
    SuggestedItem,
    SuggestionApply,
    SuggestionApplyResult,
    SuggestionRequest,
)
from loregarden.services.initiative_autopilot import (
    mark_needs_person,
    run_autopilot,
    set_autopilot,
    start_ready_work,
)
from loregarden.services.initiative_plan_service import (
    ScheduleValidationError,
    accept_proposal,
    discard_proposal,
    plan_view,
    update_plan,
)
from loregarden.services.initiative_planner_service import (
    PlannerChatSnapshot,
    PlannerConflictError,
    cancel_planner_turn,
    planner_snapshot,
    schedule_planner_turn,
    start_turn,
)
from loregarden.services.initiative_service import (
    attachable_milestones,
    get_initiative,
    list_initiatives,
)
from loregarden.services.initiative_suggestion_agent import regroup_with_agent
from loregarden.services.initiative_suggestions import (
    SuggestionConflictError,
    addable_work,
    apply_suggestions,
    suggest_initiatives,
)
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/initiatives", tags=["initiatives"])

#: Who an edit made on this page is attributed to.
_OPERATOR = "human"


class TicketSelection(BaseModel):
    ticket_ids: list[str]


class NeedsPersonUpdate(TicketSelection):
    needs_person: bool = True


class PlannerMessageCreate(BaseModel):
    content: str = ""
    mode: PlannerTurnMode = PlannerTurnMode.CHAT


@router.get("", response_model=list[InitiativeView])
def list_initiatives_endpoint(session: Session = Depends(get_session)) -> list[InitiativeView]:
    return list_initiatives(session)


@router.get("/attachable-milestones", response_model=list[InitiativeMilestoneView])
def attachable_milestones_endpoint(
    session: Session = Depends(get_session),
) -> list[InitiativeMilestoneView]:
    return attachable_milestones(session)


@router.get("/suggestions", response_model=InitiativeSuggestionSet)
def suggestions_endpoint(
    sprint_days: int = Query(default=DEFAULT_SPRINT_DAYS, ge=MIN_SPRINT_DAYS, le=MAX_SPRINT_DAYS),
    session: Session = Depends(get_session),
) -> InitiativeSuggestionSet:
    """Instant keyword themes plus a paced sprint, from work no initiative owns."""
    return suggest_initiatives(session, days=sprint_days)


@router.post("/suggestions/agent", response_model=InitiativeSuggestionSet)
def agent_suggestions_endpoint(
    body: SuggestionRequest, session: Session = Depends(get_session)
) -> InitiativeSuggestionSet:
    """One agent turn over the same candidates. Synchronous: it holds the request
    for as long as the turn takes, and nothing is stored if it is abandoned."""
    try:
        return regroup_with_agent(session, days=body.sprint_days)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/suggestions/apply", response_model=SuggestionApplyResult, status_code=201)
def apply_suggestions_endpoint(
    body: SuggestionApply, session: Session = Depends(get_session)
) -> SuggestionApplyResult:
    try:
        return apply_suggestions(session, body)
    except SuggestionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{initiative_id}", response_model=InitiativeView)
def get_initiative_endpoint(
    initiative_id: str, session: Session = Depends(get_session)
) -> InitiativeView:
    try:
        return get_initiative(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{initiative_id}/addable-work", response_model=list[SuggestedItem])
def addable_work_endpoint(
    initiative_id: str,
    search: str = Query(min_length=2, max_length=200),
    session: Session = Depends(get_session),
) -> list[SuggestedItem]:
    """Open features and bugs that could join a sprint-style initiative."""
    return addable_work(session, initiative_id, search)


@router.get("/{initiative_id}/plan", response_model=InitiativePlanView)
def get_plan_endpoint(
    initiative_id: str, session: Session = Depends(get_session)
) -> InitiativePlanView:
    try:
        return plan_view(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{initiative_id}/plan", response_model=InitiativePlanView)
def update_plan_endpoint(
    initiative_id: str, body: InitiativePlanUpdate, session: Session = Depends(get_session)
) -> InitiativePlanView:
    try:
        return update_plan(session, initiative_id, body, actor=_OPERATOR)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/{initiative_id}/plan/proposals/{proposal_id}/accept", response_model=InitiativePlanView
)
def accept_proposal_endpoint(
    initiative_id: str, proposal_id: str, session: Session = Depends(get_session)
) -> InitiativePlanView:
    try:
        return accept_proposal(session, initiative_id, proposal_id, actor=_OPERATOR)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{initiative_id}/plan/proposals/{proposal_id}/discard", response_model=InitiativePlanView
)
def discard_proposal_endpoint(
    initiative_id: str, proposal_id: str, session: Session = Depends(get_session)
) -> InitiativePlanView:
    try:
        return discard_proposal(session, initiative_id, proposal_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{initiative_id}/autopilot", response_model=InitiativePlanView)
def update_autopilot_endpoint(
    initiative_id: str, body: AutopilotUpdate, session: Session = Depends(get_session)
) -> InitiativePlanView:
    """Turning it on queues the first batch now rather than at the next tick."""
    try:
        plan = set_autopilot(session, initiative_id, body, actor=_OPERATOR)
        if plan.autopilot:
            run_autopilot(session, initiative_id)
        return plan_view(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{initiative_id}/work", response_model=dict[str, str])
def start_work_endpoint(
    initiative_id: str, body: TicketSelection, session: Session = Depends(get_session)
) -> dict[str, str]:
    """Queue named tickets if the plan says they are ready; returns id -> outcome."""
    try:
        return start_ready_work(session, initiative_id, body.ticket_ids, actor=_OPERATOR)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{initiative_id}/needs-person", response_model=InitiativePlanView)
def needs_person_endpoint(
    initiative_id: str, body: NeedsPersonUpdate, session: Session = Depends(get_session)
) -> InitiativePlanView:
    try:
        mark_needs_person(session, initiative_id, body.ticket_ids, needs_person=body.needs_person)
        return plan_view(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{initiative_id}/planner", response_model=PlannerChatSnapshot)
def planner_snapshot_endpoint(
    initiative_id: str, session: Session = Depends(get_session)
) -> PlannerChatSnapshot:
    try:
        return planner_snapshot(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{initiative_id}/planner/messages", response_model=PlannerChatSnapshot, status_code=202
)
def send_planner_message_endpoint(
    initiative_id: str, body: PlannerMessageCreate, session: Session = Depends(get_session)
) -> PlannerChatSnapshot:
    try:
        assistant = start_turn(session, initiative_id, body.content, mode=body.mode)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlannerConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    schedule_planner_turn(assistant.id)
    # The turn settles in its own session (at once, under LOREGARDEN_SYNC_RUNS);
    # without this the snapshot reads this session's cached pending row.
    session.expire_all()
    return planner_snapshot(session, initiative_id)


@router.post("/{initiative_id}/planner/stop", response_model=PlannerChatSnapshot)
def stop_planner_endpoint(
    initiative_id: str, session: Session = Depends(get_session)
) -> PlannerChatSnapshot:
    try:
        cancel_planner_turn(session, initiative_id)
        return planner_snapshot(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
