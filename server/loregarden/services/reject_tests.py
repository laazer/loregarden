"""A reviewer's reject is a red test: it names a test that fails where it rejects.

Rework went round in circles because a reject reached the implementer as
prose. Of 41 review and verify reject rounds on the live database (Oct 20 -
Nov 18), 16 re-rejected a criterion an earlier round had already named: most
were a new site of the same defect, found after the implementer fixed the one
site the prose described, and verify had passed each round on tests of its own
choosing. The reproduction the reviewer worked out was thrown away.

So a blocking finding from a judging stage names `test`, a test the reviewer
wrote that fails at the commit it rejects, or says in `judgment` why no test
can show the defect (`finding_bar`). Then:

- **Admission runs it** (`check_reject_tests`). A named test that is not in
  the checkout, or that passes, shows no defect, and the finding does not
  block. A test no command can run, or a run that could not finish, is
  recorded as unverified and the reject stands: the machine failing is not
  the reviewer being wrong.
- **The tests are recorded** against the criteria the reject named unmet
  (`record_reject_tests`), and the implementer's brief names them as the job
  (`rework_brief`).
- **The criteria check reruns them at HEAD** with each criterion's own tests
  (`criterion_check`), so the gate cannot pass while one fails, whatever tests
  verify settled the criterion on. They stay in the suite after.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from loregarden.models.domain import GateOutcome, OrchestrationRun, Ticket
from loregarden.models.domain.orchestrator_decisions import OrchestratorDecision
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.named_tests import (
    named_test_exists,
    run_named_tests,
    run_named_tests_in_turn,
)
from loregarden.services.orchestrator_decisions import decisions_of, record_orchestrator_decision
from loregarden.services.stage_report import Finding
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ticket_worktree import resolve_ticket_root
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session


@dataclass(frozen=True)
class RejectTestNote:
    """One named test and what became of it."""

    test: str
    why: str


@dataclass
class RejectTestCheck:
    #: Failed at the rejected commit: the reject stands on these.
    failing: list[str] = field(default_factory=list)
    #: In the checkout but not run to a verdict (no command runs it, no
    #: capacity, a timeout). The reject stands; the record says why.
    unverified: list[RejectTestNote] = field(default_factory=list)
    #: Not in the checkout, or passing: these show no defect.
    refuted: list[RejectTestNote] = field(default_factory=list)

    def refuted_tests(self) -> set[str]:
        return {note.test for note in self.refuted}

    def standing_tests(self) -> list[str]:
        return self.failing + [note.test for note in self.unverified]


def _blocking_tests(findings: list[Finding]) -> list[str]:
    tests: list[str] = []
    for finding in findings:
        if finding.blocking and finding.test and finding.test not in tests:
            tests.append(finding.test)
    return tests


def check_reject_tests(
    session: Session,
    ticket: Ticket,
    findings: list[Finding],
    *,
    orch_run: OrchestrationRun | None = None,
    in_turn: bool = False,
) -> RejectTestCheck:
    """Run each test a blocking finding names, at the ticket's checkout as it is now.

    The reviewer has just written them, so the checkout is the commit it
    rejects plus the new tests, uncommitted: exactly what to run. ``in_turn``
    runs them under the agent's own lease (`run_named_tests_in_turn`), for the
    Stop hook, which reads the database read-only and cannot book one.
    """
    check = RejectTestCheck()
    tests = _blocking_tests(findings)
    if not tests:
        return check
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        check.unverified = [
            RejectTestNote(t, "the ticket has no workspace to run it in") for t in tests
        ]
        return check
    root = resolve_ticket_root(session, ticket, workspace)
    present = [t for t in tests if named_test_exists(root, t)]
    check.refuted = [
        RejectTestNote(t, "not in the checkout (its file, or its name in that file)")
        for t in tests
        if t not in present
    ]
    groups = {t: [t] for t in present}
    ran = (
        run_named_tests_in_turn(session, ticket, root, groups)
        if in_turn
        else run_named_tests(
            session, ticket, root, groups, orch_run=orch_run, transition="reject test check"
        )
    )
    check.unverified += [RejectTestNote(t, "no configured command runs it") for t in ran.not_run]
    for group in ran.runs:
        (test,) = group.ids
        if group.outcome is GateOutcome.FAILED:
            check.failing.append(test)
        elif group.outcome is GateOutcome.PASSED:
            check.refuted.append(
                RejectTestNote(test, "it passes at the rejected commit, so it shows no defect")
            )
        else:
            check.unverified.append(RejectTestNote(test, f"its run ended {group.outcome.value}"))
    if ran.unavailable:
        seen = set(check.failing) | {n.test for n in check.unverified + check.refuted}
        check.unverified += [
            RejectTestNote(t, f"it could not be run: {ran.unavailable}")
            for t in present
            if t not in seen
        ]
    return check


def standing(findings: list[Finding], check: RejectTestCheck) -> list[Finding]:
    """``findings`` with every blocking one whose test was refuted no longer blocking."""
    refuted = check.refuted_tests()
    return [
        replace(finding, blocking=False)
        if finding.blocking and finding.test in refuted
        else finding
        for finding in findings
    ]


def refuted_reason(check: RejectTestCheck) -> str:
    notes = "; ".join(f"{note.test}: {note.why}" for note in check.refuted)
    return f"no blocking finding's test fails at the rejected commit ({notes})"


def record_reject_tests(
    session: Session,
    ticket: Ticket,
    *,
    agent_id: str,
    stage_key: str,
    run_id: str | None,
    criteria: list[str],
    check: RejectTestCheck,
) -> None:
    """Put the reject's tests on record, against the criteria it named unmet."""
    standing_tests = check.standing_tests()
    unattached = (
        " It named no unmet criterion, so the criteria check does not rerun them."
        if not criteria
        else ""
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REJECT_TESTS_RECORDED,
        stage_key=stage_key,
        run_id=run_id,
        reason=(
            f"{agent_id}'s reject on '{stage_key}' stands on {len(standing_tests)} test(s) "
            f"({len(check.failing)} failing at the rejected commit, {len(check.unverified)} "
            f"not run to a verdict); rework is done when they pass.{unattached}"
        ),
        evidence={
            "criteria": criteria,
            "tests": standing_tests,
            "failing": check.failing,
            "unverified": [{"test": n.test, "why": n.why} for n in check.unverified],
            "refuted": [{"test": n.test, "why": n.why} for n in check.refuted],
            "commit_sha": resolve_head_sha(session, ticket),
        },
    )


class _RecordedRejectTests(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: str
    criteria: list[str] = []
    tests: list[str] = []


def reject_tests_by_criterion(session: Session, ticket: Ticket) -> dict[str, list[str]]:
    """Every test an admitted reject on ``ticket`` stood on, by the criterion it named unmet."""
    by_criterion: dict[str, list[str]] = {}
    for _event, payload in decisions_of(session, [ticket.id]):
        if payload.get("decision") != OrchestratorDecision.REJECT_TESTS_RECORDED.value:
            continue
        # Raises on a malformed row rather than skipping it: a skipped row is a
        # test the gate should rerun and silently does not.
        recorded = _RecordedRejectTests.model_validate(payload)
        for criterion in recorded.criteria:
            tests = by_criterion.setdefault(criterion, [])
            tests.extend(t for t in recorded.tests if t not in tests)
    return by_criterion
