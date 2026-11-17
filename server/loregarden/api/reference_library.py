"""The reference library: the survey record for the Memory section.

Reads, plus the writes a person makes here: adding a source (a person adding
it is its admission, so no inbox card is filed), reviewing one now, and linking
an initiative so the source's open tickets join it. Those writes and
`loregarden_survey_queue` are one operation (`operations/survey_queue.py`); an
agent's proposal still waits for its admission card. Every other write to the
record goes through the survey's MCP tools.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.api.operation_routes import refusal, run_rest_operation
from loregarden.db.session import get_session
from loregarden.models.domain.survey_enums import SurveyQueueAction
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.operations import OperationId
from loregarden.services.operations.survey_queue import AddSourceResult
from loregarden.services.reference_survey.bulk import BulkAddItem, enqueue_many
from loregarden.services.reference_survey.discovery import (
    DiscoveryIn,
    DiscoveryView,
    start_discovery,
)
from loregarden.services.reference_survey.errors import AlreadyRefused, SurveyError, SurveyNotFound
from loregarden.services.reference_survey.initiatives import (
    LinkResult,
    UnlinkResult,
    follow,
    resolve_initiatives,
)
from loregarden.services.reference_survey.library import (
    LibraryDetail,
    LibraryList,
    LibraryRow,
    library_detail,
    library_list,
    library_row,
)
from loregarden.services.reference_survey.queue import (
    drop_source,
    enqueue_source,
    keep_source,
    promote_resolved_sources,
)
from loregarden.services.reference_survey.rereview import (
    RereviewSettingsIn,
    RereviewSettingsView,
    save_settings,
    settings_view,
)
from loregarden.services.reference_survey.schedule import ReviewNowIn, ReviewNowResult
from loregarden.services.reference_survey.schemas import BulkSourceIn, SourceIn
from loregarden.services.reference_survey.topics import TopicView, topic_list
from pydantic import BaseModel, Field
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


def _already_refused(exc: AlreadyRefused) -> HTTPException:
    # Structured, so the form can link the source it names.
    return HTTPException(status_code=409, detail={"message": str(exc), "source_id": exc.source_id})


#: The statuses these routes always gave the survey's refusals.
_NOT_FOUND = {SurveyNotFound: refusal(404)}
_INVALID = {**_NOT_FOUND, SurveyError: refusal(422)}


@router.post("/sources", response_model=AddSourceResult)
def add_source(body: SourceIn, session: Session = Depends(get_session)) -> AddSourceResult:
    """Add a repository or site. A person adding it here is its admission, so a new
    one is queued with no inbox card; a known one gains the workspaces, as
    `loregarden_survey_queue` enqueue does (where an agent's proposal does wait
    for its card)."""
    return run_rest_operation(
        session,
        OperationId.SURVEY_QUEUE,
        {"action": SurveyQueueAction.ENQUEUE, **body.model_dump()},
        refusals={**_INVALID, AlreadyRefused: _already_refused},
    )


class LinkTargetsRequest(BaseModel):
    #: Workspaces the source should answer from now on.
    workspace_slugs: list[str] = Field(default_factory=list)
    #: Initiatives to follow it, by UUID or external id.
    initiative_ids: list[str] = Field(default_factory=list)
    #: What it should answer for the new workspaces; optional.
    question: str = ""
    queued_reason: str = ""


class LinkTargetsResult(BaseModel):
    #: Workspaces this call made the source serve.
    attached: list[str]
    #: A scoped pass queued because a surveyed source gained a workspace.
    scoped_pass: int | None
    #: What each initiative link did (tickets joined, skipped, settled left out).
    initiatives: list[LinkResult]


@router.post("/{source_id}/links", response_model=LinkTargetsResult)
def link_targets(
    source_id: str, body: LinkTargetsRequest, session: Session = Depends(get_session)
) -> LinkTargetsResult:
    """Link several workspaces and initiatives to a source at once.

    Workspaces join as enqueueing its URL from them would: a surveyed source gets
    one scoped pass, a waiting one reads for them too, one not yet admitted still
    waits for its card. Initiatives follow it (`link_initiative`). Every name is
    checked before anything is written.
    """
    source = session.get(SurveySource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"No survey source {source_id!r}")
    if not body.workspace_slugs and not body.initiative_ids:
        raise HTTPException(status_code=422, detail="Pick at least one workspace or initiative.")
    try:
        initiatives = resolve_initiatives(session, body.initiative_ids)
        attached: list[str] = []
        scoped = None
        if body.workspace_slugs:
            request = SourceIn(
                workspace_slugs=body.workspace_slugs, kind=source.kind, url=source.url,
                name=source.name, question=body.question, queued_reason=body.queued_reason,
            )  # fmt: skip
            added = enqueue_source(session, request, admitted=True)
            attached = added.attached
            scoped = added.scoped_pass.ordinal if added.scoped_pass else None
        linked = follow(session, source.id, initiatives, actor="operator")
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LinkTargetsResult(attached=attached, scoped_pass=scoped, initiatives=linked)


@router.post("/sources/bulk", response_model=list[BulkAddItem])
def add_sources(body: BulkSourceIn, session: Session = Depends(get_session)) -> list[BulkAddItem]:
    """Propose several URLs; each is added on its own and reported on its own, so
    one bad URL is a failed row in the answer, not a failed request. An unknown
    initiative refuses the whole request before any URL is added."""
    try:
        return enqueue_many(session, body)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/auto-rereview", response_model=RereviewSettingsView)
def get_auto_rereview(session: Session = Depends(get_session)) -> RereviewSettingsView:
    """Auto re-review for the whole library: the switch, both numbers, what is due,
    what is running, and the sources a person took out."""
    return settings_view(session)


@router.put("/auto-rereview", response_model=RereviewSettingsView)
def put_auto_rereview(
    body: RereviewSettingsIn, session: Session = Depends(get_session)
) -> RereviewSettingsView:
    return save_settings(session, body)


class AutoRereviewExclusion(BaseModel):
    excluded: bool


@router.get("/topics", response_model=list[TopicView])
def topics() -> list[TopicView]:
    """What people commonly survey for: each fills the add form, or starts a discovery."""
    return topic_list()


@router.post("/discover", response_model=DiscoveryView)
def discover(body: DiscoveryIn, session: Session = Depends(get_session)) -> DiscoveryView:
    """Have an agent search for references on a topic. What it finds waits in the
    queue for a person to keep or drop; refused when no run can start (no carrier
    parent, or a sandbox)."""
    try:
        return start_discovery(session, body)
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class DropRequest(BaseModel):
    #: Why it is not worth reading; optional.
    reason: str = ""


@router.post("/{source_id}/keep", response_model=LibraryRow)
def keep(source_id: str, session: Session = Depends(get_session)) -> LibraryRow:
    """Keep a proposal: it joins the review queue, and its pending card is approved."""
    try:
        return library_row(session, keep_source(session, source_id))
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{source_id}/drop", response_model=LibraryRow)
def drop(source_id: str, body: DropRequest, session: Session = Depends(get_session)) -> LibraryRow:
    """Drop a proposal: refused with the reason, and its pending card rejected."""
    try:
        return library_row(session, drop_source(session, source_id, body.reason))
    except SurveyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurveyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{source_id}/review-now", response_model=ReviewNowResult)
def review_source_now(
    source_id: str, body: ReviewNowIn, session: Session = Depends(get_session)
) -> ReviewNowResult:
    """Put the source first in line and dispatch its pass now, unless the pass limit,
    a sandbox or a missing carrier parent holds it (then `waiting` says which)."""
    return run_rest_operation(
        session,
        OperationId.SURVEY_QUEUE,
        {
            "action": SurveyQueueAction.REVIEW_NOW,
            "source_id": source_id,
            "question": body.question,
            "queued_reason": body.reason,
            "workspace_slugs": body.workspace_slugs,
            "initiative_ids": body.initiative_ids,
            "only_for": body.only_for,
        },
        refusals={**_NOT_FOUND, SurveyError: refusal(409)},
    )


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
    return run_rest_operation(
        session,
        OperationId.SURVEY_QUEUE,
        {
            "action": SurveyQueueAction.LINK_INITIATIVE,
            "source_id": source_id,
            "initiative_id": body.initiative_id,
        },
        refusals=_INVALID,
    )


@router.delete("/{source_id}/initiatives/{initiative_id}", response_model=UnlinkResult)
def unlink(
    source_id: str, initiative_id: str, session: Session = Depends(get_session)
) -> UnlinkResult:
    """Remove the link, and the memberships only this link added."""
    return run_rest_operation(
        session,
        OperationId.SURVEY_QUEUE,
        {
            "action": SurveyQueueAction.UNLINK_INITIATIVE,
            "source_id": source_id,
            "initiative_id": initiative_id,
        },
        refusals=_INVALID,
    )


@router.post("/{source_id}/check-upstream", response_model=LibraryRow)
def check_upstream(source_id: str, session: Session = Depends(get_session)) -> LibraryRow:
    """Read the repository's newest commit now. A check that cannot answer still
    returns 200 with the reason in `upstream_check_error`: the row is the answer."""
    return run_rest_operation(
        session,
        OperationId.SURVEY_QUEUE,
        {"action": SurveyQueueAction.CHECK_UPSTREAM, "source_id": source_id},
        refusals=_NOT_FOUND,
    )


@router.put("/{source_id}/auto-rereview", response_model=LibraryRow)
def exclude_from_auto_rereview(
    source_id: str, body: AutoRereviewExclusion, session: Session = Depends(get_session)
) -> LibraryRow:
    """Take the source out of auto re-review (withdrawing a queued re-review), or put it back."""
    return run_rest_operation(
        session,
        OperationId.SURVEY_QUEUE,
        {
            "action": SurveyQueueAction.SET_AUTO_REREVIEW,
            "source_id": source_id,
            "excluded": body.excluded,
        },
        refusals=_NOT_FOUND,
    )
