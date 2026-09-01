"""Persisted per-(ticket, stage) dispatch counter — the circuit breaker that
stops a stage from being redispatched indefinitely (ticket 105).

The count is derived purely from dedicated ``Artifact`` rows, the same
durable-counter pattern as the gate-fix attempt counter (``count_gate_fix_attempts``,
below). It is deliberately *not* derived from ``AgentRun``
timestamps: two dispatches of the same self-redoing stage within one
``execute()`` call can land in the same instant (an agent whose stage report
reroutes to its own stage_key resets it straight back to PENDING), so there is
no per-run time gap to cluster on. Persisting an explicit marker per dispatch
pass is what makes the counter survive a server restart and hold across
separate orchestration runs against the same ticket.

One marker is written per dispatch *pass* — once for a whole parallel stage,
not once per member agent — because the caller invokes
``record_stage_dispatch`` once per turn of the orchestrator's main loop,
regardless of how many agents that turn spawns.

Four kinds of durable row live here, all scoped to (ticket, stage) by the
artifact title:

``stage_dispatch``
    The counter itself. One row per dispatch pass.
``stage_dispatch_override``
    One row per dispatch deliberately forced past an exhausted budget, carrying
    *who* forced it (`DispatchOrigin`) in ``content_json``.
``stage_dispatch_reroute``
    One row per dispatch the scope-denial reroute exemption let through free.
    The exemption is bounded by these, or an unconsumed
    ``tickets.scope_reroute_agent`` pin — which only self-clears when a dispatch
    picks the pinned agent — would hand a stage unlimited free retries.
``stage_retry_block``
    The structural mark that *this* breaker blocked the ticket, and the only
    thing `blocked_on_stage_retry_budget` reads. Blocking prose is written by
    half the control plane and by agents themselves; deciding "was this my
    block?" by searching it for a phrase makes a substring of English
    load-bearing for whether the counter resets.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    DispatchSurface,
    RunStatus,
    StageBudgetArtifactKind,
    StageFanoutGroup,
    StageFanoutOutcome,
    Ticket,
    Workspace,
    utcnow,
)
from loregarden.services.orchestration_profile import (
    RetryBudgetConfig,
    resolve_orchestration_profile,
)
from loregarden.services.run_lease import agent_run_lease_expired, run_has_renewer
from pydantic import BaseModel
from sqlmodel import Session, select

if TYPE_CHECKING:
    from loregarden.models.domain import OrchestrationRun
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

# The four dedicated artifact kinds, defined as an enum in
# `models.domain.enums` because they are persisted. The stage key rides in the
# title, scoping each row to (ticket, stage) together.
_DISPATCH_KIND = StageBudgetArtifactKind.DISPATCH
_DISPATCH_OVERRIDE_KIND = StageBudgetArtifactKind.DISPATCH_OVERRIDE
_DISPATCH_REROUTE_KIND = StageBudgetArtifactKind.DISPATCH_REROUTE
_RETRY_BLOCK_KIND = StageBudgetArtifactKind.RETRY_BLOCK


class DispatchOrigin(BaseModel):
    """Who asked for a dispatch, for the override audit row.

    ``run_id`` is deliberately absent: `Artifact.run_id` is a foreign key into
    ``agent_runs``, and every forced dispatch is recorded *before* the run it
    authorises exists. The orchestration run (when there is one) and the agent
    the caller named are what identify the asker at that moment.
    """

    surface: DispatchSurface
    orchestration_run_id: str = ""
    agent_id: str = ""


class StageBudgetState(BaseModel):
    """The shared read both enforcement paths make before they diverge.

    The orchestrated path turns ``at_budget`` into a blocked ticket and the
    standalone path turns it into a raised refusal; only that outcome is
    forked. The comparison and the reroute exemption behind it are decided
    here, once, so the two cannot drift into disagreeing about whether a stage
    is spent.
    """

    #: A scope-denial reroute this stage is still owed. Neither checked nor
    #: counted — see `_reroute_exempt`.
    exempt: bool
    attempts: int
    at_budget: bool


class StageDispatchDecision(BaseModel):
    """What a standalone dispatch owes the counter, decided before any write.

    Separating the decision from the write is what makes the refusal
    side-effect-free: `evaluate_standalone_stage_dispatch` raises having read
    only, and `commit_standalone_stage_dispatch` runs after the caller has
    finished mutating the ticket.
    """

    counts: bool
    forced: bool
    exempt_reroute: bool


#: How long a RUNNING run whose kind has no lease renewer may go unheard from
#: before it stops counting as evidence that a dispatch pass is in flight.
#:
#: `run_lease` deliberately never judges such a run — see `run_has_renewer` —
#: and that is right for *reaping*, where a false expiry kills live work. It is
#: wrong for grouping: an unjudgeable run held `dispatch_pass_open` True
#: forever, so every dispatch of the stage it stranded was neither checked nor
#: counted. The budget therefore asks a weaker question with a ceiling of its
#: own. Generous against a real stage (hours, not the ten-minute lease), because
#: overshooting only delays a refusal, while undershooting would charge each
#: member of an externally-harnessed parallel pass separately.
NO_RENEWER_DISPATCH_CEILING = timedelta(hours=6)


def _dispatch_title(stage_key: str) -> str:
    return f"stage-dispatch:{stage_key}"


def _override_title(stage_key: str) -> str:
    return f"stage-dispatch-override:{stage_key}"


def _reroute_title(stage_key: str) -> str:
    return f"stage-dispatch-reroute:{stage_key}"


def _block_title(stage_key: str) -> str:
    return f"stage-retry-block:{stage_key}"


def _markers(
    session: Session,
    ticket_id: str,
    kind: StageBudgetArtifactKind,
    title: str,
) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket_id)
            .where(Artifact.kind == kind)
            .where(Artifact.title == title)
        ).all()
    )


def _drop_markers(
    session: Session,
    ticket_id: str,
    kind: StageBudgetArtifactKind,
    title: str,
) -> int:
    rows = _markers(session, ticket_id, kind, title)
    for row in rows:
        session.delete(row)
    if rows:
        session.commit()
    return len(rows)


def record_stage_dispatch(session: Session, ticket_id: str, stage_key: str) -> None:
    """Record one dispatch pass of ``stage_key`` for ``ticket_id``.

    Called by the orchestrator exactly once per dispatch pass, before any agent
    for that pass runs. Committed so the count survives a restart and is visible
    to a fresh session.
    """
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_DISPATCH_KIND,
            title=_dispatch_title(stage_key),
        )
    )
    session.commit()


def count_stage_dispatches(session: Session, ticket_id: str, stage_key: str) -> int:
    """How many times ``record_stage_dispatch`` has run for this (ticket, stage)
    pair, ever — across every orchestration run against the ticket."""
    return len(_markers(session, ticket_id, _DISPATCH_KIND, _dispatch_title(stage_key)))


def clear_stage_dispatches(session: Session, ticket_id: str, stage_key: str) -> int:
    """Drop the persisted dispatch markers for ``(ticket_id, stage_key)``.

    A human resetting a stage to pending (or otherwise intervening after the
    breaker fires) must get a fresh budget — otherwise the next start blocks
    immediately on the same exhausted counter and the reset is a no-op.
    Returns how many markers were removed.

    The reroute-exemption ledger is cleared alongside it: a fresh budget that
    left the exemption spent would refuse the sibling implementer the pin exists
    to hand the stage to.
    """
    _drop_markers(session, ticket_id, _DISPATCH_REROUTE_KIND, _reroute_title(stage_key))
    return _drop_markers(session, ticket_id, _DISPATCH_KIND, _dispatch_title(stage_key))


def refund_stage_dispatch_budget(session: Session, ticket_id: str, stage_key: str) -> None:
    """Put this stage back exactly as the breaker had never seen it.

    `clear_stage_dispatches` drops the counter and the reroute ledger but leaves
    the `stage_retry_block` mark standing, which is right where the caller has
    just read that mark to decide the reset was owed
    (`refresh_stage_retry_budget` clears it itself, in that order). It is wrong
    for a caller that is simply handing the budget back: a stranded mark is read
    by `blocked_on_stage_retry_budget` as *this breaker's* block the next time
    the stage is blocked for anything at all, and earns a reset nobody asked
    for — the same free reset the prose fallback used to hand out, arriving
    through a stale row instead of a phrase.
    """
    clear_stage_dispatches(session, ticket_id, stage_key)
    _drop_markers(session, ticket_id, _RETRY_BLOCK_KIND, _block_title(stage_key))


def record_stage_retry_block(session: Session, ticket_id: str, stage_key: str) -> None:
    """Mark that this breaker is the reason the ticket is blocked."""
    if _markers(session, ticket_id, _RETRY_BLOCK_KIND, _block_title(stage_key)):
        return
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_RETRY_BLOCK_KIND,
            title=_block_title(stage_key),
        )
    )
    session.commit()


def clear_stage_retry_block(session: Session, ticket_id: str, stage_key: str) -> int:
    """Drop that mark — the block it recorded is being cleared."""
    return _drop_markers(session, ticket_id, _RETRY_BLOCK_KIND, _block_title(stage_key))


def blocked_on_stage_retry_budget(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """Whether the ticket's current block is *this* breaker's own block.

    Only that block earns a fresh counter when a human re-enters the stage: a
    ticket blocked for some other reason (an interrupted run, a failing gate)
    keeps its count, or the breaker could never accumulate across exactly the
    manual re-runs it exists to bound.

    Decided on the structural mark this module writes with the block, and on
    nothing else. There was a prose fallback — `"retry budget" in
    blocking_issues` — narrowed to fire only at budget; the narrowing was
    vacuous, because at budget is precisely the state an agent that wants a
    reset is in. `loregarden_block_ticket` is not denied to an orchestrated
    agent, so a stage at 5/5 could block itself with the right words and the
    next human start would reset the counter to 1 instead of refusing, skipping
    both the force decision and the `stage_dispatch_override` audit row. Blocks
    written before the mark existed are backfilled by migration
    `0102_backfill_stage_retry_block` rather than recognised by their text.
    """
    return bool(_markers(session, ticket.id, _RETRY_BLOCK_KIND, _block_title(stage_key)))


def stage_retry_block_message(stage_key: str, attempts: int, max_attempts: int) -> str:
    """ "" while ``attempts < max_attempts``; a human-readable block message once
    the budget is exhausted.

    Worded to hold whether the exhausted attempts passed or failed: the same
    breaker fires for a stage that kept reporting ``pass`` without the workflow
    advancing past it as for one that kept failing, so it must not accuse the
    stage of repeated failure.
    """
    if attempts < max_attempts:
        return ""
    return (
        f"Stage '{stage_key}' reached its retry budget of {max_attempts} dispatches "
        "without the workflow advancing past it. Blocking for a human rather than "
        "dispatching it again."
    )


def exceeds_stage_retry_budget(
    session: Session,
    ticket_id: str,
    stage_key: str,
    *,
    enabled: bool,
    max_attempts: int,
) -> str:
    """The composed pre-dispatch check the orchestrator calls: "" when the
    budget is disabled or the stage is still within it, else the block
    message."""
    if not enabled:
        return ""
    attempts = count_stage_dispatches(session, ticket_id, stage_key)
    return stage_retry_block_message(stage_key, attempts, max_attempts)


# -- the one predicate both enforcement paths share ---------------------------


def _reroute_exempt(session: Session, ticket: Ticket, stage_key: str, max_free: int) -> bool:
    """Whether a pending scope-denial reroute exempts this dispatch.

    A pending reroute is a handoff to the *sibling* implementer, not a retry of
    the same failing work: it re-dispatches the stage under a different agent
    doing a different half of the change, so it must neither consume this
    stage's dispatch budget nor trip its breaker.

    Bounded, because `tickets.scope_reroute_agent` self-clears only when a
    dispatch actually picks the pinned agent (`_consume_scope_reroute_pin`). A
    pin nothing ever consumes — the classifier keeps routing elsewhere, the
    pinned agent is not on the stage — would otherwise be an unlimited supply of
    free, uncounted dispatches, which is precisely the runaway this module
    exists to stop. Past ``max_free`` the pin stops exempting and the dispatch
    is charged like any other.
    """
    if not ticket.scope_reroute_agent:
        return False
    granted = len(_markers(session, ticket.id, _DISPATCH_REROUTE_KIND, _reroute_title(stage_key)))
    return granted < max_free


def record_reroute_exempt_dispatch(session: Session, ticket_id: str, stage_key: str) -> None:
    """Spend one of the reroute exemption's bounded free dispatches."""
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_DISPATCH_REROUTE_KIND,
            title=_reroute_title(stage_key),
        )
    )
    session.commit()


