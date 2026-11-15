"""The paths a ticket's runs have recorded as theirs.

Its own module, below anything that touches git, because both the handoff
check (`handoff_committed_work`) and the execution-root resolver
(`ticket_worktree`) need it, and the resolver sits underneath the git services
the handoff check imports.
"""

from __future__ import annotations

import json

from loregarden.models.domain import AgentRun, Ticket
from sqlmodel import Session, col, select


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


def tickets_that_recorded_writing_nothing(session: Session, ticket_ids: list[str]) -> set[str]:
    """The ids of these tickets whose every run looked and wrote nothing.

    At least one run, and every run's `changed_paths_recorded_at` set with an
    empty record. A run with no record (`changed_paths_recorded_at` NULL) cannot
    say it wrote nothing, so one such run leaves its ticket out, as does a
    ticket with no runs at all.
    """
    if not ticket_ids:
        return set()
    rows = session.exec(
        select(
            AgentRun.ticket_id, AgentRun.changed_paths_json, AgentRun.changed_paths_recorded_at
        ).where(col(AgentRun.ticket_id).in_(ticket_ids))
    ).all()
    looked: set[str] = set()
    wrote_or_unknown: set[str] = set()
    for ticket_id, raw, recorded_at in rows:
        if recorded_at is None or any(json.loads(raw or "[]")):
            wrote_or_unknown.add(ticket_id)
        else:
            looked.add(ticket_id)
    return looked - wrote_or_unknown
