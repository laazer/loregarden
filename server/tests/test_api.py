from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket
from loregarden.services.artifact_service import record_blocking_issue
from loregarden.services.block_classification import BLOCKING_POINTER_PHRASE
from sqlmodel import Session


def _ticket_id_by_external_id(client: TestClient, external_id: str) -> str:
    """Find a seeded ticket by either spelling of its id.

    The seed's literals are the ids these tickets shipped under; they are now
    carried as ``legacy_external_id`` while the ticket reads ``lor-…-3``. Both
    identify the same row, which is the whole point of keeping the old one.
    """
    for t in client.get("/api/tickets").json():
        if external_id in (t["external_id"], t["legacy_external_id"]):
            return t["id"]
    raise AssertionError(f"ticket not found: {external_id}")


def _ticket_detail(client: TestClient, ticket_id: str) -> dict:
    res = client.get(f"/api/tickets/{ticket_id}")
    assert res.status_code == 200
    return res.json()


def test_create_milestone_ticket(client: TestClient):
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "New milestone from test",
            "work_item_type": "milestone",
            "description": "Created via API test",
            "priority": 3,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "New milestone from test"
    assert body["work_item_type"] == "milestone"
    assert body["workspace_slug"] == "loregarden"
    assert body["state"] == "backlog"


def test_ticket_tree_multi_state_filter(client: TestClient):
    all_tickets = client.get("/api/tickets?workspace=loregarden").json()
    states = sorted({t["state"] for t in all_tickets})
    assert len(states) >= 1
    selected = states[: min(2, len(states))]
    params = "&".join(f"state={state}" for state in selected)
    filtered = client.get(f"/api/tickets?workspace=loregarden&{params}").json()
    assert filtered
    assert all(t["state"] in selected for t in filtered)


def test_ticket_tree_multi_type_filter(client: TestClient):
    all_tickets = client.get("/api/tickets?workspace=loregarden").json()
    types = sorted({t["work_item_type"] for t in all_tickets})
    assert len(types) >= 2
    selected = types[:2]
    params = "&".join(f"work_item_type={work_item_type}" for work_item_type in selected)
    filtered = client.get(f"/api/tickets/tree?workspace=loregarden&{params}").json()

    def collect_matching_nodes(nodes: list[dict]) -> set[str]:
        """Collect IDs of nodes that match the filter."""
        result = set()
        for node in nodes:
            if node["work_item_type"] in selected:
                result.add(node["id"])
            result.update(collect_matching_nodes(node.get("children") or []))
        return result

    matching_nodes = collect_matching_nodes(filtered)
    # Tree should contain nodes matching the filter (and their ancestors)
    assert matching_nodes, "Tree should contain at least some matching nodes"


def test_health(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_list_tickets_seeded(client: TestClient):
    res = client.get("/api/tickets")
    assert res.status_code == 200
    tickets = res.json()
    assert len(tickets) >= 5
    legacy_ids = {t["legacy_external_id"] for t in tickets}
    assert "01-bootstrap-fastapi-control-plane" in legacy_ids
    # And it is reachable under the id it now reads as.
    seeded = next(
        t for t in tickets if t["legacy_external_id"] == "01-bootstrap-fastapi-control-plane"
    )
    assert client.get(f"/api/tickets/{seeded['external_id']}").json()["id"] == seeded["id"]


def test_ticket_tree_hierarchy(client: TestClient):
    res = client.get("/api/tickets/tree")
    assert res.status_code == 200
    tree = res.json()
    assert tree
    root = tree[0]
    assert root["work_item_type"] == "milestone"
    assert root.get("workspace_slug") == "loregarden"
    assert "workflow_stage_status" in root
    assert root["children"]
    flat_types = {root["work_item_type"]}
    stack = list(root["children"])
    while stack:
        node = stack.pop()
        flat_types.add(node["work_item_type"])
        assert "workflow_stage_status" in node
        stack.extend(node["children"])
    assert "feature" in flat_types
    assert "capability" in flat_types
    assert "task" in flat_types


def test_ticket_detail_has_stages(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert "stages" in detail
    assert len(detail["stages"]) >= 5


def test_start_run_specific_stage(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(
        f"/api/tickets/{ticket_id}/start",
        json={"manual": True, "stage_key": "plan"},
    )
    assert started.status_code == 200
    body = _ticket_detail(client, ticket_id)
    assert body["workflow_stage_key"] == "plan"
    assert body["workflow_stage_status"] == "done"
    stages = {s["key"]: s["status"] for s in body["stages"]}
    assert stages["plan"] == "done"


def test_start_run_bootstraps_live_log(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = started.json()
    assert body["artifacts"]["logs"]
    assert any(line["tag"] == "RUN" for line in body["artifacts"]["logs"])


def test_start_run_success_updates_stage(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = started.json()
    assert body["workflow_stage_status"] in {"running", "done"}
    if body["workflow_stage_status"] == "running":
        body = _ticket_detail(client, ticket_id)
    assert body["workflow_stage_status"] == "done"
    assert body["artifacts"]["logs"]


def test_start_run_failure_blocks_ticket(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = _ticket_detail(client, ticket_id)
    assert body["state"] == "blocked"
    assert body["workflow_stage_status"] == "blocked"
    assert "forced to fail" in body["blocking_issues"].lower()


def test_ticket_detail_resolves_a_long_block_to_its_full_text(
    client: TestClient, db_session: Session
):
    """A long block leaves only a pointer inline ("see the Timeline tab"); the
    Timeline is where that pointer sends the operator, so the detail has to
    carry the text it points at."""
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    ticket = db_session.get(Ticket, ticket_id)
    long_message = "Test-break could not write a failing test: " + "x" * 600
    ticket.blocking_issues = record_blocking_issue(
        db_session, ticket, run_id=None, stage_key="test-break", message=long_message
    )
    db_session.add(ticket)
    db_session.commit()

    body = _ticket_detail(client, ticket_id)

    assert BLOCKING_POINTER_PHRASE in body["blocking_issues"]
    assert body["block_message"] == long_message


def test_ticket_detail_block_message_is_the_inline_block_when_short(
    client: TestClient, db_session: Session
):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    ticket = db_session.get(Ticket, ticket_id)
    ticket.blocking_issues = record_blocking_issue(
        db_session, ticket, run_id=None, stage_key="plan", message="Waiting on API key"
    )
    db_session.add(ticket)
    db_session.commit()

    body = _ticket_detail(client, ticket_id)

    assert body["block_message"] == body["blocking_issues"] == "Waiting on API key"


def test_runs_api_after_start(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    ticket = _ticket_detail(client, ticket_id)
    runs = client.get(f"/api/runs?ticket_id={ticket_id}").json()
    assert runs
    assert runs[0]["status"] == "succeeded"
    assert "local_runner" in runs[0]["command"]
    assert runs[0]["ticket_title"] == ticket["title"]
    assert runs[0]["ticket_external_id"] == ticket["external_id"]
    detail = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert detail["run_code"] == runs[0]["run_code"]


def test_agents_registry(client: TestClient):
    res = client.get("/api/agents")
    assert res.status_code == 200
    agents = res.json()
    # Agents are now DB-backed views keyed by slug (id is a uuid); the seeded
    # built-in planner is present and flagged built_in.
    planner = next((a for a in agents if a["slug"] == "planner"), None)
    assert planner is not None
    assert planner["built_in"] is True


def test_approvals_inbox(client: TestClient):
    res = client.get("/api/inbox/approvals")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
