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
- the ticket's spec changed since the earlier run was given it
  (`spec_snapshot`): its session holds the old text;
- the finding's rejecting run already resumed a session once. The same finding
  delivered twice resumes once: a second round on it starts from a clean slate,
  since the resumed one did not settle it.

An agent-group run (`agent_group_runs`) is scoped by its group rather than its
step: its earlier run is the group's, its finding may be addressed to any step
it covers, and a group run that ended before its last step is continued in the
same session once (a continuation round, with no finding).

A resumed claude round runs under the system prompt the earlier run's session
started under (`session_prompt`), not a freshly rendered one: a changed system
prompt invalidates the cached history, which made every resume re-write it. What
changed since (the feedback, the unresolved ledger, rechecks, deferrals) rides
in the follow-up turn instead (`turn_context`).

`rework_round_tokens` puts resumed and fresh rounds side by side from the same
rows, so whether resuming pays is a query, not a claim.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loregarden.agents.cli_adapters import session_resume_unsupported
from loregarden.core.agent_groups import group_span
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    CliAdapter,
    StageStatus,
    Ticket,
    WorkflowInstance,
)
from loregarden.models.domain.enums import ReworkArtifactKind
from loregarden.services.rework_feedback import (
    render_rework_feedback,
    rework_feedback_artifact_title,
)
from loregarden.services.session_prompt import read_session_prompt
from loregarden.services.stage_report import REPORT_BLOCK_REMINDER, result_envelope
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import parse_stage_map
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
    #: The system prompt `session_id` started under, for a claude resume to pass
    #: back unchanged; "" when none was kept (the resume re-caches its history).
    session_prompt: str = ""

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


@dataclass(frozen=True)
class ResumeScope:
    """What a run answers for: the steps it covers, and its group ("" when ungrouped)."""

    stage_keys: tuple[str, ...]
    group_key: str


def _scope_of(run: AgentRun) -> ResumeScope:
    if run.agent_group_key:
        return ResumeScope(tuple(json.loads(run.covered_stage_keys_json)), run.agent_group_key)
    return ResumeScope((run.stage_key,), "")


def _prior_run(session: Session, run: AgentRun) -> AgentRun | None:
    same_scope = (
        AgentRun.agent_group_key == run.agent_group_key
        if run.agent_group_key
        else AgentRun.stage_key == run.stage_key
    )
    return session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == run.ticket_id)
        .where(same_scope)
        .where(AgentRun.id != run.id)
        .where(col(AgentRun.created_at) <= run.created_at)
        .order_by(col(AgentRun.created_at).desc())
        .limit(1)
    ).first()


def _newest_feedback(
    session: Session, ticket: Ticket, stage_keys: Sequence[str], since: datetime | None
) -> Artifact | None:
    titles = [rework_feedback_artifact_title(key) for key in stage_keys]
    query = (
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id)
        .where(Artifact.kind == ReworkArtifactKind.FEEDBACK)
        .where(col(Artifact.title).in_(titles))
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


#: The sections of the stage prompt that change between runs, rendered for the
#: run being planned (`stage_context.turn_context_lines`); "" when there are none.
TurnContext = Callable[[], str]


def follow_up_prompt(
    session: Session, ticket: Ticket, stage_key: str, turn_context: TurnContext | None = None
) -> str:
    """The resumed session's next turn: the finding, and the report it owes.

    With `turn_context`, the finding arrives inside it (its "why you're here"
    section renders the same feedback ledger) beside the unresolved ledger,
    rechecks and deferrals the session's kept system prompt predates.
    """
    context = turn_context().strip() if turn_context is not None else ""
    finding = context or render_rework_feedback(session, ticket, stage_key)
    return (
        "Your earlier turn on this stage was sent back for rework. The finding:\n\n"
        f"{finding}\n\n"
        "Other stages may have changed the tree since your turn: re-read any file "
        "before you rely on what you remember of it. Address the finding, then "
        + REPORT_BLOCK_REMINDER
    )


def continuation_prompt(session: Session, ticket: Ticket, run: AgentRun) -> str:
    """A group session's next turn after it ended before its last step."""
    covered = list(_scope_of(run).stage_keys)
    done = _done_group_keys(session, ticket, run)
    return (
        "Your earlier turn on this group ended before every step was complete. Steps "
        f"already complete: {', '.join(done) or 'none'}. Continue from step "
        f"'{run.stage_key}' (remaining: {', '.join(covered)}). Other stages may have changed "
        "the tree since your turn: re-read any file before you rely on what you remember "
        "of it. Then " + REPORT_BLOCK_REMINDER
    )