def stage_retry_budget_state(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    config: RetryBudgetConfig,
) -> StageBudgetState:
    """Read-only: is this (ticket, stage) exempt, and is it spent?

    The single comparison both `enforce_stage_retry_budget` (which blocks) and
    `evaluate_standalone_stage_dispatch` (which raises) are built on.
    """
    if _reroute_exempt(session, ticket, stage_key, config.max_attempts_per_stage):
        return StageBudgetState(exempt=True, attempts=0, at_budget=False)
    attempts = count_stage_dispatches(session, ticket.id, stage_key)
    return StageBudgetState(
        exempt=False,
        attempts=attempts,
        at_budget=config.enabled and attempts >= config.max_attempts_per_stage,
    )


def enforce_stage_retry_budget(
    session: Session,
    callbacks: OrchestrationCallbackService,
    orch_run: OrchestrationRun,
    ticket: Ticket,
    stage_key: str,
    config: RetryBudgetConfig,
) -> OrchestrationRun | None:
    """Pre-dispatch guard for the orchestrator's main loop.

    When ``stage_key`` is at its retry budget: block the ticket and return the
    now-BLOCKED run for the caller to hand straight back. Otherwise record this
    dispatch pass and return ``None`` so the caller proceeds.

    The block, not a raise: the orchestrator has a run to park the failure on,
    and a human comes back to a blocked ticket. That is the only half of this
    that differs from the standalone paths below.
    """
    state = stage_retry_budget_state(session, ticket, stage_key, config)
    if state.exempt:
        record_reroute_exempt_dispatch(session, ticket.id, stage_key)
        return None
    if state.at_budget:
        callbacks.block_ticket(
            orch_run,
            ticket,
            stage_key=stage_key,
            message=stage_retry_block_message(
                stage_key, state.attempts, config.max_attempts_per_stage
            ),
        )
        record_stage_retry_block(session, ticket.id, stage_key)
        session.refresh(orch_run)
        return orch_run
    record_stage_dispatch(session, ticket.id, stage_key)
    return None


