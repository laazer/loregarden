"""Documents on a ticket, written without a run and read back by descendants.

The case that motivated the tools: a product brief decomposed into an
initiative spanning several workspaces, whose architecture and decision records
had no place to live — initiatives never orchestrate, and `attach_artifact`
needs an orchestration run — and whose executing agents had no tool to read an
artifact at all.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    ORCHESTRATED_DENIED_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    McpTool,
)
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Artifact, Ticket, WorkItemType
from loregarden.services.ticket_documents import MAX_DOCUMENT_CHARS
from loregarden.services.ticket_service import TicketService
from sqlmodel import select


def _call(session, name: str, args: dict) -> dict:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def _tree(session) -> dict[str, Ticket]:
    """initiative → milestone → feature → capability → task, the full chain."""
    svc = TicketService(session)
    initiative = svc.create_ticket(
        workspace_slug=None, title="Docs initiative", work_item_type=WorkItemType.INITIATIVE
    )
    milestone = svc.create_ticket(
        workspace_slug="loregarden",
        title="Docs milestone",
        work_item_type=WorkItemType.MILESTONE,
        parent_ticket_id=initiative.id,
    )
    feature = svc.create_ticket(
        workspace_slug="loregarden",
        title="Docs feature",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=milestone.id,
    )
    capability = svc.create_ticket(
        workspace_slug="loregarden",
        title="Docs capability",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=feature.id,
    )
    task = svc.create_ticket(
        workspace_slug="loregarden",
        title="Docs task",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=capability.id,
    )
    return {
        "initiative": initiative,
        "milestone": milestone,
        "feature": feature,
        "capability": capability,
        "task": task,
    }


def _write(session, ticket: Ticket, title: str, body: str, **extra) -> dict:
    return _call(
        session,
        McpTool.WRITE_DOCUMENT,
        {"ticket_id": ticket.external_id, "title": title, "body": body, **extra},
    )


def test_a_document_lands_on_an_initiative_with_no_run(db_session):
    tree = _tree(db_session)

    written = _write(
        db_session,
        tree["initiative"],
        "Architecture",
        "# Architecture\n\nOne render engine.",
        kind="architecture",
        summary="system model",
    )

    row = db_session.get(Artifact, written["artifact_id"])
    assert row is not None
    assert row.ticket_id == tree["initiative"].id
    assert row.run_id is None
    assert row.kind == "architecture"
    content = json.loads(row.content_json)
    assert content["body"].startswith("# Architecture")
    assert content["summary"] == "system model"
    assert written["version"] == 1
    assert written["supersedes"] is None


def test_rewriting_keeps_every_version_and_the_newest_wins(db_session):
    tree = _tree(db_session)
    first = _write(db_session, tree["milestone"], "Decisions", "v1")
    second = _write(db_session, tree["milestone"], "Decisions", "v2")

    assert second["version"] == 2
    assert second["supersedes"] == first["artifact_id"]
    rows = db_session.exec(select(Artifact).where(Artifact.ticket_id == tree["milestone"].id)).all()
    assert len(rows) == 2

    listed = _call(
        db_session,
        McpTool.LIST_ARTIFACTS,
        {"ticket_id": tree["milestone"].external_id, "include_ancestors": False},
    )
    assert [item["artifact_id"] for item in listed["items"]] == [second["artifact_id"]]
    assert listed["items"][0]["versions"] == 2

    every = _call(
        db_session,
        McpTool.LIST_ARTIFACTS,
        {
            "ticket_id": tree["milestone"].external_id,
            "include_ancestors": False,
            "latest_only": False,
        },
    )
    assert {item["artifact_id"] for item in every["items"]} == {
        first["artifact_id"],
        second["artifact_id"],
    }


def test_a_task_sees_the_documents_of_every_ancestor(db_session):
    """The point of the feature: an executing agent finds its planning context."""
    tree = _tree(db_session)
    _write(db_session, tree["initiative"], "Architecture", "arch")
    _write(db_session, tree["milestone"], "Scope", "scope")
    _write(db_session, tree["task"], "Notes", "notes")

    listed = _call(db_session, McpTool.LIST_ARTIFACTS, {"ticket_id": tree["task"].id})

    by_title = {item["title"]: item for item in listed["items"]}
    assert set(by_title) == {"Architecture", "Scope", "Notes"}
    assert by_title["Notes"]["depth"] == 0
    assert by_title["Scope"]["depth"] == 3
    assert by_title["Architecture"]["depth"] == 4
    assert by_title["Architecture"]["work_item_type"] == "initiative"
    assert by_title["Architecture"]["workspace"] == ""
    assert listed["searched"][-1] == tree["initiative"].external_id
    # Listings carry metadata, never bodies.
    assert all("content" not in item for item in listed["items"])


def test_include_ancestors_false_lists_only_the_ticket(db_session):
    tree = _tree(db_session)
    _write(db_session, tree["initiative"], "Architecture", "arch")

    listed = _call(
        db_session,
        McpTool.LIST_ARTIFACTS,
        {"ticket_id": tree["task"].external_id, "include_ancestors": "false"},
    )
    assert listed["items"] == []
    assert listed["total"] == 0


def test_kind_filter_and_limit(db_session):
    tree = _tree(db_session)
    for i in range(3):
        _write(db_session, tree["milestone"], f"Finding {i}", "body", kind="finding")
    _write(db_session, tree["milestone"], "Plan", "body", kind="plan")

    findings = _call(
        db_session,
        McpTool.LIST_ARTIFACTS,
        {"ticket_id": tree["milestone"].id, "kind": "finding", "limit": 2},
    )
    assert findings["total"] == 3
    assert findings["truncated"] is True
    assert len(findings["items"]) == 2
    assert {item["kind"] for item in findings["items"]} == {"finding"}


def test_read_returns_the_body_and_flags_a_superseded_version(db_session):
    tree = _tree(db_session)
    old = _write(db_session, tree["feature"], "Spec", "old body")
    new = _write(db_session, tree["feature"], "Spec", "new body")

    stale = _call(db_session, McpTool.READ_ARTIFACT, {"artifact_id": old["artifact_id"]})
    assert stale["content"]["body"] == "old body"
    assert stale["is_latest"] is False
    assert stale["latest_artifact_id"] == new["artifact_id"]

    fresh = _call(db_session, McpTool.READ_ARTIFACT, {"artifact_id": new["artifact_id"]})
    assert fresh["is_latest"] is True
    assert fresh["external_id"] == tree["feature"].external_id


def test_read_artifact_reads_run_artifacts_too(db_session):
    """The read tool is not documents-only: a run's log was REST-only before."""
    tree = _tree(db_session)
    row = Artifact(ticket_id=tree["task"].id, kind="log", title="run log", content_json='{"x": 1}')
    db_session.add(row)
    db_session.commit()

    read = _call(db_session, McpTool.READ_ARTIFACT, {"artifact_id": row.id})
    assert read["content"] == {"x": 1}
    assert read["is_latest"] is True


