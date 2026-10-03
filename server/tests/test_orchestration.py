from unittest.mock import patch

from fastapi.testclient import TestClient
from loregarden.models.domain import OrchestrationRunStatus
from loregarden.services import gate_runner


def test_orchestration_profile_loaded(client: TestClient):
    res = client.get("/api/orchestration/workspaces/loregarden/profile")
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "loregarden"
    assert body["driver"] == "builtin_autopilot"


def test_external_mcp_start_orchestration(client: TestClient):
    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["legacy_external_id"] == "02-bootstrap-react-ide-shell":
            ticket_id = t["id"]
            break
    res = client.post(
        f"/api/orchestration/tickets/{ticket_id}/start",
        json={"driver": "external_mcp"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["driver"] == "external_mcp"
    assert body["status"] == OrchestrationRunStatus.RUNNING.value


def test_external_mcp_start_claims_a_slot(client: TestClient, db_session):
    """This path used to bypass admission and leave the board idle."""
    from loregarden.models.domain import AgentSlot
    from sqlmodel import select

    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["legacy_external_id"] == "02-bootstrap-react-ide-shell":
            ticket_id = t["id"]
            break
    res = client.post(
        f"/api/orchestration/tickets/{ticket_id}/start",
        json={"driver": "external_mcp"},
    )
    assert res.status_code == 200
    run_id = res.json()["id"]

    held = db_session.exec(
        select(AgentSlot).where(AgentSlot.current_orchestration_run_id == run_id)
    ).one()
    assert held.is_available is False


def test_orchestrate_ticket_one_stage(client: TestClient):
    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["legacy_external_id"] == "04-workflow-template-overrides":
            ticket_id = t["id"]
            break
    res = client.post(
        f"/api/tickets/{ticket_id}/orchestrate",
        json={"max_stages": 1},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["state"] in ("in_progress", "blocked", "done")


def test_orchestration_callbacks(client: TestClient):
    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["legacy_external_id"] == "03-wire-cli-agent-runner":
            ticket_id = t["id"]
            break
    started = client.post(
        f"/api/orchestration/tickets/{ticket_id}/start",
        json={"driver": "external_mcp"},
    ).json()
    run_id = started["id"]

    state = client.get(f"/api/orchestration/tickets/{ticket_id}/state").json()
    assert state["ticket_id"] == ticket_id

    complete = client.post(
        f"/api/orchestration/runs/{run_id}/complete_stage",
        json={"stage_key": "plan", "next_agent": "spec"},
    )
    assert complete.status_code == 200
    assert complete.json()["ok"] is True

    done = client.post(
        f"/api/orchestration/runs/{run_id}/complete",
        json={"status": "succeeded"},
    )
    assert done.status_code == 200


def test_get_ticket_by_external_id(client: TestClient):
    """The by-external route still answers to a pre-restructure id, and reports
    the ticket under the id it now reads as."""
    res = client.get(
        "/api/orchestration/tickets/by-external/loregarden/01-bootstrap-fastapi-control-plane/state"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["legacy_external_id"] == "01-bootstrap-fastapi-control-plane"
    assert body["external_id"].startswith("lg-")


def test_the_seeded_workspace_passes_its_transition_gates_without_recovery(client: TestClient):
    """Orchestrating the seeded workspace takes the gate-pass path a real workspace takes.

    Its profile is loregarden's, whose gates need loregarden's `server/` and
    `client/`; the seeded repo has neither. `client` gives it a trivial gate
    instead, so a run here reaches no fixer, no recovery and no agent fallback.
    """
    ticket_id = next(
        t["id"]
        for t in client.get("/api/tickets").json()
        if t["legacy_external_id"] == "04-workflow-template-overrides"
    )
    real_run = gate_runner._run_command
    ran: list[tuple[str, bool]] = []

    def recording(command, cwd):
        result = real_run(command, cwd)
        ran.append((command, result.ok))
        return result

    with patch.object(gate_runner, "_run_command", recording):
        res = client.post(f"/api/tickets/{ticket_id}/orchestrate", json={"max_stages": 2})

    assert res.status_code == 200
    assert ran, "no transition gate ran, so this proves nothing"
    assert ran == [("true", True)] * len(ran)
