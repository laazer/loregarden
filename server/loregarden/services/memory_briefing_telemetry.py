"""Briefing telemetry — the sole writer, classifier and aggregate reader.

The memory pipeline degrades silently by design: a stalled iCloud vault must
never fail a run. Nothing recorded that degradation anywhere anyone looked, so
an empty briefing was indistinguishable from "no memory exists yet", and a dead
retrieval path stayed dead for a month without producing a single signal.

This module is the one place `memory_briefings` is written to and aggregated
from. Nothing else may INSERT into or SELECT-aggregate that table: a second
classifier drifts from this one, and the drift shows up as summary numbers that
disagree with the rows they claim to summarise.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from loregarden.agents.inherited_wisdom import InheritedWisdom
from loregarden.models.domain import (
    AgentRun,
    MemoryBriefing,
    MemoryBriefingAssembly,
    MemoryBriefingOutcome,
    MemoryStoreState,
    Ticket,
    utcnow,
)
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def classify(result: InheritedWisdom, *, skipped: bool) -> MemoryBriefingOutcome:
    """Which bucket one assembly belongs in.

    Precedence: SKIPPED > STORE_ERROR > BUILT > NO_STORE > EMPTY.

    Every rule but the content one reads `store_states` and nothing else.
    Deriving error-versus-empty from `checkpoints_injected` /
    `learnings_injected` is the exact defect this ticket exists to remove: a
    store that would not open and a store that held nothing produce identical
    counters, and only the store states can tell them apart.
    """
    if skipped:
        return MemoryBriefingOutcome.SKIPPED
    states = list(result.store_states.values())
    if MemoryStoreState.ERRORED in states:
        return MemoryBriefingOutcome.STORE_ERROR
    if result.chars_injected > 0:
        return MemoryBriefingOutcome.BUILT
    if MemoryStoreState.READ not in states:
        return MemoryBriefingOutcome.NO_STORE
    return MemoryBriefingOutcome.EMPTY


def record_briefing(
    session: Session,
    run: AgentRun,
    ticket: Ticket,
    result: InheritedWisdom,
    *,
    skipped: bool,
    assembly_source: MemoryBriefingAssembly,
) -> str:
    """Persist one briefing assembly. Returns the row id, or "" if nothing was written.

    Never raises: telemetry inherits the briefing's never-fatal property, so a
    failed write costs the row and nothing else. The failure is logged with a
    traceback rather than swallowed, so a seam that has stopped recording is
    visible in the log as well as in `briefing_stats`'s run-denominated holes.

    The write runs on its own `Session` over the caller's engine. The caller's
    session is READ only here: a failed INSERT on it would leave the run's
    transaction needing a rollback the run lifecycle never asked for, and the
    damage would surface at its next commit, far from here.

    Ticket 178 attaches its surfaced-learning rows by foreign-keying the id this
    returns; extend this function's signature and body rather than adding a
    second writer.
    """
    try:
        row = MemoryBriefing(
            run_id=run.id,
            ticket_id=ticket.id,
            workspace_id=run.workspace_id,
            stage_key=run.stage_key,
            assembly_source=assembly_source,
            outcome=classify(result, skipped=skipped),
            checkpoints_injected=result.checkpoints_injected,
            learnings_injected=result.learnings_injected,
            checkpoints_saturated=result.checkpoints_saturated,
            learnings_saturated=result.learnings_saturated,
            query_had_terms=result.query_had_terms,
            chars_injected=result.chars_injected,
            pre_truncation_chars=result.pre_truncation_chars,
            truncated=result.truncated,
            store_states_json=json.dumps(
                {kind.value: state.value for kind, state in result.store_states.items()},
                sort_keys=True,
            ),
            store_errors=",".join(result.store_errors),
            elapsed_ms=result.elapsed_ms,
        )
        with Session(session.get_bind()) as telemetry:
            telemetry.add(row)
            telemetry.commit()
            return row.id
    except Exception:
        logger.warning(
            "Memory briefing telemetry write failed for run %s", run.run_code, exc_info=True
        )
        return ""


class MemoryBriefingStats(BaseModel):
    """Briefing health over a window of runs, not over a window of rows."""

    window_days: int
    window_from: datetime
    window_to: datetime
    runs_in_window: int
    runs_with_briefing_row: int
    runs_with_no_briefing_row: int
    rows_in_window: int
    newest_run_at: datetime | None
    last_row_at: datetime | None
    built: int
    empty: int
    store_error: int
    no_store: int
    skipped: int


def briefing_stats(session: Session, *, window_days: int) -> MemoryBriefingStats:
    """Briefing outcomes over the last `window_days`, denominated over runs.

    The denominator is `agent_runs` rather than `memory_briefings`, and that is
    the point. A telemetry write that fails is silent by design (AC4), so an
    aggregate counting only its own rows reports the last healthy numbers
    forever once the seam dies — this ticket's own defect, one level up.
    Counting runs makes absence a positive number: `runs_with_no_briefing_row`,
    and a `newest_run_at` newer than `last_row_at`.

    `started_at IS NOT NULL` excludes runs that never reached prompt assembly
    (queued then cancelled). It is not exact: a run that died between
    `started_at` and the prompt build still counts as a hole. That errs toward
    reporting a hole that is not one, which is the safe direction for a health
    signal.

    Buckets count ROWS and group by the stored `outcome` column and nothing
    else. They may exceed `runs_with_briefing_row`, because two assemblies for
    one run — supervised dispatch and `render_stage_prompt` — is a live path.
    """
    window_to = utcnow()
    window_from = window_to - timedelta(days=window_days)
    in_window = (
        AgentRun.started_at.is_not(None),
        AgentRun.started_at >= window_from,
        AgentRun.started_at <= window_to,
    )

    runs_in_window, newest_run_at = session.exec(
        select(func.count(func.distinct(AgentRun.id)), func.max(AgentRun.started_at)).where(
            *in_window
        )
    ).one()

    rows_in_window, runs_with_briefing_row, last_row_at = session.exec(
        select(
            func.count(MemoryBriefing.id),
            func.count(func.distinct(MemoryBriefing.run_id)),
            func.max(MemoryBriefing.created_at),
        )
        .join(AgentRun, AgentRun.id == MemoryBriefing.run_id)
        .where(*in_window)
    ).one()

    buckets = dict.fromkeys(MemoryBriefingOutcome, 0)
    grouped = session.exec(
        select(MemoryBriefing.outcome, func.count(MemoryBriefing.id))
        .join(AgentRun, AgentRun.id == MemoryBriefing.run_id)
        .where(*in_window)
        .group_by(MemoryBriefing.outcome)
    ).all()
    for outcome, count in grouped:
        buckets[outcome] = int(count)

    return MemoryBriefingStats(
        window_days=window_days,
        window_from=window_from,
        window_to=window_to,
        runs_in_window=runs_in_window,
        runs_with_briefing_row=runs_with_briefing_row,
        runs_with_no_briefing_row=runs_in_window - runs_with_briefing_row,
        rows_in_window=rows_in_window,
        newest_run_at=newest_run_at,
        last_row_at=last_row_at,
        built=buckets[MemoryBriefingOutcome.BUILT],
        empty=buckets[MemoryBriefingOutcome.EMPTY],
        store_error=buckets[MemoryBriefingOutcome.STORE_ERROR],
        no_store=buckets[MemoryBriefingOutcome.NO_STORE],
        skipped=buckets[MemoryBriefingOutcome.SKIPPED],
    )
