"""Which learnings went into which run, and how that run ended (lg-improved-memory-178).

Three jobs, one module, because they are one ledger:

1. **Link.** `record_applications` writes one `learning_applications` row per
   learning a briefing actually injected, keyed by the real `agent_runs.id` and
   the graph `node_id`. Called only from `record_briefing`, the single seam that
   already records every assembly.
2. **Settle.** `settle_pending` grades concluded runs onto the four-rung ladder
   from signals the control plane recorded itself — never from anything the
   run's agent reported. There is no self-report path, by construction: nothing
   here reads a stage report, a verdict, or an agent's text.
3. **Read.** `confidence_for` folds settled rows into a Beta posterior via
   `learning_confidence.score`.

The ladder, and where each rung is read from. The window is the run's own
stage: from the run's start until the next run of the same stage on the same
ticket starts (a re-run is what closes it), or open while there is none.

- ``blocked`` — an `ORCHESTRATOR_DECISION` event, `classified_block`, for this
  stage. Every path that leaves a ticket blocked goes through `settle_block` →
  `record_block`, which emits it.
- ``rerouted`` — a rework-feedback artifact targeting this stage (a reviewer
  sent the work back) or a transition-gate-failure artifact for it (the gate
  sent it back to its agent). Both are the durable ledgers the reroute caps
  count, so this counts exactly what the caps count. A failed run whose stage
  re-ran with neither is a retry, and is also graded ``rerouted``.
- ``passed_after_autofix`` — a `GATE_EVALUATED` event for this stage, passed,
  with fix tier ``mechanical``.
- ``clean_pass`` — the run succeeded and none of the above appeared.

The worst rung found wins. A run is settled when it is terminal and either a
block has been recorded (nothing is worse), its window has closed, or the
ticket is finished. A cancelled run settles with no rung: it says nothing about
the learning, and grading it would be a measurement nobody took.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    DomainEvent,
    EventType,
    GateFixTier,
    GateOutcome,
    LearningApplication,
    LearningOutcomeRung,
    OrchestratorDecision,
    RunStatus,
    Ticket,
    TicketState,
    utcnow,
)
from loregarden.services.learning_confidence import (
    UNOBSERVED,
    LearningConfidence,
    Observation,
    score,
)
from loregarden.services.rework_feedback import (
    REWORK_FEEDBACK_KIND,
    rework_feedback_artifact_title,
)
from loregarden.services.stage_retry_budget import gate_failure_artifact_title
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_TERMINAL_RUNS = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)
_FINISHED_TICKETS = (TicketState.DONE, TicketState.WONT_DO)
#: Settlement runs once per briefing; this bounds what one call can cost.
SETTLE_BATCH = 200
_RUNG_ORDER = list(LearningOutcomeRung)


class _SignalPayload(BaseModel):
    """The fields of a gate or decision event payload this module reads."""

    stage_key: str = ""
    outcome: str = ""
    fix_tier: str = ""
    decision: str = ""


@dataclass(frozen=True, slots=True)
class Settlement:
    settled: bool
    rung: LearningOutcomeRung | None = None


_PENDING = Settlement(settled=False)


def record_applications(
    session: Session, run: AgentRun, *, briefing_id: str, node_ids: Sequence[str]
) -> int:
    """Link each injected learning to `run`. Returns how many rows were added.

    Idempotent per (run, learning): a second assembly for the same run adds
    nothing. The caller owns the transaction.
    """
    if not node_ids:
        return 0
    existing = set(
        session.exec(
            select(LearningApplication.node_id).where(LearningApplication.run_id == run.id)
        ).all()
    )
    added = 0
    for position, node_id in enumerate(node_ids):
        if node_id in existing:
            continue
        existing.add(node_id)
        session.add(
            LearningApplication(
                run_id=run.id,
                briefing_id=briefing_id or None,
                node_id=node_id,
                workspace_id=run.workspace_id,
                ticket_id=run.ticket_id,
                stage_key=run.stage_key,
                position=position,
            )
        )
        added += 1
    return added


def _payloads(events: Iterable[DomainEvent]) -> list[_SignalPayload]:
    parsed: list[_SignalPayload] = []
    for event in events:
        try:
            parsed.append(_SignalPayload.model_validate_json(event.payload_json or "{}"))
        except ValidationError:
            # A malformed payload is not evidence of anything; say so and skip it.
            logger.warning("learning outcomes: unreadable payload on event %s", event.id)
    return parsed


def _stage_window_end(session: Session, run: AgentRun) -> datetime | None:
    """When the next run of this stage on this ticket started, if one has."""
    return session.exec(
        select(AgentRun.started_at)
        .where(AgentRun.ticket_id == run.ticket_id)
        .where(AgentRun.stage_key == run.stage_key)
        .where(AgentRun.id != run.id)
        .where(col(AgentRun.started_at).is_not(None))
        .where(col(AgentRun.started_at) > run.started_at)
        .order_by(col(AgentRun.started_at))
    ).first()


def _in_window(column, start: datetime, end: datetime | None):
    clauses = [column >= start]
    if end is not None:
        clauses.append(column < end)
    return clauses


def _signals(
    session: Session, run: AgentRun, start: datetime, end: datetime | None
) -> set[LearningOutcomeRung]:
    """Every rung the recorded signals in this run's window point at."""
    found: set[LearningOutcomeRung] = set()
    events = session.exec(
        select(DomainEvent)
        .where(DomainEvent.ticket_id == run.ticket_id)
        .where(
            col(DomainEvent.type).in_([EventType.GATE_EVALUATED, EventType.ORCHESTRATOR_DECISION])
        )
        .where(*_in_window(DomainEvent.created_at, start, end))
    ).all()
    for payload in _payloads(events):
        if payload.stage_key != run.stage_key:
            continue
        if payload.decision == OrchestratorDecision.CLASSIFIED_BLOCK:
            found.add(LearningOutcomeRung.BLOCKED)
        elif payload.outcome == GateOutcome.PASSED and payload.fix_tier == GateFixTier.MECHANICAL:
            found.add(LearningOutcomeRung.PASSED_AFTER_AUTOFIX)
    reroute_ledgers = session.exec(
        select(Artifact.id)
        .where(Artifact.ticket_id == run.ticket_id)
        .where(
            (
                (Artifact.kind == REWORK_FEEDBACK_KIND)
                & (Artifact.title == rework_feedback_artifact_title(run.stage_key))
            )
            | (
                (Artifact.kind == ArtifactKind.ERROR)
                & (Artifact.title == gate_failure_artifact_title(run.stage_key))
            )
        )
        .where(*_in_window(Artifact.created_at, start, end))
    ).first()
    if reroute_ledgers is not None:
        found.add(LearningOutcomeRung.REROUTED)
    return found


