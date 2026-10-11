"""A parent ticket's timeline: direct children as rows of stage visits."""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import RunStatus, TicketDependency, TicketState, WorkItemType
from loregarden.testing.factories import make_agent_run, make_ticket, make_workspace
from sqlmodel import Session

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def family(db_session: Session):
    """A milestone with: a child that ran and went back, a running child with a
    grandchild, a not-started child waiting on an open sibling, and one waiting
    on nothing."""
    ws = make_workspace(db_session, slug="tl-ws")
    parent = make_ticket(
        db_session, workspace_id=ws.id, external_id="tl-m", work_item_type=WorkItemType.MILESTONE
    )

    def child(ext: str, state: TicketState) -> str:
        return make_ticket(
            db_session,
            workspace_id=ws.id,
            external_id=ext,
            title=ext,
            state=state,
            parent_ticket_id=parent.id,
            work_item_type=WorkItemType.FEATURE,
        ).id

    reworked = child("tl-reworked", TicketState.DONE)
    running = child("tl-running", TicketState.IN_PROGRESS)
    waiting = child("tl-waiting", TicketState.BACKLOG)
    ready = child("tl-ready", TicketState.BACKLOG)
    make_ticket(
        db_session, workspace_id=ws.id, external_id="tl-grandchild", parent_ticket_id=running
    )

    def run(
        ticket_id: str, code: str, stage: str, minute: int, status: RunStatus, finished: bool = True
    ):
        start = T0 + timedelta(minutes=minute)
        make_agent_run(
            db_session,
            workspace_id=ws.id,
            ticket_id=ticket_id,
            run_code=code,
            stage_key=stage,
            status=status,
            created_at=start,
            started_at=start,
            finished_at=start + timedelta(minutes=5) if finished else None,
        )

    run(reworked, "R1", "implement", 0, RunStatus.SUCCEEDED)
    run(reworked, "R2", "verify", 10, RunStatus.FAILED)
    run(reworked, "R3", "implement", 20, RunStatus.SUCCEEDED)
    run(running, "R4", "implement", 30, RunStatus.RUNNING, finished=False)

    db_session.add(TicketDependency(ticket_id=waiting, depends_on_ticket_id=running))
    db_session.add(
        TicketDependency(ticket_id=ready, depends_on_ticket_id=reworked)
    )  # resolved: not waiting
    db_session.commit()
    return parent


def _rows(client, parent_id: str) -> dict:
    response = client.get(f"/api/tickets/{parent_id}/children-timeline")
    assert response.status_code == 200
    return response.json()


def test_children_with_history_come_first_in_start_order_then_the_rest(client, family):
    body = _rows(client, family.id)
    assert [c["external_id"] for c in body["children"]] == [
        "tl-reworked",
        "tl-running",
        "tl-ready",
        "tl-waiting",
    ]


def test_each_stage_visit_is_a_segment_and_a_return_is_a_second_visit(client, family):
    reworked = _rows(client, family.id)["children"][0]
    assert [(s["stage_key"], s["visit_number"], s["status"]) for s in reworked["segments"]] == [
        ("implement", 1, "succeeded"),
        ("verify", 1, "failed"),
        ("implement", 2, "succeeded"),
    ]
    assert reworked["total_runs"] == 3


def test_a_running_segment_has_no_end_and_timestamps_carry_utc(client, family):
    body = _rows(client, family.id)
    running = body["children"][1]
    (segment,) = running["segments"]
    assert segment["status"] == "running"
    assert segment["finished_at"] is None
    assert datetime.fromisoformat(segment["started_at"]) == T0 + timedelta(minutes=30)
    assert datetime.fromisoformat(body["window_start"]) == T0


def test_only_unresolved_dependencies_are_waited_on(client, family):
    by_ext = {c["external_id"]: c for c in _rows(client, family.id)["children"]}
    assert [d["external_id"] for d in by_ext["tl-waiting"]["waiting_on"]] == ["tl-running"]
    assert by_ext["tl-ready"]["waiting_on"] == []


def test_grandchildren_are_counted_not_flattened_and_expand_by_their_own_request(client, family):
    by_ext = {c["external_id"]: c for c in _rows(client, family.id)["children"]}
    assert by_ext["tl-running"]["child_count"] == 1
    assert "tl-grandchild" not in by_ext
    nested = _rows(client, by_ext["tl-running"]["id"])
    assert [c["external_id"] for c in nested["children"]] == ["tl-grandchild"]


def test_the_window_covers_grandchildren_so_an_expanded_row_fits_the_axis(
    client, db_session: Session, family
):
    grandchild = next(
        c for c in _rows(client, _rows(client, family.id)["children"][1]["id"])["children"]
    )
    late = T0 + timedelta(days=3)
    make_agent_run(
        db_session,
        workspace_id=family.workspace_id,
        ticket_id=grandchild["id"],
        run_code="R9",
        stage_key="implement",
        status=RunStatus.SUCCEEDED,
        created_at=late,
        started_at=late,
        finished_at=late + timedelta(hours=1),
    )
    body = _rows(client, family.id)
    assert datetime.fromisoformat(body["window_end"]) == late + timedelta(hours=1)
    assert datetime.fromisoformat(body["window_start"]) == T0


def test_a_parent_with_no_runs_has_no_window(client, db_session: Session):
    ws = make_workspace(db_session, slug="tl-empty")
    parent = make_ticket(
        db_session, workspace_id=ws.id, external_id="tl-e", work_item_type=WorkItemType.MILESTONE
    )
    make_ticket(db_session, workspace_id=ws.id, external_id="tl-e-1", parent_ticket_id=parent.id)
    body = _rows(client, parent.id)
    assert body["window_start"] is None and body["window_end"] is None
    assert body["children"][0]["segments"] == []


def test_an_unknown_parent_is_404(client):
    assert client.get("/api/tickets/nope/children-timeline").status_code == 404
