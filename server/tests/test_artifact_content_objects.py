"""`Artifact.content_json` is always a JSON object.

A `log` artifact stored as a JSON *string* holding an object (an agent that
encoded twice) made `GET /api/tickets/{id}` raise on every load. The attach
tools now refuse one, and migration 20261013_artifact_content_objects unwraps
the rows already stored.
"""

from __future__ import annotations

import json
import tempfile

import loregarden.models.domain  # noqa: F401  (registers the tables on SQLModel.metadata)
import pytest
from fastapi.testclient import TestClient
from loregarden.db.versions.artifact_content_objects import m_artifact_content_objects
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Artifact, Ticket, WorkItemType, Workspace
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel
from tests.factories import make_agent_run, make_workspace_ticket

_OBJECT = {"finding": "landed", "detail": "x"}
_DOUBLE_ENCODED = json.dumps(json.dumps(_OBJECT))


@pytest.fixture(name="run_ref")
def run_ref_fixture(db_session):
    ticket = make_workspace_ticket(db_session, "content-object-ticket")
    run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="run_c0ffee",
        stage_key="plan",
        skill_name="",
        status="running",
        command="",
        stdout="",
        stderr="",
    )
    return run.run_code


@pytest.mark.parametrize("tool", ["loregarden_attach_artifact", "loregarden_attach_evidence"])
@pytest.mark.parametrize("raw", [_DOUBLE_ENCODED, "[1, 2]", "3", "not json"])
def test_attach_refuses_content_that_is_not_an_object(db_session, run_ref, tool, raw):
    with pytest.raises(ValueError, match="content_json must be a JSON object"):
        execute_tool(
            db_session,
            tool,
            {
                "run_id": run_ref,
                "kind": "log",
                "evidence_kind": "test_red_green",
                "title": "t",
                "content_json": raw,
            },
        )


@pytest.mark.parametrize("content", [json.dumps(_OBJECT), _OBJECT])
def test_attach_stores_an_object_whether_given_as_text_or_object(db_session, run_ref, content):
    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_attach_artifact",
            {"run_id": run_ref, "kind": "log", "title": "t", "content_json": content},
        )
    )

    stored = db_session.get(Artifact, result["artifact_id"]).content_json
    assert json.loads(stored) == _OBJECT


def _ticket_with(engine, contents: dict[str, str]) -> dict[str, str]:
    with Session(engine) as session:
        workspace = Workspace(slug="w", name="w", repo_path="/nonexistent")
        session.add(workspace)
        session.commit()
        ticket = Ticket(
            external_id="t-1",
            workspace_id=workspace.id,
            title="t",
            work_item_type=WorkItemType.TASK,
        )
        session.add(ticket)
        session.commit()
        rows = {
            name: Artifact(ticket_id=ticket.id, kind="log", title=name, content_json=raw)
            for name, raw in contents.items()
        }
        session.add_all(rows.values())
        session.commit()
        return {name: row.id for name, row in rows.items()}


def test_migration_unwraps_only_double_encoded_objects_and_is_idempotent():
    engine = create_engine(f"sqlite:///{tempfile.mkdtemp()}/t.db")
    SQLModel.metadata.create_all(engine)
    ids = _ticket_with(
        engine,
        {
            "double": _DOUBLE_ENCODED,
            "object": json.dumps(_OBJECT),
            "plain_string": json.dumps("just text"),
        },
    )

    for _ in range(2):
        with engine.begin() as conn:
            m_artifact_content_objects(conn)

    with Session(engine) as session:
        stored = {name: session.get(Artifact, i).content_json for name, i in ids.items()}
    assert json.loads(stored["double"]) == _OBJECT
    assert json.loads(stored["object"]) == _OBJECT
    # Not an object inside either: reported, not guessed at.
    assert stored["plain_string"] == json.dumps("just text")


def test_a_ticket_whose_log_was_double_encoded_loads_after_the_migration(
    client: TestClient, db_session
):
    ticket = make_workspace_ticket(db_session, "double-encoded-log")
    db_session.add(
        Artifact(ticket_id=ticket.id, kind="log", title="l", content_json=_DOUBLE_ENCODED)
    )
    db_session.commit()

    m_artifact_content_objects(db_session.connection())
    db_session.commit()

    assert client.get(f"/api/tickets/{ticket.id}").status_code == 200
