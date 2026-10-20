"""The workspaces loregarden is still working in: every one not archived.

A sweep that visits every workspace visits these. Archiving is how a person
says a workspace is finished with, and a sweep that still visited one kept
logging about it: three workspaces a test run left in the live database on
2026-10-11 and 2026-10-17, pointing at temp directories, stayed in the chat
branch sweep and the workflow monitor after they were archived.
"""

from __future__ import annotations

from loregarden.models.domain import Workspace
from sqlmodel import Session, col, select


def active_workspaces(session: Session) -> list[Workspace]:
    """Every workspace not archived, by slug."""
    query = select(Workspace).where(col(Workspace.archived_at).is_(None)).order_by(Workspace.slug)
    return list(session.exec(query).all())
