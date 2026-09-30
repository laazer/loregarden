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
from loregarden.services.hierarchy_service import validate_parent_assignment
from loregarden.services.ticket_import import (
    enrich_import_preview,
    parse_import_files,
    should_show_import_preview,
)
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select


def _index_spellings(external_to_id: dict[str, str], ticket: Ticket) -> None:
    """Make ``ticket`` findable by every id it answers to.

    A `parent_external_id` in an import file names the id that file knows, which
    is the ref the ticket was created under — and that lands in
    `legacy_external_id`, not `external_id`.
    """
    for spelling in (ticket.external_id, ticket.legacy_external_id):
        if spelling:
            external_to_id[spelling] = ticket.id


def _import_sort_key(item: TicketImportItem) -> tuple[int, str, str]:
    order = {
        WorkItemType.INITIATIVE: 0,
        WorkItemType.MILESTONE: 1,
        WorkItemType.FEATURE: 2,
        WorkItemType.CAPABILITY: 3,
        WorkItemType.TASK: 4,
        WorkItemType.BUG: 4,
    }
    return (order.get(item.work_item_type, 99), item.source_label, item.title)


#: Sentinel from ``_resolve_parent_id`` — parent link is illegal; do not create.
_PARENT_REJECTED = object()


class TicketImportService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview(
        self,
        *,
        workspace_slug: str,
        files: list[tuple[str, str]],
        mode: str = "smart",
    ) -> TicketImportPreviewResponse:
        ws = self.session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if not ws:
            error_response_data = {
                "tickets": [],
                "errors": [f"Workspace not found: {workspace_slug}"],
                "warnings": [],
                "total": 0,
                "by_type": {},
                "formats": [],
                "show_preview": False,
                "mode": mode,
            }
            if mode == "smart":
                error_response_data["studio_context"] = {"imported_tickets": []}
            return TicketImportPreviewResponse(**error_response_data)

        parsed = parse_import_files(files)
        tickets, by_type, formats, preview_warnings = enrich_import_preview(
            parsed.tickets,
            workspace_slug=workspace_slug,
        )

        response_data = {
            "tickets": tickets,
            "errors": parsed.errors,
            "warnings": [*parsed.warnings, *preview_warnings],
            "total": len(tickets),
            "by_type": by_type,
            "formats": formats,
            "show_preview": should_show_import_preview(total=len(tickets), formats=formats),
            "mode": mode,
        }

        if mode == "smart":
            response_data["studio_context"] = {
                "imported_tickets": [item.model_dump() for item in tickets]
            }

        return TicketImportPreviewResponse(**response_data)

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
        external_to_id: dict[str, str] = {}
        for ticket in self.session.exec(select(Ticket).where(Ticket.workspace_id == ws.id)).all():
            _index_spellings(external_to_id, ticket)

        ordered = sorted(tickets, key=_import_sort_key)
        pending = list(ordered)

        for _ in range(len(pending) + 1):
            if not pending:
                break
            next_pending, progress = self._import_round(
                svc=svc,
                workspace_slug=workspace_slug,
                workspace_id=ws.id,
                pending=pending,
                external_to_id=external_to_id,
                created_ids=created_ids,
                errors=errors,
            )

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

    def _import_round(
        self,
        *,
        svc: TicketService,
        workspace_slug: str,
        workspace_id: str,
        pending: list[TicketImportItem],
        external_to_id: dict[str, str],
        created_ids: list[str],
        errors: list[str],
    ) -> tuple[list[TicketImportItem], bool]:
        """Create every item whose parent resolves this round.

        Returns the items still waiting on an unresolved parent, and whether
        anything was created — no progress means the remaining parents are
        unresolvable and the caller stops.
        """
        next_pending: list[TicketImportItem] = []
        progress = False

        for item in pending:
            parent_id = self._resolve_parent_id(
                item,
                workspace_id=workspace_id,
                external_to_id=external_to_id,
                errors=errors,
            )
            if parent_id is False:
                next_pending.append(item)
                continue
            if parent_id is _PARENT_REJECTED:
                continue

            try:
                created = svc.create_ticket(
                    workspace_slug=(
                        None if item.work_item_type == WorkItemType.INITIATIVE else workspace_slug
                    ),
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
            _index_spellings(external_to_id, created)
            progress = True

        return next_pending, progress

    def _resolve_parent_id(
        self,
        item: TicketImportItem,
        *,
        workspace_id: str,
        external_to_id: dict[str, str],
        errors: list[str],
    ) -> str | None | bool | object:
        parent: Ticket | None = None

        if item.parent_ticket_id:
            parent = self.session.get(Ticket, item.parent_ticket_id)
            if not parent:
                label = item.source_label or item.title
                errors.append(f"{label}: parent_ticket_id not found in workspace")
                return _PARENT_REJECTED
            # Null-workspace INITIATIVE parents may own a workspace-bound child.
            if parent.workspace_id is not None and parent.workspace_id != workspace_id:
                label = item.source_label or item.title
                errors.append(f"{label}: parent_ticket_id not found in workspace")
                return _PARENT_REJECTED
        elif item.parent_external_id:
            resolved = external_to_id.get(item.parent_external_id)
            if not resolved:
                return False
            parent = self.session.get(Ticket, resolved)
            if not parent:
                label = item.source_label or item.title
                errors.append(f"{label}: parent_external_id not found in workspace")
                return _PARENT_REJECTED
            if parent.workspace_id is not None and parent.workspace_id != workspace_id:
                label = item.source_label or item.title
                errors.append(f"{label}: parent_external_id not found in workspace")
                return _PARENT_REJECTED

        parent_type = parent.work_item_type if parent is not None else None
        try:
            validate_parent_assignment(item.work_item_type, parent_type)
        except ValueError as exc:
            label = item.source_label or item.title
            errors.append(f"{label}: {exc}")
            return _PARENT_REJECTED

        return parent.id if parent is not None else None
