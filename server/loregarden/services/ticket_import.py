"""Parse and import tickets from markdown, JSON, and YAML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

import yaml
from loregarden.models.domain import TicketImportItem, WorkItemType

_MD_TICKET_RE = re.compile(r"^#\s*TICKET:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_MD_TITLE_RE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_MD_TYPE_RE = re.compile(
    r"^(?:Work item type|Work Item Type|Type):\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_MD_PRIORITY_RE = re.compile(r"^Priority:\s*(\d+)$", re.MULTILINE | re.IGNORECASE)
_MD_MILESTONE_RE = re.compile(r"^Milestone:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_MD_PARENT_RE = re.compile(
    r"^(?:Parent(?: external id)?|Parent ticket):\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class ParsedImportBatch:
    tickets: list[TicketImportItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _detect_format(name: str, content: str) -> str:
    suffix = PurePath(name).suffix.lower()
    if suffix == ".md":
        return "md"
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"

    stripped = content.lstrip()
    if _MD_TICKET_RE.search(content):
        return "md"
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    return "yaml"


def _parse_work_item_type(raw: Any) -> WorkItemType:
    if isinstance(raw, WorkItemType):
        return raw
    text = str(raw or "task").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return WorkItemType(text)
    except ValueError as exc:
        raise ValueError(f"Unknown work item type: {raw}") from exc


def _normalize_acceptance_criteria(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        lines: list[str] = []
        for line in raw.splitlines():
            cleaned = line.strip()
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            elif cleaned.startswith("* "):
                cleaned = cleaned[2:].strip()
            if cleaned:
                lines.append(cleaned)
        return lines
    return []


def _extract_md_section(content: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s*{re.escape(heading)}\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(content)
    if not match:
        return ""
    start = match.end()
    rest = content[start:]
    end_match = re.search(r"^---\s*$|^##\s+", rest, re.MULTILINE)
    body = rest[: end_match.start()] if end_match else rest
    return body.strip()


def _parse_markdown_ticket(content: str, *, source_label: str) -> TicketImportItem:
    ticket_match = _MD_TICKET_RE.search(content)
    title_match = _MD_TITLE_RE.search(content)
    if not title_match:
        raise ValueError("Markdown ticket is missing a Title: line")

    external_id = (ticket_match.group(1).strip() if ticket_match else "").strip()
    title = title_match.group(1).strip()
    if not title:
        raise ValueError("Markdown ticket title is empty")

    work_item_type = WorkItemType.TASK
    type_match = _MD_TYPE_RE.search(content)
    if type_match:
        work_item_type = _parse_work_item_type(type_match.group(1))

    priority = 3
    priority_match = _MD_PRIORITY_RE.search(content)
    if priority_match:
        priority = int(priority_match.group(1))

    milestone = ""
    milestone_match = _MD_MILESTONE_RE.search(content)
    if milestone_match:
        milestone = milestone_match.group(1).strip()

    parent_external_id = ""
    parent_match = _MD_PARENT_RE.search(content)
    if parent_match:
        parent_external_id = parent_match.group(1).strip()

    description = _extract_md_section(content, "Description")
    acceptance_criteria = _normalize_acceptance_criteria(
        _extract_md_section(content, "Acceptance Criteria")
    )

    return TicketImportItem(
        title=title,
        work_item_type=work_item_type,
        description=description,
        acceptance_criteria=acceptance_criteria,
        priority=priority,
        milestone=milestone,
        external_id=external_id,
        parent_external_id=parent_external_id,
        source_format="md",
        source_label=source_label,
        preview_markdown=content.strip(),
    )


def _coerce_ticket_dict(
    raw: dict[str, Any], *, source_label: str, source_format: str
) -> TicketImportItem:
    title = str(raw.get("title") or raw.get("name") or "").strip()
    if not title:
        raise ValueError("Ticket is missing title")

    work_item_type = _parse_work_item_type(
        raw.get("work_item_type") or raw.get("workItemType") or raw.get("type") or "task"
    )

    parent_ticket_id = raw.get("parent_ticket_id") or raw.get("parentTicketId")
    parent_external_id = (
        raw.get("parent_external_id") or raw.get("parentExternalId") or raw.get("parent")
    )

    return TicketImportItem(
        title=title,
        work_item_type=work_item_type,
        description=str(raw.get("description") or "").strip(),
        acceptance_criteria=_normalize_acceptance_criteria(
            raw.get("acceptance_criteria") or raw.get("acceptanceCriteria")
        ),
        priority=int(raw.get("priority") or 3),
        milestone=str(raw.get("milestone") or "").strip(),
        external_id=str(
            raw.get("external_id") or raw.get("externalId") or raw.get("id") or ""
        ).strip(),
        parent_external_id=str(parent_external_id or "").strip(),
        parent_ticket_id=str(parent_ticket_id).strip() if parent_ticket_id else None,
        source_format=source_format,
        source_label=source_label,
    )


def _parse_structured_payload(
    payload: Any,
    *,
    source_label: str,
    source_format: str,
) -> list[TicketImportItem]:
    if isinstance(payload, list):
        return [
            _coerce_ticket_dict(
                item, source_label=f"{source_label}[{index}]", source_format=source_format
            )
            for index, item in enumerate(payload)
            if isinstance(item, dict)
        ]

    if not isinstance(payload, dict):
        raise ValueError(f"Expected object or array in {source_label}")

    nested = payload.get("tickets")
    if isinstance(nested, list):
        return [
            _coerce_ticket_dict(
                item, source_label=f"{source_label}[{index}]", source_format=source_format
            )
            for index, item in enumerate(nested)
            if isinstance(item, dict)
        ]

    if "title" in payload or "name" in payload:
        return [
            _coerce_ticket_dict(payload, source_label=source_label, source_format=source_format)
        ]

    raise ValueError(f"No tickets found in {source_label}")


def parse_import_file(name: str, content: str) -> ParsedImportBatch:
    batch = ParsedImportBatch()
    source_format = _detect_format(name, content)

    try:
        if source_format == "md":
            batch.tickets.append(_parse_markdown_ticket(content, source_label=name))
        elif source_format == "json":
            payload = json.loads(content)
            batch.tickets.extend(
                _parse_structured_payload(payload, source_label=name, source_format="json")
            )
        else:
            payload = yaml.safe_load(content)
            if payload is None:
                raise ValueError("YAML file is empty")
            batch.tickets.extend(
                _parse_structured_payload(payload, source_label=name, source_format="yaml")
            )
    except Exception as exc:  # noqa: BLE001 - surface parse errors to caller
        batch.errors.append(f"{name}: {exc}")

    return batch


def parse_import_files(files: list[tuple[str, str]]) -> ParsedImportBatch:
    combined = ParsedImportBatch()
    for name, content in files:
        if not content.strip():
            combined.errors.append(f"{name}: file is empty")
            continue
        parsed = parse_import_file(name, content)
        combined.tickets.extend(parsed.tickets)
        combined.errors.extend(parsed.errors)
        combined.warnings.extend(parsed.warnings)
    return combined


def render_import_preview_markdown(item: TicketImportItem, *, workspace_slug: str) -> str:
    if item.preview_markdown.strip():
        return item.preview_markdown.strip()

    ac_lines = "\n".join(f"- {line}" for line in item.acceptance_criteria) or "- None"
    external_id = item.external_id or "pending-id"
    return f"""# TICKET: {external_id}
