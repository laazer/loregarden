"""A rework round continues the session that did the work, with the finding.

A reviewer that sends a stage back used to cost a whole fresh run: a new agent
re-read the ticket, the code and its own predecessor's diff before it could
start on a one-paragraph finding. Of the 192 rework rounds on the live database
to 2026-11-07 that had an earlier run at their stage, 166 followed a claude or
cursor run whose output named its session, and a fresh claude rework round read
about 7.8M input tokens.

So a rework round resumes the earlier run's session when it can, and sends the
finding as the session's next turn (`follow_up`). It runs fresh, recording why
(`AgentRun.resume_skip_reason`), when:

- there is no earlier run at the stage, or it was another agent;
- the adapter cannot resume (`cli_adapters.SESSION_RESUME_UNSUPPORTED`);
- the earlier run named no session, or ran in another checkout (a CLI finds a
  session by its working directory);
- the finding's rejecting run already resumed a session once. The same finding
  delivered twice resumes once: a second round on it starts from a clean slate,
  since the resumed one did not settle it.

`rework_round_tokens` puts resumed and fresh rounds side by side from the same
rows, so whether resuming pays is a query, not a claim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loregarden.agents.cli_adapters import session_resume_unsupported
from loregarden.models.domain import AgentRun, Artifact, Ticket
from loregarden.models.domain.enums import ReworkArtifactKind
from loregarden.services.rework_feedback import (
    render_rework_feedback,
    rework_feedback_artifact_title,
)
from loregarden.services.stage_report import REPORT_BLOCK_REMINDER, result_envelope
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResumePlan:
    """How a rework round runs: resuming `session_id` of `prior_run_id`, or
    fresh for `skip_reason`. Exactly one of `session_id` and `skip_reason` is set."""

    feedback_id: str
    prior_run_id: str = ""
    session_id: str = ""
    skip_reason: str = ""
    follow_up: str = ""

    @property
    def resumes(self) -> bool:
        return bool(self.session_id)


def session_id_of(run: AgentRun) -> str:
    """The CLI session `run` ran in: its recorded id, else its stdout's envelope.

    The stdout reading covers runs from before `cli_session_id` existed.
    """
    if run.cli_session_id:
        return run.cli_session_id
    envelope = result_envelope(run.stdout or "")
    return envelope.session_id if envelope is not None else ""


def _prior_run(session: Session, run: AgentRun) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == run.ticket_id)
        .where(AgentRun.stage_key == run.stage_key)
        .where(AgentRun.id != run.id)
        .where(col(AgentRun.created_at) <= run.created_at)
        .order_by(col(AgentRun.created_at).desc())
        .limit(1)
    ).first()


def _newest_feedback(
    session: Session, ticket: Ticket, stage_key: str, since: datetime | None
) -> Artifact | None:
    query = (
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id)
        .where(Artifact.kind == ReworkArtifactKind.FEEDBACK)
        .where(Artifact.title == rework_feedback_artifact_title(stage_key))
    )
    if since is not None:
        query = query.where(col(Artifact.created_at) > since)
    return session.exec(query.order_by(col(Artifact.created_at).desc()).limit(1)).first()


def _already_resumed_for(session: Session, ticket: Ticket, feedback: Artifact) -> AgentRun | None:
    """A run that already resumed a session for this finding's rejecting run.

    Keyed by the rejecting run, so the same finding recorded twice (two
    artifacts, one rejecting run) counts as one. A finding with no rejecting
    run is keyed by its own artifact.
    """
    same_finding = select(Artifact.id).where(Artifact.ticket_id == ticket.id)
    same_finding = (
        same_finding.where(Artifact.run_id == feedback.run_id)
        if feedback.run_id
        else same_finding.where(Artifact.id == feedback.id)
    )
    return session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .where(AgentRun.resumed_from_run_id != "")
        .where(col(AgentRun.rework_feedback_id).in_(same_finding))
        .limit(1)
    ).first()


def follow_up_prompt(session: Session, ticket: Ticket, stage_key: str) -> str:
    """The resumed session's next turn: the finding, and the report it owes."""
    return (
        "Your earlier turn on this stage was sent back for rework. The finding:\n\n"
        f"{render_rework_feedback(session, ticket, stage_key)}\n\n"
        "Other stages may have changed the tree since your turn: re-read any file "
        "before you rely on what you remember of it. Address the finding, then "
        + REPORT_BLOCK_REMINDER
    )


