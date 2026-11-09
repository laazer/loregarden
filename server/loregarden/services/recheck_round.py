"""A review lane re-run after its reject rechecks its own findings, not the whole change.

After a rework round the review lanes ran as fresh reviews, and a fresh review
finds new things. That is how a ticket that met its criteria at round three ran
three more rounds (CLAUDE.md, "Knowing when a ticket is done"). fest returns a
rejected gate to the same gate step with "Original reason: …", and Festival's
re-reviews narrowed to the fix; both loops converged.

A **recheck round** is a judging lane of a parallel stage whose last run of that
stage rejected on blocking findings that stood. Its prompt lists those findings
(`P1`, `P2`, …) and the diff since, and asks it to answer each one, resolved or
not. Its reject stands only on a prior finding it names unresolved
(`finding_bar.rechecked`); anything new is recorded as not blocking.

A lane that passed last time and re-runs because the rework touched what it read
(`review_relens`) has no prior findings, so it reviews as before. The findings a
recheck round carries forward are the ones its own reject stood on: a first
reject's complete blocking findings, or a recheck's unresolved ones.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    AgentRun,
    RecheckStatus,
    RunStatus,
    StageReportStatus,
    StageType,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.finding_bar import (
    PriorFinding,
    is_judging,
    recheck_answers,
    settle_review,
)
from loregarden.services.stage_report import parse_stage_report
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, col, select

_REJECTS = frozenset({StageReportStatus.FAIL, StageReportStatus.NEEDS_REWORK})
#: How many changed paths the prompt lists before it says how many more.
_PATHS_SHOWN = 30


def _previous_run(session: Session, run: AgentRun) -> AgentRun | None:
    """This lane's last finished run of the same stage before `run`."""
    return session.exec(
        select(AgentRun)
        .where(
            col(AgentRun.ticket_id) == run.ticket_id,
            col(AgentRun.stage_key) == run.stage_key,
            col(AgentRun.agent_id) == run.agent_id,
            col(AgentRun.skill_name) == run.skill_name,
            col(AgentRun.status) == RunStatus.SUCCEEDED,
            col(AgentRun.created_at) < run.created_at,
            col(AgentRun.id) != run.id,
        )
        .order_by(col(AgentRun.created_at).desc())
    ).first()


def _review_lane(session: Session, ticket: Ticket, run: AgentRun) -> WorkflowStageDef | None:
    """`run`'s stage when it is a judging lane of a parallel stage, else None."""
    _, stages = resolve_ticket_stages(session, ticket)
    stage = next((stage for stage in stages if stage.key == run.stage_key), None)
    if stage is None or stage.stage_type != StageType.PARALLEL:
        return None
    return stage if is_judging(run.agent_id, stage) else None


def _findings_its_reject_stood_on(
    session: Session, ticket: Ticket, stage: WorkflowStageDef, run: AgentRun
) -> list[PriorFinding]:
    """The blocking findings `run`'s own reject stood on, or [] when it did not reject."""
    priors = _rechecked_by(session, ticket, stage, run)
    report = parse_stage_report(run.stdout or "")
    settled, _ = settle_review(report, agent_id=run.agent_id, stage=stage, priors=priors)
    if settled is None or settled.status not in _REJECTS:
        return []
    if priors:
        return [
            prior
            for prior, status, _ in recheck_answers(report, priors)
            if status is RecheckStatus.UNRESOLVED
        ]
    complete = [f for f in settled.findings if f.blocking and not f.missing_parts()]
    return [PriorFinding(f"P{index}", finding) for index, finding in enumerate(complete, 1)]


def _rechecked_by(
    session: Session, ticket: Ticket, stage: WorkflowStageDef, run: AgentRun
) -> list[PriorFinding]:
    previous = _previous_run(session, run)
    if previous is None:
        return []
    return _findings_its_reject_stood_on(session, ticket, stage, previous)


def prior_findings(session: Session, ticket: Ticket, run: AgentRun) -> list[PriorFinding]:
    """The findings `run` rechecks, or [] when it is not a recheck round."""
    stage = _review_lane(session, ticket, run)
    return _rechecked_by(session, ticket, stage, run) if stage is not None else []


def _changed_paths(session: Session, ticket: Ticket, since: AgentRun) -> list[str] | None:
    """What runs after `since` changed, or None when one of them recorded nothing."""
    later = session.exec(
        select(AgentRun).where(
            col(AgentRun.ticket_id) == ticket.id,
            col(AgentRun.created_at) > since.created_at,
        )
    ).all()
    changed: set[str] = set()
    for later_run in later:
        if later_run.changed_paths_recorded_at is None:
            return None
        changed.update(json.loads(later_run.changed_paths_json or "[]"))
    return sorted(changed)


def recheck_context_lines(session: Session | None, ticket: Ticket, run: AgentRun) -> list[str]:
    """The prompt section for a recheck round, or [] when `run` is not one."""
    if session is None:
        return []
    priors = prior_findings(session, ticket, run)
    previous = _previous_run(session, run) if priors else None
    if previous is None:
        return []
    head = resolve_head_sha(session, ticket)
    lines = [
        "",
        "## This is a recheck round",
        "Your last run of this stage rejected the work on the findings below, and the work "
        "has been reworked since. Answer one question per finding: did the rework resolve "
        "it? You may reject only by naming one of these unresolved. Anything new you notice "
        "goes in `findings` with `blocking: false` and a `disposition` (or file a ticket); "
        "it does not send the work back in this round.",
        "",
        f"The diff since your last run: `git diff {previous.start_head_sha or '<unknown>'}"
        f"..{head or 'HEAD'}`.",
    ]
    changed = _changed_paths(session, ticket, previous)
    if changed is None:
        lines.append("Not every run since recorded its changed files; read the diff itself.")
    elif changed:
        shown = ", ".join(f"`{path}`" for path in changed[:_PATHS_SHOWN])
        more = f" (+{len(changed) - _PATHS_SHOWN} more)" if len(changed) > _PATHS_SHOWN else ""
        lines.append(f"Files changed since: {shown}{more}.")
    else:
        lines.append("The runs since recorded no changed files.")
    lines += ["", "Your prior findings:"]
    for prior in priors:
        finding = prior.finding
        lines.append(
            f"- **{prior.id}**: {finding.summary} — trigger: {finding.trigger}; "
            f"path: {finding.path}; impact: {finding.impact}"
        )
    lines += [
        "",
        "Answer each in your stage report as "
        '`"rechecks": [{"id": "P1", "resolved": true, "note": "what you checked"}]`. '
        "Report `needs_rework` only when at least one is `resolved: false`.",
    ]
    return lines