Title: {item.title}
Project: {workspace_slug}
Work item type: {item.work_item_type.value}

---

## Description
{item.description or "—"}

---

## Acceptance Criteria
{ac_lines}
"""


def enrich_import_preview(
    tickets: list[TicketImportItem],
    *,
    workspace_slug: str,
) -> tuple[list[TicketImportItem], dict[str, int], list[str], list[str]]:
    by_type: dict[str, int] = {}
    formats: set[str] = set()
    warnings: list[str] = []
    enriched: list[TicketImportItem] = []

    for item in tickets:
        by_type[item.work_item_type.value] = by_type.get(item.work_item_type.value, 0) + 1
        formats.add(item.source_format)
        if (
            item.work_item_type != WorkItemType.MILESTONE
            and not item.parent_ticket_id
            and not item.parent_external_id
        ):
            label = item.source_label or item.title
            warnings.append(f"{label}: missing parent — import will fail unless parent is set")
        preview = item.preview_markdown.strip() or render_import_preview_markdown(
            item, workspace_slug=workspace_slug
        )
        enriched.append(item.model_copy(update={"preview_markdown": preview}))

    return enriched, by_type, sorted(formats), warnings


def should_show_import_preview(*, total: int, formats: list[str]) -> bool:
    if total == 0:
        return False
    if total == 1:
        return True
    return len(formats) == 1 and formats[0] == "md"