# -- The standalone dispatch paths --------------------------------------------
# `enforce_stage_retry_budget` above is the orchestrator's version: it has an
# OrchestrationRun to park a block on, so an exhausted budget becomes a blocked
# ticket a human comes back to. The manual "Run stage" button and MCP
# `loregarden_start_stage` have no such run, and blocking there is worse than
# useless: `_prepare_stage_start` clears the counter whenever it re-enters a
# stage the breaker blocked, so a block would wipe the very budget that produced
# it and the breaker would never hold for more than one start. The standalone
# version therefore *refuses* — it raises before writing any state — and the
# human whose click it refused can say `force` to spend the budget deliberately.


class StageRetryBudgetExceeded(ValueError):
    """A standalone dispatch refused because the stage is at its retry budget.

    A `ValueError` because that is the refusal every other `start_run` guard
    raises, and what the API layer already maps to a non-2xx.
    """


def record_stage_dispatch_override(
    session: Session,
    ticket_id: str,
    stage_key: str,
    origin: DispatchOrigin,
) -> None:
    """Record that somebody forced one dispatch past an exhausted budget.

    Durable, like the counter itself, so the decision survives a restart and is
    visible to a fresh session — spending a budget the breaker had already
    closed is exactly the kind of choice that should leave a trace, and a trace
    that says only "this happened" cannot tell a human's click apart from an
    agent's own `loregarden_start_stage`. So ``content_json`` carries the
    surface the force arrived on, the orchestration run behind it, the agent
    named, and when.
    """
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_DISPATCH_OVERRIDE_KIND,
            title=_override_title(stage_key),
            content_json=json.dumps(
                {
                    "stage_key": stage_key,
                    "forced_at": utcnow().isoformat(),
                    **origin.model_dump(mode="json"),
                }
            ),
        )
    )
    session.commit()


