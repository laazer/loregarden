"""Branch triage API: inspect branches, review diffs, inline comments."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import BranchDiffComment, Ticket, Workspace
from loregarden.services.branch_triage_chat_service import branch_chat_snapshot
from loregarden.services.branch_triage_run_service import (
    BranchTriageConflictError,
    schedule_branch_triage_turn,
    start_branch_triage_run,
)
from loregarden.services.branch_triage_service import (
    branch_diff_snapshot,
    branch_triage_snapshot,
    delete_branch,
    remove_branch_worktree,
)
from loregarden.services.file_editor import checkout_editor_branch
from loregarden.services.triage_service import send_triage_message
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["branch-triage"])


class BranchDiffCommentCreate(BaseModel):
    file_path: str
    line_index: int = Field(ge=0)
    line_kind: str = "c"
    content: str = Field(min_length=1)
    created_by: str = "reviewer"


class BranchDiffCommentSubmit(BaseModel):
    instructions: str = ""
    created_by: str = "reviewer"
    include_resolved: bool = False


class BranchCheckoutRequest(BaseModel):
    branch: str


class BranchDeleteRequest(BaseModel):
    force: bool = False
    remove_worktrees: bool = False


class BranchWorktreeRemoveRequest(BaseModel):
    path: str = Field(min_length=1)


class BranchChatMessageCreate(BaseModel):
    content: str = Field(min_length=1)


def _workspace_or_404(session: Session, slug: str) -> Workspace:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return ws


def _serialize_comment(comment: BranchDiffComment) -> dict:
    return {
        "id": comment.id,
        "workspace_id": comment.workspace_id,
        "branch": comment.branch,
        "file_path": comment.file_path,
        "line_index": comment.line_index,
        "line_kind": comment.line_kind,
        "content": comment.content,
        "resolved": comment.resolved,
        "created_at": comment.created_at.isoformat(),
        "created_by": comment.created_by,
        "updated_at": comment.updated_at.isoformat(),
    }


def _linked_ticket_for_branch(session: Session, workspace_id: str, branch: str) -> Ticket | None:
    tickets = session.exec(select(Ticket).where(Ticket.workspace_id == workspace_id)).all()
    for ticket in tickets:
        ticket_branch = (ticket.branch or "").strip()
        if ticket_branch and ticket_branch == branch:
            return ticket
    return None


@router.get("/{slug}/branch-triage")
def get_branch_triage(slug: str, session: Session = Depends(get_session)) -> dict:
    ws = _workspace_or_404(session, slug)
    return branch_triage_snapshot(session, ws)


@router.get("/{slug}/branch-triage/diff")
def get_branch_diff(
    slug: str,
    branch: str = Query(..., min_length=1),
    base: str | None = Query(None),
    mode: str = Query("base", pattern="^(base|remote|unstaged|uncommitted)$"),
    file: str | None = Query(None, min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        diff = branch_diff_snapshot(ws, branch, base=base, mode=mode, file_path=file)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not diff:
        raise HTTPException(404, "No diff for this branch")
    return {"branch": branch, "base": diff.get("base"), "mode": mode, "file": file, "diff": diff}


@router.post("/{slug}/branch-triage/checkout")
def checkout_branch(
    slug: str,
    body: BranchCheckoutRequest,
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        return checkout_editor_branch(ws, body.branch)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{slug}/branch-triage/delete")
def remove_branch(
    slug: str,
    branch: str = Query(..., min_length=1),
    body: BranchDeleteRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        removed = delete_branch(
            ws,
            branch,
            force=bool(body and body.force),
            remove_worktrees=bool(body and body.remove_worktrees),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    removed_worktrees = bool(body and body.remove_worktrees) and removed
    return {
        "deleted": branch,
        "already_gone": not removed,
        "removed_worktrees": removed_worktrees,
    }


@router.post("/{slug}/branch-triage/worktrees/remove")
def remove_worktree(
    slug: str,
    body: BranchWorktreeRemoveRequest,
    branch: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        remove_branch_worktree(ws, branch, body.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"branch": branch, "removed_path": body.path}


@router.get("/{slug}/branch-triage/chat")
def get_branch_chat(
    slug: str,
    branch: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    return branch_chat_snapshot(session, ws, branch)


@router.post("/{slug}/branch-triage/chat/messages", status_code=202)
def post_branch_chat_message(
    slug: str,
    body: BranchChatMessageCreate,
    branch: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        user_message, assistant_message = start_branch_triage_run(session, ws, branch, body.content)
    except BranchTriageConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    schedule_branch_triage_turn(assistant_message.id)
    return {
        "user_message": {
            "id": user_message.id,
            "role": user_message.role,
            "content": user_message.content,
            "created_at": user_message.created_at.isoformat(),
        },
        "active_turn_id": assistant_message.id,
        "status": "queued",
    }


@router.get("/{slug}/branch-triage/diff-comments")
def list_branch_diff_comments(
    slug: str,
    branch: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    comments = session.exec(
        select(BranchDiffComment)
        .where(BranchDiffComment.workspace_id == ws.id, BranchDiffComment.branch == branch)
        .order_by(
            BranchDiffComment.file_path,
            BranchDiffComment.line_index,
            BranchDiffComment.created_at,
        )
    ).all()
    return {
        "workspace_id": ws.id,
        "branch": branch,
        "comments": [_serialize_comment(c) for c in comments],
        "total": len(comments),
    }


@router.post("/{slug}/branch-triage/diff-comments")
def add_branch_diff_comment(
    slug: str,
    body: BranchDiffCommentCreate,
    branch: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    comment = BranchDiffComment(
        workspace_id=ws.id,
        branch=branch,
        file_path=body.file_path,
        line_index=body.line_index,
        line_kind=body.line_kind,
        content=body.content.strip(),
        created_by=body.created_by,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return _serialize_comment(comment)


@router.post("/{slug}/branch-triage/diff-comments/submit-to-agent")
def submit_branch_diff_to_agent(
    slug: str,
    body: BranchDiffCommentSubmit,
    branch: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    stmt = select(BranchDiffComment).where(
        BranchDiffComment.workspace_id == ws.id,
        BranchDiffComment.branch == branch,
    )
    if not body.include_resolved:
        stmt = stmt.where(BranchDiffComment.resolved.is_(False))
    comments = session.exec(stmt).all()
    if not comments and not body.instructions.strip():
        raise HTTPException(400, "No review comments to submit")

    lines: list[str] = [f"## Inline code review — branch `{branch}`"]
    by_file: dict[str, list[BranchDiffComment]] = {}
    for comment in comments:
        by_file.setdefault(comment.file_path, []).append(comment)

    for file_path, file_comments in sorted(by_file.items()):
        lines.append(f"\n### {file_path}")
        for comment in sorted(file_comments, key=lambda c: c.line_index):
            kind = comment.line_kind
            prefix = "+" if kind == "a" else "−" if kind == "d" else " "
            lines.append(f"- Line {comment.line_index + 1} ({prefix}): {comment.content}")

    if body.instructions.strip():
        lines.append(f"\n## Additional instructions\n{body.instructions.strip()}")

    message = "\n".join(lines)
    ticket = _linked_ticket_for_branch(session, ws.id, branch)
    triage = None
    if ticket:
        triage = send_triage_message(session, ticket, message)

    return {
        "workspace_id": ws.id,
        "branch": branch,
        "ticket_id": ticket.id if ticket else None,
        "submitted_comments": len(comments),
        "message_preview": message[:500],
        "triage": triage,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitted_by": body.created_by,
    }