def _skip_reason(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    *,
    prior: AgentRun | None,
    feedback: Artifact,
    adapter: str,
    repo_root: Path,
) -> str:
    """Why this round cannot resume `prior`'s session, or "" when it can."""
    if prior is None:
        return "no earlier run at this stage to resume"
    if prior.agent_id != run.agent_id:
        return f"the earlier run {prior.run_code} was agent {prior.agent_id}, not {run.agent_id}"
    unsupported = session_resume_unsupported(adapter)
    if unsupported:
        return unsupported
    if not session_id_of(prior):
        return f"the earlier run {prior.run_code} recorded no CLI session"
    if prior.start_repo_path and Path(prior.start_repo_path) != repo_root:
        return (
            f"the earlier run {prior.run_code} ran in {prior.start_repo_path}, "
            f"this one in {repo_root}; a CLI finds its session by directory"
        )
    earlier = _already_resumed_for(session, ticket, feedback)
    if earlier is not None:
        return f"this finding already resumed a session once, in run {earlier.run_code}"
    return ""


def plan_rework_resume(
    session: Session, ticket: Ticket, run: AgentRun, *, adapter: str, repo_root: Path
) -> ResumePlan | None:
    """How `run` answers its rework finding, or None when it is not a rework round.

    A run is a rework round when a finding for its stage arrived after the
    stage's previous run (or, with no previous run, when one exists at all).
    The plan is recorded on the run before it is returned.
    """
    prior = _prior_run(session, run)
    feedback = _newest_feedback(
        session, ticket, run.stage_key, prior.created_at if prior is not None else None
    )
    if feedback is None:
        return None
    reason = _skip_reason(
        session, ticket, run, prior=prior, feedback=feedback, adapter=adapter, repo_root=repo_root
    )
    if reason or prior is None:
        plan = ResumePlan(feedback_id=feedback.id, skip_reason=reason)
    else:
        plan = ResumePlan(
            feedback_id=feedback.id,
            prior_run_id=prior.id,
            session_id=session_id_of(prior),
            follow_up=follow_up_prompt(session, ticket, run.stage_key),
        )
    run.rework_feedback_id = plan.feedback_id
    run.resumed_from_run_id = plan.prior_run_id
    run.resume_skip_reason = plan.skip_reason
    session.add(run)
    session.commit()
    return plan


def record_resume_fallback(session: Session, run: AgentRun, reason: str) -> None:
    """The resume was tried and did not start; the round runs fresh for `reason`."""
    logger.warning("Run %s: %s", run.run_code, reason)
    run.resumed_from_run_id = ""
    run.resume_skip_reason = reason
    session.add(run)
    session.commit()


def record_cli_session(session: Session, run: AgentRun, stdout: str) -> None:
    """Record the session `run`'s turn ran in, as its output named it."""
    envelope = result_envelope(stdout)
    if envelope is None or not envelope.session_id:
        return
    run.cli_session_id = envelope.session_id
    session.add(run)
    session.commit()


@dataclass(frozen=True)
class ReworkRoundTokens:
    """Rework rounds at one stage, resumed or fresh, and what they read.

    `mean_input_tokens` counts every input token the run was billed for
    (uncached, cache read and cache write), over the `measured` rounds only; a
    round with no usage recorded is `unmeasured`, never a zero.
    """

    stage_key: str
    resumed: bool
    rounds: int
    measured: int
    unmeasured: int
    mean_input_tokens: float | None
    mean_output_tokens: float | None


def _input_tokens(run: AgentRun) -> int:
    return (run.input_tokens or 0) + (run.cache_read_tokens or 0) + (run.cache_write_tokens or 0)


def rework_round_tokens(
    session: Session, *, since: datetime | None = None
) -> list[ReworkRoundTokens]:
    """Every recorded rework round, grouped by stage and by whether it resumed."""
    query = select(AgentRun).where(AgentRun.rework_feedback_id != "")
    if since is not None:
        query = query.where(col(AgentRun.created_at) >= since)
    groups: dict[tuple[str, bool], list[AgentRun]] = {}
    for run in session.exec(query).all():
        groups.setdefault((run.stage_key, bool(run.resumed_from_run_id)), []).append(run)
    rows = []
    for (stage_key, resumed), runs in sorted(groups.items()):
        measured = [run for run in runs if run.input_tokens is not None]
        rows.append(
            ReworkRoundTokens(
                stage_key=stage_key,
                resumed=resumed,
                rounds=len(runs),
                measured=len(measured),
                unmeasured=len(runs) - len(measured),
                mean_input_tokens=(
                    sum(_input_tokens(run) for run in measured) / len(measured)
                    if measured
                    else None
                ),
                mean_output_tokens=(
                    sum(run.output_tokens or 0 for run in measured) / len(measured)
                    if measured
                    else None
                ),
            )
        )
    return rows
