"""Inline code review comments on ticket git diffs."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from loregarden.db.session import get_session
from loregarden.models.domain import Ticket, TicketDiffComment
from loregarden.services.triage_service import send_triage_message

router = APIRouter(prefix="/tickets", tags=["diff-review"])


class DiffCommentCreate(BaseModel):
    file_path: str
    line_index: int = Field(ge=0)
    line_kind: str = "c"
    content: str = Field(min_length=1)
    created_by: str = "reviewer"


class DiffCommentSubmit(BaseModel):
    instructions: str = ""
    created_by: str = "reviewer"
    include_resolved: bool = False


def _serialize(comment: TicketDiffComment) -> dict:
    return {
        "id": comment.id,
        "ticket_id": comment.ticket_id,
        "file_path": comment.file_path,
        "line_index": comment.line_index,
        "line_kind": comment.line_kind,
        "content": comment.content,
        "resolved": comment.resolved,
        "created_at": comment.created_at.isoformat(),
        "created_by": comment.created_by,
        "updated_at": comment.updated_at.isoformat(),
    }


def _get_ticket(session: Session, ticket_id: str) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.get("/{ticket_id}/diff-comments")
def list_diff_comments(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    _get_ticket(session, ticket_id)
    comments = session.exec(
        select(TicketDiffComment)
        .where(TicketDiffComment.ticket_id == ticket_id)
        .order_by(TicketDiffComment.file_path, TicketDiffComment.line_index, TicketDiffComment.created_at)
    ).all()
    return {
        "ticket_id": ticket_id,
        "comments": [_serialize(c) for c in comments],
        "total": len(comments),
    }


@router.post("/{ticket_id}/diff-comments")
def add_diff_comment(
    ticket_id: str,
    body: DiffCommentCreate,
    session: Session = Depends(get_session),
) -> dict:
    _get_ticket(session, ticket_id)
    comment = TicketDiffComment(
        ticket_id=ticket_id,
        file_path=body.file_path,
        line_index=body.line_index,
        line_kind=body.line_kind,
        content=body.content.strip(),
        created_by=body.created_by,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return _serialize(comment)


@router.post("/{ticket_id}/diff-comments/submit-to-agent")
def submit_diff_comments_to_agent(
    ticket_id: str,
    body: DiffCommentSubmit,
    session: Session = Depends(get_session),
) -> dict:
    ticket = _get_ticket(session, ticket_id)
    stmt = select(TicketDiffComment).where(TicketDiffComment.ticket_id == ticket_id)
    if not body.include_resolved:
        stmt = stmt.where(TicketDiffComment.resolved.is_(False))
    comments = session.exec(stmt).all()
    if not comments and not body.instructions.strip():
        raise HTTPException(status_code=400, detail="No review comments to submit")

    lines: list[str] = ["## Inline code review"]
    by_file: dict[str, list[TicketDiffComment]] = {}
    for comment in comments:
        by_file.setdefault(comment.file_path, []).append(comment)

    for file_path, file_comments in sorted(by_file.items()):
        lines.append(f"\n### {file_path}")
        for comment in sorted(file_comments, key=lambda c: c.line_index):
            kind = comment.line_kind
            prefix = "+" if kind == "a" else "−" if kind == "d" else " "
            lines.append(
                f"- Line {comment.line_index + 1} ({prefix}): {comment.content}"
            )

    if body.instructions.strip():
        lines.append(f"\n## Additional instructions\n{body.instructions.strip()}")

    message = "\n".join(lines)
    triage = send_triage_message(session, ticket, message)

    return {
        "ticket_id": ticket_id,
        "submitted_comments": len(comments),
        "message_preview": message[:500],
        "triage": triage,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitted_by": body.created_by,
    }
