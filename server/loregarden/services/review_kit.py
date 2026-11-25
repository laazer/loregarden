"""What an agent hands a human reviewer: screenshots, links, and endpoint checks.

A human gate used to show its ticket's description and a "notes only" checklist,
so a reviewer had to find the build, the page, and the request on their own.
An agent that has just made the change knows all three. It attaches them as
`real_surface` evidence with a ``review`` block in ``content_json``:

    {"review": {
        "scene": "scenes/levels/sandbox/latch_3d.tscn",
        "steps": [{"action": "Walk to the enemy's left side and press C",
                   "expect": "The body flashes pale blue for a moment",
                   "wrong_if": "No flash, or the flash is red"}],
        "screenshots": [{"path": "/tmp/shot.png", "caption": "Start view"}],
        "links": [{"url": "http://127.0.0.1:5173/work-items", "label": "Work items"}],
        "requests": [{"name": "List", "method": "GET", "path": "/api/tickets",
                      "expect_status": 200}]
    }}

``scene`` is what the reviewer runs: the review page's launch button otherwise
starts the build at its own start screen, which is rarely the scene the steps
and screenshots are about. ``steps`` is what they do once it runs. A kit with
``replaces_earlier`` hides every older kit on the ticket, for when those
pictures or steps describe a build that no longer exists.

Screenshots are copied into ``settings.review_captures_dir`` when attached and
the path is replaced by a capture id, so the review page serves only bytes the
control plane stored itself and never a path an agent named.
"""

from __future__ import annotations

import logging
import re
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from loregarden.config import settings
from loregarden.models.domain import Artifact, ArtifactKind
from loregarden.services.path_resolve import expand_path
from loregarden.services.review_launcher import is_scene_path
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: The key inside an evidence artifact's ``content_json`` that holds the kit.
REVIEW_KEY = "review"
MAX_CAPTURE_BYTES = 10 * 1024 * 1024
MAX_SCREENSHOTS = 12
MAX_REQUESTS = 20
#: A reviewer works through these by hand; more is a test suite, not a review.
MAX_STEPS = 12

#: Leading bytes of each image type a browser renders, and its extension.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)
_CAPTURE_ID = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$")
_TICKET_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


class ReviewKitError(ValueError):
    """The kit cannot be accepted; the message names what to fix."""


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


def _scene_or_blank(value: str) -> str:
    if value and not is_scene_path(value):
        raise ValueError("a scene must be a .tscn/.scn path inside the project")
    return value


class ReviewStep(BaseModel):
    """One thing the reviewer does, and what they see if the change works."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    expect: str = Field(min_length=1)
    #: What a broken build would show instead, when that is not just "not that".
    wrong_if: str = ""


class ReviewScreenshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: str
    caption: str = ""
    #: The scene pictured, so the reviewer can open the same one.
    scene: str = ""


def _single_slash_path(value: str) -> str:
    # A path, never a URL: the base is an instance the reviewer picks, so an
    # agent cannot point the reviewer's click at a host of its choosing.
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("a path must start with a single '/'")
    return value


class ReviewLink(BaseModel):
    """A page to look at: a ``path`` on a running instance of the build, or a ``url``.

    Prefer ``path``. A URL names one port, and the instance on it is usually
    gone by the time a person reviews; the review page resolves a path against
    whichever instance of the build's branch is running then.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = ""
    path: str = ""
    label: str = ""

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("a review link must be an http(s) URL")
        return value

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _single_slash_path(value) if value else value

    @model_validator(mode="after")
    def _exactly_one(self) -> ReviewLink:
        if bool(self.url) == bool(self.path):
            raise ValueError("a review link needs exactly one of `path` or `url`")
        return self


class ReviewRequest(BaseModel):
    """One endpoint call a reviewer can send from the review page."""

    model_config = ConfigDict(extra="forbid")

    name: str
    method: HttpMethod = HttpMethod.GET
    path: str
    body: str = ""
    expect_status: int | None = None

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        return _single_slash_path(value)


class _KitGuide(BaseModel):
    """The parts of a kit that say what to run and do, as written and as stored."""

    model_config = ConfigDict(extra="forbid")

    scene: str = ""
    steps: list[ReviewStep] = Field(default_factory=list, max_length=MAX_STEPS)
    replaces_earlier: bool = False

    @field_validator("scene")
    @classmethod
    def _playable(cls, value: str) -> str:
        return _scene_or_blank(value)


class ReviewKit(_KitGuide):
    screenshots: list[ReviewScreenshot] = Field(default_factory=list)
    links: list[ReviewLink] = Field(default_factory=list)
    requests: list[ReviewRequest] = Field(default_factory=list)


class _ScreenshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    caption: str = ""
    scene: str = ""

    @field_validator("scene")
    @classmethod
    def _playable(cls, value: str) -> str:
        return _scene_or_blank(value)


class _ReviewKitIn(_KitGuide):
    """The kit as an agent writes it: screenshots by path, not yet stored."""

    screenshots: list[_ScreenshotIn] = Field(default_factory=list, max_length=MAX_SCREENSHOTS)
    links: list[ReviewLink] = Field(default_factory=list)
    requests: list[ReviewRequest] = Field(default_factory=list, max_length=MAX_REQUESTS)


def captures_root() -> Path:
    return expand_path(settings.review_captures_dir, repo_root=settings.repo_root)


