"""The run list must not carry an unbounded column.

`GET /api/runs` measured 2.67 MB across 50 rows against the live database, and
2,669 KB of that was `command` — a CLI adapter's command line embeds the whole
prompt, ~53 KB a row. The home page polls this endpoint every 15 seconds and
renders the value as a one-line label with a `title` tooltip, so the full text
was read from SQLite, serialized, parsed, and written into the DOM 240 times an
hour to show a truncated line.

`stdout`/`stderr` were already bounded here for the same reason. This pins the
third one, and pins that a cut value says it was cut.
"""

from fastapi.testclient import TestClient
from loregarden.api.runs import COMMAND_PREVIEW_CHARS
from loregarden.models.domain import AgentRun, RunStatus, Ticket
from sqlmodel import Session, select


def _seed_run(session: Session, command: str) -> AgentRun:
    ticket = session.exec(select(Ticket)).first()
    assert ticket
    run = AgentRun(
        run_code="run_bounds1",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="implementer",
        skill_name="implement",
        stage_key="implement",
        status=RunStatus.SUCCEEDED,
        command=command,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_long_command_is_bounded_and_marked(client: TestClient, db_session: Session):
    run = _seed_run(db_session, "claude -p " + "x" * 60_000)

    rows = client.get("/api/runs", params={"ticket_id": run.ticket_id}).json()
    row = next(r for r in rows if r["run_code"] == "run_bounds1")

    assert len(row["command"]) == COMMAND_PREVIEW_CHARS + 1  # + the ellipsis
    assert row["command"].endswith("…")
    assert row["command"].startswith("claude -p xxx")


def test_a_short_command_is_served_whole_and_unmarked(client: TestClient, db_session: Session):
    """A bound that ellipsised everything would make every command look truncated."""
    run = _seed_run(db_session, "codex exec")

    rows = client.get("/api/runs", params={"ticket_id": run.ticket_id}).json()
    row = next(r for r in rows if r["run_code"] == "run_bounds1")

    assert row["command"] == "codex exec"


def test_the_list_still_withholds_the_log_blobs(client: TestClient, db_session: Session):
    """Regression guard for the two that were already bounded."""
    run = _seed_run(db_session, "codex exec")

    rows = client.get("/api/runs", params={"ticket_id": run.ticket_id}).json()
    row = next(r for r in rows if r["run_code"] == "run_bounds1")

    assert row["stdout"] == ""
    assert row["stderr"] == ""


def test_the_full_command_is_still_available_per_run(client: TestClient, db_session: Session):
    """Bounding the list is only honest if the whole value is reachable."""
    command = "claude -p " + "x" * 60_000
    run = _seed_run(db_session, command)

    detail = client.get(f"/api/runs/{run.id}").json()

    assert detail["command"] == command
