"""The reference library: the survey record for the Memory section.

Reads, plus the writes a person makes here: proposing a source (it still waits
for its admission card in the inbox), reviewing one now, and linking an
initiative so the source's open tickets join it. Agents do the same through
`loregarden_survey_queue`; every other write to the record goes through the
survey's MCP tools.
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
    LibraryRow,
    library_detail,
    library_list,
    library_row,
)
from loregarden.services.reference_survey.queue import enqueue_source, promote_resolved_sources
from loregarden.services.reference_survey.schedule import ReviewNowIn, ReviewNowResult, review_now
from loregarden.services.reference_survey.schemas import SourceIn
from loregarden.services.reference_survey.upstream import check_upstream_by_id
from loregarden.services.ticket_ids import TicketNotFound
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/reference-library", tags=["reference-library"])


class LinkInitiativeRequest(BaseModel):
    initiative_id: str


@router.get("", response_model=LibraryList)
def list_library(workspace_slug: str, session: Session = Depends(get_session)) -> LibraryList:
    """Every source serving the workspace, queued, surveyed or refused, with counts."""
    # A card resolved in the inbox takes effect here too, not only at the feeder's
    # next tick: an approved source reads "queued", never "candidate" in between,
    # and a server with no clock running (a sandbox) still shows the decision.
    promote_resolved_sources(session)
    try:
        return library_list(session, workspace_slug)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AddSourceResult(BaseModel):
    source: LibraryRow
    #: Workspaces this call added to the source (empty when it already served them all).
    attached: list[str]
    #: A scoped pass was queued because a surveyed source gained a workspace.
    scoped_pass: int | None


@router.post("/sources", response_model=AddSourceResult)
def add_source(body: SourceIn, session: Session = Depends(get_session)) -> AddSourceResult:
    """Propose a repository or site. A new one waits for its admission card in the
    inbox; a known one gains the workspaces, as `loregarden_survey_queue` does."""
    try:
        added = enqueue_source(session, body)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AddSourceResult(
        source=library_row(session, added.source),
        attached=added.attached,
        scoped_pass=added.scoped_pass.ordinal if added.scoped_pass else None,
    )


@router.post("/{source_id}/review-now", response_model=ReviewNowResult)
def review_source_now(
    source_id: str, body: ReviewNowIn, session: Session = Depends(get_session)
) -> ReviewNowResult:
    """Put the source first in line and dispatch its pass now, unless the pass limit,
    a sandbox or a missing carrier parent holds it (then `waiting` says which)."""
    try:
        return review_now(session, source_id, body)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{source_id}", response_model=LibraryDetail)
def source_detail(source_id: str, session: Session = Depends(get_session)) -> LibraryDetail:
    """One source: passes, tickets touched, learnings on those tickets, what it left open."""
    promote_resolved_sources(session)
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


@router.post("/{source_id}/check-upstream", response_model=LibraryRow)
def check_upstream(source_id: str, session: Session = Depends(get_session)) -> LibraryRow:
    """Read the repository's newest commit now. A check that cannot answer still
    returns 200 with the reason in `upstream_check_error`: the row is the answer."""
    try:
        return library_row(session, check_upstream_by_id(session, source_id))
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
