"""How many attempts a stage actually needs, so the caps can be measured.

Four numbers bound re-running a stage in this control plane —
``retry_budget.max_attempts_per_stage``, ``gates.autofix_max_agent_attempts``,
``rework_feedback.MAX_REWORK_REROUTES`` and
``retry_budget.max_transient_retries`` — and every one of them was set by
judgement. Nothing computed whether the attempts they allow are attempts that
work. The comment justifying one of them says the evidence for lowering it would
be "the fix rate of attempt 3"; no code produced that figure.

This module produces it. For each stage it reports where the first `pass` verdict
lands, and what share of (ticket, stage) pairs never reached one, drawn from the
stage-report artifacts the agents themselves emit.

**The oracle, and its limit.** A `pass` report is the strongest signal available
here, and it is still not "the ticket converged" — ticket 546 met all four of its
acceptance criteria at round three and then ran three further implement rounds on
real findings that no criterion covered. So read `attempts_to_pass` as "how long
until this stage stopped objecting", not as "how long the work took". The figure
that would answer the second question is attempts-until-the-ticket-reached-its-
terminal-stage, and it is not computable per stage: the ticket may be waiting on
a sibling, a gate, or a person.

What the numbers are for is bounding the caps, and for that the weaker oracle is
enough: a cap is safe when the observed tail sits well below it, whatever else
the tail means. `cap_pressure` reports exactly that comparison, because "no pair
has ever come within four attempts of this limit" and "a fifth of them hit it
every week" call for opposite decisions about the same number.

Deliberately read-only and on demand. It is analysis, not a health check: there
is no threshold at which one of these numbers is a fault, which is why it does
not file `DoctorFinding`s.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from statistics import median

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    StageBudgetArtifactKind,
    Ticket,
)
from loregarden.services.rework_feedback import (
    REWORK_FEEDBACK_KIND,
    rework_feedback_artifact_title,
)
from loregarden.services.stage_retry_budget import (
    gate_failure_artifact_title,
    stage_dispatch_artifact_title,
)
from loregarden.services.stage_transient_retry import transient_retry_artifact_title
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Matches `run_completion._stage_report_artifact`, which is what writes them.
_REPORT_TITLE_PREFIX = "Stage report — "

#: Verdicts that end a stage's objection. `needs_rework` and `fail` do not;
#: `blocked` does not either — it stops the stage without settling it.
_PASSING_VERDICTS = frozenset({"pass"})

DEFAULT_LOOKBACK_DAYS = 90


class _ReportPayload(BaseModel):
    """The two fields this module reads off a stage-report artifact.

    A model at the boundary rather than `json.loads(...).get(...)`: the payload is
    written by whatever version of `stage_report_artifact_content` was running at
    the time, and a row from an older shape should be skipped, not crash a report
    nobody asked to be strict.
    """

    model_config = ConfigDict(extra="ignore")

    stage_key: str = ""
    status: str = ""


class StageAttemptProfile(BaseModel):
    """One stage's attempt history, as far as the reports can show it."""

    stage_key: str
    #: (ticket, stage) pairs that produced at least one report.
    pairs: int
    #: Pairs that reached a `pass`, and where that pass landed.
    passed: int
    median_attempts_to_pass: float | None
    p95_attempts_to_pass: int | None
    max_attempts_to_pass: int | None
    #: Pairs with reports but no `pass`. Not a failure rate: a stage still in
    #: flight, or one whose ticket was abandoned, counts here too.
    never_passed: int
    #: How many pairs first passed at exactly attempt N, N from 1 up. The shape
    #: that says whether late attempts do anything.
    first_pass_at_attempt: dict[int, int]


class CapPressure(BaseModel):
    """How close real work comes to one configured ceiling."""

    name: str
    cap: int
    #: Pairs whose attempts-to-pass reached or exceeded the cap. A cap these
    #: never touch is not bounding anything that happens.
    pairs_at_or_over_cap: int
    #: The largest attempts-to-pass observed anywhere, for context.
    worst_observed: int
    #: Headroom between the worst real case and the ceiling.
    margin: int


