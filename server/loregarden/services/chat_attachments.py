"""Files attached to Home chat turns.

An attachment is uploaded before the turn that carries it, stored under
``settings.chat_attachments_dir / <chat session id> / <attachment id>/``, and
named on the user message by id. The directory is per conversation so a fork
can copy it and a delete can remove it without scanning anything else.

What the agent sees depends on the kind. A text file is inlined into the turn's
prompt, which every adapter can read. An image is named by absolute path and the
session directory is granted with ``--add-dir``, which only claude honours — so
an image turn on another adapter is refused at send time instead of running and
answering as if the picture were never there.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
from pathlib import Path
from uuid import uuid4

from loregarden.config import settings
from loregarden.models.domain.enums import ChatAttachmentKind
from loregarden.services.path_resolve import expand_path
from pydantic import BaseModel, TypeAdapter

logger = logging.getLogger(__name__)

#: Images go to the model as files; text is inlined, so it is capped far lower.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_ATTACHMENTS_PER_TURN = 8

IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

#: Text a browser reports as `application/*` or as nothing at all.
TEXT_EXTENSIONS = frozenset(
    {
        ".c", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv", ".diff", ".env.example",
        ".go", ".h", ".html", ".ini", ".java", ".js", ".json", ".jsx", ".kt", ".log",
        ".md", ".patch", ".py", ".rb", ".rs", ".sh", ".sql", ".svg", ".swift", ".toml",
        ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
    }
)  # fmt: skip

_METADATA_FILE = "meta.json"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ChatAttachment(BaseModel):
    """One stored attachment, as the thread and the prompt builder see it."""

    id: str
    name: str
    mime: str
    size: int
    kind: ChatAttachmentKind


_ATTACHMENT_LIST = TypeAdapter(list[ChatAttachment])


class ChatAttachmentError(ValueError):
    """The upload or the reference cannot be accepted; the message says why."""


def attachments_root() -> Path:
    return expand_path(settings.chat_attachments_dir, repo_root=settings.repo_root)


def session_attachments_dir(chat_session_id: str) -> Path:
    return attachments_root() / chat_session_id


def _safe_filename(name: str) -> str:
    base = Path(name).name.strip() or "attachment"
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "attachment"
    return cleaned[:120]


def _suffix(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".env.example"):
        return ".env.example"
    return Path(lowered).suffix


def classify_attachment(name: str, mime: str) -> ChatAttachmentKind:
    """Decide the kind from the declared type and the name, or refuse the file."""
    declared = mime.split(";", 1)[0].strip().lower()
    guessed = (mimetypes.guess_type(name)[0] or "").lower()
    if declared in IMAGE_MIME_TYPES or (not declared and guessed in IMAGE_MIME_TYPES):
        return ChatAttachmentKind.IMAGE
    if declared.startswith("text/") or _suffix(name) in TEXT_EXTENSIONS:
        return ChatAttachmentKind.TEXT
    raise ChatAttachmentError(
        f"'{name}' is not a supported attachment. Attach text files or "
        "PNG, JPEG, GIF or WebP images."
    )


def save_attachment(chat_session_id: str, *, name: str, mime: str, data: bytes) -> ChatAttachment:
    """Validate and store one upload. Raises ``ChatAttachmentError`` on refusal."""
    if not data:
        raise ChatAttachmentError(f"'{name}' is empty.")
    kind = classify_attachment(name, mime)
    limit = MAX_IMAGE_BYTES if kind == ChatAttachmentKind.IMAGE else MAX_TEXT_BYTES
    if len(data) > limit:
        raise ChatAttachmentError(
            f"'{name}' is {len(data) // 1024} KB; the limit for "
            f"{kind.value} attachments is {limit // 1024} KB."
        )
    if kind == ChatAttachmentKind.TEXT:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ChatAttachmentError(f"'{name}' is not UTF-8 text.") from exc

    attachment = ChatAttachment(
        id=uuid4().hex,
        name=_safe_filename(name),
        mime=mime.split(";", 1)[0].strip().lower() or "text/plain",
        size=len(data),
        kind=kind,
    )
    target = session_attachments_dir(chat_session_id) / attachment.id
    target.mkdir(parents=True, exist_ok=False)
    (target / attachment.name).write_bytes(data)
    (target / _METADATA_FILE).write_text(attachment.model_dump_json(), encoding="utf-8")
    return attachment


def attachment_path(chat_session_id: str, attachment: ChatAttachment) -> Path:
    return session_attachments_dir(chat_session_id) / attachment.id / attachment.name


def resolve_attachments(chat_session_id: str, attachment_ids: list[str]) -> list[ChatAttachment]:
    """Load the named uploads of this conversation, in the order given.

    An id that was never uploaded here is refused rather than dropped: a turn
    that silently lost a file would be answered as if it had been read.
    """
    if len(attachment_ids) > MAX_ATTACHMENTS_PER_TURN:
        raise ChatAttachmentError(
            f"A message can carry at most {MAX_ATTACHMENTS_PER_TURN} attachments."
        )
    root = session_attachments_dir(chat_session_id)
    resolved: list[ChatAttachment] = []
    for attachment_id in attachment_ids:
        # An id is a uuid hex we minted; anything else could walk out of `root`.
        if not re.fullmatch(r"[0-9a-f]{32}", attachment_id):
            raise ChatAttachmentError(f"Attachment '{attachment_id}' is not valid.")
        meta = root / attachment_id / _METADATA_FILE
        if not meta.is_file():
            raise ChatAttachmentError(
                f"Attachment '{attachment_id}' was not uploaded to this conversation."
            )
        resolved.append(ChatAttachment.model_validate_json(meta.read_text(encoding="utf-8")))
    return resolved


def dump_attachments_json(attachments: list[ChatAttachment]) -> str:
    return json.dumps([a.model_dump(mode="json") for a in attachments])


def load_attachments_json(raw: str) -> list[ChatAttachment]:
    return _ATTACHMENT_LIST.validate_json(raw or "[]")


def has_image(attachments: list[ChatAttachment]) -> bool:
    return any(a.kind == ChatAttachmentKind.IMAGE for a in attachments)


def image_adapter_refusal(adapter: str) -> str:
    return (
        f"Image attachments need the claude adapter, and this conversation runs "
        f"'{adapter}'. Switch the chat's runtime to claude, or attach text instead."
    )


def render_attachments_for_prompt(chat_session_id: str, attachments: list[ChatAttachment]) -> str:
    """The block appended to the operator's message so the agent can use the files."""
    if not attachments:
        return ""
    sections = ["", "---", "Files the operator attached to this message:"]
    for attachment in attachments:
        path = attachment_path(chat_session_id, attachment)
        if attachment.kind == ChatAttachmentKind.IMAGE:
            sections.append(
                f"\n- Image `{attachment.name}` at `{path}` — open it with your Read "
                "tool before answering."
            )
            continue
        body = path.read_text(encoding="utf-8")
        fence = "````" if "```" in body else "```"
        sections.append(f"\n- `{attachment.name}`:\n{fence}\n{body}\n{fence}")
    return "\n".join(sections)


def copy_session_attachments(source_session_id: str, target_session_id: str) -> None:
    """Give a fork its own copy, so deleting the original cannot break it."""
    source = session_attachments_dir(source_session_id)
    if not source.is_dir():
        return
    shutil.copytree(source, session_attachments_dir(target_session_id), dirs_exist_ok=True)


def delete_session_attachments(chat_session_id: str) -> None:
    target = session_attachments_dir(chat_session_id)
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
    except OSError:
        # The conversation is already gone; a leftover directory costs disk,
        # not correctness, so the delete stands — but say so.
        logger.exception("Could not remove chat attachments at %s", target)
