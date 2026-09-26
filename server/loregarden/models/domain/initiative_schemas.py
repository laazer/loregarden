"""Read models for initiatives — the cross-workspace parent above milestones.

An initiative's children live in different workspaces, so no workspace-scoped
ticket list or tree can show one whole. These views are assembled without a
workspace filter and name each milestone's workspace instead.
"""

from __future__ import annotations

from loregarden.models.domain.enums import TicketState
from pydantic import BaseModel


class InitiativeMilestoneView(BaseModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    workspace_slug: str


class InitiativeProgress(BaseModel):
    #: Milestones in `done` or `wont_do` — the same resolution the rollup uses.
    resolved: int
    total: int


class InitiativeView(BaseModel):
    id: str
    external_id: str
    title: str
    description: str
    state: TicketState
    priority: int
    milestones: list[InitiativeMilestoneView]
    progress: InitiativeProgress
    #: Distinct workspaces the milestones live in, sorted.
    workspaces: list[str]
