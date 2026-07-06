import textwrap

from fastapi.testclient import TestClient
from loregarden.config import settings


def _capability_id(client: TestClient) -> str:
    for ticket in client.get("/api/tickets?workspace=loregarden").json():
        if ticket["work_item_type"] == "capability":
            return ticket["id"]
    raise AssertionError("capability not found")


def _sample_md(*, external_id: str = "99-import-md", title: str = "Imported from markdown") -> str:
    return textwrap.dedent(
        f"""\
        # TICKET: {external_id}
        Title: {title}
        Work item type: task
        Parent ticket: existing-cap

        ---

        ## Description
        Parsed from markdown import.

        ---

        ## Acceptance Criteria
        - First criterion
        - Second criterion
        """
    )


def test_preview_markdown_ticket(client: TestClient):
    res = client.post(
        "/api/tickets/import/preview",
        json={
            "workspace_slug": "loregarden",
            "files": [{"name": "ticket.md", "content": _sample_md()}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["show_preview"] is True
    assert body["formats"] == ["md"]
    assert body["tickets"][0]["title"] == "Imported from markdown"
    assert body["tickets"][0]["acceptance_criteria"] == ["First criterion", "Second criterion"]


def test_preview_json_ticket_array(client: TestClient):
    res = client.post(
        "/api/tickets/import/preview",
        json={
            "workspace_slug": "loregarden",
            "files": [
                {
                    "name": "tickets.json",
                    "content": '[{"title":"JSON task","work_item_type":"task","parent_external_id":"ignored"}]',
                }
            ],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["show_preview"] is True
    assert body["formats"] == ["json"]
    assert "preview_markdown" in body["tickets"][0]
    assert body["tickets"][0]["preview_markdown"].startswith("# TICKET:")


def test_preview_yaml_multiple_tickets_hides_preview(client: TestClient):
    res = client.post(
        "/api/tickets/import/preview",
        json={
            "workspace_slug": "loregarden",
            "files": [
                {
                    "name": "tickets.yaml",
                    "content": textwrap.dedent(
                        """\
                        tickets:
                          - title: YAML one
                            work_item_type: milestone
                          - title: YAML two
                            work_item_type: milestone
                        """
                    ),
                }
            ],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert body["show_preview"] is False
    assert body["formats"] == ["yaml"]


def test_preview_multiple_md_files_shows_preview(client: TestClient):
    res = client.post(
        "/api/tickets/import/preview",
        json={
            "workspace_slug": "loregarden",
            "files": [
                {"name": "a.md", "content": _sample_md(external_id="a", title="Ticket A")},
                {"name": "b.md", "content": _sample_md(external_id="b", title="Ticket B")},
            ],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert body["show_preview"] is True


def test_import_json_ticket(client: TestClient):
    parent_id = _capability_id(client)
    parent = next(
        ticket
        for ticket in client.get("/api/tickets?workspace=loregarden").json()
        if ticket["id"] == parent_id
    )
    res = client.post(
        "/api/tickets/import",
        json={
            "workspace_slug": "loregarden",
            "tickets": [
                {
                    "title": "Imported JSON task",
                    "work_item_type": "task",
                    "description": "From import endpoint",
                    "acceptance_criteria": ["Works end to end"],
                    "priority": 2,
                    "parent_external_id": parent["external_id"],
                    "source_format": "json",
                    "source_label": "import.json",
                }
            ],
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["created_count"] == 1
    assert len(body["ticket_ids"]) == 1

    detail = client.get(f"/api/tickets/{body['ticket_ids'][0]}").json()
    assert detail["title"] == "Imported JSON task"
    assert detail["parent_ticket_id"] == parent_id
    assert detail["acceptance_criteria"] == ["Works end to end"]


def test_import_batch_with_parent_external_id(client: TestClient):
    milestone_id = next(
        ticket["id"]
        for ticket in client.get("/api/tickets?workspace=loregarden").json()
        if ticket["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets/import",
        json={
            "workspace_slug": "loregarden",
            "tickets": [
                {
                    "title": "Imported feature",
                    "work_item_type": "feature",
                    "external_id": "import-feature-01",
                    "parent_ticket_id": milestone_id,
                },
                {
                    "title": "Imported capability",
                    "work_item_type": "capability",
                    "external_id": "import-cap-01",
                    "parent_external_id": "import-feature-01",
                },
            ],
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["created_count"] == 2


def test_import_requires_parent(client: TestClient):
    res = client.post(
        "/api/tickets/import",
        json={
            "workspace_slug": "loregarden",
            "tickets": [
                {
                    "title": "Orphan import",
                    "work_item_type": "task",
                }
            ],
        },
    )
    assert res.status_code == 400
    assert "parent" in res.json()["detail"].lower()


def test_preview_ticket_import_paths(client: TestClient, tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(settings, "browse_root", str(tmp_path))

    parent_id = _capability_id(client)
    parent = next(
        ticket
        for ticket in client.get("/api/tickets?workspace=loregarden").json()
        if ticket["id"] == parent_id
    )

    ticket_path = tmp_path / "import-task.json"
    ticket_path.write_text(
        json.dumps(
            {
                "title": "Path import task",
                "work_item_type": "task",
                "parent_external_id": parent["external_id"],
            }
        ),
        encoding="utf-8",
    )
    res = client.post(
        "/api/tickets/import/preview-paths",
        json={
            "workspace_slug": "loregarden",
            "file_paths": [str(ticket_path)],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["tickets"][0]["title"] == "Path import task"
