"""The paths a ticket's runs have recorded as theirs.

Its own module, below anything that touches git, because both the handoff
check (`handoff_committed_work`) and the execution-root resolver
(`ticket_worktree`) need it, and the resolver sits underneath the git services
the handoff check imports.
"""

from __future__ import annotations

import json

from loregarden.models.domain import AgentRun, Ticket
from sqlmodel import Session, select


def ticket_recorded_paths(session: Session, ticket: Ticket) -> set[str]:
    """Every path this ticket's runs have claimed to touch.

    The same source `gate_recovery._ticket_changed_paths` reads. Usually
    empty, which is why it is never the only basis.
    """
    rows = session.exec(
        select(AgentRun.changed_paths_json).where(AgentRun.ticket_id == ticket.id)
    ).all()
    paths: set[str] = set()
    for raw in rows:
        paths.update(json.loads(raw or "[]"))
    return {path for path in paths if path}
