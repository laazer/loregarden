"""A run is supervised from before it queues for capacity until it finishes.

A run is committed `RUNNING` before it waits for its host and docker leases, and
that wait may last `agent_run_capacity_wait_seconds` (30 minutes) — three times
AGENT_RUN_LEASE. Every call site used to enter capacity *outside* the renewal,
so a run waiting its turn looked abandoned: run_f4bf51 (2026-11-03) waited 23
minutes behind a pre-push suite, was reaped at 10, and when its lease was
granted the still-waiting thread spawned `claude` on the failed row.

Two halves, both through `run_lease.supervised`:

1. the lease is renewed while the run waits, so the sweep spares it;
2. a run settled during the wait never reaches its executor.

And one structural test, because the bug was a call-site ordering: no module
may enter run capacity except through `supervised`.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkItemType
from loregarden.services import run_service
from loregarden.services.run_lease import (
    AGENT_RUN_LEASE,
    RunSettledBeforeStart,
    supervised,
)
from loregarden.services.run_service import settle_expired_agent_runs
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session

SERVICES = Path(run_service.__file__).resolve().parent.parent


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="run supervision",
        work_item_type=WorkItemType.MILESTONE,
    )


@pytest.fixture(name="stale_run")
def stale_run_fixture(db_session, ticket) -> AgentRun:
    """A RUNNING run started longer ago than the lease and never renewed: the
    row run_f4bf51 was when the sweep found it."""
    run = AgentRun(
        run_code="run_supervision",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    run.started_at = datetime.now(timezone.utc) - (AGENT_RUN_LEASE + timedelta(minutes=5))
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _wait_for_first_renewal(engine, run_id: str) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        with Session(engine) as probe:
            if probe.get(AgentRun, run_id).last_seen_at is not None:
                return
        time.sleep(0.05)
    pytest.fail("the lease was never renewed while the run waited for capacity")


def _status(engine, run_id: str) -> RunStatus:
    with Session(engine) as probe:
        return probe.get(AgentRun, run_id).status


def test_a_run_waiting_for_capacity_past_the_lease_survives_the_sweep(
    stale_run, isolated_db
) -> None:
    swept: list[list[AgentRun]] = []

    @contextmanager
    def slow_capacity() -> Iterator[None]:
        # The sweep fires while the run is still in line, the moment that
        # killed run_f4bf51.
        _wait_for_first_renewal(isolated_db, stale_run.id)
        with Session(isolated_db) as sweeper:
            swept.append(settle_expired_agent_runs(sweeper))
        yield

    with supervised(stale_run.id, slow_capacity()):
        pass

    assert swept == [[]]
    assert _status(isolated_db, stale_run.id) is RunStatus.RUNNING


def test_without_the_renewal_the_same_wait_is_reaped(stale_run, isolated_db) -> None:
    """The control: the sweep in the test above would have settled this run,
    so its survival is the renewal's doing and not a sweep that judged nothing."""
    with Session(isolated_db) as sweeper:
        settled = settle_expired_agent_runs(sweeper)

    assert [run.id for run in settled] == [stale_run.id]


def test_a_run_settled_while_it_waits_never_reaches_its_body(stale_run, isolated_db) -> None:
    exited = []

    @contextmanager
    def capacity_granted_after_a_cancel() -> Iterator[None]:
        with Session(isolated_db) as other:
            run = other.get(AgentRun, stale_run.id)
            run.status = RunStatus.CANCELLED
            other.add(run)
            other.commit()
        try:
            yield
        finally:
            exited.append(True)

    with pytest.raises(RunSettledBeforeStart, match="cancelled while it waited"):
        with supervised(stale_run.id, capacity_granted_after_a_cancel()):
            pytest.fail("the agent started on a settled run")

    assert exited == [True], "the capacity granted to a settled run was not handed back"


def test_the_background_path_does_not_spawn_an_agent_for_a_run_reaped_in_line(
    stale_run, isolated_db
) -> None:
    """The wiring at `execute_agent_run_background`, the path run_f4bf51 took."""

    @contextmanager
    def reaped_in_line(_session, run) -> Iterator[None]:
        with Session(isolated_db) as other:
            row = other.get(AgentRun, run.id)
            row.status = RunStatus.FAILED
            other.add(row)
            other.commit()
        yield

    with (
        mock.patch.object(run_service, "run_capacity", reaped_in_line),
        mock.patch("loregarden.agents.executors.cli.CliAgentExecutor.execute") as execute,
    ):
        run_service.execute_agent_run_background(stale_run.id)

    execute.assert_not_called()
    assert _status(isolated_db, stale_run.id) is RunStatus.FAILED


#: The context managers that hold a run's capacity.
_CAPACITY = {"run_capacity", "host_capacity_for_run"}


def _direct_capacity_entries(path: Path) -> list[int]:
    """Lines where a `with` enters run capacity itself, not via `supervised`."""
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.With | ast.AsyncWith):  # py-org: allow-isinstance
            continue
        for item in node.items:
            call = item.context_expr
            func = call.func if isinstance(call, ast.Call) else None  # py-org: allow-isinstance
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in _CAPACITY:
                lines.append(call.lineno)
    return lines


def test_no_module_enters_run_capacity_outside_supervised() -> None:
    """Every writer, enumerated: the defect was one call site's ordering, and
    five call sites had copied it."""
    offenders = [
        f"{path.relative_to(SERVICES)}:{lineno}"
        for path in sorted(SERVICES.rglob("*.py"))
        if path.name != "run_capacity.py"
        for lineno in _direct_capacity_entries(path)
    ]

    assert offenders == [], f"enter run capacity through run_lease.supervised: {offenders}"


def test_the_enumeration_sees_a_direct_entry(tmp_path) -> None:
    """The control for the test above: an empty list must mean none, not blind."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "with (\n    run_capacity(s, r),\n    lease_renewal(r.id),\n):\n    pass\n"
        "with lease_renewal(r.id), svc.host_capacity_for_run(s, r):\n    pass\n",
        encoding="utf-8",
    )

    assert _direct_capacity_entries(sample) == [2, 6]
