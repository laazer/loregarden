"""Project areas for the reference library, and best-effort mapping onto them.

Its own prefix, not under `/reference-library`, whose `/{source_id}` routes
would swallow `/areas`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.services.reference_survey.area_suggest import (
    AreaMapperReplyError,
    AreaMapperRunError,
    SuggestIn,
    SuggestResult,
    suggest_areas,
)
from loregarden.services.reference_survey.areas import (
    AreaIn,
    AreaUpdate,
    AreaView,
    create_area,
    list_areas,
    update_area,
)
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from sqlmodel import Session

router = APIRouter(prefix="/reference-areas", tags=["reference-library"])


@router.get("", response_model=list[AreaView])
def areas(
    include_archived: bool = False, session: Session = Depends(get_session)
) -> list[AreaView]:
    """Every project area, by workspace."""
    return list_areas(session, include_archived=include_archived)


@router.post("", response_model=AreaView)
def add_area(body: AreaIn, session: Session = Depends(get_session)) -> AreaView:
    try:
        return create_area(session, body)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{area_id}", response_model=AreaView)
def edit_area(area_id: str, body: AreaUpdate, session: Session = Depends(get_session)) -> AreaView:
    try:
        return update_area(session, area_id, body)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/suggest", response_model=SuggestResult)
def suggest(body: SuggestIn, session: Session = Depends(get_session)) -> SuggestResult:
    """Propose the areas each pasted link serves. Writes nothing; one agent turn
    unless `mode` is `keywords`. A failed turn is a 502 naming what broke."""
    try:
        return suggest_areas(session, body)
    except (AreaMapperRunError, AreaMapperReplyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
