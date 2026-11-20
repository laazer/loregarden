"""Whether every acceptance criterion of a ticket is answered for, checked at HEAD.

Read-only apart from running the named tests. `criterion_gate` decides what
to do with the answer. Each criterion is checked by how it was settled:

- met by tests: every named test exists in the checkout (its file, and its
  name literally in that file), and a rerun at HEAD passes it. Tests no
  configured command can run are only checked to exist, and the result says
  which, so a test that was not rerun is never reported as one that was.
- met by evidence: the evidence was captured at a commit with no change
  between it and HEAD.
- delegated: the target still carries the criterion and was not closed
  without doing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import (
    Artifact,
    CriterionDisposition,
    CriterionSettlement,
    GateOutcome,
    OrchestrationRun,
    Ticket,
)
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.criterion_ledger import (
    CriterionGap,
    GapKind,
    delegation_gaps,
    settlements_of,
    test_ids,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.named_tests import named_test_exists, run_named_tests
from loregarden.services.reject_tests import reject_tests_by_criterion
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ticket_worktree import resolve_ticket_root
from sqlmodel import Session


@dataclass
class CriteriaCheck:
    gaps: list[CriterionGap] = field(default_factory=list)
    #: Tests that passed a rerun at HEAD.
    rerun: list[str] = field(default_factory=list)
    #: Tests that exist but that no configured command runs.
    not_rerun: list[str] = field(default_factory=list)
    #: Set when the tests could not be run at all: an environment problem, not
    #: an unmet criterion, so the caller blocks rather than reworking.
    unavailable: str = ""

    @property
    def passed(self) -> bool:
        return not self.gaps and not self.unavailable


def _evidence_current(root: Path, artifact: Artifact | None) -> bool:
    if artifact is None or not artifact.commit_sha:
        return False
    same = run_git(["diff", "--quiet", artifact.commit_sha, "HEAD"], cwd=root, check=False)
    return same.returncode == 0


def _met_gaps(
    session: Session, ticket: Ticket, root: Path, rows: dict[str, CriterionSettlement]
) -> tuple[list[CriterionGap], dict[str, list[str]]]:
    """Gaps from missing tests and stale evidence, and each criterion's existing tests.

    A criterion's tests are the ones it was settled on and every test a
    reviewer's admitted reject named against it (`reject_tests`): a settlement
    that leaves out the test proving it was unmet does not answer for it.
    """
    gaps: list[CriterionGap] = []
    tests_of: dict[str, list[str]] = {}
    rejected = reject_tests_by_criterion(session, ticket)
    for text, row in rows.items():
        if row.disposition is not CriterionDisposition.MET:
            continue
        settled_on = test_ids(row)
        if not settled_on:
            artifact = (
                session.get(Artifact, row.evidence_artifact_id)
                if row.evidence_artifact_id
                else None
            )
            if not _evidence_current(root, artifact):
                gaps.append(CriterionGap(text, GapKind.EVIDENCE_STALE, "the code changed after it"))
                continue
        ids = settled_on + [t for t in rejected.get(text, []) if t not in settled_on]
        if not ids:
            continue
        missing = [i for i in ids if not named_test_exists(root, i)]
        if missing:
            gaps.append(
                CriterionGap(text, GapKind.TESTS_FAILED, f"not found: {', '.join(missing)}")
            )
        else:
            tests_of[text] = ids
    return gaps, tests_of


def _rerun(
    session: Session,
    ticket: Ticket,
    root: Path,
    tests_of: dict[str, list[str]],
    check: CriteriaCheck,
    orch_run: OrchestrationRun | None,
) -> None:
    """Rerun each criterion's tests, recording failures against that criterion."""
    ran = run_named_tests(
        session, ticket, root, tests_of, orch_run=orch_run, transition="criteria check"
    )
    check.not_rerun.extend(ran.not_run)
    if ran.unavailable:
        check.unavailable = ran.unavailable
        return
    for group in ran.runs:
        if group.outcome is GateOutcome.PASSED:
            check.rerun.extend(i for i in group.ids if i not in check.rerun)
        else:
            check.gaps.append(
                CriterionGap(
                    group.key, GapKind.TESTS_FAILED, f"rerun at HEAD failed: {group.detail}"
                )
            )


def check_criteria(
    session: Session, ticket: Ticket, *, orch_run: OrchestrationRun | None = None
) -> CriteriaCheck:
    """Every current criterion of ``ticket``, checked against its settlement at HEAD.

    ``orch_run`` is the orchestration running the check, whose parent host lease
    the test rerun claims under; None outside one.
    """
    rows = settlements_of(session, ticket)
    check = CriteriaCheck(
        gaps=[
            CriterionGap(c, GapKind.UNSETTLED)
            for c in load_criteria(ticket.acceptance_criteria_json)
            if c not in rows
        ]
    )
    check.gaps.extend(delegation_gaps(session, ticket))
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        check.unavailable = f"{ticket.external_id} has no workspace to check its tests in"
        return check
    root = resolve_ticket_root(session, ticket, workspace)
    met_gaps, tests_of = _met_gaps(session, ticket, root, rows)
    check.gaps.extend(met_gaps)
    _rerun(session, ticket, root, tests_of, check, orch_run)
    return check