def stage_retry_refusal_message(stage_key: str, attempts: int, max_attempts: int) -> str:
    """The refusal text. It names both the budget that tripped and the way to
    override it, so the human reading it knows what happened and what to do."""
    return (
        f"Stage '{stage_key}' has already been dispatched {attempts} times, reaching its "
        f"retry budget of {max_attempts}. Reset the stage, or start it again with "
        "force=true to dispatch it anyway."
    )


def resolve_stage_retry_budget(session: Session, ticket: Ticket) -> RetryBudgetConfig:
    """The budget configured for this ticket's workspace.

    The same `profile.retry_budget` the orchestrator loop passes to
    `enforce_stage_retry_budget`, resolved here so the standalone paths honour
    one configured number rather than a literal of their own.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        return RetryBudgetConfig()
    return resolve_orchestration_profile(workspace).retry_budget


def _is_live_dispatch_evidence(session: Session, run: AgentRun) -> bool:
    """Whether this run is live enough to prove a dispatch pass is in flight.

    A stricter question than "should this run be reaped?", and deliberately not
    the same one. `agent_run_lease_expired` fails closed for a kind with no
    renewer (`run_lease.run_has_renewer`: an externally-harnessed run has none),
    which is correct for reaping — a false expiry would kill work an agent is
    still doing — but reading "not judgeable" as "in flight" made a stranded
    external run an unbounded, uncounted bypass: `dispatch_pass_open` stayed
    True forever, so every later dispatch of that stage was neither checked nor
    recorded.

    So the budget puts its own ceiling on the case `run_lease` will not judge,
    rather than asking `run_lease` to change a policy that is right for it. A
    recorded pid still settles the question outright, via the lease check, and
    is not second-guessed here: a live process is evidence, however long it has
    been quiet.
    """
    if agent_run_lease_expired(session, run):
        return False
    if run_has_renewer(run) or run.handoff_pid is not None:
        return True
    stamp = run.last_seen_at or run.started_at or run.created_at
    if stamp is None:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp <= NO_RENEWER_DISPATCH_CEILING


def dispatch_pass_open(
    session: Session,
    ticket_id: str,
    stage_key: str,
    *,
    stage_running: bool,
) -> bool:
    """Whether a dispatch pass for this stage is already in flight.

    The seams the standalone guard sits in fire once per *parallel member*, not
    once per dispatch, so a member joining a pass that is already going must not
    be checked or counted again — a per-member read would see the counter its
    own first member just advanced and refuse members 2 and 3, tearing the last
    affordable parallel pass in half.

    Stage status alone is the wrong key for that, and was a free dispatch: a
    stage left RUNNING by a run that died — exactly the state a runaway leaves
    behind — looked identical to a pass in progress, so the next dispatch was
    neither checked nor counted. A pass is in flight only if something is
    actually behind it: a *live* run of this stage, or an unsettled fan-out
    group that dispatched the stage itself.

    Live, not `status == RUNNING`. A row status is a claim nothing disproves: a
    server killed mid-run leaves it RUNNING forever, which made the stage it
    stranded permanently exempt from the budget on every seam that does not reap
    first — `LaneDispatch.dispatch_stage` and
    `OrchestrationCallbackService.start_stage` both do not, and
    `RunService.start_stage_execution` only escaped because `start_run_async`
    happens to call `fail_interrupted_runs` ahead of the guard. So the question
    is put to `_is_live_dispatch_evidence`, which is read-only (this predicate
    must stay side-effect-free, since the refusal it feeds is). A live run still
    reads live, which is what keeps a 3-member parallel pass costing one attempt
    rather than three: the calling agent's own run is legitimately RUNNING for
    the stage it starts.
    """
    if not stage_running:
        return False
    running = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id)
        .where(AgentRun.stage_key == stage_key)
        .where(AgentRun.status == RunStatus.RUNNING)
    ).all()
    if any(_is_live_dispatch_evidence(session, run) for run in running):
        return True
    fanout = session.exec(
        select(StageFanoutGroup)
        .where(StageFanoutGroup.ticket_id == ticket_id)
        .where(StageFanoutGroup.stage_key == stage_key)
        .where(StageFanoutGroup.outcome == StageFanoutOutcome.PENDING)
    ).first()
    return fanout is not None


def evaluate_standalone_stage_dispatch(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    stage_already_running: bool,
    force: bool,
    budget_reset_pending: bool = False,
) -> StageDispatchDecision:
    """Decide what a dispatch no orchestrator loop counted owes the counter.

    Reads only. Raises `StageRetryBudgetExceeded` when the stage is at its
    budget and ``force`` is not set — before the caller has written anything, so
    the refusal cannot leave the ticket half-started or its operator's blocking
    diagnosis erased. The matching write is
    `commit_standalone_stage_dispatch`.

    ``budget_reset_pending`` says the caller is about to hand this stage a fresh
    budget (a human re-entering a stage *this breaker* blocked). The refusal is
    skipped for that one dispatch, which then becomes the first of the new
    budget rather than a free one outside it.

    ``RetryBudgetConfig.enabled`` gates the *refusal*, not the recording: a
    disabled budget still counts every dispatch. Deliberate, and the same thing
    `enforce_stage_retry_budget` does — it consults `exceeds_stage_retry_budget`
    (which returns "" when disabled) and then records regardless. A workspace
    that switches the breaker back on gets a counter that reflects what actually
    happened while it was off, rather than a fresh budget for a stage that has
    been redispatched thirteen times; and the two paths cannot drift into
    counting differently.
    """
    if dispatch_pass_open(session, ticket.id, stage_key, stage_running=stage_already_running):
        return StageDispatchDecision(counts=False, forced=False, exempt_reroute=False)

    config = resolve_stage_retry_budget(session, ticket)
    state = stage_retry_budget_state(session, ticket, stage_key, config)
    if state.exempt:
        return StageDispatchDecision(counts=False, forced=False, exempt_reroute=True)
    if state.at_budget and not budget_reset_pending:
        if not force:
            raise StageRetryBudgetExceeded(
                stage_retry_refusal_message(
                    stage_key, state.attempts, config.max_attempts_per_stage
                )
            )
        # A forced pass is an override, not an exemption: it still costs a
        # dispatch, or a UI that always forces has no counter at all.
        return StageDispatchDecision(counts=True, forced=True, exempt_reroute=False)
    return StageDispatchDecision(counts=True, forced=False, exempt_reroute=False)


def commit_standalone_stage_dispatch(
    session: Session,
    ticket_id: str,
    stage_key: str,
    decision: StageDispatchDecision,
    *,
    origin: DispatchOrigin,
) -> None:
    """Write what `evaluate_standalone_stage_dispatch` decided."""
    if decision.exempt_reroute:
        record_reroute_exempt_dispatch(session, ticket_id, stage_key)
        return
    if decision.forced:
        record_stage_dispatch_override(session, ticket_id, stage_key, origin)
    if decision.counts:
        record_stage_dispatch(session, ticket_id, stage_key)


def guard_standalone_stage_dispatch(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    stage_already_running: bool,
    force: bool,
    origin: DispatchOrigin,
) -> None:
    """Check-and-record in one call, for a caller with no state to write in
    between. `OrchestrationService.start_run` uses the two halves separately,
    because `_prepare_stage_start` runs between them.
    """
    decision = evaluate_standalone_stage_dispatch(
        session,
        ticket,
        stage_key,
        stage_already_running=stage_already_running,
        force=force,
    )
    commit_standalone_stage_dispatch(session, ticket.id, stage_key, decision, origin=origin)


def charge_fanout_dispatch(session: Session, ticket_id: str, stage_key: str) -> None:
    """Charge one dispatch for a whole fan-out, and refuse nothing.

    A named entry point so the policy is a stated decision rather than a bare
    `record_stage_dispatch` call in another module:

    - **One, not N.** `POST /stage-fanout` launches up to `MAX_ATTEMPTS`
      subprocesses, but they are one deliberate human decision, not N retries.
    - **Charged up front**, before any attempt launches, exactly as the
      orchestrator loop charges its pass before dispatching it. The attempts
      themselves reach `evaluate_standalone_stage_dispatch` with the fan-out's
      own group open, so they are grouped into this one pass rather than
      charged again.
    - **Not refused.** A fan-out is only reachable by an explicit operator
      request, which is the same act `force` exists for on the Run-stage path;
      refusing it would mean an operator could not compare attempts on precisely
      the stage that has proved hardest. It still costs the budget, so a
      fan-out cannot be used to dodge the counter.
    """
    record_stage_dispatch(session, ticket_id, stage_key)


def stage_dispatch_would_be_refused(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """Read-only: would an unforced standalone dispatch of this stage refuse?

    For a caller that must not spend anything on a dispatch that will not
    happen — `conflict_resolution`, which otherwise burns a conflict-resolution
    attempt and writes a rework-ledger entry for a resolver it then never sends.
    """
    config = resolve_stage_retry_budget(session, ticket)
    state = stage_retry_budget_state(session, ticket, stage_key, config)
    return state.at_budget


# -- Gate-fix attempt counter -------------------------------------------------
# The sibling durable counter this module's dispatch counter is modelled on: the
# builtin orchestrator's automatic transition-gate fix retries, counted the same
# way (persisted rows, not in-process state) so the budget holds across separate
# orchestration runs. Housed here alongside the dispatch counter rather than in
# the already-oversized orchestrator, since both are the identical pattern.


def gate_failure_artifact_title(stage_key: str) -> str:
    return f"Transition gate failed — {stage_key}"


def count_gate_fix_attempts(session: Session, ticket_id: str, stage_key: str) -> int:
    """Count prior automatic gate-fix retries for this stage, persisted via the
    error artifacts `_reroute_for_agent_fix`/`_block_after_gate_failure` attach —
    so the retry budget holds across separate orchestration runs, not just within
    a single `execute()` call. A function-local counter resets every time a new
    run starts (e.g. an operator or auto-resume re-triggers orchestration after a
    pause), letting a stage that can never pass its gate (a persistent
    environment issue, not something an agent can fix by editing code) cycle
    indefinitely instead of ever durably giving up.
    """
    return len(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket_id)
            .where(Artifact.kind == "error")
            .where(Artifact.title == gate_failure_artifact_title(stage_key))
        ).all()
    )