class StageAttemptStats(BaseModel):
    generated_at: datetime
    lookback_days: int
    reports_read: int
    profiles: list[StageAttemptProfile]

    def profile(self, stage_key: str) -> StageAttemptProfile | None:
        return next((p for p in self.profiles if p.stage_key == stage_key), None)


def _percentile(values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile. None for an empty sample, rather than 0 — a
    stage nobody has run has no p95, and 0 would read as "instant"."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def _report_rows(
    session: Session,
    *,
    workspace_id: str | None,
    since: datetime,
) -> list[tuple[str, Artifact]]:
    """Stage-report artifacts in the window, with their ticket id."""
    query = (
        select(Artifact)
        .where(col(Artifact.title).startswith(_REPORT_TITLE_PREFIX))
        .where(col(Artifact.created_at) >= since)
        .order_by(col(Artifact.created_at))
    )
    if workspace_id:
        ticket_ids = session.exec(
            select(Ticket.id).where(Ticket.workspace_id == workspace_id)
        ).all()
        if not ticket_ids:
            return []
        query = query.where(col(Artifact.ticket_id).in_(ticket_ids))
    return [(row.ticket_id, row) for row in session.exec(query).all()]


def load_stage_attempt_stats(
    session: Session,
    *,
    workspace_id: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> StageAttemptStats:
    """Attempt profiles per stage, oldest report first so ordinals are real.

    Ordering is load-bearing: the attempt number is this pair's report count so
    far, so a query that returned rows newest-first would report every first pass
    as attempt 1.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = _report_rows(session, workspace_id=workspace_id, since=since)

    #: (ticket, stage) -> reports seen so far, and where the first pass landed.
    seen: dict[tuple[str, str], int] = defaultdict(int)
    first_pass: dict[tuple[str, str], int] = {}
    read = 0

    for ticket_id, row in rows:
        if not row.content_json:
            continue
        try:
            payload = _ReportPayload.model_validate_json(row.content_json)
        except ValidationError:
            logger.warning(
                "Stage-report artifact %s on ticket %s does not parse; skipped in "
                "attempt stats (the row stays, the count is short by one)",
                row.id,
                ticket_id,
            )
            continue
        stage_key = payload.stage_key or _stage_key_from_title(row.title)
        if not stage_key:
            continue
        read += 1
        key = (ticket_id, stage_key)
        seen[key] += 1
        if payload.status in _PASSING_VERDICTS and key not in first_pass:
            first_pass[key] = seen[key]

    by_stage: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in seen:
        by_stage[key[1]].append(key)

    profiles: list[StageAttemptProfile] = []
    for stage_key, keys in sorted(by_stage.items()):
        attempts = [first_pass[key] for key in keys if key in first_pass]
        histogram: dict[int, int] = defaultdict(int)
        for attempt in attempts:
            histogram[attempt] += 1
        profiles.append(
            StageAttemptProfile(
                stage_key=stage_key,
                pairs=len(keys),
                passed=len(attempts),
                median_attempts_to_pass=median(attempts) if attempts else None,
                p95_attempts_to_pass=_percentile(attempts, 0.95),
                max_attempts_to_pass=max(attempts) if attempts else None,
                never_passed=len(keys) - len(attempts),
                first_pass_at_attempt=dict(sorted(histogram.items())),
            )
        )

    return StageAttemptStats(
        generated_at=datetime.now(timezone.utc),
        lookback_days=lookback_days,
        reports_read=read,
        profiles=profiles,
    )


def _stage_key_from_title(title: str) -> str:
    """Recover the stage from the artifact title when the payload omits it.

    Older payloads predate `stage_key` in `stage_report_artifact_content`, and the
    title has carried it since the first version. Reading both is what keeps a
    90-day window from silently becoming a short one.
    """
    if not title.startswith(_REPORT_TITLE_PREFIX):
        return ""
    return title[len(_REPORT_TITLE_PREFIX) :].strip()


class RetryCounter(StrEnum):
    """Which durable counter a configured cap actually bounds.

    An enum because the four caps count four different things, and the first
    version of `cap_pressure` did not distinguish them: it compared every cap
    against attempts-to-pass, so `autofix_max_agent_attempts` — which counts gate
    *fix rounds* — was reported as having a margin of -16 against a `review`
    stage's 19 report rounds. The numbers were real and the comparison was
    meaningless, which is the worse of the two failures, because the output still
    looked like an answer.

    Each value names the artifact family that IS that cap's counter, so the
    pressure reading and the enforcement read the same rows.
    """

    #: `stage_retry_budget`'s dispatch markers — the runaway backstop.
    DISPATCH = "stage_dispatch"
    #: `gate_recovery.count_gate_fix_attempts`, which counts these error rows.
    GATE_FIX = "gate_fix"
    #: `rework_feedback`'s ledger entries, keyed by the stage rerouted TO.
    REWORK = "rework"
    #: `stage_transient_retry`'s markers.
    TRANSIENT = "transient"


#: How to find each counter's rows: the artifact kind, and the title prefix that
#: scopes them to one stage.
#:
#: Every value is imported or derived from the module that WRITES the rows, never
#: retyped. The first version of this table guessed `ArtifactKind.CONTEXT` for the
#: rework ledger, which was right until migration 0103 gave it a kind of its own —
#: so the endpoint counted 5 legacy rows and missed 59 current ones, and reported
#: one pair at the reroute cap where a direct query finds eleven. A plausible
#: undercount is the worst thing this endpoint can produce, because nobody
#: re-derives a number that already looks reasonable.
#:
#: The prefixes come from calling each title builder with an empty stage key,
#: which is exactly the string the real titles are built on.
_COUNTER_ROWS: dict[RetryCounter, tuple[str, str]] = {
    RetryCounter.DISPATCH: (
        StageBudgetArtifactKind.DISPATCH.value,
        stage_dispatch_artifact_title(""),
    ),
    RetryCounter.GATE_FIX: (ArtifactKind.ERROR.value, gate_failure_artifact_title("")),
    RetryCounter.REWORK: (REWORK_FEEDBACK_KIND.value, rework_feedback_artifact_title("")),
    RetryCounter.TRANSIENT: (
        StageBudgetArtifactKind.TRANSIENT_RETRY.value,
        transient_retry_artifact_title(""),
    ),
}


def cap_pressure(
    session: Session,
    *,
    name: str,
    cap: int,
    counter: RetryCounter,
    workspace_id: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> CapPressure:
    """Whether a configured ceiling bounds anything that actually happens.

    Counts the rows the cap's own enforcement counts — see `RetryCounter` — so
    the reading is the same question the breaker asks, one step earlier.

    Both directions of the decision need this. A cap the worst real case never
    approaches is not protecting anything, and the honest conclusion is that
    raising it is free and lowering it is untested. A cap real work reaches
    routinely is stopping work rather than runaway. The numbers do not settle
    which; they say which of the two conversations to have.
    """
    kind, prefix = _COUNTER_ROWS[counter]
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    query = (
        select(Artifact)
        .where(Artifact.kind == kind)
        .where(col(Artifact.title).startswith(prefix))
        .where(col(Artifact.created_at) >= since)
    )
    if workspace_id:
        ticket_ids = session.exec(
            select(Ticket.id).where(Ticket.workspace_id == workspace_id)
        ).all()
        if not ticket_ids:
            return CapPressure(
                name=name, cap=cap, pairs_at_or_over_cap=0, worst_observed=0, margin=cap
            )
        query = query.where(col(Artifact.ticket_id).in_(ticket_ids))

    per_pair: dict[tuple[str, str], int] = defaultdict(int)
    for row in session.exec(query).all():
        per_pair[(row.ticket_id, row.title)] += 1

    counts = list(per_pair.values())
    worst = max(counts, default=0)
    return CapPressure(
        name=name,
        cap=cap,
        pairs_at_or_over_cap=sum(1 for count in counts if count >= cap),
        worst_observed=worst,
        margin=cap - worst,
    )
