"""What a run waiting for capacity says in its own log.

A run is RUNNING from the moment it is scheduled, and its log said "Queuing
agent…" over a live line of "Agent running…" until it got capacity, so a run an
hour back in line looked like one at work (lmkr-execution-leaves-12, 2026-11-23).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from loregarden.dot_line import SYS
from loregarden.models.domain import AgentRun, DockerLease
from loregarden.services.capacity_line import leases_ahead
from loregarden.services.capacity_progress import format_seconds
from loregarden.services.docker_ledger import OCCUPYING
from loregarden.services.run_log_stream import RunLogStreamer
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: How many of the claims ahead are named; the rest are counted.
NAMED = 2


def describe_wait(session: Session, lease: DockerLease) -> str:
    """What this claim asked for and what it is behind."""
    ahead = list(leases_ahead(session, lease).values())
    holding = sorted((row for row in ahead if row.status in OCCUPYING), key=lambda row: -row.cpus)
    waiting = len(ahead) - len(holding)
    text = (
        f"Waiting for {lease.pool.value} capacity "
        f"({lease.cpus:g} cpus, {lease.memory_mb} MB); no agent has started."
    )
    if holding:
        names = ", ".join(f"{row.holder_label} ({row.cpus:g} cpus)" for row in holding[:NAMED])
        more = f" and {len(holding) - NAMED} more" if len(holding) > NAMED else ""
        text += f" Holding it: {names}{more}."
    if waiting:
        text += f" {waiting} ahead in line."
    return text


class RunWaitLog:
    """Writes a run's wait for capacity to its log: once when it starts, its live
    line while it lasts, and once when it ends.

    Best effort towards the run, never silent: a log write that fails is logged,
    and the run still gets its capacity.
    """

    def __init__(self, run: AgentRun, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._run_code = run.run_code
        self._clock = clock
        self._started = clock()
        self._said = False
        # A run with no ticket has no log artifact to write to.
        self._streamer = (
            RunLogStreamer(
                run_id=run.id,
                ticket_id=run.ticket_id,
                run_code=run.run_code,
                agent_id=run.agent_id,
                skill_name=run.skill_name or "",
            )
            if run.ticket_id
            else None
        )

    def waiting(self, session: Session, lease_id: str) -> None:
        """Say what the run waits for; call again to refresh the live line."""
        lease = session.get(DockerLease, lease_id)
        if lease is None:
            return
        text = describe_wait(session, lease)
        waited = self._clock() - self._started
        self._write(text, live=f"{text} Waited {format_seconds(waited)}.", line=not self._said)
        self._said = True

    def granted(self) -> None:
        """Say the wait is over. Nothing is said for a run that never waited."""
        if self._said:
            waited = format_seconds(self._clock() - self._started)
            text = f"Capacity granted after {waited}; starting the agent."
            self._write(text, live=text, line=True)

    def _write(self, text: str, *, live: str, line: bool) -> None:
        if self._streamer is None:
            return
        try:
            self._streamer.resume()
            if line:
                self._streamer.append_line(SYS / text, force=True)
            self._streamer.set_live(live)
        except SQLAlchemyError:
            logger.exception("Run %s: its capacity wait could not be logged", self._run_code)
