"""Queue operation review system: diffs, comments, and approval workflow."""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
import json
import difflib

from loregarden.models.domain import (
    QueueOperation,
    QueueOperationType,
    QueueOperationComment,
    RunOutputReview,
    Workspace,
)
from loregarden.db import get_session
from loregarden.api.queue_management import emit_execution_update

router = APIRouter(prefix="/api/parallel", tags=["queue-review"])


def generate_diff(before: list, after: list) -> list[dict]:
    """Generate unified diff-style changes between queue states."""
    changes = []

    # Track by run_id for easy comparison
    before_map = {r.get("run_id"): r for r in before}
    after_map = {r.get("run_id"): r for r in after}

    # Added runs
    for run_id, run_data in after_map.items():
        if run_id not in before_map:
            changes.append(
                {
                    "type": "added",
                    "run_id": run_id,
                    "ticket_id": run_data.get("ticket_id"),
                    "position": run_data.get("position"),
                }
            )

    # Removed runs
    for run_id, run_data in before_map.items():
        if run_id not in after_map:
            changes.append(
                {
                    "type": "removed",
                    "run_id": run_id,
                    "ticket_id": run_data.get("ticket_id"),
                    "position": run_data.get("position"),
                }
            )

    # Modified runs (position changed, status changed, etc)
    for run_id in before_map.keys():
        if run_id in after_map:
            before_run = before_map[run_id]
            after_run = after_map[run_id]

            if before_run != after_run:
                changes.append(
                    {
                        "type": "modified",
                        "run_id": run_id,
                        "ticket_id": before_run.get("ticket_id"),
                        "before": before_run,
                        "after": after_run,
                        "fields_changed": [
                            k for k in before_run.keys()
                            if before_run.get(k) != after_run.get(k)
                        ],
                    }
                )

    return changes


@router.post("/workspace/{workspace_id}/queue/operations/create")
async def create_queue_operation(
    workspace_id: str,
    operation_type: str,
    before_state: list,
    after_state: list,
    description: str = "",
    created_by: str = "system",
    session: Session = Depends(get_session),
) -> dict:
    """Create a new queue operation record for review."""
    ws = session.exec(
        select(Workspace).where(Workspace.id == workspace_id)
    ).first()

    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Generate diff
    diff = generate_diff(before_state, after_state)

    # Extract affected run IDs
    affected_run_ids = set()
    for change in diff:
        affected_run_ids.add(change["run_id"])

    operation = QueueOperation(
        workspace_id=workspace_id,
        operation_type=operation_type,
        description=description,
        before_state_json=json.dumps(before_state),
        after_state_json=json.dumps(after_state),
        diff_json=json.dumps(diff),
        affected_run_ids=",".join(sorted(affected_run_ids)),
        created_by=created_by,
    )

    session.add(operation)
    session.commit()

    return {
        "operation_id": operation.id,
        "operation_type": operation.operation_type,
        "changes": diff,
        "affected_count": len(affected_run_ids),
    }


