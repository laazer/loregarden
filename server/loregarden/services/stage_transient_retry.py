"""Bounded automatic re-dispatch of a stage whose run died of infrastructure.

The parallel stage path has asked `is_transient_failure` which failures were
infrastructure since it was written. The single-agent path never asked: every
FAILED run became a blocked ticket carrying the provider's stderr, whatever
killed it. Measured over 179 failed runs, 82 died with "interrupted before
completion (server reload or worker stopped)" — the control plane restarting
under its own agent — 27 timed out, 12 could not reach a saved Cursor login and 6
tripped the macOS keychain. Every one of them parked a ticket for a human, under
a message that already said what to do about it: *re-run the stage to continue*.

So this module answers the question that message implies. A failure that never
got to attempt the work earns a re-dispatch, up to
``RetryBudgetConfig.max_transient_retries`` per (ticket, stage), and the stage is
re-armed to PENDING for the driver that is still holding it rather than blocked.

Two properties are load-bearing:

**It is bounded and durable.** The counter is dedicated ``Artifact`` rows — the
same pattern as ``stage_retry_budget``'s dispatch counter, for the same reason: a
function-local counter resets when the process that held it dies, and the failure
being counted here is *the process dying*. A CLI that can never authenticate
would otherwise retry forever, which is the failure mode this replaces, not an
improvement on it.

**It does not spend the runaway backstop.** A transient retry is not an attempt
at the work, so charging it to ``max_attempts_per_stage`` would let a bad night
of server reloads exhaust the budget a genuinely looping stage needs. The
counters are separate kinds and neither reads the other.

What is *not* retried here: an agent that reported fail/needs_rework (that is the
rework loop's business), an agent that reported `blocked`, a cancelled run (a
human stopped it, and re-dispatching would fight the stop), a provider usage
limit (`run_usage_limit` already blocks with the reset window, and retrying
before it clears just spends the retry on the same wall), and the
`CONTROL_PLANE_DEATH_MESSAGES` — a server reload or a stranded stage, whose
recovery `orchestration_recovery` owns and does better.

**A clean exit with no readable stage report is not retried either**, and that is
the one exclusion worth arguing, because `parallel_stage` does call it transient.
It is not the same act there: that path *pauses for a human*, and this one
re-runs the agent. The scenario the fail-closed guard was written for
(`test_missing_stage_report_fail_closed`) is an AC gatekeeper that decided
**REJECT** in prose and had its `complete_stage` call cancelled — the verdict
existed, only the sentinel was lost. Re-running that agent risks the second pass
coming back `pass`, which converts a recorded rejection into a promotion. Pausing
on an ambiguous outcome is safe; re-rolling it is a gamble on the verdict. So the
missing report keeps its block, and a human reads the prose.

That exclusion turns on the verdict existing, so it stops exactly where the
verdict does. A CLI can exit 0 part-way through its own turn: the NDJSON stream
stops mid-sentence and the adapter's terminal usage event never arrives. Measured
over 30 days of `script_review`, three runs of 76 died this way — two of them
siblings in the same parallel round — each marked `succeeded` with an empty
`verdict_channel`, and each parked a ticket a human then re-queued by hand. There
is no prose verdict on such a run to protect, because the agent never finished
reaching one, so `_stream_ended_early` sends it back through the same bounded
budget as any other death that said nothing about the work.
"""

from __future__ import annotations

import json
import logging

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    RunStatus,
    RunUsageStatus,
    StageBudgetArtifactKind,
    StageStatus,
    Ticket,
)
from loregarden.services.interruption_messages import CONTROL_PLANE_DEATH_MESSAGES
from loregarden.services.orchestration_profile import RetryBudgetConfig
from loregarden.services.stage_report import is_transient_failure
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_RETRY_KIND = StageBudgetArtifactKind.TRANSIENT_RETRY

#: Why a stage is being re-dispatched, for the operator reading the marker. Not
#: an enum in `models.domain.enums` because it is never persisted as a column
#: value — it rides in the marker's `content_json`. One value today, kept as a
#: named field rather than inlined so a second class of retryable death arrives
#: as a new value instead of as a second boolean.
INFRASTRUCTURE = "infrastructure"
#: The CLI exited cleanly part-way through its turn — see `_stream_ended_early`.
TRUNCATED_STREAM = "truncated-stream"


