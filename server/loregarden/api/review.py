"""The review page's API: the queue, the build launcher, captures, and try-it."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from loregarden.api.operation_bodies import OperationBody
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.services.operations import OperationId
from loregarden.services.operations.review_operations import LaunchView
from loregarden.services.review_kit import capture_mime
from loregarden.services.review_launcher import LaunchState
from loregarden.services.review_queue import ReviewQueue
from loregarden.services.review_try import TryRequest, TryResult
from sqlmodel import Session

router = APIRouter(prefix="/review", tags=["review"])


class LaunchBody(
    OperationBody,
    operation=OperationId.REVIEW_LAUNCH,
    supplied={"workspace_slug"},
    extra="forbid",
):
    """The build to launch: a ref, and optionally one scene in it."""


@router.get("/queue")
def get_review_queue(
    workspace_slug: str = "",
    session: Session = Depends(get_session),
) -> ReviewQueue:
    return run_rest_operation(session, OperationId.REVIEW_QUEUE, {"workspace_slug": workspace_slug})


@router.get("/captures/{ticket_id}/{capture_id}")
def get_capture(
    ticket_id: str,
    capture_id: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    path = run_rest_operation(
        session,
        OperationId.REVIEW_CAPTURE,
        {"ticket_id": ticket_id, "capture_id": capture_id},
    )
    return FileResponse(path, media_type=capture_mime(capture_id))


@router.get("/launch/{workspace_slug}")
def get_launch(workspace_slug: str, session: Session = Depends(get_session)) -> LaunchView:
    return run_rest_operation(
        session, OperationId.REVIEW_LAUNCH_STATUS, {"workspace_slug": workspace_slug}
    )


@router.post("/launch/{workspace_slug}")
def start_launch(
    workspace_slug: str,
    body: LaunchBody,
    session: Session = Depends(get_session),
) -> LaunchState:
    return run_rest_operation(
        session,
        OperationId.REVIEW_LAUNCH,
        {"workspace_slug": workspace_slug, **body.model_dump()},
    )


@router.post("/launch/{workspace_slug}/stop")
def stop_launch(workspace_slug: str, session: Session = Depends(get_session)) -> LaunchView:
    return run_rest_operation(
        session, OperationId.REVIEW_LAUNCH_STOP, {"workspace_slug": workspace_slug}
    )


@router.post("/try")
def try_request(body: TryRequest, session: Session = Depends(get_session)) -> TryResult:
    return run_rest_operation(session, OperationId.REVIEW_TRY, body.model_dump())
