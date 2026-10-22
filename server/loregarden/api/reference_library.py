"""The reference library: the survey record for the Memory section.

Reads, plus one write a person makes here: linking an initiative to a source
so the source's open tickets join it (agents do the same through
`loregarden_survey_queue`). Every other write to the record goes through the
survey's MCP tools and the admission card in the inbox.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.initiatives import (
    LinkResult,
    UnlinkResult,
    link_initiative,
    unlink_initiative,
)
from loregarden.services.reference_survey.library import (
    LibraryDetail,
    LibraryList,
    library_detail,
    library_list,
)
from loregarden.services.ticket_ids import TicketNotFound
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/reference-library", tags=["reference-library"])


class LinkInitiativeRequest(BaseModel):
    initiative_id: str


@router.get("", response_model=LibraryList)
def list_library(workspace_slug: str, session: Session = Depends(get_session)) -> LibraryList:
    """Every source serving the workspace, queued, surveyed or refused, with counts."""
    try:
        return library_list(session, workspace_slug)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{source_id}", response_model=LibraryDetail)
def source_detail(source_id: str, session: Session = Depends(get_session)) -> LibraryDetail:
    """One source: passes, tickets touched, learnings on those tickets, what it left open."""
    try:
        return library_detail(session, source_id)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{source_id}/initiatives", response_model=LinkResult)
def link(
    source_id: str, body: LinkInitiativeRequest, session: Session = Depends(get_session)
) -> LinkResult:
    """Link an initiative; the source's open tickets join it, now and as passes add more."""
    try:
        return link_initiative(session, source_id, body.initiative_id, actor="operator")
    except (SurveyNotFound, TicketNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{source_id}/initiatives/{initiative_id}", response_model=UnlinkResult)
def unlink(
    source_id: str, initiative_id: str, session: Session = Depends(get_session)
) -> UnlinkResult:
    """Remove the link, and the memberships only this link added."""
    try:
        return unlink_initiative(session, source_id, initiative_id)
    except (SurveyNotFound, TicketNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