def transient_retry_artifact_title(stage_key: str) -> str:
    """Public so `stage_attempt_stats` can count the same rows this cap is
    enforced on, rather than retyping the prefix."""
    return f"stage-transient-retry:{stage_key}"


def _markers(session: Session, ticket_id: str, stage_key: str) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket_id)
            .where(Artifact.kind == _RETRY_KIND)
            .where(Artifact.title == transient_retry_artifact_title(stage_key))
        ).all()
    )


def count_transient_retries(session: Session, ticket_id: str, stage_key: str) -> int:
    """How many automatic re-dispatches this (ticket, stage) has already had."""
    return len(_markers(session, ticket_id, stage_key))


def clear_transient_retries(session: Session, ticket_id: str, stage_key: str) -> int:
    """Drop the counter, returning how many markers went.

    Called when the stage actually settles — reaching DONE or a human gate means
    the infrastructure problem is behind it, and a stage re-entered later (a
    rework round, a re-run months on) should not inherit a budget spent on a
    server reload that has nothing to do with it. Also called by the human-reset
    paths, for the same reason `clear_stage_dispatches` is.
    """
    rows = _markers(session, ticket_id, stage_key)
    for row in rows:
        session.delete(row)
    if rows:
        session.commit()
    return len(rows)


def _stream_ended_early(stdout: str, usage_status: RunUsageStatus) -> bool:
    """Whether a clean exit stopped part-way through the CLI's own turn.

    The narrow case the module docstring's fail-closed exclusion does not cover.
    That exclusion is about an agent that *finished* and lost only its sentinel:
    the prose verdict exists, so re-running risks overwriting a recorded
    rejection with a second opinion. This is the other shape — the stream stops
    mid-sentence, the adapter's terminal usage event never arrives, and the turn
    never reached a verdict in any channel. There is nothing for a human to read
    and nothing to re-roll, so the stage is re-dispatched instead of parked.

    `usage_status` is the discriminator because it already answers exactly this,
    per adapter, on the row: UNAVAILABLE means an adapter that *has* a usage
    surface printed no terminal event. The NDJSON check keeps a plain-text run
    from being read as a truncated stream on that basis alone.
    """
    if usage_status is not RunUsageStatus.UNAVAILABLE:
        return False
    return any(line.lstrip().startswith("{") for line in stdout.splitlines())


def transient_failure_reason(
    status: RunStatus,
    *,
    stdout: str,
    stderr: str,
    usage_status: RunUsageStatus = RunUsageStatus.UNKNOWN,
) -> str:
    """Why this run's death says nothing about the work, or "" if it does.

    Reads the same signatures `parallel_stage` reads, so the two paths cannot
    disagree about what an infrastructure failure is — with the one deliberate
    narrowing the module docstring argues: a clean exit that reached the end of
    its turn and printed no stage report is not counted here. A clean exit that
    never reached the end of its turn is, via `_stream_ended_early`. The caller
    must have ruled out a usage limit and an agent-reported verdict first.
    """
    if status is RunStatus.CANCELLED:
        return ""
    if status is RunStatus.SUCCEEDED:
        return TRUNCATED_STREAM if _stream_ended_early(stdout, usage_status) else ""
    if stderr.strip() in CONTROL_PLANE_DEATH_MESSAGES:
        # A restart, a stranded stage or an orphaned run. `is_transient_failure`
        # says yes to all three — correctly, for the parallel path, which pauses
        # for a human resume — but their recovery belongs to the boot sweep,
        # which re-admits through the slot pool and restores the driver that was
        # running. Re-arming the stage here instead would leave a PENDING stage
        # at boot with nothing driving it, and take the ticket out of
        # `resume_interrupted_orchestrations`'s reach on the way: a recovery that
        # works, replaced by a ticket that only looks busy.
        return ""
    if status is RunStatus.FAILED and is_transient_failure(stdout, stderr):
        return INFRASTRUCTURE
    return ""


