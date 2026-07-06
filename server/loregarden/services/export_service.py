import json
from datetime import datetime, timezone

from loregarden.config import settings
from loregarden.models.domain import Ticket, TicketState, Workspace
from sqlmodel import Session, select

STATE_FOLDER: dict[TicketState, str] = {
    TicketState.BACKLOG: "backlog",
    TicketState.IN_PROGRESS: "in_progress",
    TicketState.BLOCKED: "blocked",
    TicketState.DONE: "done",
    TicketState.WONT_DO: "wont_do",
}


def _slug_to_filename(external_id: str) -> str:
    return external_id.replace("-", "_") + ".md"


def render_ticket_markdown(ticket: Ticket, workspace: Workspace) -> str:
    ac = json.loads(ticket.acceptance_criteria_json or "[]")
    stage = ticket.workflow_stage_key.upper() or "BACKLOG"
    blocking = ticket.blocking_issues or "None"
    return f"""# TICKET: {ticket.external_id}
Title: {ticket.title}
Project: {workspace.slug}
Created By: loregarden-export
Created On: {ticket.created_at.isoformat()}

---

## Description
{ticket.description}

---

## Acceptance Criteria
{chr(10).join(f"- {item}" for item in ac)}

---

## Dependencies
- None

---

# WORKFLOW STATE (DO NOT FREEFORM EDIT)

## Stage
{stage}

## Revision
{ticket.revision}

## Last Updated By
{ticket.last_updated_by or "—"}

## Validation Status
- Tests: N/A
- Static QA: N/A
- Integration: N/A

## Blocking Issues
{blocking}

## Escalation Notes
- None

---

# NEXT ACTION

## Next Responsible Agent
{ticket.next_agent or "Human"}

## Required Input Schema
```json
{{}}
```

## Status
{ticket.next_status}

## Reason
Exported from SQLite at {datetime.now(timezone.utc).isoformat()}
"""


class ExportService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def export_project_board(self, *, milestone: str | None = None) -> dict:
        board_root = settings.project_board_dir
        board_root.mkdir(parents=True, exist_ok=True)

        tickets = list(self.session.exec(select(Ticket)).all())
        exported: list[str] = []

        for ticket in tickets:
            ws = self.session.get(Workspace, ticket.workspace_id)
            if not ws:
                continue
            ms = milestone or ticket.milestone or "01_milestone_bootstrap"
            folder = STATE_FOLDER[ticket.state]
            target_dir = board_root / ms / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / _slug_to_filename(ticket.external_id)
            path.write_text(render_ticket_markdown(ticket, ws), encoding="utf-8")
            exported.append(str(path.relative_to(settings.repo_root)))

        checkpoint = board_root / "CHECKPOINTS.md"
        if checkpoint.is_file():
            body = checkpoint.read_text(encoding="utf-8")
        else:
            body = "# Checkpoint Index\n\n"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body += f"\n## Export: {stamp}\n- Exported {len(exported)} tickets from SQLite\n"
        checkpoint.write_text(body, encoding="utf-8")

        return {"exported": len(exported), "paths": exported}
