"""Dispatch for the memory, learning, blog and checkpoint tools.

Split from `mcp.tools`, which sits on its 1500-line cap and had reached it twice
in a day. These handlers are one concern — everything that writes to or reads
from the memory layer — and they share no state with the ticket and stage
dispatch they were interleaved with.

The session and the memory store are passed in rather than built here: this
module decides what a tool call means, not where the store lives
(lg-workflow-integrity-568, incidentally, while fixing the slot leak).
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

#: The tools this module owns. Kept beside the dispatch it gates rather than in
#: `mcp.tools`, so adding a memory tool touches one file.
MEMORY_TOOL_NAMES = frozenset(
    {
        "loregarden_memory_status",
        "loregarden_append_learning",
        "loregarden_upsert_memory",
        "loregarden_upsert_blog_post",
        "loregarden_append_checkpoint",
        "loregarden_search_memory",
        "loregarden_create_memory_relation",
    }
)


def execute_memory_tool(session: Session, name: str, arguments: dict[str, Any]) -> str | None:
    """This module's entry point: run `name` if it is a memory tool, else None.

    Dispatch loregarden's memory/learnings/blog-post/checkpoint tools.
    Returns None if `name` isn't one of these (caller falls through).

    Takes a session for one reason: a checkpoint may declare an unresolved
    blocker, and that has to reach the ticket (430). Everything else here
    writes to the vault and needs no database at all.
    """
    if name not in MEMORY_TOOL_NAMES:
        return None

    memory = AgentMemoryService.from_settings()

    if name == "loregarden_memory_status":
        return json.dumps(
            memory.status(workspace_slug=arguments.get("workspace_slug", "")), indent=2
        )

    if name == "loregarden_append_learning":
        result = memory.append_learning(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            content=arguments["content"],
            tags=arguments.get("tags"),
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_upsert_memory":
        result = memory.upsert_memory(
            node_id=arguments.get("node_id", ""),
            title=arguments["title"],
            body=arguments.get("body", ""),
            tags=arguments.get("tags"),
            ticket_id=arguments.get("ticket_id", ""),
            workspace_slug=arguments["workspace_slug"],
            discredited=arguments.get("discredited"),
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_upsert_blog_post":
        result = memory.upsert_blog_post(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            title=arguments["title"],
            body=arguments["body"],
            tags=arguments.get("tags"),
            note_id=arguments.get("note_id", ""),
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_append_checkpoint":
        result = memory.append_checkpoint(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            run_id=arguments["run_id"],
            entry=arguments["entry"],
        )
        if arguments.get("blocker"):
            # 430. The checkpoint is written first: the record of *why* must
            # survive even if resolving the ticket fails, and an operator
            # reading a blocked ticket needs the entry to already exist.
            ticket = OrchestrationCallbackService(session).resolve_ticket(
                ticket_id=arguments["ticket_id"],
                workspace_slug=arguments["workspace_slug"],
            )
            result = {
                **result,
                "blocked": True,
                "blocking_issues": block_ticket_for_unresolved_blocker(
                    session, ticket, entry=arguments["entry"]
                ),
            }
        return json.dumps(result, indent=2)

    if name == "loregarden_search_memory":
        result = memory.search(
            arguments["query"],
            workspace_slug=arguments.get("workspace_slug", ""),
            limit=int(arguments.get("limit") or 20),
        )
        return json.dumps(result, indent=2)

    # loregarden_create_memory_relation
    result = memory.create_relation(
        source_id=arguments["source_id"],
        target_id=arguments["target_id"],
        relation_type=arguments.get("relation_type", "related"),
        workspace_slug=arguments["workspace_slug"],
    )
    return json.dumps(result, indent=2)
