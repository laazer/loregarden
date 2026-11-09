"""A review, verify or gate reject stands only on a finding that shows its defect.

A judging stage sent work back on anything it found concerning: the gatekeeper's
prompt told it to treat every ticket as not ready by default and to prefer
rework, and lg-agent-prompt-196 went back to implement nine times. CLAUDE.md
already says a reject needs a named unmet criterion and a defect no criterion
covers is filed and passed; nothing enforced it.

The bar, from Festival's review loops (findings per round converged 6→2→1,
13→1, 4→0): a blocking finding states a reachable **trigger**, the execution
**path** from there to the defect, and the **impact** on a person using the
system. A `fail`/`needs_rework` report from a judging stage with no such finding
is not a reject: the stage proceeds as passed, and the history records why
(`reject_not_admitted`). A finding that does not block says where it went
(`FindingDisposition`) and is recorded (`findings_recorded`), never dropped.

Judging is decided by who ran (`JUDGING_AGENTS`) or the stage's type (verify,
gate). `admitted` is pure, so the parallel-stage reconciler can re-judge a lane
as often as it likes; `admit_run_report` records, once per run, at completion.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace

from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    RecheckStatus,
    StageReportStatus,
    StageType,
    Ticket,
    WorkflowStageDef,
)
from loregarden.models.domain.orchestrator_decisions import OrchestratorDecision
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.stage_report import Finding, StageReport, parse_stage_report
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, select

#: The agents whose reject must show its defect: the verifier, the gatekeepers,
#: and every review lens.
JUDGING_AGENTS = frozenset(
    {
        "verifier",
        "gatekeeper",
        "ac_gatekeeper",
        "architecture_reviewer",
        "static_qa",
        "security_reviewer",
        "gdscript_reviewer",
        "visual_qa",
    }
)

_JUDGING_STAGE_TYPES = frozenset({StageType.VERIFY, StageType.GATE})
_REJECTS = frozenset({StageReportStatus.FAIL, StageReportStatus.NEEDS_REWORK})


def is_judging(agent_id: str, stage: WorkflowStageDef | None) -> bool:
    return agent_id in JUDGING_AGENTS or (
        stage is not None and stage.stage_type in _JUDGING_STAGE_TYPES
    )


def unadmitted_reason(findings: list[Finding]) -> str:
    """Why these findings do not make a reject, or "" when one blocking finding is complete."""
    blocking = [finding for finding in findings if finding.blocking]
    if not blocking:
        return "it named no blocking finding"
    if any(not finding.missing_parts() for finding in blocking):
        return ""
    gaps = "; ".join(
        f"'{finding.summary or 'an unnamed finding'}' gives no {', '.join(finding.missing_parts())}"
        for finding in blocking
    )
    return f"no blocking finding was complete ({gaps})"


def admitted(
    report: StageReport | None, *, agent_id: str, stage: WorkflowStageDef | None
) -> tuple[StageReport | None, str]:
    """`report` as it settles the stage, and why its reject was not admitted ("" if it was).

    Only a judging stage's `fail`/`needs_rework` is examined. One that is not
    admitted settles as a pass: nothing to reroute to, no criteria named unmet.
    """
    if report is None or report.status not in _REJECTS or not is_judging(agent_id, stage):
        return report, ""
    reason = unadmitted_reason(report.findings)
    if not reason:
        return report, ""
    return _as_pass(report), reason


def _as_pass(report: StageReport) -> StageReport:
    """A reject that did not stand: a pass, with nothing to reroute to and no criteria unmet."""
    return replace(
        report,
        status=StageReportStatus.PASS.value,
        reroute_to_stage=None,
        reroute_to_agent=None,
        unmet_criteria=[],
    )


@dataclass(frozen=True)
class PriorFinding:
    """One blocking finding a review lane's last reject stood on, as a recheck round sees it.

    `id` (`P1`, `P2`, …) is how the prompt shows it and how the report answers it.
    """

    id: str
    finding: Finding


def recheck_answers(
    report: StageReport | None, priors: list[PriorFinding]
) -> list[tuple[PriorFinding, RecheckStatus, str]]:
    """Each prior finding with what the report said about it, and the reviewer's note."""
    answers = {recheck.id: recheck for recheck in report.rechecks} if report else {}
    rows = []
    for prior in priors:
        answer = answers.get(prior.id)
        if answer is None:
            rows.append((prior, RecheckStatus.UNANSWERED, ""))
        else:
            status = RecheckStatus.RESOLVED if answer.resolved else RecheckStatus.UNRESOLVED
            rows.append((prior, status, answer.note))
    return rows


