"""Tests for the `loregarden_create_ticket` MCP tool (ticket CRUD parity, a9-create-ticket-mcp-tool).

The tool is new — these tests are written ahead of the implementation stage and are
expected to fail red (ImportError / "Unknown tool") until `loregarden_create_ticket` is
registered in `loregarden/mcp/tools.py` and dispatched to `TicketService.create_ticket`.

Design assumptions pinned here (see the ticket's own acceptance criteria):
  - Tool name: `loregarden_create_ticket`.
  - Input mirrors `TicketCreate` (schemas.py): workspace_slug, title required;
    work_item_type (default "task"), description (default ""), acceptance_criteria
    (default []), priority (default 3), external_id (default "", auto-slugged), and a
    new `parent` field (UUID or external_id slug, resolved the same way
    `OrchestrationCallbackService.resolve_ticket` resolves ticket_id) that maps onto
    `TicketService.create_ticket`'s `parent_ticket_id`.
  - The handler delegates validation to `TicketService.create_ticket` rather than
    reimplementing it — pinned below by asserting the *exact* ValueError message
    TicketService raises surfaces unchanged through the tool.
  - Success returns at minimum `id`, `external_id`, and `title` of the created ticket
    (per acceptance criteria); richer invariants (parent linkage, priority, work_item_type,
    description, acceptance_criteria) are verified by reading the persisted `Ticket` row
    directly rather than assuming the tool's response shape carries them.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments, tool_names
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.ticket_service import TicketService
from sqlmodel import select


def _workspace(session) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()


def _milestone(session) -> Ticket:
    ws = _workspace(session)
    milestone = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.work_item_type == WorkItemType.MILESTONE,
        )
    ).first()
    assert milestone, "seed produced no milestone to use as a parent"
    return milestone


def _task_ticket(session) -> Ticket:
    ws = _workspace(session)
    task = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.work_item_type == WorkItemType.TASK,
        )
    ).first()
    assert task, "seed produced no task ticket"
    return task


def _create(session, args: dict) -> dict:
    normalized = normalize_tool_arguments("loregarden_create_ticket", args)
    return json.loads(execute_tool(session, "loregarden_create_ticket", normalized))


# --- registration ---------------------------------------------------------


def test_tool_is_registered():
    assert "loregarden_create_ticket" in tool_names()


# --- happy path ------------------------------------------------------------


def test_create_milestone_with_no_parent_returns_id_external_id_title(client, db_session):
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "MCP-created milestone",
            "work_item_type": "milestone",
        },
    )
    assert result["title"] == "MCP-created milestone"
    assert result["id"]
    assert result["external_id"]

    stored = db_session.get(Ticket, result["id"])
    assert stored is not None
    assert stored.external_id == result["external_id"]
    assert stored.work_item_type == WorkItemType.MILESTONE
    assert stored.parent_ticket_id is None
    assert stored.priority == 3  # schema default
    assert stored.description == ""
    assert load_criteria(stored.acceptance_criteria_json) == []


def test_create_feature_with_parent_as_uuid(client, db_session):
    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "Feature via parent UUID",
            "work_item_type": "feature",
            "parent": milestone.id,
            "priority": 1,
            "description": "created over MCP",
            "acceptance_criteria": ["AC one", "AC two"],
        },
    )
    stored = db_session.get(Ticket, result["id"])
    assert stored.parent_ticket_id == milestone.id
    assert stored.priority == 1
    assert stored.description == "created over MCP"
    assert load_criteria(stored.acceptance_criteria_json) == ["AC one", "AC two"]


def test_create_feature_with_parent_as_external_id_slug(client, db_session):
    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "Feature via parent slug",
            "work_item_type": "feature",
            "parent": milestone.external_id,
        },
    )
    stored = db_session.get(Ticket, result["id"])
    assert stored.parent_ticket_id == milestone.id


def test_create_ticket_auto_slugs_external_id_when_empty(client, db_session):
    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "Auto slug me please",
            "work_item_type": "feature",
            "parent": milestone.id,
        },
    )
    assert result["external_id"]
    assert "auto-slug-me-please" in result["external_id"] or "auto" in result["external_id"]


def test_create_ticket_respects_explicit_external_id(client, db_session):
    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "Explicit slug",
            "work_item_type": "feature",
            "parent": milestone.id,
            "external_id": "custom-explicit-slug",
        },
    )
    assert result["external_id"] == "custom-explicit-slug"


def test_create_ticket_default_work_item_type_and_priority_match_schema_defaults(
    client, db_session
):
    """Omitting work_item_type/priority applies TicketCreate's own defaults (task, 3) —
    proven here because the default type (task) then still requires a parent, exactly
    as TicketService enforces for every non-milestone type."""
    with pytest.raises(ValueError, match="requires a parent work item"):
        _create(db_session, {"workspace_slug": "loregarden", "title": "No type, no parent"})


# --- validation delegated to TicketService, not reimplemented --------------


def test_milestone_with_parent_is_rejected_with_service_message(client, db_session):
    milestone = _milestone(db_session)
    other_milestone = _milestone(db_session)
    with pytest.raises(ValueError, match="Milestones cannot have a parent"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Bad milestone",
                "work_item_type": "milestone",
                "parent": other_milestone.id,
            },
        )
    assert milestone  # keep the seeded milestone referenced/unused-import-safe


def test_unknown_workspace_slug_is_rejected_with_service_message(client, db_session):
    with pytest.raises(ValueError, match="Workspace not found: does-not-exist"):
        _create(
            db_session,
            {
                "workspace_slug": "does-not-exist",
                "title": "Orphan workspace ticket",
                "work_item_type": "milestone",
            },
        )


def test_duplicate_external_id_is_rejected_with_service_message(client, db_session):
    existing = _task_ticket(db_session)
    milestone = _milestone(db_session)
    with pytest.raises(ValueError, match=f"external_id already exists: {existing.external_id}"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Duplicate slug attempt",
                "work_item_type": "feature",
                "parent": milestone.id,
                "external_id": existing.external_id,
            },
        )


def test_invalid_hierarchy_nesting_is_rejected(client, db_session):
    """Task-under-task is invalid per VALID_HIERARCHY; the MCP layer must not
    special-case this — TicketService.hierarchy_service already does."""
    task = _task_ticket(db_session)
    with pytest.raises(ValueError, match="cannot contain"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Task under task",
                "work_item_type": "task",
                "parent": task.id,
            },
        )


def test_priority_out_of_range_is_rejected_with_service_message(client, db_session):
    milestone = _milestone(db_session)
    with pytest.raises(ValueError, match="Priority must be between 1 and 3"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Bad priority",
                "work_item_type": "feature",
                "parent": milestone.id,
                "priority": 9,
            },
        )


# --- parent resolution (mirrors loregarden_get_ticket's ticket_id resolution) ---


def test_unresolvable_parent_is_a_structured_error_not_a_stack_trace(client, db_session):
    with pytest.raises(ValueError, match="(?i)parent"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Orphan parent reference",
                "work_item_type": "feature",
                "parent": "not-a-real-uuid-or-slug",
            },
        )


def test_parent_in_a_different_workspace_is_not_resolved(client, db_session):
    """A parent external_id that exists only in another *valid* workspace must not
    silently resolve when creating in a different valid workspace — the MCP layer's
    `parent` resolution must respect `workspace_slug` scoping the same way
    `loregarden_get_ticket` does, not just reject because the target workspace itself
    doesn't exist.

    Deliberately uses two *real* workspaces (not an unknown workspace_slug, which the
    original version of this test used): TicketService.create_ticket raises
    "Workspace not found" before ever reaching parent resolution, so an unknown-workspace
    setup would pass for the wrong reason. Worse, with no `loregarden_create_ticket` tool
    registered at all, `execute_tool` raises a bare `ValueError("Unknown tool: ...")` —
    which satisfies an unqualified `pytest.raises(ValueError)` too. Both made this test
    pass red-suite-wide with zero implementation. Pinning `match="(?i)parent"` and using
    a genuinely different workspace closes both gaps."""
    loregarden_ws = _workspace(db_session)
    other_ws = Workspace(
        slug="other-workspace-for-parent-scoping",
        name="Other Workspace",
        workflow_template_id=loregarden_ws.workflow_template_id,
    )
    db_session.add(other_ws)
    db_session.commit()
    other_milestone = TicketService(db_session).create_ticket(
        workspace_slug=other_ws.slug,
        title="Milestone in the other workspace",
        work_item_type=WorkItemType.MILESTONE,
    )
    with pytest.raises(ValueError, match="(?i)parent"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Cross-workspace parent",
                "work_item_type": "feature",
                "parent": other_milestone.external_id,
            },
        )


# --- HTTP/JSON-RPC layer: errors surface as clean tool errors, not 500s ----


def test_http_layer_reports_unresolvable_parent_as_isError_not_a_crash(client):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loregarden_create_ticket",
                "arguments": {
                    "workspace_slug": "loregarden",
                    "title": "HTTP layer bad parent",
                    "work_item_type": "feature",
                    "parent": "totally-bogus-parent-reference",
                },
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert "error" in body or body["result"].get("isError"), (
        "loregarden_create_ticket must surface an unresolvable parent as a clean "
        "tool error, not a raw 500/stack trace"
    )
    # A bare "or isError" check passes vacuously if the tool isn't registered at all
    # (execute_tool raises "Unknown tool: ..." -> isError True for the wrong reason).
    # Pin the error text to the actual parent-resolution failure so this test can only
    # pass once loregarden_create_ticket really rejects the bogus parent reference.
    message = json.dumps(body).lower()
    assert "unknown tool" not in message, body
    assert "parent" in message, body


def test_http_layer_happy_path_returns_created_ticket(client):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "loregarden_create_ticket",
                "arguments": {
                    "workspace_slug": "loregarden",
                    "title": "HTTP layer milestone",
                    "work_item_type": "milestone",
                },
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert "error" not in body, body.get("error")
    result = body["result"]
    assert not result.get("isError"), result
    payload = json.loads(result["content"][0]["text"])
    assert payload["title"] == "HTTP layer milestone"
    assert payload["id"]
    assert payload["external_id"]


# --- HTTP/JSON-RPC layer: the real dispatch entrypoint enforces the interim
# orchestrated-agent deny too, not just the CLI-subprocess permission bridge ---


def test_http_layer_denies_create_ticket_when_orchestrated_header_set(client):
    """A run Loregarden's own CLI invocation builders mark as orchestrated (see
    agents/mcp_context.py) must be denied here even though it reaches `/mcp` directly,
    bypassing the CLI-subprocess permission bridge entirely."""
    res = client.post(
        "/mcp",
        headers={"X-Loregarden-Orchestrated": "1"},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "loregarden_create_ticket",
                "arguments": {
                    "workspace_slug": "loregarden",
                    "title": "Should never be created",
                    "work_item_type": "task",
                },
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    result = body["result"]
    assert result.get("isError"), result
    assert "orchestrated" in result["content"][0]["text"].lower()


def test_http_layer_allows_create_ticket_without_orchestrated_header(client):
    """A direct operator/HTTP call — no `X-Loregarden-Orchestrated` header — is the
    interactive case the ticket's triage decision allows by default."""
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "loregarden_create_ticket",
                "arguments": {
                    "workspace_slug": "loregarden",
                    "title": "Direct operator call",
                    "work_item_type": "milestone",
                },
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    result = body["result"]
    assert not result.get("isError"), result
