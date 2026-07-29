"""Home Baxter chat API — workspace-scoped one-shot model turns."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import Workspace
from loregarden.services.baxter_chat_service import invoke_baxter_chat_model
from loregarden.services.chat_primitives import (
    parse_primitive_parts,
    parts_to_jsonable,
    resolve_parts,
)
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.triage_service import TRIAGE_AGENT_NAME
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["baxter-chat"])


class BaxterChatHistoryItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1)


class BaxterChatMessageCreate(BaseModel):
    content: str = Field(min_length=1)
    history: list[BaxterChatHistoryItem] = Field(default_factory=list)


class BaxterChatMessageReply(BaseModel):
    reply: str
    parts: list[dict[str, Any]] = Field(default_factory=list)


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return workspace


@router.post("/{slug}/baxter-chat/messages", response_model=BaxterChatMessageReply)
def send_baxter_chat_message(
    slug: str,
    body: BaxterChatMessageCreate,
    session: Session = Depends(get_session),
) -> BaxterChatMessageReply:
    workspace = _workspace(session, slug)
    try:
        reply = invoke_baxter_chat_model(
            session,
            workspace,
            content=body.content,
            history=[item.model_dump() for item in body.history],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface CLI/auth failures to the UI
        raise HTTPException(502, format_agent_unavailable(TRIAGE_AGENT_NAME, exc)) from exc
    parts = resolve_parts(session, parse_primitive_parts(reply), workspace_id=workspace.id)
    return BaxterChatMessageReply(reply=reply, parts=parts_to_jsonable(parts))
