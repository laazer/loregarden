"""MCP tools for documents on a ticket, and for reading any artifact back.

Three tools, registered in `tool_registry.EXTENDED_TOOLS` rather than in the
`execute_tool` chain: none needs the run and ticket context the chain resolves.

- `loregarden_write_document` stores a markdown document on any ticket —
  initiative and milestone included — without an orchestration run.
- `loregarden_list_artifacts` lists what a ticket and its ancestors carry.
- `loregarden_read_artifact` returns one artifact in full.

The logic lives in `services.ticket_documents`; this module is schema and JSON.
Argument normalizers live beside the other table residents in `tool_args`.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_args import MAX_ARTIFACT_LIST_LIMIT
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import boolean_prop, integer_prop, string_prop, tool_schema
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_documents import (
    DEFAULT_DOCUMENT_KIND,
    MAX_DOCUMENT_CHARS,
    list_artifacts,
    read_artifact,
    write_document,
)

_TICKET_ID = string_prop("Ticket UUID or external id (initiatives and milestones included).")
_WORKSPACE = string_prop("Workspace slug — only needed to disambiguate a legacy external id.")

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.WRITE_DOCUMENT,
        "description": (
            "Store a markdown document on a ticket — architecture, domain model, decision "
            "record, plan, findings — without an orchestration run, so it can live on an "
            "initiative or milestone. Writing the same ticket + kind + title again stores "
            "a new version; every version is kept and the newest wins. Agents working on "
            "any descendant see it through loregarden_list_artifacts. Use this instead of "
            "writing a report .md into a repository."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": _TICKET_ID,
                "workspace_slug": _WORKSPACE,
                "title": string_prop(
                    "Document title. Also its identity: the same title on the same ticket "
                    "and kind is a new version of this document."
                ),
                "body": string_prop(f"Markdown body, at most {MAX_DOCUMENT_CHARS} characters."),
                "kind": string_prop(
                    f"What the document is, e.g. architecture, decision, plan, finding "
                    f"(default {DEFAULT_DOCUMENT_KIND})."
                ),
                "summary": string_prop("One line shown in listings, so a reader can choose."),
            },
            required=["ticket_id", "title", "body"],
        ),
    },
    {
        "name": McpTool.LIST_ARTIFACTS,
        "description": (
            "List the artifacts on a ticket and, by default, on every ancestor up to its "
            "initiative — where the documents describing a ticket's context are kept. "
            "Returns titles, kinds, summaries and ids, not bodies; read one with "
            "loregarden_read_artifact. Read this before inferring a design decision "
            "from code."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": _TICKET_ID,
                "workspace_slug": _WORKSPACE,
                "kind": string_prop("Only artifacts of this kind, e.g. document, plan, log."),
                "include_ancestors": boolean_prop(
                    "Also list the parent chain's artifacts (default true)."
                ),
                "latest_only": boolean_prop(
                    "Collapse each ticket + kind + title to its newest version (default true)."
                ),
                "limit": integer_prop(f"Max items (default 50, max {MAX_ARTIFACT_LIST_LIMIT})."),
            },
            required=["ticket_id"],
        ),
    },
    {
        "name": McpTool.READ_ARTIFACT,
        "description": (
            "Read one artifact in full by id, with is_latest telling you whether a newer "
            "version of the same document exists (and latest_artifact_id to read it)."
        ),
        "inputSchema": tool_schema(
            properties={"artifact_id": string_prop("Artifact id from loregarden_list_artifacts.")},
            required=["artifact_id"],
        ),
    },
]


def _ticket(session: Session, arguments: dict[str, Any]):
    return OrchestrationCallbackService(session).resolve_ticket(
        ticket_id=arguments["ticket_id"],
        workspace_slug=arguments.get("workspace_slug") or None,
    )


def write_document_tool(session: Session, arguments: dict[str, Any]) -> str:
    ticket = _ticket(session, arguments)
    written = write_document(
        session,
        ticket,
        title=arguments["title"],
        body=arguments["body"],
        kind=arguments.get("kind") or DEFAULT_DOCUMENT_KIND,
        summary=arguments.get("summary") or "",
    )
    return json.dumps(
        {
            "ok": True,
            "artifact_id": written.artifact.id,
            "ticket": ticket.external_id,
            "kind": written.artifact.kind,
            "title": written.artifact.title,
            "version": written.version,
            "supersedes": written.supersedes,
        },
        indent=2,
    )


def list_artifacts_tool(session: Session, arguments: dict[str, Any]) -> str:
    return json.dumps(
        list_artifacts(
            session,
            _ticket(session, arguments),
            kind=arguments.get("kind") or "",
            include_ancestors=arguments["include_ancestors"],
            latest_only=arguments["latest_only"],
            limit=arguments["limit"],
        ),
        indent=2,
    )


def read_artifact_tool(session: Session, arguments: dict[str, Any]) -> str:
    return json.dumps(read_artifact(session, arguments["artifact_id"]), indent=2)


HANDLERS = {
    McpTool.WRITE_DOCUMENT.value: write_document_tool,
    McpTool.LIST_ARTIFACTS.value: list_artifacts_tool,
    McpTool.READ_ARTIFACT.value: read_artifact_tool,
}
