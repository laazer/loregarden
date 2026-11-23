"""Capture: screenshot a ticket's pages on the running build and file them in its kit.

The review page's Capture button. The pages are the ticket kit's path links
(or paths the reviewer names), the base is the client instance running the
build (`review_preview`), and the picture is taken by Playwright through
`client/scripts/review-capture.mjs`. The shots go through `ingest_review_kit`
like an agent's, so they are stored, size-checked and served by id the same way.

The base URL is resolved here, never taken from the page: what gets
screenshotted is an instance this server launched or found running from the
build's own checkout.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import ArtifactKind, Ticket, Workspace
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.review_kit import (
    MAX_SCREENSHOTS,
    REVIEW_KEY,
    ReviewKitError,
    ingest_review_kit,
)
from loregarden.services.review_preview import preview_status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Per page the script waits up to ~40 s (load + settle); the cap covers a full set.
CAPTURE_TIMEOUT_SECONDS = 60 + 40 * MAX_SCREENSHOTS
SCRIPT = Path("client") / "scripts" / "review-capture.mjs"


class CaptureError(ValueError):
    """Nothing was captured; the message says what to do."""


class CapturePaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(min_length=1, max_length=MAX_SCREENSHOTS)

    @field_validator("paths")
    @classmethod
    def _relative(cls, value: list[str]) -> list[str]:
        for path in value:
            if not path.startswith("/") or path.startswith("//"):
                raise ValueError(f"{path!r}: a path must start with a single '/'")
        return value


class CapturedPage(BaseModel):
    path: str
    captured: bool
    #: Why it was not captured, or a caveat about the picture that was.
    note: str = ""


class CaptureResult(BaseModel):
    base_url: str
    pages: list[CapturedPage]
    artifact_id: str = ""


class _ScriptPage(BaseModel):
    path: str
    file: str
    error: str


class _ScriptOutput(BaseModel):
    pages: list[_ScriptPage]


def _run_script(base_url: str, out_dir: Path, paths: list[str]) -> list[_ScriptPage]:
    client_dir = settings.repo_root / "client"
    script = settings.repo_root / SCRIPT
    try:
        completed = subprocess.run(  # noqa: S603 — argv list, no shell; paths validated above
            ["node", str(script), "--base-url", base_url, "--out", str(out_dir), *paths],  # noqa: S607
            cwd=client_dir,
            capture_output=True,
            text=True,
            timeout=CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CaptureError("node is not installed on this machine; Capture needs it") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"Capture did not finish in {CAPTURE_TIMEOUT_SECONDS} s") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or ["no output"]
        raise CaptureError(f"The capture script failed: {detail[0]}")
    try:
        return _ScriptOutput.model_validate_json(completed.stdout.strip().splitlines()[-1]).pages
    except (ValidationError, IndexError) as exc:
        logger.warning("review-capture printed %r", completed.stdout[-500:])
        raise CaptureError("The capture script's report could not be read") from exc


def capture_pages(
    session: Session, workspace: Workspace, ticket: Ticket, *, ref: str, request: CapturePaths
) -> CaptureResult:
    preview = preview_status(workspace, ref)
    if preview.client is None:
        raise CaptureError(f"No instance of {ref} is running; start a preview first")
    base_url = preview.client.url
    with tempfile.TemporaryDirectory(prefix="review-capture-") as tmp:
        pages = _run_script(base_url, Path(tmp), request.paths)
        shots = [
            {
                "path": page.file,
                "caption": f"{page.path} on {ref}" + (f" ({page.error})" if page.error else ""),
            }
            for page in pages
            if page.file
        ]
        result = [
            CapturedPage(path=page.path, captured=bool(page.file), note=page.error)
            for page in pages
        ]
        if not shots:
            return CaptureResult(base_url=base_url, pages=result)
        try:
            content = ingest_review_kit(
                ticket.id,
                {REVIEW_KEY: {"screenshots": shots}, "captured_from": base_url, "ref": ref},
            )
        except ReviewKitError as exc:
            raise CaptureError(str(exc)) from exc
    artifact = OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind=ArtifactKind.EVIDENCE,
        title=f"Review capture: {len(shots)} page(s) of {ref}",
        content=content,
        evidence_kind="real_surface",
        commit_sha=resolve_head_sha(session, ticket),
    )
    return CaptureResult(base_url=base_url, pages=result, artifact_id=artifact.id)