def record_transient_retry(
    session: Session,
    ticket_id: str,
    stage_key: str,
    *,
    run_id: str,
    reason: str,
    message: str,
) -> None:
    """Charge one automatic re-dispatch, and be the record that it happened.

    ``run_id`` rides in the payload rather than in `Artifact.run_id` so the row
    survives the run being pruned, and so the orchestrator can ask
    `armed_for_run` whether the stage in front of it was re-armed for the run it
    just watched die — the difference between re-dispatching a stage and
    re-dispatching one somebody else is holding.

    ``message`` is carried here rather than written to `blocking_issues`, which
    would make the ticket read as blocked, or filed as an `error` artifact, which
    `_upsert_artifact` keys by (ticket, kind) alone — one error row per ticket, so
    a retry note would overwrite the ticket's actual blocking diagnosis. A stage
    that quietly ran twice is the failure mode this feature must not introduce,
    so the row that authorises the retry is also the row that explains it.
    """
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_RETRY_KIND,
            title=transient_retry_artifact_title(stage_key),
            content_json=json.dumps({"run_id": run_id, "reason": reason, "message": message}),
        )
    )
    session.commit()


def armed_for_run(session: Session, ticket_id: str, stage_key: str, run_id: str) -> bool:
    """Whether this stage was re-armed for a transient retry of ``run_id``.

    Asked by the orchestrator loop about the run it just executed. Deliberately
    not "is the stage PENDING": a cancelled run also leaves it PENDING, and
    re-dispatching there would restart work a human stopped.
    """
    for row in _markers(session, ticket_id, stage_key):
        if not row.content_json:
            continue
        try:
            payload = json.loads(row.content_json)
        except json.JSONDecodeError:
            logger.warning(
                "Unreadable transient-retry marker %s on ticket %s; treating the stage as "
                "not re-armed, which blocks rather than re-dispatches",
                row.id,
                ticket_id,
            )
            continue
        if payload.get("run_id") == run_id:
            return True
    return False


def stage_rearmed_for_latest_run(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """Whether this stage is sitting re-armed after its most recent run.

    The orchestrator loop's question, asked immediately after a dispatch returns:
    should this pass re-run the stage rather than advance past it or block on it?

    Both halves are needed. The marker alone goes stale — it stays on the ticket
    after the retry it bought has run — so the stage must also still be PENDING,
    which is the state the re-arm left it in and which the next dispatch replaces
    with RUNNING. And PENDING alone is not enough: a cancelled run leaves the
    stage PENDING too, and re-dispatching there would restart work a human
    stopped.
    """
    if stage_key != ticket.workflow_stage_key:
        return False
    if ticket.workflow_stage_status is not StageStatus.PENDING:
        return False
    latest = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .where(AgentRun.stage_key == stage_key)
        .order_by(AgentRun.created_at.desc())
    ).first()
    if latest is None:
        return False
    return armed_for_run(session, ticket.id, stage_key, latest.id)


def retry_exhausted_message(stage_key: str, attempts: int, detail: str) -> str:
    """What a human reads when the automatic retries are spent.

    Names the count and that the cause was infrastructure, because "it failed
    five times" and "the machine failed under it five times" call for different
    actions — no further re-running fixes the second.
    """
    return (
        f"Stage '{stage_key}' died of an infrastructure failure on {attempts} "
        f"consecutive automatic retries. "
        f"That is a fact about the environment rather than the work — re-running "
        f"again is unlikely to differ. Last failure: {detail[:500]}"
    )


def retrying_message(stage_key: str, attempt: int, config: RetryBudgetConfig, detail: str) -> str:
    """The note left on a stage between a transient death and its re-dispatch.

    Written to the run's error artifact rather than to `blocking_issues`: the
    ticket is not blocked, and text in `blocking_issues` is what
    `reconcile_workflow_state` reads to decide that it is.
    """
    return (
        f"Stage '{stage_key}' hit a transient failure and was re-armed automatically "
        f"(retry {attempt} of {config.max_transient_retries}). Nothing about the work "
        f"was rejected. Cause: {detail[:500]}"
    )
