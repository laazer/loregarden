"""Request and response bodies for importing tickets from files (`ticket_import_service`).

Split out of `schemas` when it reached its size cap: these are one flow, and
nothing else in `schemas` refers to them.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain.work_item_types import WorkItemType
from sqlmodel import SQLModel


class TicketImportFile(SQLModel):
    name: str
    content: str


class TicketImportItem(SQLModel):
    title: str
    work_item_type: WorkItemType = WorkItemType.TASK
    description: str = ""
    acceptance_criteria: list[str] = []
    priority: int = 3
    milestone: str = ""
    external_id: str = ""
    parent_external_id: str = ""
    parent_ticket_id: str | None = None
    source_format: str = ""
    source_label: str = ""
    preview_markdown: str = ""


class TicketImportPreviewRequest(SQLModel):
    workspace_slug: str
    files: list[TicketImportFile]
    mode: str = "smart"  # "smart" or "regular"


class TicketImportPreviewPathsRequest(SQLModel):
    workspace_slug: str
    file_paths: list[str]


class TicketImportPreviewResponse(SQLModel):
    # `exclude_none` was here as a ConfigDict key, which Pydantic v2 has no such
    # setting for: it was stored and ignored, and every None field serialized
    # anyway. Removing it changes nothing observable — omitting None from this
    # response would be a deliberate API change, not a typo fix. Found by
    # re-enabling `typeddict-unknown-key` (547).

    tickets: list[TicketImportItem]
    errors: list[str]
    warnings: list[str]
    total: int
    by_type: dict[str, int]
    formats: list[str]
    show_preview: bool
    mode: str = "regular"
    studio_context: dict[str, Any] | None = None


class TicketImportRequest(SQLModel):
    workspace_slug: str
    tickets: list[TicketImportItem]


class TicketImportResult(SQLModel):
    created_count: int
    ticket_ids: list[str]
    errors: list[str]