def derive_settlement(session: Session, run: AgentRun) -> Settlement:
    """Grade one run onto the ladder, or report it is not concluded yet."""
    if run.status not in _TERMINAL_RUNS:
        return _PENDING
    if run.status is RunStatus.CANCELLED or run.started_at is None or run.ticket_id is None:
        return Settlement(settled=True)
    end = _stage_window_end(session, run)
    found = _signals(session, run, run.started_at, end)
    if LearningOutcomeRung.BLOCKED in found:
        return Settlement(settled=True, rung=LearningOutcomeRung.BLOCKED)
    ticket = session.get(Ticket, run.ticket_id)
    finished = ticket is not None and ticket.state in _FINISHED_TICKETS
    if end is None and not finished:
        return _PENDING
    if run.status is RunStatus.FAILED and end is not None:
        found.add(LearningOutcomeRung.REROUTED)
    if not found:
        if run.status is RunStatus.SUCCEEDED:
            return Settlement(settled=True, rung=LearningOutcomeRung.CLEAN_PASS)
        return Settlement(settled=True)
    return Settlement(settled=True, rung=max(found, key=_RUNG_ORDER.index))


def settle_pending(session: Session, *, limit: int = SETTLE_BATCH) -> int:
    """Grade every unsettled application whose run has concluded. Returns rows settled.

    The caller owns the transaction. Rows whose run is still open are left
    alone and picked up by a later call.
    """
    rows = session.exec(
        select(LearningApplication, AgentRun)
        .join(AgentRun, col(AgentRun.id) == LearningApplication.run_id)
        .where(col(LearningApplication.settled_at).is_(None))
        .where(col(AgentRun.status).in_(_TERMINAL_RUNS))
        .order_by(col(LearningApplication.created_at))
        .limit(limit)
    ).all()
    by_run: dict[str, Settlement] = {}
    settled = 0
    now = utcnow()
    for application, run in rows:
        if run.id not in by_run:
            by_run[run.id] = derive_settlement(session, run)
        outcome = by_run[run.id]
        if not outcome.settled:
            continue
        application.outcome = outcome.rung
        application.settled_at = now
        session.add(application)
        settled += 1
    return settled


def confidence_for(
    session: Session, node_ids: Sequence[str], *, as_of: datetime | None = None
) -> dict[str, LearningConfidence]:
    """The posterior for each node id; `UNOBSERVED` for one with no settled rung.

    Pooled across workspaces on purpose: a node id is global, and a lesson
    that holds up in one repository is evidence about it everywhere.
    """
    if not node_ids:
        return {}
    reference = as_of or utcnow()
    rows = session.exec(
        select(
            LearningApplication.node_id, LearningApplication.outcome, LearningApplication.created_at
        )
        .where(col(LearningApplication.node_id).in_(list(node_ids)))
        .where(col(LearningApplication.outcome).is_not(None))
    ).all()
    grouped: dict[str, list[Observation]] = {}
    for node_id, rung, surfaced_at in rows:
        grouped.setdefault(node_id, []).append(
            Observation(rung=rung, observed_at=_aware(surfaced_at))
        )
    return {
        node_id: score(grouped[node_id], as_of=_aware(reference))
        if node_id in grouped
        else UNOBSERVED
        for node_id in node_ids
    }


def ladder_counts(session: Session, node_id: str) -> dict[LearningOutcomeRung, int]:
    """How many settled runs landed on each rung, for one learning."""
    counts = dict.fromkeys(LearningOutcomeRung, 0)
    rungs = session.exec(
        select(LearningApplication.outcome)
        .where(LearningApplication.node_id == node_id)
        .where(col(LearningApplication.outcome).is_not(None))
    ).all()
    for rung in rungs:
        counts[rung] += 1
    return counts


def _aware(moment: datetime) -> datetime:
    """SQLite hands datetimes back naive; every stamp here was written in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=utcnow().tzinfo)
