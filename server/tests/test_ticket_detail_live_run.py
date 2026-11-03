"""The ticket detail says what the page needs to tell a live run from an idle one.

lg-machine-resource-1923 spent minutes landing (an orchestration, no agent run)
while its page offered "Continue Run" and "Complete ticket", and its dock offered
"Commit, push, and open a PR" for work that ships in its tree's PR.
"""

import pytest
from fastapi.testclient import TestClient
from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus
from sqlmodel import Session
from tests.factories import make_task_in_tree, make_workspace_ticket


@pytest.fixture
def api(db_session: Session):
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_a_live_orchestration_and_its_stage_are_on_the_detail(db_session: Session, api):
    ticket = make_workspace_ticket(db_session, "detail-live-run")
    db_session.add(
        OrchestrationRun(
            run_code="orch_live",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
            current_stage_key="done",
        )
    )
    db_session.commit()

    live = api.get(f"/api/tickets/{ticket.id}").json()["active_orchestration"]

    assert (live["run_code"], live["current_stage_key"]) == ("orch_live", "done")


def test_nothing_live_reads_as_null(db_session: Session, api):
    ticket = make_workspace_ticket(db_session, "detail-idle")

    assert api.get(f"/api/tickets/{ticket.id}").json()["active_orchestration"] is None


def test_a_tree_member_names_the_branch_its_work_ships_on(db_session: Session, api):
    member = make_task_in_tree(db_session, "detail tree member")
    loose = make_workspace_ticket(db_session, "detail-loose")

    assert api.get(f"/api/tickets/{member.id}").json()["ships_with"].startswith("integration/")
    assert api.get(f"/api/tickets/{loose.id}").json()["ships_with"] == ""
