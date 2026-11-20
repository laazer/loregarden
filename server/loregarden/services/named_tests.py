"""Running tests an agent named, by id, at the ticket's checkout.

One runner for every place a named test is taken as proof: a criterion settled
met (`criterion_check`, which needs them to pass) and a reviewer's reject
(`reject_tests`, which needs them to fail). A test id is `path::name` for
pytest or `path::"exact name"` for jest; the workspace profile's
`criterion_test_commands` say which command runs which prefix.

A test exists when its file does and its name is literally in that file. A
test no configured command runs exists but is not run, and the result says
so, so a test that was not run is never reported as one that was.
"""

from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import GateOutcome, OrchestrationRun, Ticket, Workspace
from loregarden.models.domain.enums_capacity import DockerFootprint
from loregarden.services.gate_capacity import GateCapacityUnavailable, acquire_gate_capacity
from loregarden.services.land_verification import run_verify_command
from loregarden.services.orchestration_capacity import parent_lease_for
from loregarden.services.orchestration_profile import (
    CriterionTestCommand,
    resolve_orchestration_profile,
)
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from sqlmodel import Session

#: A rerun of a handful of named tests; long enough for a cold `uv run`.
_LEASE_TTL_SECONDS = 900


def split_test_id(test_id: str) -> tuple[str, str]:
    path, _, rest = test_id.partition("::")
    name = rest.rsplit("::", 1)[-1].split("[", 1)[0].strip().strip("\"'")
    return path.strip(), name


def named_test_exists(root: Path, test_id: str) -> bool:
    path, name = split_test_id(test_id)
    target = root / path
    if not name or not target.is_file():
        return False
    return name in target.read_text(encoding="utf-8", errors="replace")


def _runner_for(test_id: str, commands: list[CriterionTestCommand]) -> CriterionTestCommand | None:
    return next((c for c in commands if test_id.startswith(c.prefix)), None)


def _render(command: CriterionTestCommand, ids: list[str]) -> str:
    stripped = [i[len(command.prefix) :] if command.strip_prefix else i for i in ids]
    files = sorted({split_test_id(i)[0] for i in stripped})
    return command.command.replace("{tests}", " ".join(shlex.quote(i) for i in stripped)).replace(
        "{files}", " ".join(shlex.quote(f) for f in files)
    )


@dataclass(frozen=True)
class GroupRun:
    """One group's tests, run by one command, and how the run ended."""

    key: str
    ids: list[str]
    outcome: GateOutcome
    #: The tail of the run's output, or why it could not run.
    detail: str


@dataclass
class NamedTestRun:
    runs: list[GroupRun] = field(default_factory=list)
    #: Tests that exist but that no configured command runs.
    not_run: list[str] = field(default_factory=list)
    #: Set when the tests could not be run at all (no capacity, a command that
    #: could not start): a fact about the machine, not about the tests.
    unavailable: str = ""


#: One planned run: the group it answers for, the command, and its tests.
_Batch = tuple[str, CriterionTestCommand, list[str]]


def _plan(
    session: Session, ticket: Ticket, groups: dict[str, list[str]], result: NamedTestRun
) -> tuple[Workspace | None, list[_Batch]]:
    """Which command runs which group's tests; tests none runs go to ``result.not_run``."""
    workspace = workspace_for_ticket(session, ticket)
    commands = (
        resolve_orchestration_profile(workspace).gates.criterion_test_commands if workspace else []
    )
    batches: list[_Batch] = []
    for key, ids in groups.items():
        by_command: dict[int, list[str]] = {}
        for test_id in ids:
            runner = _runner_for(test_id, commands)
            if runner is None:
                result.not_run.append(test_id)
            else:
                by_command.setdefault(commands.index(runner), []).append(test_id)
        batches.extend((key, commands[index], group) for index, group in by_command.items())
    return workspace, batches


def _run_batches(batches: list[_Batch], root: Path, result: NamedTestRun) -> NamedTestRun:
    """Run each batch with Python's bytecode cache in a directory of its own.

    A cached `.pyc` is trusted when the source's whole-second mtime and size
    match. An agent that ran its tests (filling `__pycache__`) and then changed
    a file at the same size within that second would have the old body rerun
    here, and pytest's rewritten test modules are read that way even under
    `-B`. `PYTHONPYCACHEPREFIX` moves every read and write, CPython's and
    pytest's, to an empty directory, so nothing cached in the checkout is read.
    """
    with tempfile.TemporaryDirectory(prefix="lg-named-tests-pyc-") as pycache:
        return _run_batches_in(batches, root, result, {"PYTHONPYCACHEPREFIX": pycache})


def _run_batches_in(
    batches: list[_Batch], root: Path, result: NamedTestRun, env: dict[str, str]
) -> NamedTestRun:
    for key, command, ids in batches:
        outcome = run_verify_command(
            _render(command, ids), root, footprint=DockerFootprint.SERVICE, extra_env=env
        )
        if outcome.outcome is GateOutcome.UNAVAILABLE:
            result.unavailable = outcome.detail or f"could not run {outcome.command}"
            return result
        tail = (outcome.output or outcome.detail or "")[-600:]
        result.runs.append(GroupRun(key, ids, outcome.outcome, tail))
    return result


def run_named_tests(
    session: Session,
    ticket: Ticket,
    root: Path,
    groups: dict[str, list[str]],
    *,
    orch_run: OrchestrationRun | None,
    transition: str,
) -> NamedTestRun:
    """Run each group's tests at ``root``, one run per group and command.

    One run per group, so a failure lands on the group it belongs to: batching
    every group into one run would let one failing test fail them all.

    Inside an orchestration the lease is a child of the run's parent, the claim
    `run_transition_gates` makes, so it never takes a place in line behind the
    capacity its own run already holds. A top-level claim did: the check queued
    behind two pre-push suites and its own orchestration, and gave up after the
    stall window (lg-milestone-that-1425). A lease that cannot be had is
    `unavailable`, never an exception that escapes the run boundary.
    """
    result = NamedTestRun()
    workspace, batches = _plan(session, ticket, groups, result)
    if not batches or workspace is None:
        return result
    parent = parent_lease_for(session, orch_run) if orch_run is not None else None
    try:
        lease = acquire_gate_capacity(
            session,
            DockerFootprint.SERVICE,
            ticket=ticket,
            workspace=workspace,
            transition=transition,
            ttl_seconds=_LEASE_TTL_SECONDS,
            parent_lease_id=parent.id if parent is not None else None,
            orchestration_run_id=orch_run.id if orch_run is not None else None,
        )
    except GateCapacityUnavailable as exc:
        result.unavailable = str(exc)
        return result
    try:
        return _run_batches(batches, root, result)
    finally:
        lease.release(session)


def run_named_tests_in_turn(
    session: Session, ticket: Ticket, root: Path, groups: dict[str, list[str]]
) -> NamedTestRun:
    """`run_named_tests` with no lease of its own, for a check inside an agent's live turn.

    The stage-report Stop hook reads the database read-only, so it cannot book a
    lease. It needs none of its own: it runs the command the agent just ran from
    its own shell, inside the turn whose agent-run lease already covers that.
    """
    result = NamedTestRun()
    workspace, batches = _plan(session, ticket, groups, result)
    if not batches or workspace is None:
        return result
    return _run_batches(batches, root, result)
