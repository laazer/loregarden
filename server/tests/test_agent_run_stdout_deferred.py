"""`agent_runs.stdout` is deferred: a plain AgentRun read does not load it.

The column holds each run's whole transcript (3.5 GB across the live table), so
every `select(AgentRun)` that only wanted status or timestamps was reading it
off disk. These pin the deferral, that a reader in a session still gets the
value, and that the one detector reading it across many rows asks for it in its
own query rather than one lazy load per run.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from loregarden.models.domain import AgentRun, RunStatus
from loregarden.services.monitor_harness_cluster import detect_harness_failure_clusters
from loregarden.testing.factories import make_agent_run, make_workspace
from sqlalchemy import event
from sqlalchemy.orm import undefer
from sqlmodel import Session, select

TRANSCRIPT = '{"type":"result","result":"done"}'


@contextmanager
def _statements(engine):
    seen: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _selects_stdout(statement: str) -> bool:
    return statement.lstrip().upper().startswith("SELECT") and "agent_runs.stdout" in statement


def test_plain_select_leaves_stdout_out_and_loads_it_on_access(isolated_db):
    with Session(isolated_db) as session:
        ws = make_workspace(session)
        run_id = make_agent_run(session, workspace_id=ws.id, stdout=TRANSCRIPT).id

    with Session(isolated_db) as session, _statements(isolated_db) as seen:
        run = session.exec(select(AgentRun).where(AgentRun.id == run_id)).one()
        assert run.status == RunStatus.QUEUED
        assert not any(_selects_stdout(s) for s in seen)

        assert run.stdout == TRANSCRIPT
        assert any(_selects_stdout(s) for s in seen)


def test_undefer_loads_stdout_in_the_same_statement(isolated_db):
    with Session(isolated_db) as session:
        ws = make_workspace(session)
        run_id = make_agent_run(session, workspace_id=ws.id, stdout=TRANSCRIPT).id

    with Session(isolated_db) as session, _statements(isolated_db) as seen:
        run = session.exec(
            select(AgentRun).where(AgentRun.id == run_id).options(undefer(AgentRun.stdout))
        ).one()
        statements_after_query = len(seen)
        assert run.stdout == TRANSCRIPT
        assert len(seen) == statements_after_query


def test_harness_cluster_detector_reads_stdout_without_a_query_per_run(isolated_db):
    now = datetime.now(timezone.utc)
    with Session(isolated_db) as session:
        ws = make_workspace(session)
        for n in range(3):
            make_agent_run(
                session,
                workspace_id=ws.id,
                ticket_id=f"ticket-{n}",
                run_code=f"RUN-{n}",
                status=RunStatus.FAILED,
                stage_key="implement",
                started_at=now,
                stdout=TRANSCRIPT,
            )

    with Session(isolated_db) as session, _statements(isolated_db) as seen:
        detect_harness_failure_clusters(session)

    stdout_reads = [s for s in seen if _selects_stdout(s)]
    assert len(stdout_reads) == 1, stdout_reads
    assert "agent_runs.status" in stdout_reads[0]