def _done_group_keys(session: Session, ticket: Ticket, run: AgentRun) -> list[str]:
    """The group's steps already DONE on this ticket, in template order."""
    _, stages = resolve_ticket_stages(session, ticket)
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    if instance is None:
        return []
    stage_map = parse_stage_map(instance, stages)
    return [
        member.key
        for member in group_span(stages, run.stage_key)
        if stage_map.get(member.key) is StageStatus.DONE
    ]


def _is_continuation(run: AgentRun, prior: AgentRun | None) -> bool:
    """Whether `run` picks up a group run that ended at or before `run`'s step."""
    if prior is None or not run.agent_group_key:
        return False
    covered = list(_scope_of(prior).stage_keys)
    if run.stage_key not in covered or prior.stage_key not in covered:
        return False
    return covered.index(run.stage_key) >= covered.index(prior.stage_key)


def _skip_reason(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    *,
    prior: AgentRun | None,
    feedback: Artifact | None,
    adapter: str,
    repo_root: Path,
) -> str:
    """Why this round cannot resume `prior`'s session, or "" when it can.

    `feedback` None is a group continuation round (`_is_continuation`).
    """
    if prior is None:
        return "no earlier run at this stage to resume"
    # A group's session is the group's: its steps' agents differ by design, and
    # validation guarantees they share one adapter.
    if not run.agent_group_key and prior.agent_id != run.agent_id:
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
    if prior.spec_hash and run.spec_hash and prior.spec_hash != run.spec_hash:
        return (
            f"the spec changed since the earlier run {prior.run_code} read it; "
            "its session holds the old description, criteria or documents"
        )
    if feedback is None:
        if prior.resumed_from_run_id and not prior.rework_feedback_id:
            return (
                f"the earlier run {prior.run_code} already continued a session once after "
                "an early end"
            )
        return ""
    earlier = _already_resumed_for(session, ticket, feedback)
    if earlier is not None:
        return f"this finding already resumed a session once, in run {earlier.run_code}"
    return ""


def plan_rework_resume(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    *,
    adapter: str,
    repo_root: Path,
    turn_context: TurnContext | None = None,
) -> ResumePlan | None:
    """How `run` answers its rework finding, or None when it is not a rework round.

    A run is a rework round when a finding for its stage arrived after the
    stage's previous run (or, with no previous run, when one exists at all).
    The plan is recorded on the run before it is returned. A group run with no
    finding that picks up where its group's earlier run ended is a continuation
    round (`feedback_id == ""`). `turn_context` is called only when the round
    resumes on a finding.
    """
    prior = _prior_run(session, run)
    scope = _scope_of(run)
    feedback = _newest_feedback(
        session, ticket, scope.stage_keys, prior.created_at if prior is not None else None
    )
    if feedback is None and not _is_continuation(run, prior):
        return None
    reason = _skip_reason(
        session, ticket, run, prior=prior, feedback=feedback, adapter=adapter, repo_root=repo_root
    )
    feedback_id = feedback.id if feedback is not None else ""
    if reason or prior is None:
        plan = ResumePlan(feedback_id=feedback_id, skip_reason=reason)
    else:
        plan = ResumePlan(
            feedback_id=feedback_id,
            prior_run_id=prior.id,
            session_id=session_id_of(prior),
            follow_up=(
                follow_up_prompt(session, ticket, _feedback_key(feedback, scope), turn_context)
                if feedback is not None
                else continuation_prompt(session, ticket, run)
            ),
            session_prompt=read_session_prompt(prior) if adapter == CliAdapter.CLAUDE else "",
        )
    run.rework_feedback_id = plan.feedback_id
    run.resumed_from_run_id = plan.prior_run_id
    run.resume_skip_reason = plan.skip_reason
    session.add(run)
    session.commit()
    return plan


def _feedback_key(feedback: Artifact, scope: ResumeScope) -> str:
    """The covered step `feedback` is addressed to."""
    return next(
        key for key in scope.stage_keys if rework_feedback_artifact_title(key) == feedback.title
    )


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
