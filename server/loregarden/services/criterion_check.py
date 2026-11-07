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

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import (
    Artifact,
    CriterionDisposition,
    CriterionSettlement,
    GateOutcome,
    Ticket,
)
from loregarden.models.domain.enums_capacity import DockerFootprint
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.criterion_ledger import (
    CriterionGap,
    GapKind,
    delegation_gaps,
    settlements_of,
    test_ids,
)
from loregarden.services.gate_capacity import acquire_gate_capacity
from loregarden.services.git_subprocess import run_git
from loregarden.services.land_verification import run_verify_command
from loregarden.services.orchestration_profile import (
    CriterionTestCommand,
    resolve_orchestration_profile,
)
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ticket_worktree import resolve_ticket_root
from sqlmodel import Session

#: A rerun of a handful of named tests; long enough for a cold `uv run`.
_LEASE_TTL_SECONDS = 900


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


def _test_file_and_name(test_id: str) -> tuple[str, str]:
    path, _, rest = test_id.partition("::")
    name = rest.rsplit("::", 1)[-1].split("[", 1)[0].strip().strip("\"'")
    return path.strip(), name


def _exists(root: Path, test_id: str) -> bool:
    path, name = _test_file_and_name(test_id)
    target = root / path
    if not name or not target.is_file():
        return False
    return name in target.read_text(encoding="utf-8", errors="replace")


def _runner_for(test_id: str, commands: list[CriterionTestCommand]) -> CriterionTestCommand | None:
    return next((c for c in commands if test_id.startswith(c.prefix)), None)


def _render(command: CriterionTestCommand, ids: list[str]) -> str:
    stripped = [i[len(command.prefix) :] if command.strip_prefix else i for i in ids]
    files = sorted({_test_file_and_name(i)[0] for i in stripped})
    return command.command.replace("{tests}", " ".join(shlex.quote(i) for i in stripped)).replace(
        "{files}", " ".join(shlex.quote(f) for f in files)
    )


def _evidence_current(root: Path, artifact: Artifact | None) -> bool:
    if artifact is None or not artifact.commit_sha:
        return False
    same = run_git(["diff", "--quiet", artifact.commit_sha, "HEAD"], cwd=root, check=False)
    return same.returncode == 0


def _met_gaps(
    session: Session, root: Path, rows: dict[str, CriterionSettlement]
) -> tuple[list[CriterionGap], dict[str, list[str]]]:
    """Gaps from missing tests and stale evidence, and each criterion's existing tests."""
    gaps: list[CriterionGap] = []
    tests_of: dict[str, list[str]] = {}
    for text, row in rows.items():
        if row.disposition is not CriterionDisposition.MET:
            continue
        ids = test_ids(row)
        if not ids:
            artifact = (
                session.get(Artifact, row.evidence_artifact_id)
                if row.evidence_artifact_id
                else None
            )
            if not _evidence_current(root, artifact):
                gaps.append(CriterionGap(text, GapKind.EVIDENCE_STALE, "the code changed after it"))
            continue
        missing = [i for i in ids if not _exists(root, i)]
        if missing:
            gaps.append(
                CriterionGap(text, GapKind.TESTS_FAILED, f"not found: {', '.join(missing)}")
            )
        else:
            tests_of[text] = ids
    return gaps, tests_of


def _batches(
    tests_of: dict[str, list[str]], commands: list[CriterionTestCommand], check: CriteriaCheck
) -> list[tuple[str, CriterionTestCommand, list[str]]]:
    """One run per criterion and command, so a failure lands on the criterion it belongs to.

    Batching every criterion's tests into one run would let one failing test
    mark every criterion in the batch failed. A ticket has about five criteria,
    so the price is a few short runs.
    """
    batches: list[tuple[str, CriterionTestCommand, list[str]]] = []
    for criterion, ids in tests_of.items():
        by_command: dict[int, list[str]] = {}
        for test_id in ids:
            runner = _runner_for(test_id, commands)
            if runner is None:
                check.not_rerun.append(test_id)
            else:
                by_command.setdefault(commands.index(runner), []).append(test_id)
        batches.extend((criterion, commands[index], group) for index, group in by_command.items())
    return batches


def _rerun(
    session: Session,
    ticket: Ticket,
    root: Path,
    tests_of: dict[str, list[str]],
    check: CriteriaCheck,
) -> None:
    """Rerun each criterion's tests, recording failures against that criterion."""
    workspace = workspace_for_ticket(session, ticket)
    commands = (
        resolve_orchestration_profile(workspace).gates.criterion_test_commands if workspace else []
    )
    batches = _batches(tests_of, commands, check)
    if not batches or workspace is None:
        return
    lease = acquire_gate_capacity(
        session,
        DockerFootprint.SERVICE,
        ticket=ticket,
        workspace=workspace,
        transition="criteria check",
        ttl_seconds=_LEASE_TTL_SECONDS,
    )
    try:
        for criterion, command, ids in batches:
            outcome = run_verify_command(
                _render(command, ids), root, footprint=DockerFootprint.SERVICE
            )
            if outcome.outcome is GateOutcome.UNAVAILABLE:
                check.unavailable = outcome.detail or f"could not run {outcome.command}"
                return
            if outcome.outcome is GateOutcome.PASSED:
                check.rerun.extend(i for i in ids if i not in check.rerun)
            else:
                tail = (outcome.output or outcome.detail)[-600:]
                check.gaps.append(
                    CriterionGap(criterion, GapKind.TESTS_FAILED, f"rerun at HEAD failed: {tail}")
                )
    finally:
        lease.release(session)


def check_criteria(session: Session, ticket: Ticket) -> CriteriaCheck:
    """Every current criterion of ``ticket``, checked against its settlement at HEAD."""
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
    met_gaps, tests_of = _met_gaps(session, root, rows)
    check.gaps.extend(met_gaps)
    _rerun(session, ticket, root, tests_of, check)
    return check
