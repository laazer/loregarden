"""Which Home Baxter threads a request may see, and where a global one runs.

A workspace-scoped thread belongs to one workspace. A global thread belongs to
no workspace: it answers across all of them, and may be focused on one
initiative, whose milestones can live anywhere. It still needs a checkout to
run in, because a CLI refuses a directory that does not exist, so it borrows a
*host* workspace at creation and records it in ``workspace_id``. The host only
decides the turn's cwd, runtime defaults and run lane. It never narrows what
the thread can see.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.config import settings
from loregarden.mcp.tool_ids import INITIATIVE_PLANNER_MCP_TOOLS, mcp_tool_values
from loregarden.models.domain import BaxterChatScope, BaxterChatSession, Ticket, Workspace
from loregarden.services.initiative_plan_service import plan_view
from loregarden.services.initiative_planner_service import MAX_PLAN_CHARS, plan_digest
from loregarden.services.initiative_service import load_initiative, milestones_under
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select


class NoGlobalChatHostError(ValueError):
    """No workspace is checked out on this machine, so a global turn has nowhere to run."""


@dataclass(frozen=True)
class ChatOwner:
    """The archive a request addresses: one workspace's threads, or the global ones.

    ``initiative_id`` narrows a global listing to the threads focused on that
    initiative; ``None`` lists the unfocused global threads. Reading one thread
    by id only checks the scope, so a global thread opened from a link does not
    404 because the picker happens to show another focus.
    """

    scope: BaxterChatScope
    workspace_id: str = ""
    initiative_id: str | None = None

    @classmethod
    def for_workspace(cls, workspace_id: str) -> ChatOwner:
        return cls(scope=BaxterChatScope.WORKSPACE, workspace_id=workspace_id)

    @classmethod
    def global_(cls, initiative_id: str | None = None) -> ChatOwner:
        return cls(scope=BaxterChatScope.GLOBAL, initiative_id=initiative_id)

    def owns(self, row: BaxterChatSession) -> bool:
        if row.scope != self.scope:
            return False
        if self.scope == BaxterChatScope.WORKSPACE:
            return row.workspace_id == self.workspace_id
        return True

    def listing_filter(self) -> list[ColumnElement[bool]]:
        clauses = [col(BaxterChatSession.scope) == self.scope]
        if self.scope == BaxterChatScope.WORKSPACE:
            clauses.append(col(BaxterChatSession.workspace_id) == self.workspace_id)
        elif self.initiative_id is None:
            clauses.append(col(BaxterChatSession.initiative_id).is_(None))
        else:
            clauses.append(col(BaxterChatSession.initiative_id) == self.initiative_id)
        return clauses


def _checked_out(workspace: Workspace | None) -> bool:
    return workspace is not None and resolve_workspace_root(workspace).is_dir()


def _initiative_host(session: Session, initiative_id: str) -> Workspace | None:
    """The first of the initiative's workspaces checked out here, so the turn can read its code."""
    for milestone in milestones_under(session, initiative_id):
        workspace = (
            session.get(Workspace, milestone.workspace_id) if milestone.workspace_id else None
        )
        if workspace is not None and workspace.archived_at is None and _checked_out(workspace):
            return workspace
    return None


def _control_plane_host(session: Session) -> Workspace | None:
    """Loregarden's own workspace when registered, else any checked-out live workspace."""
    live = [
        workspace
        for workspace in session.exec(
            select(Workspace)
            .where(col(Workspace.archived_at).is_(None))
            .order_by(col(Workspace.created_at).asc())
        ).all()
        if _checked_out(workspace)
    ]
    control_plane = settings.repo_root.resolve()
    for workspace in live:
        if resolve_workspace_root(workspace) == control_plane:
            return workspace
    return live[0] if live else None


def resolve_global_host(session: Session, initiative_id: str | None) -> Workspace:
    """Pick the checkout a new global thread runs in.

    An initiative-focused thread prefers one of the initiative's own workspaces,
    so a question about its code can be answered from files. An initiative with
    no milestones yet, or none checked out here, falls back to the control
    plane, which is still a correct host: the thread reads tickets through MCP.
    The snapshot names the host, so the fallback is visible.

    Raises ``LookupError`` for an unknown initiative and
    ``NoGlobalChatHostError`` when nothing is checked out at all.
    """
    if initiative_id is not None:
        load_initiative(session, initiative_id)
        host = _initiative_host(session, initiative_id)
        if host is not None:
            return host
    host = _control_plane_host(session)
    if host is None:
        raise NoGlobalChatHostError(
            "No workspace is checked out on this machine, so a global chat has nowhere to run. "
            "Add a workspace, or check one out, first."
        )
    return host


@dataclass(frozen=True)
class FocusedInitiative:
    """One initiative a thread works on, with its plan rendered now."""

    ticket: Ticket
    plan: str


@dataclass(frozen=True)
class GlobalChatContext:
    """What a cross-workspace turn sees beyond its host workspace."""

    #: The host's slug: the checkout the turn runs in, named so the agent knows
    #: which repository its file tools are reading.
    host_slug: str
    #: workspace id -> slug, so cross-workspace rows can be labelled.
    workspace_slugs: dict[str, str]
    #: The workspaces the turn answers about, or None for every workspace.
    workspace_ids: frozenset[str] | None = None
    #: The initiatives the thread is focused on, the archive's own first.
    initiatives: tuple[FocusedInitiative, ...] = ()


def _plan_digest(session: Session, initiative_id: str, budget: int) -> str:
    digest = plan_digest(plan_view(session, initiative_id))
    if len(digest) > budget:
        digest = digest[:budget] + "\n…[truncated — call loregarden_get_initiative_plan]"
    return digest


def load_global_context(
    session: Session,
    host: Workspace,
    *,
    workspace_ids: frozenset[str] | None,
    initiative_ids: tuple[str, ...],
) -> GlobalChatContext:
    """The cross-workspace context for a turn, from its resolved focus.

    Several initiatives share one plan budget, so focusing on five cannot make
    a prompt five times the size one focus was sized for.
    """
    slugs = {workspace.id: workspace.slug for workspace in session.exec(select(Workspace)).all()}
    budget = MAX_PLAN_CHARS // max(1, len(initiative_ids))
    initiatives = tuple(
        FocusedInitiative(
            ticket=load_initiative(session, initiative_id),
            plan=_plan_digest(session, initiative_id, budget),
        )
        for initiative_id in initiative_ids
    )
    return GlobalChatContext(
        host_slug=host.slug,
        workspace_slugs=slugs,
        workspace_ids=workspace_ids,
        initiatives=initiatives,
    )


def global_chat_agent(agent: dict) -> dict:
    """The triage agent, also granted the initiative planner's tools.

    A global thread is where an operator works on initiatives, so it needs to
    read a plan and drive it. Merged rather than replaced: it is still Baxter.
    """
    tools = list(agent.get("mcp_tools") or [])
    tools += [tool for tool in mcp_tool_values(INITIATIVE_PLANNER_MCP_TOOLS) if tool not in tools]
    return {**agent, "mcp_tools": tools}
