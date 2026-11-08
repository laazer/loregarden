"""Spawn a stage run, resuming its rework round's session when the plan says so.

`services.rework_resume` decides; this performs it. A resumed turn that never
started (the CLI could not find the session, it exited before any `result`
envelope) falls back to the fresh run it would have been, once, under its own
file stem (`<run_code>-fresh`): a second process may not write the first one's
`.out`/`.rc`. The fallback's reason is recorded on the run. A resumed turn that
did start and then failed is a failed run like any other; running it again
fresh would bill the work twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from loregarden.agents.cli_adapters import CliInvocation, session_resume_invocation
from loregarden.dot_line import SYS
from loregarden.models.domain import AgentRun, RunStatus
from loregarden.services.rework_resume import (
    ResumePlan,
    record_cli_session,
    record_resume_fallback,
)
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.stage_report import result_envelope
from sqlmodel import Session

FRESH_STEM_SUFFIX = "-fresh"

#: Spawns `invocation` with `prompt` under file stem `run_code`.
Spawn = Callable[[CliInvocation, str, str], tuple[str, str, RunStatus]]


@dataclass(frozen=True)
class RoundResult:
    stdout: str
    stderr: str
    status: RunStatus
    #: The invocation that produced `stdout`, for the evidence ledger.
    invocation: CliInvocation


def prepare_round(
    session: Session, run: AgentRun, plan: ResumePlan | None, invocation: CliInvocation
) -> tuple[ResumePlan | None, CliInvocation]:
    """The plan as it will run, and what the round spawns first.

    An invocation that cannot carry a resume (a command override with no
    ``-p``, say) turns the plan into a fresh run, with the reason recorded.
    """
    if plan is None or not plan.resumes:
        return plan, invocation
    try:
        resumed = session_resume_invocation(
            invocation, session_id=plan.session_id, prompt=plan.follow_up
        )
    except ValueError as exc:
        reason = f"this run's command cannot carry a resume ({exc})"
        record_resume_fallback(session, run, reason)
        return replace(plan, prior_run_id="", session_id="", skip_reason=reason), invocation
    return plan, resumed


def run_round(
    session: Session,
    run: AgentRun,
    *,
    plan: ResumePlan | None,
    invocation: CliInvocation,
    first: CliInvocation,
    prompt: str,
    spawn: Spawn,
    streamer: RunLogStreamer,
) -> RoundResult:
    """Spawn `run`'s turn per `plan` and record the session it ran in.

    `first` is what `prepare_round` returned; `invocation` is the fresh one.
    """
    if plan is not None and not plan.resumes:
        streamer.append(SYS.name, f"Rework round runs fresh: {plan.skip_reason}.", force=True)
    if plan is None or not plan.resumes:
        stdout, stderr, status = spawn(invocation, prompt, run.run_code)
        record_cli_session(session, run, stdout)
        return RoundResult(stdout, stderr, status, invocation)

    streamer.append(
        SYS.name,
        f"Rework round resumes session {plan.session_id} with the finding.",
        force=True,
    )
    stdout, stderr, status = spawn(first, plan.follow_up, run.run_code)
    if status is RunStatus.FAILED and result_envelope(stdout) is None:
        tail = (stderr or "").strip()[-400:]
        reason = f"resuming session {plan.session_id} did not start a turn" + (
            f": {tail}" if tail else ""
        )
        record_resume_fallback(session, run, reason)
        streamer.append(SYS.name, f"{reason}. Running fresh.", force=True)
        stdout, stderr, status = spawn(invocation, prompt, f"{run.run_code}{FRESH_STEM_SUFFIX}")
        record_cli_session(session, run, stdout)
        return RoundResult(stdout, stderr, status, invocation)
    record_cli_session(session, run, stdout)
    return RoundResult(stdout, stderr, status, first)
