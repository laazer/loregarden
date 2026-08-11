"""Run timestamps must reach the browser with an explicit UTC offset.

SQLite's zone-less DATETIME hands back a naive datetime, and an offset-less ISO
string is parsed by ECMAScript as *local* time — so serializing one shifts every
run in the UI by the viewer's UTC offset.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from loregarden.core.timestamps import as_utc, iso_utc
from loregarden.models.domain import AgentRun, RunStatus, Ticket
from sqlmodel import Session, select

NAIVE = datetime(2026, 8, 8, 14, 19, 57, 465660)


def _seed_run(session: Session, **overrides) -> AgentRun:
    ticket = session.exec(select(Ticket)).first()
    assert ticket
    run = AgentRun(
        run_code="run_861ef1",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="architecture_reviewer",
        skill_name="review",
        stage_key="script_review",
        status=RunStatus.FAILED,
        command="codex exec",
        started_at=NAIVE,
        finished_at=datetime(2026, 8, 8, 14, 20, 14, 599074),
        **overrides,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_as_utc_tags_a_naive_value_as_utc():
    assert as_utc(NAIVE) == NAIVE.replace(tzinfo=timezone.utc)


def test_as_utc_converts_an_aware_value_rather_than_relabelling_it():
    eastern = datetime(2026, 8, 8, 10, 19, 57, tzinfo=timezone(-timedelta(hours=4)))
    assert as_utc(eastern) == datetime(2026, 8, 8, 14, 19, 57, tzinfo=timezone.utc)


def test_iso_utc_carries_an_offset_and_passes_none_through():
    assert iso_utc(NAIVE) == "2026-08-08T14:19:57.465660+00:00"
    assert iso_utc(None) is None


def test_run_list_dates_every_run_with_an_offset(client: TestClient, db_session: Session):
    run = _seed_run(db_session)

    rows = client.get("/api/runs", params={"ticket_id": run.ticket_id}).json()
    row = next(r for r in rows if r["run_code"] == "run_861ef1")

    assert row["started_at"] == "2026-08-08T14:19:57.465660+00:00"
    assert row["finished_at"] == "2026-08-08T14:20:14.599074+00:00"
    # A run that never started is still placeable in time.
    assert row["created_at"].endswith("+00:00")


def test_run_detail_and_log_date_runs_with_an_offset(client: TestClient, db_session: Session):
    run = _seed_run(db_session)

    detail = client.get(f"/api/runs/{run.id}").json()
    log = client.get(f"/api/runs/{run.id}/log").json()

    for payload in (detail, log):
        assert payload["started_at"] == "2026-08-08T14:19:57.465660+00:00"
        assert payload["finished_at"] == "2026-08-08T14:20:14.599074+00:00"


def test_ledger_attempts_are_dated_with_an_offset(client: TestClient, db_session: Session):
    run = _seed_run(db_session)

    ledger = client.get(f"/api/tickets/{run.ticket_id}/ledger").json()
    attempts = [a for v in ledger["visits"] for a in v["attempts"]]
    attempt = next(a for a in attempts if a["run_code"] == "run_861ef1")

    assert attempt["started_at"] == "2026-08-08T14:19:57.465660+00:00"
    assert attempt["finished_at"] == "2026-08-08T14:20:14.599074+00:00"
