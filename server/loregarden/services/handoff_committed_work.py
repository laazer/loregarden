"""Whether a stage's work is committed, checked in-stage rather than after it.

Observed on blobert ticket 22 (2026-08-14). The implementation stage scored 4/4
required checklist items — all attestations, none of which asserts that the work
is committed — and the handoff was honest: `head_sha 1c932d72` with all six
produced files under `dirty_paths`. The transition gate caught the delta and
blocked, but only after the stage had been declared complete, and with no repair
path. Detection belongs where the agent can still act on it (429).

The hard part is not the git call. It is knowing *whose* uncommitted work it is,
and refusing to pretend when that cannot be told — see `CommittedWorkBasis`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import AgentRun, Ticket
from loregarden.models.domain.enums import CommittedWorkBasis
from loregarden.services.git_boundary import read_boundary
from sqlmodel import Session, select


@dataclass(frozen=True)
class UncommittedWork:
    """Uncommitted paths attributable to this ticket, and what that rests on."""

    paths: tuple[str, ...]
    basis: CommittedWorkBasis

    @property
    def blocks_handoff(self) -> bool:
        """UNDETERMINED does not block.

        The check cannot tell whose the dirt is in a shared checkout, and
        blocking every handoff written from one would stop far more work than
        the defect does. It is reported instead, so "we could not tell" reaches
        the reader rather than being rendered as "clean".
        """
        return bool(self.paths) and self.basis is not CommittedWorkBasis.UNDETERMINED

    def message(self) -> str:
        listed = "\n".join(f"  {path}" for path in self.paths)
        if self.basis is CommittedWorkBasis.TICKET_PATHS:
            whose = "this ticket's own recorded paths are uncommitted"
        else:
            whose = "this ticket's worktree has uncommitted work"
        return (
            f"Handoff refused: {whose}. Commit them, then write the handoff "
            f"again — the next stage runs against the committed tree, so work "
            f"left here is work the next agent cannot see.\n{listed}"
        )


def ticket_recorded_paths(session: Session, ticket: Ticket) -> set[str]:
    """Every path this ticket's runs have claimed to touch.

    The same source `builtin_orchestrator._ticket_changed_paths` reads. Usually
    empty, which is why it is never the only basis.
    """
    rows = session.exec(
        select(AgentRun.changed_paths_json).where(AgentRun.ticket_id == ticket.id)
    ).all()
    paths: set[str] = set()
    for raw in rows:
        paths.update(json.loads(raw or "[]"))
    return {path for path in paths if path}


def uncommitted_ticket_work(
    session: Session,
    ticket: Ticket,
    *,
    ticket_root: Path,
    is_ticket_worktree: bool,
) -> UncommittedWork:
    """Uncommitted work this handoff should refuse to be written over.

    `is_ticket_worktree` is passed rather than re-derived so the caller's notion
    of "this ticket's tree" and this function's stay the same one — a check that
    disagreed with its caller about which tree it read would be worse than no
    check.
    """
    dirty = set(read_boundary(ticket_root).dirty_paths)
    if not dirty:
        return UncommittedWork(paths=(), basis=CommittedWorkBasis.TICKET_PATHS)

    recorded = ticket_recorded_paths(session, ticket)
    if recorded:
        # Precise, and the only basis that satisfies "dirty unrelated paths do
        # not fail" outright.
        return UncommittedWork(
            paths=tuple(sorted(dirty & recorded)), basis=CommittedWorkBasis.TICKET_PATHS
        )
    if is_ticket_worktree:
        # No recorded paths, but nothing else runs in this tree.
        return UncommittedWork(paths=tuple(sorted(dirty)), basis=CommittedWorkBasis.WHOLE_WORKTREE)
    return UncommittedWork(paths=tuple(sorted(dirty)), basis=CommittedWorkBasis.UNDETERMINED)
