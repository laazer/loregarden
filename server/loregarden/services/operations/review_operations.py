"""The Review page's calls, as operations: the queue, a capture, the build
launcher (status, launch, stop), a try-it request, and the browser preview of a
build (which instance runs it, start one, screenshot its pages).

These are a person's tools. An agent's half of review is to attach the kit
(`loregarden_attach_evidence`) and wait at the gate; it has no reason to
launch a build on the operator's machine or read the queue it is in. So each
is offered on REST only.

Launching, stopping, previewing and sending a request write nothing to the
database (a capture writes an evidence row), but
each is a side effect (a process started or killed, a request sent), so they
are declared writes. The reads are only reads.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.organization_gate_service import workspace_for_slug
from loregarden.services.review_capture import CapturePaths, CaptureResult, capture_pages
from loregarden.services.review_config import ConfigStatus, config_status
from loregarden.services.review_kit import capture_path
from loregarden.services.review_launcher import LaunchState, review_launcher
from loregarden.services.review_preview import PreviewView, preview_status, start_preview
from loregarden.services.review_queue import ReviewQueue, build_review_queue
from loregarden.services.review_try import TryRequest, TryResult, send_try_request
from loregarden.services.ticket_ids import resolve_one
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session


class CaptureNotFound(LookupError):  # noqa: N818 — reads as the condition
    """No stored capture under that ticket and id (or the ids are malformed)."""


class QueueQuery(BaseModel):
    workspace_slug: str = ""


class CaptureRef(BaseModel):
    ticket_id: str
    capture_id: str


class WorkspaceRef(BaseModel):
    workspace_slug: str


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_slug: str
    ref: str
    scene: str = ""
    #: A target named in the workspace's review.yaml; may be empty when it has one.
    target: str = ""


class LaunchView(BaseModel):
    config: ConfigStatus
    state: LaunchState | None


class BuildRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_slug: str
    ref: str


class CaptureRequest(CapturePaths):
    workspace_slug: str
    ref: str
    ticket_id: str


def _queue(session: Session, query: QueueQuery, _caller: Caller) -> ReviewQueue:
    return build_review_queue(session, workspace_slug=query.workspace_slug)


def _capture(_session: Session, ref: CaptureRef, _caller: Caller) -> Path:
    path = capture_path(ref.ticket_id, ref.capture_id)
    if path is None:
        raise CaptureNotFound("Capture not found")
    return path


def _launch_status(session: Session, ref: WorkspaceRef, _caller: Caller) -> LaunchView:
    workspace = workspace_for_slug(session, ref.workspace_slug)
    return LaunchView(config=config_status(workspace), state=review_launcher.status(workspace.slug))


def _launch(session: Session, request: LaunchRequest, _caller: Caller) -> LaunchState:
    workspace = workspace_for_slug(session, request.workspace_slug)
    return review_launcher.launch(
        workspace, ref=request.ref, scene=request.scene, target=request.target
    )


def _stop(session: Session, ref: WorkspaceRef, _caller: Caller) -> LaunchView:
    workspace = workspace_for_slug(session, ref.workspace_slug)
    return LaunchView(config=config_status(workspace), state=review_launcher.stop(workspace.slug))


def _try(_session: Session, request: TryRequest, _caller: Caller) -> TryResult:
    return send_try_request(request)


def _preview_status(session: Session, ref: BuildRef, _caller: Caller) -> PreviewView:
    return preview_status(workspace_for_slug(session, ref.workspace_slug), ref.ref)


def _preview_start(session: Session, ref: BuildRef, _caller: Caller) -> PreviewView:
    return start_preview(workspace_for_slug(session, ref.workspace_slug), ref.ref)


def _capture_pages(session: Session, request: CaptureRequest, _caller: Caller) -> CaptureResult:
    workspace = workspace_for_slug(session, request.workspace_slug)
    ticket = resolve_one(session, request.ticket_id, workspace_id=workspace.id)
    return capture_pages(
        session, workspace, ticket, ref=request.ref, request=CapturePaths(paths=request.paths)
    )


_PERSON_ONLY = {
    Transport.MCP: "the operator's review page; an agent attaches its review kit "
    "with loregarden_attach_evidence and waits at the gate",
    Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
}

REVIEW_OPERATIONS = (
    Operation(
        id=OperationId.REVIEW_QUEUE,
        effect=Effect.READ,
        args=QueueQuery,
        handler=_queue,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_CAPTURE,
        effect=Effect.READ,
        args=CaptureRef,
        handler=_capture,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_LAUNCH_STATUS,
        effect=Effect.READ,
        args=WorkspaceRef,
        handler=_launch_status,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_LAUNCH,
        effect=Effect.WRITE,
        args=LaunchRequest,
        handler=_launch,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_LAUNCH_STOP,
        effect=Effect.WRITE,
        args=WorkspaceRef,
        handler=_stop,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_TRY,
        effect=Effect.WRITE,
        args=TryRequest,
        handler=_try,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_PREVIEW_STATUS,
        effect=Effect.READ,
        args=BuildRef,
        handler=_preview_status,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_PREVIEW_START,
        effect=Effect.WRITE,
        args=BuildRef,
        handler=_preview_start,
        unavailable_on=_PERSON_ONLY,
    ),
    Operation(
        id=OperationId.REVIEW_CAPTURE_PAGES,
        effect=Effect.WRITE,
        args=CaptureRequest,
        handler=_capture_pages,
        unavailable_on=_PERSON_ONLY,
    ),
)