def snapshot_captures(target: Path) -> Path:
    """Copy every stored screenshot to ``target``, for a server on a database copy.

    The database names screenshots by id and the files live outside it, so a
    copied database without them shows a broken image for each one. ``target``
    is replaced, and exists (empty) when nothing has been captured, so that
    server never reads or writes the original's files.
    """
    source = captures_root().resolve()
    resolved = target.resolve()
    if resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise ValueError(f"refusing to replace {target}: it overlaps the live captures in {source}")
    if target.exists():
        shutil.rmtree(target)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        target.mkdir(parents=True)
    return target


def _sniff_image(data: bytes) -> tuple[str, str]:
    for signature, mime, suffix in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime, suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ReviewKitError("is not a PNG, JPEG, GIF or WebP image")


def _store_capture(ticket_id: str, source: Path) -> str:
    if not source.is_file():
        raise ReviewKitError(f"screenshot {source} does not exist or is not a file")
    size = source.stat().st_size
    if size == 0 or size > MAX_CAPTURE_BYTES:
        raise ReviewKitError(
            f"screenshot {source} is {size} bytes; it must be 1..{MAX_CAPTURE_BYTES}"
        )
    data = source.read_bytes()
    try:
        _mime, suffix = _sniff_image(data)
    except ReviewKitError as exc:
        raise ReviewKitError(f"screenshot {source} {exc}") from exc
    capture_id = f"{uuid4().hex}{suffix}"
    target_dir = captures_root() / ticket_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / capture_id).write_bytes(data)
    return capture_id


def ingest_review_kit(ticket_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """``content`` with its ``review`` block validated and screenshots stored.

    Content without a ``review`` key is returned untouched. A malformed kit is
    refused whole (``ReviewKitError``) rather than stored half-read: evidence a
    reviewer cannot open is worse than an attach that told the agent why.
    """
    raw = content.get(REVIEW_KEY)
    if raw is None:
        return content
    try:
        incoming = _ReviewKitIn.model_validate(raw)
    except ValidationError as exc:
        raise ReviewKitError(f"content_json.review is malformed: {exc}") from exc
    stored: list[str] = []
    try:
        screenshots = [
            ReviewScreenshot(
                capture_id=_remember(stored, _store_capture(ticket_id, Path(item.path))),
                caption=item.caption,
                scene=item.scene or incoming.scene,
            )
            for item in incoming.screenshots
        ]
    except (ReviewKitError, OSError):
        _discard(ticket_id, stored)
        raise
    kit = ReviewKit(
        scene=incoming.scene,
        steps=incoming.steps,
        replaces_earlier=incoming.replaces_earlier,
        screenshots=screenshots,
        links=incoming.links,
        requests=incoming.requests,
    )
    return {**content, REVIEW_KEY: kit.model_dump(mode="json")}


def _remember(stored: list[str], capture_id: str) -> str:
    stored.append(capture_id)
    return capture_id


def _discard(ticket_id: str, capture_ids: list[str]) -> None:
    for capture_id in capture_ids:
        try:
            (captures_root() / ticket_id / capture_id).unlink()
        except FileNotFoundError:  # silent-ok: already gone is the goal of this cleanup
            pass


def capture_path(ticket_id: str, capture_id: str) -> Path | None:
    """The stored capture file, or None when the ids are malformed or it is absent."""
    if not _TICKET_ID.match(ticket_id) or not _CAPTURE_ID.match(capture_id):
        return None
    path = captures_root() / ticket_id / capture_id
    return path if path.is_file() else None


def capture_mime(capture_id: str) -> str:
    suffix = Path(capture_id).suffix
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }[suffix]


class TicketReviewKit(BaseModel):
    """Every kit attached to one ticket, merged, newest artifact first.

    ``scene`` and ``steps`` come whole from the newest kit that has them: two
    rounds' steps interleaved would describe neither build.
    """

    scene: str = ""
    steps: list[ReviewStep] = Field(default_factory=list)
    screenshots: list[ReviewScreenshot] = Field(default_factory=list)
    links: list[ReviewLink] = Field(default_factory=list)
    requests: list[ReviewRequest] = Field(default_factory=list)
    #: Kits that no longer parse, by artifact id — shown, never dropped quietly.
    unreadable: list[str] = Field(default_factory=list)


def review_kits_for(session: Session, ticket_ids: list[str]) -> dict[str, TicketReviewKit]:
    """Merged review kits per ticket id, for the tickets that have any evidence."""
    if not ticket_ids:
        return {}
    rows = session.exec(
        select(Artifact)
        .where(col(Artifact.ticket_id).in_(ticket_ids), Artifact.kind == ArtifactKind.EVIDENCE)
        .order_by(col(Artifact.created_at).desc())
    ).all()
    kits: dict[str, TicketReviewKit] = {}
    replaced: set[str] = set()
    for artifact in rows:
        if artifact.ticket_id in replaced:
            continue
        if f'"{REVIEW_KEY}"' not in artifact.content_json:
            continue
        merged = kits.setdefault(artifact.ticket_id, TicketReviewKit())
        try:
            kit = _ArtifactContent.model_validate_json(artifact.content_json).review
        except ValidationError:
            logger.warning("review kit on artifact %s does not parse", artifact.id)
            merged.unreadable.append(artifact.id)
            continue
        if kit is None:
            continue
        merged.scene = merged.scene or kit.scene
        merged.steps = merged.steps or kit.steps
        merged.screenshots.extend(kit.screenshots)
        merged.links.extend(kit.links)
        merged.requests.extend(kit.requests)
        if kit.replaces_earlier:
            replaced.add(artifact.ticket_id)
    return kits


class _ArtifactContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    review: ReviewKit | None = None
