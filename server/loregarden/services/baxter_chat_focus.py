"""Which workspaces and initiatives one Home Baxter thread works across.

A thread's archive already implies part of its scope: a workspace thread is
about its workspace, a focused global thread about its initiative. The focus
set is what the operator ticks on top of that in the chat's Scope card, any
number of each, stored in ``baxter_chat_focus``.

What the set means:

- A workspace thread spans its own workspace plus the ticked ones.
- A global thread with workspaces ticked spans just those; with none, it
  spans every workspace, as it always has.
- Every ticked initiative joins the archive's own, if any, and each one's
  plan is rendered into the turn.

The host checkout a turn runs in never changes: it was fixed when the thread
was created (see ``baxter_chat_scope``), and a thread's worktree lives in it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from loregarden.models.domain import (
    BaxterChatFocus,
    BaxterChatScope,
    BaxterChatSession,
    ChatFocusKind,
    Ticket,
    Workspace,
)
from loregarden.services.initiative_service import load_initiative
from sqlmodel import Session, col, select


class ChatFocusError(ValueError):
    """A focus naming a workspace or initiative that cannot be focused on."""


@dataclass(frozen=True)
class ChatFocus:
    """The ticked rows only; ``scope_*`` below add what the archive implies."""

    workspace_ids: tuple[str, ...] = ()
    initiative_ids: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.workspace_ids and not self.initiative_ids


def load_focus(session: Session, chat_session: BaxterChatSession) -> ChatFocus:
    rows = session.exec(
        select(BaxterChatFocus)
        .where(BaxterChatFocus.session_id == chat_session.id)
        .order_by(col(BaxterChatFocus.created_at).asc())
    ).all()
    return ChatFocus(
        workspace_ids=tuple(r.ref_id for r in rows if r.kind == ChatFocusKind.WORKSPACE),
        initiative_ids=tuple(r.ref_id for r in rows if r.kind == ChatFocusKind.INITIATIVE),
    )


def scope_workspace_ids(chat_session: BaxterChatSession, focus: ChatFocus) -> frozenset[str] | None:
    """The workspaces a turn answers about, or ``None`` for every workspace."""
    if chat_session.scope == BaxterChatScope.WORKSPACE:
        return frozenset({chat_session.workspace_id, *focus.workspace_ids})
    return frozenset(focus.workspace_ids) or None


def scope_initiative_ids(chat_session: BaxterChatSession, focus: ChatFocus) -> tuple[str, ...]:
    """The archive's initiative first, then the ticked ones."""
    implied = (chat_session.initiative_id,) if chat_session.initiative_id else ()
    return implied + tuple(i for i in focus.initiative_ids if i not in implied)


def spans_workspaces(chat_session: BaxterChatSession, focus: ChatFocus) -> bool:
    """Whether a turn needs the cross-workspace prompt and tools.

    True for every global thread, and for a workspace thread once anything is
    ticked beyond its own workspace.
    """
    return chat_session.scope == BaxterChatScope.GLOBAL or not focus.empty


def _live_workspaces(session: Session, slugs: Sequence[str]) -> list[Workspace]:
    wanted = list(dict.fromkeys(slugs))
    if not wanted:
        return []
    found = {
        w.slug: w
        for w in session.exec(select(Workspace).where(col(Workspace.slug).in_(wanted))).all()
    }
    missing = [slug for slug in wanted if slug not in found]
    if missing:
        raise ChatFocusError(f"No workspace named {', '.join(missing)}")
    archived = [slug for slug in wanted if found[slug].archived_at is not None]
    if archived:
        raise ChatFocusError(f"Archived, so not focusable: {', '.join(archived)}")
    return [found[slug] for slug in wanted]


def _initiatives(session: Session, initiative_ids: Sequence[str]) -> list[Ticket]:
    try:
        return [load_initiative(session, i) for i in dict.fromkeys(initiative_ids)]
    except LookupError as exc:
        raise ChatFocusError(str(exc)) from exc


def set_focus(
    session: Session,
    chat_session: BaxterChatSession,
    *,
    workspace_slugs: Sequence[str],
    initiative_ids: Sequence[str],
) -> ChatFocus:
    """Replace the thread's ticked set. Every name is checked before anything is written.

    What the archive already implies (the thread's own workspace, its archive
    initiative) is dropped rather than stored twice, so un-ticking can never
    leave a thread without the scope it was opened in.
    """
    workspaces = _live_workspaces(session, workspace_slugs)
    initiatives = _initiatives(session, initiative_ids)
    implied_workspace = (
        chat_session.workspace_id if chat_session.scope == BaxterChatScope.WORKSPACE else None
    )
    for row in session.exec(
        select(BaxterChatFocus).where(BaxterChatFocus.session_id == chat_session.id)
    ).all():
        session.delete(row)
    session.flush()
    for workspace in workspaces:
        if workspace.id != implied_workspace:
            session.add(
                BaxterChatFocus(
                    session_id=chat_session.id, kind=ChatFocusKind.WORKSPACE, ref_id=workspace.id
                )
            )
    for initiative in initiatives:
        if initiative.id != chat_session.initiative_id:
            session.add(
                BaxterChatFocus(
                    session_id=chat_session.id,
                    kind=ChatFocusKind.INITIATIVE,
                    ref_id=initiative.id,
                )
            )
    session.commit()
    return load_focus(session, chat_session)


def copy_focus(session: Session, source_id: str, target_id: str) -> None:
    """Carry a thread's focus onto its fork. The caller commits."""
    for row in session.exec(
        select(BaxterChatFocus).where(BaxterChatFocus.session_id == source_id)
    ).all():
        session.add(BaxterChatFocus(session_id=target_id, kind=row.kind, ref_id=row.ref_id))


def delete_focus(session: Session, session_id: str) -> None:
    """Drop a thread's focus rows ahead of the thread itself. The caller commits."""
    for row in session.exec(
        select(BaxterChatFocus).where(BaxterChatFocus.session_id == session_id)
    ).all():
        session.delete(row)


def focus_view(session: Session, chat_session: BaxterChatSession) -> dict:
    """The Scope card's data: every workspace and initiative in scope, implied ones marked fixed."""
    focus = load_focus(session, chat_session)
    workspace_ids = scope_workspace_ids(chat_session, focus)
    workspaces: list[dict] = []
    if workspace_ids is not None:
        rows = {
            w.id: w
            for w in session.exec(
                select(Workspace).where(col(Workspace.id).in_(list(workspace_ids)))
            ).all()
        }
        ordered = [
            *(
                [chat_session.workspace_id]
                if chat_session.scope == BaxterChatScope.WORKSPACE
                else []
            ),
            *focus.workspace_ids,
        ]
        workspaces = [
            {
                "id": rows[wid].id,
                "slug": rows[wid].slug,
                "fixed": chat_session.scope == BaxterChatScope.WORKSPACE
                and wid == chat_session.workspace_id,
            }
            for wid in dict.fromkeys(ordered)
            if wid in rows
        ]
    initiatives = []
    for initiative_id in scope_initiative_ids(chat_session, focus):
        ticket = session.get(Ticket, initiative_id)
        if ticket is None:
            continue
        initiatives.append(
            {
                "id": ticket.id,
                "external_id": ticket.external_id,
                "title": ticket.title,
                "fixed": ticket.id == chat_session.initiative_id,
            }
        )
    return {
        "all_workspaces": workspace_ids is None,
        "workspaces": workspaces,
        "initiatives": initiatives,
    }
