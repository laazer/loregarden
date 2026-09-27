"""Workspace launch templates: code, the committed file, and stored rows.

Driven through the API against the seeded workspace, whose repo the `client`
fixture points at a throwaway git repository — so the file is a real file in a
real checkout, and the one launch here is a real process.
"""

import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Workspace
from loregarden.services.local_instances import get_instance_manager
from sqlmodel import Session, select

SERVE = [sys.executable, "-m", "http.server", "{port}", "--bind", "{host}"]


@pytest.fixture(name="repo")
def repo_fixture(client: TestClient, isolated_db) -> Path:
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        return Path(workspace.repo_path)


def _write_file(repo: Path, body: str) -> None:
    (repo / ".loregarden").mkdir(exist_ok=True)
    (repo / ".loregarden" / "instances.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


def _workspace(client: TestClient, slug: str = "loregarden") -> dict:
    listing = client.get("/api/instance-templates")
    assert listing.status_code == 200, listing.text
    return next(w for w in listing.json() if w["slug"] == slug)


def _entries(workspace: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in workspace["entries"]}


def _spec(name: str = "docs", **overrides) -> dict:
    return {"name": name, "kind": "server", "command": SERVE, "cwd": ".", **overrides}


def test_code_templates_are_qualified_by_workspace(client: TestClient, repo: Path) -> None:
    entries = _entries(_workspace(client))
    assert {name: e["origin"] for name, e in entries.items()} == {"client": "code", "server": "code"}
    assert entries["server"]["qualified_name"] == "loregarden/server"


def test_a_committed_file_adds_launchable_templates(client: TestClient, repo: Path) -> None:
    _write_file(repo, """
        version: 1
        templates:
          - name: docs
            kind: server
            command: ["python3", "-m", "http.server", "{port}"]
    """)
    docs = _entries(_workspace(client))["docs"]
    assert (docs["origin"], docs["launchable"], docs["qualified_name"]) == ("file", True, "loregarden/docs")
    names = [t["name"] for t in client.get("/api/instances/templates").json()]
    assert "loregarden/docs" in names


def test_a_broken_file_is_reported_and_the_rest_still_works(client: TestClient, repo: Path) -> None:
    _write_file(repo, "version: 1\ntemplates: [{name: Bad Name, kind: server, command: [x]}]\n")
    workspace = _workspace(client)
    assert "lowercase" in workspace["file_error"]
    assert set(_entries(workspace)) == {"client", "server"}


def test_stored_templates_round_trip(client: TestClient, repo: Path) -> None:
    created = client.post("/api/instance-templates/loregarden", json=_spec(description="first"))
    assert created.status_code == 201, created.text
    assert _entries(created.json())["docs"]["origin"] == "stored"

    updated = client.put("/api/instance-templates/loregarden/docs", json=_spec(description="second"))
    assert updated.status_code == 200, updated.text
    assert _entries(updated.json())["docs"]["description"] == "second"

    assert client.delete("/api/instance-templates/loregarden/docs").status_code == 204
    assert "docs" not in _entries(_workspace(client))


@pytest.mark.parametrize(
    ("method", "path", "body", "code"),
    [
        ("post", "/api/instance-templates/nope", _spec(), 404),
        ("post", "/api/instance-templates/loregarden", _spec("server"), 409),
        ("post", "/api/instance-templates/loregarden", _spec(cwd="../out"), 422),
        ("post", "/api/instance-templates/loregarden", {**_spec(), "shell": True}, 422),
        ("put", "/api/instance-templates/loregarden/missing", _spec("missing"), 404),
        ("delete", "/api/instance-templates/loregarden/missing", None, 404),
    ],
)
def test_refusals(client: TestClient, repo: Path, method: str, path: str, body, code: int) -> None:
    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert response.status_code == code, response.text


def test_a_rename_is_refused(client: TestClient, repo: Path) -> None:
    assert client.post("/api/instance-templates/loregarden", json=_spec()).status_code == 201
    response = client.put("/api/instance-templates/loregarden/docs", json=_spec("docs-two"))
    assert response.status_code == 422
    assert "cannot rename" in response.json()["detail"]


def test_a_stored_row_the_file_later_claims_is_shadowed_not_launched(client: TestClient, repo: Path) -> None:
    assert client.post("/api/instance-templates/loregarden", json=_spec()).status_code == 201
    _write_file(repo, """
        version: 1
        templates:
          - name: docs
            kind: server
            command: ["python3", "-m", "http.server", "{port}"]
    """)
    rows = [e for e in _workspace(client)["entries"] if e["name"] == "docs"]
    assert sorted((e["origin"], e["launchable"], e["shadowed_by"]) for e in rows) == [
        ("file", True, None),
        ("stored", False, "file"),
    ]


def test_a_stored_template_launches_from_the_workspace(client: TestClient, repo: Path) -> None:
    assert client.post("/api/instance-templates/loregarden", json=_spec(health_path="/")).status_code == 201
    launched = client.post(
        "/api/instances", json={"template": "loregarden/docs", "params": {"worktree": str(repo.resolve())}}
    )
    assert launched.status_code == 201, launched.text
    instance = launched.json()
    assert instance["project"] == "loregarden"
    assert instance["cwd"] == str(repo.resolve())
    assert get_instance_manager().wait_ready(instance["id"], timeout=15).state == "ready"
    assert client.delete(f"/api/instances/{instance['id']}").status_code == 204