def rechecked(
    report: StageReport | None, priors: list[PriorFinding]
) -> tuple[StageReport | None, str]:
    """`report` as it settles a recheck round, and why its reject was not admitted.

    A recheck round asks one question: did the rework resolve each prior
    finding? So a reject stands only when it names a prior finding unresolved;
    whatever the reviewer newly found is recorded as not blocking, never a new
    reject in this round. Pure, like `admitted`.
    """
    if report is None or not priors:
        return report, ""
    report = replace(
        report, findings=[replace(finding, blocking=False) for finding in report.findings]
    )
    if report.status not in _REJECTS:
        return report, ""
    if any(status is RecheckStatus.UNRESOLVED for _, status, _ in recheck_answers(report, priors)):
        return report, ""
    reason = "in a recheck round a reject names a prior finding unresolved, and it named none"
    return _as_pass(report), reason


def settle_review(
    report: StageReport | None,
    *,
    agent_id: str,
    stage: WorkflowStageDef | None,
    priors: list[PriorFinding],
) -> tuple[StageReport | None, str]:
    """The bar a judging report meets: the recheck rule when it has prior findings, else `admitted`."""
    if priors:
        return rechecked(report, priors)
    return admitted(report, agent_id=agent_id, stage=stage)


def finding_record(finding: Finding) -> dict:
    """A finding as the history's evidence carries it."""
    record = asdict(finding)
    record["disposition"] = finding.disposition.value if finding.disposition else None
    return record


def _stage(session: Session, ticket: Ticket, stage_key: str) -> WorkflowStageDef | None:
    _, stages = resolve_ticket_stages(session, ticket)
    return next((stage for stage in stages if stage.key == stage_key), None)


def _already_recorded(session: Session, run: AgentRun) -> bool:
    """Whether a decision of this module is already on record for `run`.

    A run completes once, but a reattaching server can settle a run again;
    the record is kept to one.
    """
    events = session.exec(
        select(DomainEvent.payload_json)
        .where(DomainEvent.run_id == run.id)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
    ).all()
    kinds = {
        OrchestratorDecision.REJECT_NOT_ADMITTED,
        OrchestratorDecision.FINDINGS_RECORDED,
        OrchestratorDecision.RECHECK_RECORDED,
    }
    return any(json.loads(payload or "{}").get("decision") in kinds for payload in events)


def record_unadmitted_reject(
    session: Session,
    ticket: Ticket,
    *,
    agent_id: str,
    stage_key: str,
    claimed: str,
    reason: str,
    findings: list[Finding],
    run_id: str | None,
) -> None:
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REJECT_NOT_ADMITTED,
        stage_key=stage_key,
        run_id=run_id,
        reason=(
            f"{agent_id} reported {claimed} on '{stage_key}', which is not a reject: {reason}. "
            "A blocking finding states its trigger, the path to the defect and the impact; "
            "the stage proceeds as passed and its findings are on record."
        ),
        evidence={"claimed": claimed, "findings": [finding_record(f) for f in findings]},
    )


