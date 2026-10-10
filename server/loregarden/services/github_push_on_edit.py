"""Push a linked ticket's edits to its GitHub issue as soon as they commit.

**What triggers it.** Any committed change to a ticket's title, description or
state, from whichever writer: the board, `loregarden_update_ticket`, an agent,
the orchestrator settling a stage. It is caught at the ORM (`before_update` on
`Ticket`, then the session's commit), so no call site has to remember it.

**What it does.** After a short debounce, so a burst of edits costs one round
trip, it runs the ordinary `sync_link` for the ticket. That is the full
three-way merge, not a blind push: an issue edited on GitHub meanwhile is still
a conflict, reported and left alone.

**Who it applies to.** Linked tickets in workspaces whose `GithubSyncSettings`
row has `push_on_edit` on — off by default. Edits the sync itself pulled from
GitHub are skipped (`github_sync_origin`). Never started in a sandboxed branch
server, whose database is a snapshot (see `main.lifespan`).

**Failure.** `sync_link` records a `gh` failure on the link (`last_error`,
shown on the ticket card) and it is logged here at warning. A dropped push is
not lost: the ticket stays changed against the link's base, so the next manual,
scheduled or pushed sync sends it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import GithubSyncSettings, Ticket
from loregarden.services.github_issue_sync import link_for_ticket, sync_link
from loregarden.services.github_sync_origin import is_applying_remote_changes
from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, object_session
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: The ticket fields a GitHub issue mirrors.
_SYNCED_ATTRIBUTES = ("title", "description", "state")
_PENDING_KEY = "github_push_pending_ticket_ids"


class EditPushQueue:
    """Ticket ids waiting to be pushed, each with the time it becomes due.

    Enqueueing an id already waiting pushes its due time back, which is the
    debounce: a ticket edited five times in a second is synced once.
    """

    def __init__(self) -> None:
        self._due: dict[str, float] = {}
        self._lock = threading.Lock()
        #: Off until the worker starts, so a process with no worker (a sandbox,
        #: a CLI, a test) does not collect ids nobody will ever drain.
        self.accepting = False

    def enqueue(self, ticket_ids: set[str], *, delay: float, now: float | None = None) -> None:
        if not self.accepting or not ticket_ids:
            return
        due = (time.monotonic() if now is None else now) + delay
        with self._lock:
            for ticket_id in ticket_ids:
                self._due[ticket_id] = due

    def take_due(self, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        with self._lock:
            ready = [tid for tid, due in self._due.items() if due <= now]
            for tid in ready:
                del self._due[tid]
        return ready

    def pending(self) -> set[str]:
        with self._lock:
            return set(self._due)

    def clear(self) -> None:
        with self._lock:
            self._due.clear()


edit_push_queue = EditPushQueue()


@event.listens_for(Ticket, "before_update")
def _note_synced_edit(_mapper: Mapper, _connection: Connection, target: Ticket) -> None:
    """Remember, on the session, a ticket whose mirrored fields this flush changes."""
    if not edit_push_queue.accepting or is_applying_remote_changes():
        return
    state = inspect(target)
    if not any(state.attrs[name].history.has_changes() for name in _SYNCED_ATTRIBUTES):
        return
    session = object_session(target)
    if session is not None:
        session.info.setdefault(_PENDING_KEY, set()).add(target.id)


@event.listens_for(OrmSession, "after_commit")
def _enqueue_committed_edits(session: OrmSession) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if pending:
        edit_push_queue.enqueue(pending, delay=settings.github_push_debounce_seconds)


@event.listens_for(OrmSession, "after_rollback")
def _forget_rolled_back_edits(session: OrmSession) -> None:
    session.info.pop(_PENDING_KEY, None)


def _push_one(session: Session, ticket_id: str) -> str | None:
    """Sync one edited ticket. None when it is not ours to push; else its error."""
    link = link_for_ticket(session, ticket_id)
    if link is None:
        return None
    workspace_settings = session.get(GithubSyncSettings, link.workspace_id)
    if workspace_settings is None or not workspace_settings.push_on_edit:
        return None
    result = sync_link(session, link)
    if result.error:
        logger.warning(
            "Push-on-edit for %s (#%s) failed: %s",
            result.external_id,
            result.issue_number,
            result.error,
        )
    return result.error


def process_due_pushes(now: float | None = None) -> dict[str, str]:
    """Sync every ticket whose debounce has elapsed. Returns ticket id -> error."""
    ticket_ids = edit_push_queue.take_due(now)
    if not ticket_ids:
        return {}
    outcomes: dict[str, str] = {}
    with Session(engine) as session:
        for ticket_id in ticket_ids:
            try:
                error = _push_one(session, ticket_id)
            except Exception as exc:  # noqa: BLE001 — logged; one ticket must not stop the rest
                logger.exception("Push-on-edit failed for ticket %s", ticket_id)
                session.rollback()
                error = str(exc) or type(exc).__name__
            if error is not None:
                outcomes[ticket_id] = error
    return outcomes


async def run_push_worker(poll_seconds: float) -> None:
    try:
        while True:
            try:
                await asyncio.sleep(poll_seconds)
                await asyncio.to_thread(process_due_pushes)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the worker outliving a bad pass is the point
                logger.exception("Push-on-edit worker pass failed; continuing")
    finally:
        edit_push_queue.accepting = False
        edit_push_queue.clear()


def start_push_worker(poll_seconds: float | None = None) -> asyncio.Task | None:
    """Start draining pushes, or return None when the poll interval disables it."""
    if poll_seconds is None:
        poll_seconds = settings.github_push_poll_seconds
    if poll_seconds <= 0:
        logger.info("GitHub push-on-edit worker disabled (poll=%s)", poll_seconds)
        return None
    edit_push_queue.accepting = True
    return asyncio.create_task(run_push_worker(poll_seconds))
