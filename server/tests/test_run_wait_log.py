"""A run waiting for capacity says so in its own log.

Until it got capacity its log read "Queuing agent…" under a live "Agent running…",
so a run an hour back in line looked like one at work.
"""

from __future__ import annotations

import json
from functools import partial
from unittest import mock

import pytest
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    CapacityPool,
    CliAdapter,
    DockerFootprint,
    DockerLease,
    RunLogLine,
    RunStatus,
    WorkflowStageDef,
)
from loregarden.services import capacity_line, docker_leases, run_capacity, stage_docker_capacity
from loregarden.services.run_capacity import HostCapacityUnavailable, host_capacity_for_run
from loregarden.services.run_log_stream import RunLogStreamer, bootstrap_run_log
from loregarden.services.run_wait_log import RunWaitLog, describe_wait
from loregarden.services.stage_docker_capacity import docker_capacity_for_stage
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_ticket, make_workspace
from tests.test_capacity_host_pool import _reserve, _set_ceiling

GRANTED_AT = 130


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="claude", autouse=True)
def claude_fixture(monkeypatch):
    """The suite forces the in-process LOCAL adapter, which takes no host lease."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", CliAdapter.CLAUDE.value)


@pytest.fixture(name="run")
def run_fixture(session):
    workspace = make_workspace(session, slug="run-wait-log")
    ticket = make_ticket(session, workspace_id=workspace.id)
    run = make_agent_run(
        session,
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        stage_key="plan",
        status=RunStatus.RUNNING,
    )
    bootstrap_run_log(run)
    return run


def _log(session: Session, run) -> tuple[list[str], str]:
    session.expire_all()
    artifact = session.exec(
        select(Artifact).where(Artifact.run_id == run.id, Artifact.kind == ArtifactKind.LOG)
    ).one()
    rows = session.exec(
        select(RunLogLine).where(RunLogLine.run_id == run.id).order_by(RunLogLine.seq)
    ).all()
    return [row.text for row in rows], json.loads(artifact.content_json).get("live") or ""


def _seqs(session: Session, run) -> list[int]:
    rows = session.exec(select(RunLogLine).where(RunLogLine.run_id == run.id)).all()
    return sorted(row.seq for row in rows)


class Line:
    """A fake clock that frees the machine at `GRANTED_AT`, and keeps what the live
    line read while the run waited."""

    def __init__(self, session: Session, run, holder_id: str) -> None:
        self.session = session
        self.run = run
        self.holder_id = holder_id
        self.now = 0.0
        self.live: list[str] = []

    def sleep(self, _seconds: float) -> None:
        self.now += 1
        self.live.append(_log(self.session, self.run)[1])
        if self.now == GRANTED_AT:
            docker_leases.release_lease(self.session, self.holder_id)

    def install(self, monkeypatch, module) -> None:
        monkeypatch.setattr(
            module,
            "wait_while_line_moves",
            partial(capacity_line.wait_while_line_moves, clock=lambda: self.now, sleep=self.sleep),
        )
        monkeypatch.setattr(
            module, "RunWaitLog", partial(module.RunWaitLog, clock=lambda: self.now)
        )


def _full(session: Session, pool: CapacityPool) -> str:
    _set_ceiling(session, pool, cpus=4, memory_mb=8192, leases=6)
    holder = _reserve(session, pool, label="pre-push server-tests", cpus=4)
    assert holder.granted
    return holder.lease_id


def _assert_told(session: Session, run, line: Line) -> None:
    text, live = _log(session, run)
    assert _seqs(session, run) == list(range(1, len(text) + 1)), "appended to the run's own log"
    waits = [entry for entry in text if entry.startswith("Waiting for ")]
    assert len(waits) == 1, "one line for the wait; the live line carries the rest"
    assert "pre-push server-tests (4 cpus)" in waits[0]
    assert all("Agent running" not in shown for shown in line.live)
    assert any("Waited 2m00s" in shown for shown in line.live), "the live line is refreshed"
    assert text[-1].startswith("Capacity granted after 2m10s")
    assert live == text[-1]


def test_a_run_waiting_for_the_host_says_what_it_waits_behind(session, run, monkeypatch) -> None:
    line = Line(session, run, _full(session, CapacityPool.HOST))
    line.install(monkeypatch, run_capacity)

    with host_capacity_for_run(session, run, wait_seconds=1000) as reservation:
        assert reservation is not None and reservation.granted

    _assert_told(session, run, line)


def test_so_does_a_stage_waiting_for_docker(session, run, monkeypatch) -> None:
    _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=16_384)
    line = Line(session, run, _full(session, CapacityPool.DOCKER))
    line.install(monkeypatch, stage_docker_capacity)
    stage = WorkflowStageDef(
        key="plan",
        name="Plan",
        docker_footprint=DockerFootprint.CUSTOM,
        docker_cpus=1.0,
        docker_memory_mb=1024,
    )

    with docker_capacity_for_stage(session, run, stage, wait_seconds=1000) as reservation:
        assert reservation is not None and reservation.granted

    _assert_told(session, run, line)


def test_a_run_that_never_waits_says_nothing(session, run) -> None:
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192)
    before = _log(session, run)

    with host_capacity_for_run(session, run):
        pass

    assert _log(session, run) == before


def test_a_run_that_gives_up_is_not_told_it_was_granted(session, run) -> None:
    _full(session, CapacityPool.HOST)

    with pytest.raises(HostCapacityUnavailable):
        with host_capacity_for_run(session, run, wait_seconds=0.3):
            pytest.fail("the run started without its lease")

    text, _ = _log(session, run)
    assert any(entry.startswith("Waiting for ") for entry in text)
    assert not any(entry.startswith("Capacity granted") for entry in text)


def test_a_log_that_cannot_be_written_does_not_cost_the_run_its_capacity(
    session, run, monkeypatch, caplog
) -> None:
    line = Line(session, run, _full(session, CapacityPool.HOST))
    line.install(monkeypatch, run_capacity)
    locked = OperationalError("UPDATE artifacts", {}, Exception("database is locked"))

    with mock.patch.object(RunLogStreamer, "append_line", side_effect=locked):
        with host_capacity_for_run(session, run, wait_seconds=1000) as reservation:
            assert reservation is not None and reservation.granted

    assert any(record.exc_info and run.run_code in record.getMessage() for record in caplog.records)


def test_a_grant_before_anything_was_said_is_not_announced(session, run) -> None:
    before = _log(session, run)

    RunWaitLog(run).granted()

    assert _log(session, run) == before


def test_a_run_with_no_ticket_has_no_log_and_still_waits(session, caplog) -> None:
    workspace = make_workspace(session, slug="run-wait-log-no-ticket")
    run = make_agent_run(session, workspace_id=workspace.id, stage_key="plan", ticket_id=None)
    _full(session, CapacityPool.HOST)
    waiter = _reserve(session, CapacityPool.HOST, label="waiter", cpus=1)
    assert not waiter.granted

    log = RunWaitLog(run)
    log.waiting(session, waiter.lease_id)
    log.granted()

    assert not caplog.records
    assert session.exec(select(Artifact).where(Artifact.run_id == run.id)).all() == []


def test_what_a_wait_is_behind_is_named_then_counted(session) -> None:
    _set_ceiling(session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=6)
    for label, cpus in (("lint", 0.5), ("suite", 2.0), ("stack", 1.5)):
        assert _reserve(session, CapacityPool.HOST, label=label, cpus=cpus).granted
    _reserve(session, CapacityPool.HOST, label="ahead", cpus=3)
    waiter = _reserve(session, CapacityPool.HOST, label="waiter", cpus=1)

    text = describe_wait(session, session.get(DockerLease, waiter.lease_id))

    assert "suite (2 cpus), stack (1.5 cpus) and 1 more" in text
    assert "1 ahead in line" in text