def admit_run_report(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    report: StageReport | None,
    *,
    priors: list[PriorFinding],
) -> StageReport | None:
    """`settle_review`, with the decision recorded once for `run`.

    Records `reject_not_admitted` when a reject did not stand, and
    `recheck_recorded` for a recheck round, else `findings_recorded` when the
    report carried findings that do not block.
    """
    if priors and report is not None:
        return _admit_recheck(session, ticket, run, report, priors)
    if report is None or not report.findings and report.status not in _REJECTS:
        return report
    stage = _stage(session, ticket, run.stage_key)
    if not is_judging(run.agent_id, stage):
        return report
    settled, reason = admitted(report, agent_id=run.agent_id, stage=stage)
    if _already_recorded(session, run):
        return settled
    if reason:
        record_unadmitted_reject(
            session,
            ticket,
            agent_id=run.agent_id,
            stage_key=run.stage_key,
            claimed=report.status,
            reason=reason,
            findings=report.findings,
            run_id=run.id,
        )
        return settled
    non_blocking = [finding for finding in report.findings if not finding.blocking]
    if non_blocking:
        record_orchestrator_decision(
            session,
            ticket,
            decision=OrchestratorDecision.FINDINGS_RECORDED,
            stage_key=run.stage_key,
            run_id=run.id,
            reason=(
                f"{run.agent_id} recorded {len(non_blocking)} finding(s) on '{run.stage_key}' "
                "that do not block, each with where it went."
            ),
            evidence={"findings": [finding_record(f) for f in non_blocking]},
        )
    return settled


def _admit_recheck(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    report: StageReport,
    priors: list[PriorFinding],
) -> StageReport | None:
    settled, reason = rechecked(report, priors)
    if _already_recorded(session, run):
        return settled
    rows = recheck_answers(report, priors)
    counts = {status: sum(1 for _, s, _ in rows if s is status) for status in RecheckStatus}
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.RECHECK_RECORDED,
        stage_key=run.stage_key,
        run_id=run.id,
        reason=(
            f"{run.agent_id} rechecked {len(rows)} prior finding(s) on '{run.stage_key}': "
            + ", ".join(f"{counts[status]} {status.value}" for status in RecheckStatus)
            + (
                f"; {len(report.findings)} new finding(s) recorded as not blocking."
                if report.findings
                else "."
            )
        ),
        evidence={
            "claimed": report.status,
            "rechecks": [
                {
                    "id": prior.id,
                    "summary": prior.finding.summary,
                    "status": status.value,
                    "note": note,
                }
                for prior, status, note in rows
            ],
            "new_findings": [finding_record(f) for f in settled.findings] if settled else [],
        },
    )
    if reason:
        record_unadmitted_reject(
            session,
            ticket,
            agent_id=run.agent_id,
            stage_key=run.stage_key,
            claimed=report.status,
            reason=reason,
            findings=report.findings,
            run_id=run.id,
        )
    return settled


def tool_outcome(
    session: Session,
    ticket: Ticket,
    stage: WorkflowStageDef,
    run: AgentRun | None,
    *,
    agent_id: str,
    outcome: str,
    findings: list[Finding],
) -> str:
    """`outcome` of an agent's `complete_stage`, or a pass when its reject does not stand.

    The tool path's twin of `admit_run_report`: a judging agent's reject needs
    a complete blocking finding in the call's `findings`; one that has none is
    recorded and the stage completes as passed.
    """
    if outcome == StageReportStatus.PASS or not is_judging(agent_id, stage):
        return outcome
    reason = unadmitted_reason(findings)
    if not reason:
        return outcome
    record_unadmitted_reject(
        session,
        ticket,
        agent_id=agent_id,
        stage_key=stage.key,
        claimed="reject",
        reason=reason,
        findings=findings,
        run_id=run.id if run else None,
    )
    return StageReportStatus.PASS.value


def settle_stage_claim(
    session: Session,
    ticket: Ticket,
    stage: WorkflowStageDef,
    run: AgentRun | None,
    *,
    agent_id: str,
    outcome: str,
    findings: list[Finding] | None,
) -> tuple[StageReport | None, str]:
    """The run's report and the tool's `outcome`, each as it settles a `complete_stage`.

    `findings` is None for an internal caller (a person's inbox reject), whose
    outcome is not examined; an agent's call passes its list, empty or not.
    """
    report, _ = admitted(
        parse_stage_report(run.stdout or "") if run else None, agent_id=agent_id, stage=stage
    )
    if findings is not None:
        outcome = tool_outcome(
            session, ticket, stage, run, agent_id=agent_id, outcome=outcome, findings=findings
        )
    return report, outcome