@router.get("/workspace/{workspace_id}/queue/operations/{operation_id}/diff")
async def get_operation_diff(
    workspace_id: str,
    operation_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Get the diff view for a queue operation."""
    operation = session.exec(
        select(QueueOperation).where(
            (QueueOperation.id == operation_id)
            & (QueueOperation.workspace_id == workspace_id)
        )
    ).first()

    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    before_state = json.loads(operation.before_state_json)
    after_state = json.loads(operation.after_state_json)
    diff = json.loads(operation.diff_json) if operation.diff_json else []

    # Get comments for this operation
    comments = session.exec(
        select(QueueOperationComment).where(
            QueueOperationComment.operation_id == operation_id
        )
    ).all()

    return {
        "operation_id": operation.id,
        "operation_type": operation.operation_type,
        "description": operation.description,
        "created_by": operation.created_by,
        "created_at": operation.created_at.isoformat(),
        "before_state": before_state,
        "after_state": after_state,
        "diff": diff,
        "affected_run_ids": operation.affected_run_ids.split(",") if operation.affected_run_ids else [],
        "comments": [
            {
                "id": c.id,
                "line_number": c.line_number,
                "run_id": c.run_id,
                "content": c.content,
                "created_by": c.created_by,
                "created_at": c.created_at.isoformat(),
                "resolved": c.resolved,
            }
            for c in comments
        ],
        "approved": operation.approved,
        "approved_by": operation.approved_by,
        "approved_at": operation.approved_at.isoformat()
        if operation.approved_at
        else None,
    }


@router.post("/workspace/{workspace_id}/queue/operations/{operation_id}/comment")
async def add_operation_comment(
    workspace_id: str,
    operation_id: str,
    content: str,
    line_number: Optional[int] = None,
    run_id: Optional[str] = None,
    created_by: str = "system",
    session: Session = Depends(get_session),
) -> dict:
    """Add a comment to a queue operation (like GitHub PR review)."""
    operation = session.exec(
        select(QueueOperation).where(
            (QueueOperation.id == operation_id)
            & (QueueOperation.workspace_id == workspace_id)
        )
    ).first()

    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    comment = QueueOperationComment(
        operation_id=operation_id,
        line_number=line_number,
        run_id=run_id,
        content=content,
        created_by=created_by,
    )

    session.add(comment)
    session.commit()

    return {
        "comment_id": comment.id,
        "operation_id": operation_id,
        "line_number": comment.line_number,
        "run_id": comment.run_id,
        "content": comment.content,
        "created_by": comment.created_by,
        "created_at": comment.created_at.isoformat(),
    }


@router.post("/workspace/{workspace_id}/queue/operations/{operation_id}/approve")
async def approve_operation(
    workspace_id: str,
    operation_id: str,
    approved_by: str = "system",
    session: Session = Depends(get_session),
) -> dict:
    """Approve a queue operation for execution."""
    operation = session.exec(
        select(QueueOperation).where(
            (QueueOperation.id == operation_id)
            & (QueueOperation.workspace_id == workspace_id)
        )
    ).first()

    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    operation.approved = True
    operation.approved_by = approved_by
    operation.approved_at = datetime.now(datetime.timezone.utc)

    session.add(operation)
    session.commit()

    return {
        "operation_id": operation.id,
        "approved": True,
        "approved_by": operation.approved_by,
        "approved_at": operation.approved_at.isoformat(),
    }


@router.post("/workspace/{workspace_id}/queue/operations/{operation_id}/submit-to-agent")
async def submit_operation_to_agent(
    workspace_id: str,
    operation_id: str,
    agent_id: str = "default-orchestrator",
    instructions: str = "",
    approved_by: str = "system",
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Submit reviewed operation to agent for execution with review context."""
    operation = session.exec(
        select(QueueOperation).where(
            (QueueOperation.id == operation_id)
            & (QueueOperation.workspace_id == workspace_id)
        )
    ).first()

    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    # Get all comments for context
    comments = session.exec(
        select(QueueOperationComment).where(
            QueueOperationComment.operation_id == operation_id
        )
    ).all()

    # Mark as approved and collect context for agent
    operation.approved = True
    operation.approved_by = approved_by
    operation.approved_at = datetime.now(datetime.timezone.utc)

    session.add(operation)
    session.commit()

    # Prepare agent submission with full review context
    review_context = {
        "operation_type": operation.operation_type,
        "description": operation.description,
        "diff": json.loads(operation.diff_json),
        "affected_runs": operation.affected_run_ids.split(",")
        if operation.affected_run_ids
        else [],
        "comments": [
            {
                "line_number": c.line_number,
                "run_id": c.run_id,
                "content": c.content,
                "created_by": c.created_by,
                "resolved": c.resolved,
            }
            for c in comments
        ],
        "approved_by": approved_by,
        "custom_instructions": instructions,
    }

    if background_tasks:
        background_tasks.add_task(
            emit_execution_update,
            workspace_id,
            {
                "type": "operation_submitted_to_agent",
                "operation_id": operation_id,
                "agent_id": agent_id,
            },
        )

    return {
        "operation_id": operation.id,
        "submitted_to_agent": agent_id,
        "review_context": review_context,
        "submission_time": datetime.now(datetime.timezone.utc).isoformat(),
    }


@router.get("/workspace/{workspace_id}/queue/operations")
async def list_operations(
    workspace_id: str,
    approved_only: bool = False,
    executed_only: bool = False,
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    """List queue operations with optional filtering."""
    query = select(QueueOperation).where(
        QueueOperation.workspace_id == workspace_id
    )

    if approved_only:
        query = query.where(QueueOperation.approved == True)

    if executed_only:
        query = query.where(QueueOperation.executed == True)

    operations = session.exec(
        query.order_by(QueueOperation.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    total = session.exec(
        select(QueueOperation).where(QueueOperation.workspace_id == workspace_id)
    ).all()

    return {
        "total": len(total),
        "operations": [
            {
                "id": op.id,
                "operation_type": op.operation_type,
                "description": op.description,
                "created_by": op.created_by,
                "created_at": op.created_at.isoformat(),
                "approved": op.approved,
                "executed": op.executed,
                "affected_count": len(
                    op.affected_run_ids.split(",") if op.affected_run_ids else []
                ),
            }
            for op in operations
        ],
    }


@router.post("/workspace/{workspace_id}/runs/{run_id}/output-review")
async def create_output_review(
    workspace_id: str,
    run_id: str,
    output_type: str,  # "stdout" or "stderr"
    output_content: str,
    session: Session = Depends(get_session),
) -> dict:
    """Create a run output review for line-by-line commenting."""
    review = RunOutputReview(
        run_id=run_id,
        workspace_id=workspace_id,
        output_type=output_type,
        output_content=output_content,
    )

    session.add(review)
    session.commit()

    return {
        "review_id": review.id,
        "run_id": run_id,
        "output_type": output_type,
        "line_count": len(output_content.split("\n")),
    }


@router.post("/workspace/{workspace_id}/runs/{run_id}/output-review/{review_id}/comment")
async def add_output_comment(
    workspace_id: str,
    run_id: str,
    review_id: str,
    line_number: int,
    content: str,
    session: Session = Depends(get_session),
) -> dict:
    """Add a line-specific comment to run output review."""
    review = session.exec(
        select(RunOutputReview).where(
            (RunOutputReview.id == review_id)
            & (RunOutputReview.run_id == run_id)
            & (RunOutputReview.workspace_id == workspace_id)
        )
    ).first()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    # Load existing comments
    comments = json.loads(review.comments_json) if review.comments_json else []

    # Add new comment
    comments.append(
        {
            "line_number": line_number,
            "content": content,
            "created_at": datetime.now(datetime.timezone.utc).isoformat(),
        }
    )

    review.comments_json = json.dumps(comments)
    review.updated_at = datetime.now(datetime.timezone.utc)

    session.add(review)
    session.commit()

    return {
        "review_id": review.id,
        "line_number": line_number,
        "content": content,
        "total_comments": len(comments),
    }


@router.get("/workspace/{workspace_id}/runs/{run_id}/output-review/{review_id}")
async def get_output_review(
    workspace_id: str,
    run_id: str,
    review_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Get full output review with all comments."""
    review = session.exec(
        select(RunOutputReview).where(
            (RunOutputReview.id == review_id)
            & (RunOutputReview.run_id == run_id)
            & (RunOutputReview.workspace_id == workspace_id)
        )
    ).first()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    comments = json.loads(review.comments_json) if review.comments_json else []

    # Split output into lines for display
    lines = review.output_content.split("\n")

    return {
        "review_id": review.id,
        "run_id": run_id,
        "output_type": review.output_type,
        "lines": [
            {
                "number": i + 1,
                "content": line,
                "comments": [c for c in comments if c["line_number"] == i + 1],
            }
            for i, line in enumerate(lines)
        ],
        "total_comments": len(comments),
        "approved": review.approved,
        "approved_by": review.approved_by,
    }