@pytest.mark.parametrize(
    ("title", "body", "message"),
    [
        ("   ", "body", "title"),
        ("Title", "   ", "body"),
        ("Title", "x" * (MAX_DOCUMENT_CHARS + 1), "limit"),
    ],
)
def test_write_refuses_what_it_cannot_file(db_session, title, body, message):
    tree = _tree(db_session)
    with pytest.raises(ValueError, match=message):
        execute_tool(
            db_session,
            McpTool.WRITE_DOCUMENT,
            normalize_tool_arguments(
                McpTool.WRITE_DOCUMENT,
                {"ticket_id": tree["task"].id, "title": title, "body": body},
            ),
        )
    assert (
        db_session.exec(select(Artifact).where(Artifact.ticket_id == tree["task"].id)).all() == []
    )


def test_unknown_ids_are_errors_not_empty_answers(db_session):
    with pytest.raises(ValueError, match="Artifact not found"):
        _call(db_session, McpTool.READ_ARTIFACT, {"artifact_id": "no-such-artifact"})
    with pytest.raises(ValueError):
        _call(db_session, McpTool.LIST_ARTIFACTS, {"ticket_id": "no-such-ticket-999"})


def test_grants_reads_to_stages_and_keeps_writes_auto_approved():
    assert McpTool.LIST_ARTIFACTS in STAGE_DEFAULT_MCP_TOOLS
    assert McpTool.READ_ARTIFACT in STAGE_DEFAULT_MCP_TOOLS
    assert {McpTool.LIST_ARTIFACTS, McpTool.READ_ARTIFACT, McpTool.WRITE_DOCUMENT} <= (
        AUTO_APPROVED_MCP_TOOLS
    )
    assert (
        not {
            McpTool.LIST_ARTIFACTS,
            McpTool.READ_ARTIFACT,
            McpTool.WRITE_DOCUMENT,
        }
        & ORCHESTRATED_DENIED_MCP_TOOLS
    )


def test_get_ticket_indexes_ancestor_documents_but_not_run_output(db_session):
    tree = _tree(db_session)
    written = _write(db_session, tree["initiative"], "Architecture", "arch", summary="model")
    db_session.add(Artifact(ticket_id=tree["task"].id, kind="log", title="run log"))
    db_session.commit()

    payload = _call(db_session, McpTool.GET_TICKET, {"ticket_id": tree["task"].id})

    assert payload["documents"] == [
        {
            "artifact_id": written["artifact_id"],
            "external_id": tree["initiative"].external_id,
            "depth": 4,
            "kind": "document",
            "title": "Architecture",
            "summary": "model",
        }
    ]
