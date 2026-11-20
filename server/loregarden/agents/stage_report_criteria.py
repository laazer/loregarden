"""The stage-report `Stop` hook's criteria check: a settling stage's pass waits for its settlements.

A builtin-driver stage run reports through the sentinel, not
`loregarden_complete_stage`, so the refusal that tool makes
(`services/criterion_settle_check`) never reached it, and the check before the
gate reopened verify as a new run to call `loregarden_settle_criterion`. In the
week to 2026-11-18 that was 7 of 9 such send-backs. The hook can still refuse
while the turn is open, so the agent settles them in its own context.

The hook needs the ledger, and the agent's environment carries no database
binding: `cli_adapters.STATE_BINDING_ENV_VARS` strips it so nothing the agent
spawns binds to a database by inheritance. So the server that supervises the
run names its own database on the hook's command line (`--db`), resolved the
way its engine resolves it. A sandbox server names its sandbox copy, never the
live one. The hook opens it read-only (`mode=ro`): it can refuse a stop, never
write a row.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loregarden.models.domain import AgentRun, Ticket
from loregarden.services.criterion_settle_check import unsettled_pass_message
from sqlalchemy import create_engine
from sqlmodel import Session

#: What the agent does once it has settled them: the report is what it re-sends.
THEN = "write your stage report again"


def read_only_url(db_path: Path) -> str:
    """A SQLite URL that opens ``db_path`` read-only and never creates it."""
    return f"sqlite:///file:{db_path}?mode=ro&uri=true"


@contextmanager
def ledger(db_path: Path, run_id: str) -> Iterator[tuple[Session, AgentRun, Ticket]]:
    """A read-only session on ``db_path``, with run ``run_id`` and its ticket.

    Raises what opening or reading the database raises, and LookupError when
    it does not hold the run: that is the wrong database, not a stop to allow.
    The hook fails open on all of them and says why on stderr.
    """
    engine = create_engine(read_only_url(db_path))
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            ticket = session.get(Ticket, run.ticket_id) if run is not None else None
            if run is None or ticket is None:
                raise LookupError(f"run {run_id} or its ticket is not in {db_path}")
            yield session, run, ticket
    finally:
        engine.dispose()


def unsettled_refusal(db_path: Path, run_id: str) -> str | None:
    """Why run ``run_id``'s pass cannot end the turn, or None when it can."""
    with ledger(db_path, run_id) as (session, run, ticket):
        return unsettled_pass_message(session, ticket, run.stage_key, then=THEN)
