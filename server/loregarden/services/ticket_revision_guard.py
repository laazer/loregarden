"""An opt-in compare-and-swap on ``tickets.revision``, for the two edges a caller drives.

``tickets.revision`` is incremented by fourteen writers and was read back by
nobody: it is served to the client as ``TicketDetail.revision`` and no endpoint
accepted it in. So the column had the shape of optimistic concurrency and none
of the behaviour, and every concurrent edit was last-write-wins, silently.

Four things about where this guard sits, because all four are decisions rather
than accidents:

**(a) It is opt-in, per request.** A caller that sends no ``expected_revision``
never reaches this module, so every existing caller is unchanged. There is no
flag to set, no migration, and no behaviour that arrives by default.

**(b) The orchestrator's writers keep their unguarded bump.** Those are
``ticket_state_service.py:135``, ``orchestration.py:575``,
``orchestration_callbacks.py:442``/``:475``/``:619``, ``hierarchy_service.py:168``,
``initiative_autopilot.py:414``, ``requeue.py:66``,
``permission_allowlist.py:190`` and ``run_log_stream.py:810``. A stage recording
what happened is not an edit competing with a person — it is the system writing
down a fact — and a CAS there would make a stage completion *fail on contention*
instead of proceeding. Measured on the live database on 2026-10-20, a single
agent run emits 1,257–4,333 revision bumps, so a guard on those writers would
fail constantly and correctly describe nothing.

**(c) The exemption is structural, not a flag that happened to be None.** The
guard is called from the two caller-driven edges — ``PATCH /api/tickets/{id}``
and the MCP ``loregarden_update_ticket`` handler — and never from the shared
applier ``services/ticket_manual_edit.update_ticket_manual``, which has ten
non-test callers: those two, and eight system writers. Inside the applier the
exemption would be "the optional field was None" on eight call sites that reach
it by accident; at the edges the system's writes never reach the guard at all.

**(d) The convention this follows is ``docker_leases.claim_pool_capacity``.**
That function compare-and-swaps on exactly this column shape — a single guarded
UPDATE whose WHERE carries the revision, bumping it in the same statement — and
this module is deliberately its twin, so the codebase has one answer to "how do
we claim a row" rather than two.
"""

from __future__ import annotations

import json

from loregarden.models.domain import Ticket
from sqlalchemy import update
from sqlmodel import Session, select


class TicketRevisionConflict(Exception):
    """A guarded edit lost: the row moved between the read and the write.

    Deliberately **not** a ``ValueError``. ``api/tickets.py`` maps ``ValueError``
    to a 400 with prose, and a conflict that could be caught by that arm would
    answer 400 — a status a caller cannot distinguish from a malformed body,
    carrying no number to recover with — depending only on which ``except``
    clause came first.

    One payload shape serves both transports: HTTP sends ``payload`` as the 409
    ``detail``, and MCP has only a text channel (``mcp/protocol.py`` renders a
    raised exception as ``{text: str(exc)}``), so ``str(self)`` is that same
    payload as one line of JSON.
    """

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(json.dumps(self.payload))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "error": "revision_conflict",
            "expected_revision": self.expected_revision,
            "current_revision": self.current_revision,
            "message": (
                f"This ticket was at revision {self.expected_revision} when the edit "
                f"started and is now at revision {self.current_revision}, so the edit "
                "was not applied."
            ),
        }

    def __str__(self) -> str:
        return json.dumps(self.payload)


def claim_ticket_revision(session: Session, *, ticket_id: str, expected_revision: int) -> bool:
    """Claim one ticket at ``expected_revision``, or say it did not match.

    One statement, and no SELECT before it: the comparison and the bump are the
    same UPDATE, so the database decides. A ``SELECT revision`` first is the
    whole defect in miniature — between the read and the write another writer
    can move the row, and the guard then approves an edit against a revision
    that no longer exists.

    Commits nothing. The caller's transaction owns the commit, so an edit that
    fails after the claim takes the claim with it rather than burning a revision
    on a write that never landed.

    ``synchronize_session=False`` leaves ``revision`` **clean** on any ``Ticket``
    already loaded in this session, and that is load-bearing rather than
    incidental: the CAS owns one bump and the in-body bumps in
    ``services/ticket_manual_edit.py`` are left alone, which is only safe
    because SQLAlchemy flushes dirty attributes. A value nobody assigned is
    never written back over the claim. So this must not assign, ``refresh`` or
    ``expire`` ``revision`` on a loaded instance — the tempting repair for the
    now-stale in-memory number is exactly what would flush the pre-claim value
    back on top of the claim.
    """
    result = session.exec(
        update(Ticket)
        .where(Ticket.id == ticket_id)
        .where(Ticket.revision == expected_revision)
        .values(revision=Ticket.revision + 1)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount == 1)


def guard_ticket_revision(session: Session, *, ticket_id: str, expected_revision: int) -> None:
    """Claim the row, or raise ``TicketRevisionConflict`` carrying the real number.

    The re-read happens **only** on failure, and it reads the column rather than
    the entity: on the path that matters the edge has already loaded the
    ``Ticket``, so ``session.get`` would hand back the identity-mapped instance
    holding the stale number the caller sent, and the conflict would report
    ``expected == current`` — telling the pane the ticket did not move while
    refusing the write because it did. A scalar ``select(Ticket.revision)``
    bypasses the identity map and, like the claim above, leaves the loaded
    instance's ``revision`` untouched and clean.
    """
    if claim_ticket_revision(session, ticket_id=ticket_id, expected_revision=expected_revision):
        return
    current = session.exec(select(Ticket.revision).where(Ticket.id == ticket_id)).first()
    raise TicketRevisionConflict(
        expected_revision=expected_revision,
        current_revision=current if current is not None else expected_revision,
    )
