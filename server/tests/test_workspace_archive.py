"""Archiving a workspace lists it apart from the active ones, and nothing else.

The Workspaces page shows active workspaces and keeps the archived ones in a
folded list. Archiving is display-only, so what is pinned here is the flag
round-trip, its idempotence, and that a database from before the column gains
it without touching any existing row.
"""

import tempfile

from fastapi.testclient import TestClient
from loregarden.db.migrations_workspace_archive import m_workspace_archived_at
from sqlalchemy import create_engine, text


def _row(client: TestClient, slug: str) -> dict:
    response = client.get("/api/workspaces")
    assert response.status_code == 200, response.text
    return next(w for w in response.json() if w["slug"] == slug)


def test_a_workspace_starts_active(client: TestClient) -> None:
    assert _row(client, "loregarden")["archived_at"] is None


def test_archive_then_restore(client: TestClient) -> None:
    archived = client.post("/api/workspaces/loregarden/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None
    assert _row(client, "loregarden")["archived_at"] == archived.json()["archived_at"]

    restored = client.post("/api/workspaces/loregarden/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None
    assert _row(client, "loregarden")["archived_at"] is None


def test_archiving_twice_keeps_the_first_date(client: TestClient) -> None:
    first = client.post("/api/workspaces/loregarden/archive").json()["archived_at"]
    again = client.post("/api/workspaces/loregarden/archive").json()["archived_at"]
    assert again == first


def test_restoring_an_active_workspace_is_a_no_op(client: TestClient) -> None:
    response = client.post("/api/workspaces/loregarden/restore")
    assert response.status_code == 200
    assert response.json()["archived_at"] is None


def test_an_unknown_workspace_is_404(client: TestClient) -> None:
    assert client.post("/api/workspaces/nope/archive").status_code == 404
    assert client.post("/api/workspaces/nope/restore").status_code == 404


def test_the_migration_adds_the_column_and_leaves_rows_active() -> None:
    engine = create_engine(f"sqlite:///{tempfile.mkdtemp()}/t.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE workspaces (id TEXT PRIMARY KEY, slug TEXT, name TEXT)"))
        conn.execute(text("INSERT INTO workspaces VALUES ('w1', 'shop', 'Shop')"))
        m_workspace_archived_at(conn)
        m_workspace_archived_at(conn)  # guards its own change; safe to re-run
        rows = conn.execute(text("SELECT slug, archived_at FROM workspaces")).fetchall()
    assert [tuple(r) for r in rows] == [("shop", None)]
