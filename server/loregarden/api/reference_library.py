"""The reference library: the survey record for the Memory section.

Read-only. Writes to the record go through the survey's MCP tools and the
admission card in the inbox; this router only shows what is there.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.services.reference_survey.errors import SurveyNotFound
from loregarden.services.reference_survey.library import (
    LibraryDetail,
    LibraryList,
    library_detail,
    library_list,
)
from sqlmodel import Session

router = APIRouter(prefix="/reference-library", tags=["reference-library"])


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
