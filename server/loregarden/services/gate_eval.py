"""A gate-based eval baseline: how agents fare against transition gates.

An **episode** is every `GateEvaluated` event for one (ticket, from_stage,
to_stage), in time order, with stage spellings normalized. It is the unit an
eval can score: the agent handed a transition either cleared the gate, or was
handed the failure and tried again.

What the numbers are for is comparing adapters and models, and the metric that
separates them is **repair**, not first pass: nearly every episode passes on its
first attempt once harness noise is set aside, so a first-pass rate reads the
same for any competent model. Repair — was the failure fixed, in how many
tries, and did the agent hand back the identical failure — is where they differ.

Deliberately read-only and deterministic: the same events produce the same
scorecard, ordered by (created_at, event id). `skipped` and `disabled`
evaluations are not attempts — no gate ran — and are left out. `unavailable` is
an attempt that failed for harness reasons.

Pure over lists (`build_episodes`, `build_scorecard`) so the rules are testable
on a handful of events; `load_gate_events` is the only part that reads the DB.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from statistics import median

from loregarden.core.timestamps import as_utc
from loregarden.models.domain import (
    AgentRun,
    CliAdapter,
    DomainEvent,
    EventType,
    GateFailureCategory,
    GateFailureKind,
    GateFixTier,
    GateOutcome,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.gate_eval_classify import canonical_stage, classify_gate_failure
from loregarden.services.gate_producer import GateProducer, ProducerJoin, pick_producer
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Outcomes that mean a gate actually ran. The rest recorded that none did.
_ATTEMPT_OUTCOMES = (GateOutcome.PASSED, GateOutcome.FAILED, GateOutcome.UNAVAILABLE)


class GateEvent(BaseModel):
    """One `GateEvaluated` event with its ticket context, stages normalized."""

    event_id: str
    at: datetime
    ticket_id: str
    ticket_ref: str
    workspace: str
    ticket_state: TicketState
    outcome: GateOutcome
    message: str = ""
    from_stage: str
    to_stage: str
    command: str = ""
    #: None on events recorded before `fix_tier` existed.
    fix_tier: GateFixTier | None = None
    orchestration_run_id: str | None = None
    producer: GateProducer | None = None


class GateFailure(BaseModel):
    event_id: str
    at: datetime
    fix_tier: GateFixTier | None
    kind: GateFailureKind
    category: GateFailureCategory
    message: str
    producer: GateProducer | None = None


class GateEpisode(BaseModel):
    ticket_id: str
    ticket_ref: str
    workspace: str
    from_stage: str
    to_stage: str
    ticket_state: TicketState
    attempts: int
    first_pass: bool
    eventually_passed: bool
    #: 1-based attempt of the first pass; None when it never passed.
    attempts_to_pass: int | None
    #: The agent was handed a work failure and returned the byte-identical one.
    repeated_identical_failure: bool
    failure_kinds: list[GateFailureKind]
    failure_categories: list[GateFailureCategory]
    failures: list[GateFailure]
    #: Who produced the first attempt, when known.
    producer: GateProducer | None = None

    @property
    def transition(self) -> str:
        return f"{self.from_stage}→{self.to_stage}"

    @property
    def first_attempt_clean(self) -> bool:
        """The first attempt had no failure the agent caused. `unknown` does not
        count against it — it is unscored, and listed separately."""
        first = self.failures[0] if self.failures and not self.first_pass else None
        return first is None or first.kind is not GateFailureKind.WORK

    @property
    def needed_repair(self) -> bool:
        return GateFailureKind.WORK in self.failure_kinds


def build_episodes(events: Iterable[GateEvent]) -> list[GateEpisode]:
    """Group attempts into episodes, ordered by (workspace, ticket, first attempt)."""
    grouped: dict[tuple[str, str, str], list[GateEvent]] = defaultdict(list)
    for event in events:
        if event.outcome not in _ATTEMPT_OUTCOMES:
            continue
        key = (event.ticket_id, canonical_stage(event.from_stage), canonical_stage(event.to_stage))
        grouped[key].append(event)
    episodes = [
        _episode(sorted(attempts, key=lambda e: (as_utc(e.at), e.event_id)))
        for attempts in grouped.values()
    ]
    episodes.sort(key=lambda ep: (ep.workspace, ep.ticket_ref, ep.from_stage, ep.to_stage))
    return episodes


def _episode(attempts: Sequence[GateEvent]) -> GateEpisode:
    first = attempts[0]
    from_stage = canonical_stage(first.from_stage)
    failures = [
        _failure(event, from_stage) for event in attempts if event.outcome is not GateOutcome.PASSED
    ]
    pass_at = next(
        (i for i, e in enumerate(attempts, start=1) if e.outcome is GateOutcome.PASSED), None
    )
    return GateEpisode(
        ticket_id=first.ticket_id,
        ticket_ref=first.ticket_ref,
        workspace=first.workspace,
        from_stage=from_stage,
        to_stage=canonical_stage(first.to_stage),
        ticket_state=attempts[-1].ticket_state,
        attempts=len(attempts),
        first_pass=pass_at == 1,
        eventually_passed=pass_at is not None,
        attempts_to_pass=pass_at,
        repeated_identical_failure=_repeated_identical(attempts, failures),
        failure_kinds=list(dict.fromkeys(f.kind for f in failures)),
        failure_categories=list(dict.fromkeys(f.category for f in failures)),
        failures=failures,
        producer=first.producer,
    )


def _failure(event: GateEvent, from_stage: str) -> GateFailure:
    cls = classify_gate_failure(event.message, from_stage=from_stage, outcome=event.outcome)
    return GateFailure(
        event_id=event.event_id,
        at=event.at,
        fix_tier=event.fix_tier,
        kind=cls.kind,
        category=cls.category,
        message=event.message,
        producer=event.producer,
    )


def _repeated_identical(attempts: Sequence[GateEvent], failures: Sequence[GateFailure]) -> bool:
    """Two consecutive attempts failed as work with the same message, and the
    second was not merely a mechanical fixer's re-check.

    A mechanical re-evaluation returning the same text says the fixer could not
    help, not that an agent resubmitted; counting it would charge the model for
    the autofix tier. A missing `fix_tier` (older events) is counted, since
    those rows cannot say either way.
    """
    by_event = {f.event_id: f for f in failures}
    for prev, cur in zip(attempts, attempts[1:], strict=False):
        a, b = by_event.get(prev.event_id), by_event.get(cur.event_id)
        if (
            a is not None
            and b is not None
            and a.kind is GateFailureKind.WORK
            and b.kind is GateFailureKind.WORK
            and a.message == b.message
            and b.fix_tier is not GateFixTier.MECHANICAL
        ):
            return True
    return False


# --- scorecard ---------------------------------------------------------------


class ScoreLine(BaseModel):
    """Headline numbers for a set of episodes. A rate is None when nothing was
    eligible — "no repairs happened" must not read as "0% of repairs worked"."""

    episodes: int
    first_attempt_clean_rate: float | None
    first_pass_rate: float | None
    harness_blocked_rate: float | None
    repair_episodes: int
    repair_recovery_rate: float | None
    identical_resubmission_rate: float | None
    attempts_to_pass_median: float | None
    attempts_to_pass_max: int | None
    failures_by_kind: dict[GateFailureKind, int]


class NeverPassed(BaseModel):
    ticket_ref: str
    workspace: str
    transition: str
    attempts: int
    ticket_state: TicketState
    failure_kinds: list[GateFailureKind]
    last_message: str


class UnknownFailure(BaseModel):
    ticket_ref: str
    workspace: str
    transition: str
    message: str


class ProducerCoverage(BaseModel):
    """How many failures could be tied to an agent run, and by which join."""

    failures: int
    by_join: dict[str, int]


class GateScorecard(BaseModel):
    overall: ScoreLine
    by_workspace: dict[str, ScoreLine]
    #: workspace → transition → line.
    by_transition: dict[str, dict[str, ScoreLine]]
    #: "adapter/model" of the episode's first attempt → line. "unknown/unknown"
    #: when no producer could be joined.
    by_producer: dict[str, ScoreLine]
    never_passed: list[NeverPassed]
    harness_causes: dict[GateFailureCategory, int]
    unknown_failures: list[UnknownFailure]
    producer_coverage: ProducerCoverage
    #: Events whose payload could not be read; counted, never silently dropped.
    unreadable_events: int = 0


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(episodes: Sequence[GateEpisode]) -> ScoreLine:
    repairs = [ep for ep in episodes if ep.needed_repair]
    to_pass = [ep.attempts_to_pass for ep in episodes if ep.attempts_to_pass is not None]
    kinds = Counter(f.kind for ep in episodes for f in ep.failures)
    return ScoreLine(
        episodes=len(episodes),
        first_attempt_clean_rate=_rate(
            sum(ep.first_attempt_clean for ep in episodes), len(episodes)
        ),
        first_pass_rate=_rate(sum(ep.first_pass for ep in episodes), len(episodes)),
        harness_blocked_rate=_rate(
            sum(GateFailureKind.HARNESS in ep.failure_kinds for ep in episodes), len(episodes)
        ),
        repair_episodes=len(repairs),
        repair_recovery_rate=_rate(sum(ep.eventually_passed for ep in repairs), len(repairs)),
        identical_resubmission_rate=_rate(
            sum(ep.repeated_identical_failure for ep in repairs), len(repairs)
        ),
        attempts_to_pass_median=float(median(to_pass)) if to_pass else None,
        attempts_to_pass_max=max(to_pass) if to_pass else None,
        failures_by_kind={kind: kinds[kind] for kind in GateFailureKind if kinds[kind]},
    )


def _producer_key(ep: GateEpisode) -> str:
    adapter = ep.producer.adapter.value if ep.producer and ep.producer.adapter else "unknown"
    model = ep.producer.model if ep.producer and ep.producer.model else "unknown"
    return f"{adapter}/{model}"


def build_scorecard(
    episodes: Sequence[GateEpisode], *, unreadable_events: int = 0
) -> GateScorecard:
    by_ws: dict[str, list[GateEpisode]] = defaultdict(list)
    by_tr: dict[str, dict[str, list[GateEpisode]]] = defaultdict(lambda: defaultdict(list))
    by_prod: dict[str, list[GateEpisode]] = defaultdict(list)
    for ep in episodes:
        by_ws[ep.workspace].append(ep)
        by_tr[ep.workspace][ep.transition].append(ep)
        by_prod[_producer_key(ep)].append(ep)
    failures = [f for ep in episodes for f in ep.failures]
    joins = Counter(f.producer.join.value if f.producer else "none" for f in failures)
    return GateScorecard(
        overall=score(episodes),
        by_workspace={ws: score(eps) for ws, eps in sorted(by_ws.items())},
        by_transition={
            ws: {tr: score(eps) for tr, eps in sorted(trs.items())}
            for ws, trs in sorted(by_tr.items())
        },
        by_producer={key: score(eps) for key, eps in sorted(by_prod.items())},
        never_passed=[
            NeverPassed(
                ticket_ref=ep.ticket_ref,
                workspace=ep.workspace,
                transition=ep.transition,
                attempts=ep.attempts,
                ticket_state=ep.ticket_state,
                failure_kinds=ep.failure_kinds,
                last_message=ep.failures[-1].message if ep.failures else "",
            )
            for ep in episodes
            if not ep.eventually_passed
        ],
        harness_causes=dict(
            sorted(
                Counter(f.category for f in failures if f.kind is GateFailureKind.HARNESS).items()
            )
        ),
        unknown_failures=[
            UnknownFailure(
                ticket_ref=ep.ticket_ref,
                workspace=ep.workspace,
                transition=ep.transition,
                message=f.message,
            )
            for ep in episodes
            for f in ep.failures
            if f.kind is GateFailureKind.UNKNOWN
        ],
        producer_coverage=ProducerCoverage(
            failures=len(failures), by_join=dict(sorted(joins.items()))
        ),
        unreadable_events=unreadable_events,
    )


# --- loading -----------------------------------------------------------------


class _GatePayload(BaseModel):
    """The `GateEvaluated` payload as `gate_observability` writes it. Fields
    added later are optional so every historical row still reads."""

    model_config = ConfigDict(extra="ignore")

    outcome: GateOutcome
    message: str = ""
    from_stage: str
    to_stage: str
    command: str = ""
    fix_tier: GateFixTier | None = None
    orchestration_run_id: str | None = None
    agent_run_id: str | None = None
    agent_id: str | None = None
    adapter: CliAdapter | None = None
    model: str | None = None


class LoadedGateEvents(BaseModel):
    events: list[GateEvent]
    unreadable: int


def load_gate_events(
    session: Session,
    *,
    workspace_slug: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> LoadedGateEvents:
    """Every `GateEvaluated` event with its ticket, workspace and producer.

    Producers come from the payload when the event recorded one, and otherwise
    from `pick_producer` over the ticket's agent runs — see `ProducerJoin` for
    how far to trust each.
    """
    stmt = (
        select(DomainEvent, Ticket, Workspace)
        .join(Ticket, col(Ticket.id) == col(DomainEvent.ticket_id))
        .join(Workspace, col(Workspace.id) == col(Ticket.workspace_id))
        .where(col(DomainEvent.type) == EventType.GATE_EVALUATED)
    )
    if workspace_slug:
        stmt = stmt.where(col(Workspace.slug) == workspace_slug)
    if since is not None:
        stmt = stmt.where(col(DomainEvent.created_at) >= since)
    if until is not None:
        stmt = stmt.where(col(DomainEvent.created_at) < until)
    rows = session.exec(stmt).all()

    ticket_ids = {ticket.id for _, ticket, _ in rows}
    runs_by_ticket: dict[str, list[AgentRun]] = defaultdict(list)
    if ticket_ids:
        for run in session.exec(select(AgentRun).where(col(AgentRun.ticket_id).in_(ticket_ids))):
            runs_by_ticket[run.ticket_id].append(run)

    events: list[GateEvent] = []
    unreadable = 0
    for event, ticket, workspace in rows:
        try:
            payload = _GatePayload.model_validate_json(event.payload_json)
        except ValidationError as exc:
            unreadable += 1
            logger.warning("GateEvaluated %s has an unreadable payload: %s", event.id, exc)
            continue
        events.append(
            GateEvent(
                event_id=event.id,
                at=as_utc(event.created_at),
                ticket_id=ticket.id,
                ticket_ref=ticket.external_id,
                workspace=workspace.slug,
                ticket_state=ticket.state,
                outcome=payload.outcome,
                message=payload.message,
                from_stage=payload.from_stage,
                to_stage=payload.to_stage,
                command=payload.command,
                fix_tier=payload.fix_tier,
                orchestration_run_id=payload.orchestration_run_id,
                producer=_producer(payload, runs_by_ticket[ticket.id], event.created_at),
            )
        )
    return LoadedGateEvents(events=events, unreadable=unreadable)


def _producer(payload: _GatePayload, runs: Sequence[AgentRun], at: datetime) -> GateProducer | None:
    if payload.agent_run_id:
        recorded = next((r for r in runs if r.id == payload.agent_run_id), None)
        return GateProducer(
            agent_run_id=payload.agent_run_id,
            agent_id=payload.agent_id or (recorded.agent_id if recorded else ""),
            adapter=payload.adapter,
            model=payload.model,
            join=ProducerJoin.RECORDED,
        )
    return pick_producer(
        runs,
        orchestration_run_id=payload.orchestration_run_id,
        stage_key=payload.from_stage,
        before=at,
    )


def gate_scorecard(
    session: Session,
    *,
    workspace_slug: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[GateScorecard, list[GateEpisode]]:
    loaded = load_gate_events(session, workspace_slug=workspace_slug, since=since, until=until)
    episodes = build_episodes(loaded.events)
    return build_scorecard(episodes, unreadable_events=loaded.unreadable), episodes
