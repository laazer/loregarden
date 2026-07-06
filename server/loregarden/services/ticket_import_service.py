"""Bulk ticket import orchestration."""

from __future__ import annotations

from loregarden.models.domain import (
    Ticket,
    TicketImportItem,
    TicketImportPreviewResponse,
    TicketImportResult,
    WorkItemType,
    Workspace,
)
from loregarden.services.ticket_import import (
    enrich_import_preview,
    parse_import_files,
    should_show_import_preview,
)
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select


def _import_sort_key(item: TicketImportItem) -> tuple[int, str]:
    order = {
        WorkItemType.MILESTONE: 0,
        WorkItemType.FEATURE: 1,
        WorkItemType.CAPABILITY: 2,
        WorkItemType.TASK: 3,
        WorkItemType.BUG: 3,
    }
    return (order.get(item.work_item_type, 99), item.source_label, item.title)


class TicketImportService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview(
        self,
        *,
        workspace_slug: str,
        files: list[tuple[str, str]],
    ) -> TicketImportPreviewResponse:
        ws = self.session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if not ws:
            return TicketImportPreviewResponse(
                tickets=[],
                errors=[f"Workspace not found: {workspace_slug}"],
                warnings=[],
                total=0,
                by_type={},
                formats=[],
                show_preview=False,
            )

        parsed = parse_import_files(files)
        tickets, by_type, formats, preview_warnings = enrich_import_preview(
            parsed.tickets,
            workspace_slug=workspace_slug,
        )
        return TicketImportPreviewResponse(
            tickets=tickets,
            errors=parsed.errors,
            warnings=[*parsed.warnings, *preview_warnings],
            total=len(tickets),
            by_type=by_type,
            formats=formats,
            show_preview=should_show_import_preview(total=len(tickets), formats=formats),
        )

    def import_tickets(
        self,
        *,
        workspace_slug: str,
        tickets: list[TicketImportItem],
    ) -> TicketImportResult:
        ws = self.session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if not ws:
            return TicketImportResult(
                created_count=0, ticket_ids=[], errors=[f"Workspace not found: {workspace_slug}"]
            )

        svc = TicketService(self.session)
        created_ids: list[str] = []
        errors: list[str] = []
        external_to_id: dict[str, str] = {
            ticket.external_id: ticket.id
            for ticket in self.session.exec(
                select(Ticket).where(Ticket.workspace_id == ws.id)
            ).all()
            if ticket.external_id
        }

        ordered = sorted(tickets, key=_import_sort_key)
        pending = list(ordered)

        for _ in range(len(pending) + 1):
            if not pending:
                break
            next_pending: list[TicketImportItem] = []
            progress = False

            for item in pending:
                parent_id = self._resolve_parent_id(
                    item,
                    workspace_id=ws.id,
                    external_to_id=external_to_id,
                    errors=errors,
                )
                if parent_id is False:
                    next_pending.append(item)
                    continue

                try:
                    created = svc.create_ticket(
                        workspace_slug=workspace_slug,
                        title=item.title,
                        work_item_type=item.work_item_type,
                        parent_ticket_id=parent_id,
                        description=item.description,
                        acceptance_criteria=item.acceptance_criteria,
                        priority=item.priority,
                        milestone=item.milestone,
                        external_id=item.external_id,
                    )
                except ValueError as exc:
                    label = item.source_label or item.title
                    errors.append(f"{label}: {exc}")
                    continue

                created_ids.append(created.id)
                if created.external_id:
                    external_to_id[created.external_id] = created.id
                progress = True

            if not progress:
                for item in next_pending:
                    label = item.source_label or item.title
                    errors.append(f"{label}: could not resolve parent work item")
                break
            pending = next_pending

        return TicketImportResult(
            created_count=len(created_ids),
            ticket_ids=created_ids,
            errors=errors,
        )

    def _resolve_parent_id(
        self,
        item: TicketImportItem,
        *,
        workspace_id: str,
        external_to_id: dict[str, str],
        errors: list[str],
    ) -> str | None | bool:
        if item.work_item_type == WorkItemType.MILESTONE:
            if item.parent_ticket_id or item.parent_external_id:
                label = item.source_label or item.title
                errors.append(f"{label}: milestones cannot have a parent")
            return None

        if item.parent_ticket_id:
            parent = self.session.get(Ticket, item.parent_ticket_id)
            if not parent or parent.workspace_id != workspace_id:
                label = item.source_label or item.title
                errors.append(f"{label}: parent_ticket_id not found in workspace")
                return None
            return parent.id

        if item.parent_external_id:
            resolved = external_to_id.get(item.parent_external_id)
            if not resolved:
                return False
            return resolved

        label = item.source_label or item.title
        errors.append(f"{label}: {item.work_item_type.value} requires a parent")
        return None
