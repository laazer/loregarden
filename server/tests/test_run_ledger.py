"""The ledger: what happened to a ticket, in order."""

from datetime import datetime, timedelta, timezone

from loregarden.models.domain import AgentRun, RunStatus, Ticket
from loregarden.services.run_ledger import build_ledger, ledger_payload
from sqlmodel import Session, select

_BASE = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


def _run(
    stage: str,
    *,
    minute: int,
    agent: str = "planner",
    skill: str = "",
    status: RunStatus = RunStatus.SUCCEEDED,
    seconds: int = 30,
) -> AgentRun:
    created = _BASE + timedelta(minutes=minute)
    return AgentRun(
        run_code=f"run_{stage}_{minute}",
        ticket_id="t1",
        workspace_id="w1",
        agent_id=agent,
        skill_name=skill,
        stage_key=stage,
        status=status,
        created_at=created,
        started_at=created,
        finished_at=created + timedelta(seconds=seconds),
    )


def test_each_stage_becomes_a_visit():
    visits = build_ledger([_run("plan", minute=0), _run("spec", minute=1)])
    assert [v.stage_key for v in visits] == ["plan", "spec"]
    assert [v.visit_number for v in visits] == [1, 1]


def test_runs_are_ordered_by_creation_not_input_order():
    visits = build_ledger([_run("spec", minute=5), _run("plan", minute=1)])
    assert [v.stage_key for v in visits] == ["plan", "spec"]


def test_consecutive_runs_of_one_stage_are_retries_within_a_visit():
    """Gate autofix re-runs a stage in place. Three rows for `implement` in a
    row is one visit that took three attempts, not three separate visits."""
    visits = build_ledger(
        [
            _run("implement", minute=0, agent="backend_implementer"),
            _run("implement", minute=1, agent="backend_implementer"),
            _run("implement", minute=2, agent="backend_implementer"),
        ]
    )
    assert len(visits) == 1
    assert len(visits[0].attempts) == 3
    assert visits[0].visit_number == 1
    # Same agent and skill throughout — a retry, not a fan-out.
    assert visits[0].is_parallel is False


def test_returning_to_a_stage_later_is_a_second_visit():
    """This is the signal the ledger exists for: verify refused, and the work
    went back to implement. A flat list of runs never showed that."""
    visits = build_ledger(
        [
            _run("implement", minute=0, agent="backend_implementer"),
            _run("verify", minute=1, agent="verifier"),
            _run("implement", minute=2, agent="backend_implementer"),
            _run("verify", minute=3, agent="verifier"),
        ]
    )
    assert [(v.stage_key, v.visit_number) for v in visits] == [
        ("implement", 1),
        ("verify", 1),
        ("implement", 2),
        ("verify", 2),
    ]


def test_distinct_lanes_are_a_fan_out():
    """Three planners under different lenses ran at once; that is a different
    thing from one planner retrying, and reads differently in the UI."""
    visits = build_ledger(
        [
            _run("plan", minute=0, skill="plan-simplest"),
            _run("plan", minute=0, skill="plan-risk"),
            _run("plan", minute=0, skill="plan-seams"),
        ]
    )
    assert len(visits) == 1
    assert visits[0].is_parallel is True
    assert len(visits[0].attempts) == 3


def test_a_visit_with_a_running_attempt_is_running():
    visits = build_ledger(
        [_run("implement", minute=0, status=RunStatus.SUCCEEDED), _run("verify", minute=1)]
    )
    assert visits[-1].status == "succeeded"

    live = build_ledger([_run("implement", minute=0, status=RunStatus.RUNNING)])
    assert live[0].status == "running"


def test_a_visit_takes_its_outcome_from_the_last_attempt():
    """An autofix that eventually passed is a passing visit; the earlier
    failures are still visible as attempts."""
    visits = build_ledger(
        [
            _run("gate", minute=0, status=RunStatus.FAILED),
            _run("gate", minute=1, status=RunStatus.SUCCEEDED),
        ]
    )
    assert visits[0].status == "succeeded"
    assert [a.status for a in visits[0].attempts] == ["failed", "succeeded"]


def test_a_queued_run_has_no_duration():
    run = _run("plan", minute=0)
    run.started_at = None
    run.finished_at = None
    visits = build_ledger([run])
    assert visits[0].attempts[0].duration_seconds is None


def test_the_payload_summarises_rework_and_time():
    payload = ledger_payload(
        [
            _run("implement", minute=0, seconds=60),
            _run("verify", minute=1, seconds=30),
            _run("implement", minute=2, seconds=90),
        ]
    )
    assert payload["total_runs"] == 3
    assert payload["reworked_stages"] == ["implement"]
    assert payload["total_seconds"] == 180.0
    assert [v["visit_number"] for v in payload["visits"]] == [1, 1, 2]


def test_an_empty_ledger_is_empty_not_an_error():
    payload = ledger_payload([])
    assert payload == {
        "visits": [],
        "total_runs": 0,
        "reworked_stages": [],
        "total_seconds": 0.0,
    }


def test_the_endpoint_serves_a_tickets_ledger(client, db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    for run in (
        _run("implement", minute=0, agent="backend_implementer"),
        _run("verify", minute=1, agent="verifier"),
        _run("implement", minute=2, agent="backend_implementer"),
    ):
        run.ticket_id = ticket.id
        run.workspace_id = ticket.workspace_id
        db_session.add(run)
    db_session.commit()

    body = client.get(f"/api/tickets/{ticket.id}/ledger").json()
    assert body["total_runs"] == 3
    assert body["reworked_stages"] == ["implement"]
    assert [v["stage_key"] for v in body["visits"]] == ["implement", "verify", "implement"]


def test_an_unknown_ticket_ledger_is_404(client):
    assert client.get("/api/tickets/nope/ledger").status_code == 404
