"""A closed ticket's workflow is closed: no stage on it is left open.

`tickets.state` and the stage map are two records of one fact, and nothing
kept them together. Closing a ticket (`done` or `wont_do`) is a decision about
the ticket, made by an operator, an agent, `supersede_ticket` or a rollup, and
none of those writers touched the stages. So the stage map kept saying whatever
it said before: `triage/pending` on a ticket nobody ever started, or
`test-break/blocked` on lg-workflow-integrity-88, which was fixed by hand in a
PR while its last agent run sat interrupted. Measured on the live database
when this landed: 361 of 466 closed tickets had at least one open stage.

The other direction reached the same state. A writer that resets or creates a
stage map (`ensure_workflow_instance` on a closed ticket, a template rebind,
a run that finishes after its ticket was closed) writes open stages under a
ticket that is already closed.

**The rule.** At flush, a closed ticket's open stages (pending, running,
awaiting, blocked) become `wont_do`, with a note saying what they were; the
cursor follows. Reopening reverses exactly those stages, and only those, back
to `pending`: the note is the marker, so a stage an agent pruned or a stage
that finished keeps its status. Without the reverse half a reopened ticket
would have every stage resolved, derive `done` again on its next read, and
have nothing left to run.

`blocking_issues` is deliberately left alone. On a `wont_do` ticket it holds
the supersession reason, and that is information, not a stale block.

Registered on the session rather than at the writers, as `workflow_pin_guard`
is on the mapper: there are a dozen writers of `tickets.state` and of
`stages_json`, so a new one inherits this rather than remembering it. It works
on the stored rows rather than on resolved template stages so it needs nothing
from `services`, and so a guard cannot fail for want of a template.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from loregarden.models.domain import StageStatus, Ticket, TicketState, WorkflowInstance
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import UOWTransaction

#: Ticket states meaning the work is behind us. The same pair as
#: `ticket_state_service.RESOLVED_STATES`, which this layer cannot import.
CLOSED_STATES = frozenset({TicketState.DONE, TicketState.WONT_DO})

#: Stage statuses that say work is still owed.
OPEN_STAGE_STATUSES = frozenset(
    {StageStatus.PENDING, StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED}
)

#: How a note written here begins. Reopening restores the stages carrying it,
#: and only them.
CLOSED_WITH_TICKET = "Closed with the ticket"

#: Separates the closing note from the note the stage carried before, which a
#: reopen puts back.
_EARLIER = " | earlier note: "


@dataclass(frozen=True)
class StageChange:
    """What closing or reopening did to one workflow."""

    stages_json: str
    keys: tuple[str, ...]


def close_stage_rows(stages_json: str, state: TicketState) -> StageChange:
    """Every open stage row as `wont_do`, noting what it was. Pure."""
    rows = json.loads(stages_json or "[]")
    closed: list[str] = []
    for row in rows:
        status = StageStatus(row["status"])
        if status not in OPEN_STAGE_STATUSES:
            continue
        earlier = row.get("note", "")
        note = f"{CLOSED_WITH_TICKET} ({state.value}) while {status.value}"
        row["status"] = StageStatus.WONT_DO.value
        row["note"] = f"{note}{_EARLIER}{earlier}" if earlier else note
        closed.append(row["key"])
    return StageChange(json.dumps(rows), tuple(closed))


def reopen_stage_rows(stages_json: str) -> StageChange:
    """The rows `close_stage_rows` closed, back to `pending`. Pure."""
    rows = json.loads(stages_json or "[]")
    reopened: list[str] = []
    for row in rows:
        note = row.get("note", "")
        if row["status"] != StageStatus.WONT_DO.value or not note.startswith(CLOSED_WITH_TICKET):
            continue
        row["status"] = StageStatus.PENDING.value
        _, _, earlier = note.partition(_EARLIER)
        if earlier:
            row["note"] = earlier
        else:
            row.pop("note", None)
        reopened.append(row["key"])
    return StageChange(json.dumps(rows), tuple(reopened))


def open_stage_keys(stages_json: str) -> list[str]:
    """The stage rows still saying work is owed."""
    return [
        row["key"]
        for row in json.loads(stages_json or "[]")
        if StageStatus(row["status"]) in OPEN_STAGE_STATUSES
    ]


def _follow_cursor(ticket: Ticket, instance: WorkflowInstance) -> None:
    """The ticket's cursor shows its stage's status, as `_cursor_stage` would."""
    for row in json.loads(instance.stages_json or "[]"):
        if row["key"] == ticket.workflow_stage_key:
            status = StageStatus(row["status"])
            if ticket.workflow_stage_status != status:
                ticket.workflow_stage_status = status
            return


def settle_closed_workflow(ticket: Ticket, instance: WorkflowInstance) -> tuple[str, ...]:
    """Make `instance` agree with `ticket`'s state; the stage keys it changed.

    Closed: close what is open, and the cursor with it, always. Open: reopen
    what closing closed, and move the cursor only if that changed something;
    an open ticket's cursor is its writers' to set, and one that sets it
    explicitly (a block, a cursor move) must not be overridden here. Mutates
    only; the caller's transaction commits.
    """
    closed = ticket.state in CLOSED_STATES
    if closed:
        change = close_stage_rows(instance.stages_json, ticket.state)
    else:
        change = reopen_stage_rows(instance.stages_json)
    if change.keys:
        instance.stages_json = change.stages_json
    if closed or change.keys:
        _follow_cursor(ticket, instance)
    return change.keys


def _instance_for(
    session: OrmSession, ticket_id: str, pending: dict[str, WorkflowInstance]
) -> WorkflowInstance | None:
    """The ticket's instance: one this flush is writing, else the stored one."""
    if ticket_id in pending:
        return pending[ticket_id]
    return session.scalars(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
    ).first()


def _state_changed(ticket: Ticket) -> bool:
    return inspect(ticket).attrs.state.history.has_changes()


@event.listens_for(OrmSession, "before_flush")
def _settle_closed_workflows(session: OrmSession, _flush: UOWTransaction, _objects: object) -> None:
    """Close the workflow of every closed ticket this flush touches, either side.

    A closed ticket is settled whenever it is flushed; an open one only when
    its state changed, since that is the only way it can carry closed-with-the-
    ticket stages. An instance flushed on its own is settled against its
    ticket, which covers the writers that reset a stage map under a closed one.
    """
    touched = [*session.new, *session.dirty]
    # session.new/dirty is SQLAlchemy's heterogeneous set of every mapped
    # object in the flush; there is no protocol to dispatch on, only the type.
    tickets = [o for o in touched if isinstance(o, Ticket)]  # py-org: allow-isinstance
    instances = {
        o.ticket_id: o
        for o in touched
        if isinstance(o, WorkflowInstance)  # py-org: allow-isinstance
    }
    if not tickets and not instances:
        return
    with session.no_autoflush:
        settled: set[str] = set()
        for ticket in tickets:
            settled.add(ticket.id)
            if ticket.state not in CLOSED_STATES and not _state_changed(ticket):
                continue
            instance = _instance_for(session, ticket.id, instances)
            if instance is not None:
                settle_closed_workflow(ticket, instance)
        for ticket_id, instance in instances.items():
            if ticket_id in settled:
                continue
            ticket = session.get(Ticket, ticket_id)
            if ticket is not None and ticket.state in CLOSED_STATES:
                settle_closed_workflow(ticket, instance)
