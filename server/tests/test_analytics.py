"""The queue analytics endpoints (`api/analytics.py`), over real runs."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkItemType, Workspace
from loregarden.models.domain.enums import utcnow
from sqlmodel import Session

GLOBAL = "/api/parallel/analytics"


@pytest.fixture
def workspace(db_session: Session) -> Workspace:
    ws = Workspace(slug="analytics", name="Analytics")
    db_session.add(ws)
    db_session.commit()
    return ws


def _ticket(session: Session, ws: Workspace, work_item_type: WorkItemType, number: int) -> Ticket:
    ticket = Ticket(
        workspace_id=ws.id,
        external_id=f"an-{number}",
        ticket_number=number,
        title=f"Ticket {number}",
        work_item_type=work_item_type,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _run(
    session: Session,
    ticket: Ticket,
    *,
    seconds: float | None = 60,
    status: RunStatus = RunStatus.SUCCEEDED,
    days_ago: float = 1,
    finished: bool = True,
) -> None:
    end = utcnow() - timedelta(days=days_ago)
    started: datetime | None = end - timedelta(seconds=seconds) if seconds is not None else None
    session.add(
        AgentRun(
            run_code=f"run_{uuid4().hex[:8]}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="plan",
            status=status,
            started_at=started,
            finished_at=end if finished else None,
        )
    )
    session.commit()


def _get(client: TestClient, ws: Workspace, range: str = "7d") -> list[dict]:
    res = client.get(f"/api/parallel/workspace/{ws.id}/analytics", params={"range": range})
    assert res.status_code == 200
    body = res.json()
    assert (body["workspace_id"], body["range"]) == (ws.id, range)
    return body["metrics"]


def test_runs_are_grouped_by_ticket_type_most_frequent_first(client, db_session, workspace):
    feature = _ticket(db_session, workspace, WorkItemType.FEATURE, 1)
    bug = _ticket(db_session, workspace, WorkItemType.BUG, 2)
    _run(db_session, feature)
    _run(db_session, bug)
    _run(db_session, bug)

    metrics = _get(client, workspace)

    assert [(m["ticket_type"], m["count"]) for m in metrics] == [("bug", 2), ("feature", 1)]


def test_durations_come_from_start_to_finish(client, db_session, workspace):
    ticket = _ticket(db_session, workspace, WorkItemType.BUG, 1)
    for seconds in (10, 20, 60):
        _run(db_session, ticket, seconds=seconds)

    (bug,) = _get(client, workspace)

    assert bug["min_duration_seconds"] == 10
    assert bug["avg_duration_seconds"] == 30
    assert bug["max_duration_seconds"] == 60


def test_a_run_that_never_started_is_counted_but_has_no_duration(client, db_session, workspace):
    """It used to count as zero seconds, so one failed start made the minimum 0
    and pulled the average down."""
    ticket = _ticket(db_session, workspace, WorkItemType.BUG, 1)
    _run(db_session, ticket, seconds=40)
    _run(db_session, ticket, seconds=None, status=RunStatus.FAILED)

    (bug,) = _get(client, workspace)

    assert bug["count"] == 2
    assert bug["success_rate"] == 0.5
    assert bug["min_duration_seconds"] == 40
    assert bug["avg_duration_seconds"] == 40


def test_the_recent_rate_covers_seven_days_whatever_the_range(client, db_session, workspace):
    ticket = _ticket(db_session, workspace, WorkItemType.BUG, 1)
    _run(db_session, ticket, days_ago=1, status=RunStatus.FAILED)
    _run(db_session, ticket, days_ago=6, status=RunStatus.SUCCEEDED)
    for _ in range(2):
        _run(db_session, ticket, days_ago=20, status=RunStatus.SUCCEEDED)

    (bug,) = _get(client, workspace, "30d")

    assert (bug["count"], bug["success_rate"]) == (4, 0.75)
    assert (bug["last_7_days_count"], bug["last_7_days_success_rate"]) == (2, 0.5)


@pytest.mark.parametrize(("range", "count"), [("7d", 1), ("30d", 2), ("90d", 3)])
def test_the_range_bounds_which_runs_count(client, db_session, workspace, range, count):
    ticket = _ticket(db_session, workspace, WorkItemType.BUG, 1)
    for days_ago in (3, 20, 60, 120):
        _run(db_session, ticket, days_ago=days_ago)

    (bug,) = _get(client, workspace, range)

    assert bug["count"] == count


def test_an_unfinished_run_is_not_counted(client, db_session, workspace):
    ticket = _ticket(db_session, workspace, WorkItemType.BUG, 1)
    _run(db_session, ticket, finished=False, status=RunStatus.RUNNING)

    assert _get(client, workspace) == []


def test_a_workspace_sees_only_its_own_runs_and_global_sees_all(client, db_session, workspace):
    other = Workspace(slug="analytics-other", name="Other")
    db_session.add(other)
    db_session.commit()
    _run(db_session, _ticket(db_session, workspace, WorkItemType.BUG, 1))
    _run(db_session, _ticket(db_session, other, WorkItemType.BUG, 2))

    (mine,) = _get(client, workspace)
    assert mine["count"] == 1
    everywhere = client.get(GLOBAL).json()
    assert everywhere["workspace_id"] == ""
    assert sum(m["count"] for m in everywhere["metrics"] if m["ticket_type"] == "bug") == 2


@pytest.mark.parametrize("url", [GLOBAL, "/api/parallel/workspace/any/analytics"])
def test_an_unknown_range_is_refused(client, url):
    assert client.get(url, params={"range": "1y"}).status_code == 422
