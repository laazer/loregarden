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
import logging
from typing import Any

from sqlmodel import Session, select

from loregarden.mcp.shaped_errors import UnknownWorkspaceSlug
from loregarden.models.domain import AgentRun, MemoryOriginKind, Workspace
from loregarden.models.domain.enums import MemoryStoreState
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.operations.memory_discredit import refuse_unreasoned_discredit
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.retrieval_basis import envelope_partial

logger = logging.getLogger(__name__)

#: The tools this module owns. Kept beside the dispatch it gates rather than in
#: `mcp.tools`, so adding a memory tool touches one file.
MEMORY_TOOL_NAMES = frozenset(
    {
        "loregarden_append_learning",
        "loregarden_upsert_memory",
        "loregarden_upsert_blog_post",
        "loregarden_append_checkpoint",
        "loregarden_search_memory",
    }
)


def _resolved_slug(session: Session, workspace_slug: str) -> str:
    """Resolve the slug to a `Workspace` row once, here at the boundary.

    It used to be passed straight through, unresolved, so a typo returned an
    empty hit list — the most expensive wrong answer this tool can give, and
    the one indistinguishable from "nothing is known". The typed condition
    reaches the agent as a shaped reply with a remedy rather than as `[]`.

    **Only the exact empty string is the shared-base case.** This used to
    `.strip()` first, which quietly turned a slug of spaces into "no slug
    given" — and an empty slug resolves to the shared BASE graph file, which
    holds every tenant's rows and is filtered by `workspace_slug` only when
    that slug is non-empty. One MCP argument of `"   "` therefore read another
    workspace's memory. `memory_scope.graph_path_for_workspace` rejects
    whitespace correctly; stripping here meant it was never asked.

    So no normalisation happens before the lookup, and **an empty slug is
    refused too**. That is the half the strip was hiding: the MCP argument
    coercer (`tool_args.coerce_optional_string`) also strips, so by the time
    a handler sees it a slug of spaces has *already* become `""` — fixing
    this function alone would leave the leak reachable through the real
    transport. There is no legitimate every-tenant memory search for an
    agent: the memory protocol requires `workspace_slug` on every call, and
    the run prompt always carries one.

    Whitespace, padding, a typo and an omitted argument therefore take the
    same route — a shaped reply naming what was passed. Repairing a slug on
    the agent's behalf is how the wrong scope gets queried silently.
    """
    found = (
        session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if workspace_slug
        else None
    )
    if not found:
        raise UnknownWorkspaceSlug(workspace_slug)
    return found.slug


def _search_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """The reply an agent reads: hits, the basis, and one `partial` field.

    `partial` is a mapping of key to VALUE, never a list of key names: the
    producer already holds the answer behind each flag, and naming only the
    key would discard it. A degraded leg appears as its `MemoryStoreState`,
    not as the word "graph".
    """
    basis = result["basis"]
    partial = envelope_partial(
        basis,
        extra={
            kind: state.value
            for kind, state in basis.legs.items()
            if state is not MemoryStoreState.READ
        },
    )
    return {**result, "basis": basis.as_dict(), "partial": partial}


def _write_origin(
    session: Session, *, orchestrated: bool, run_id: str
) -> tuple[MemoryOriginKind | None, str | None]:
    """Who produced a memory write arriving over MCP — as far as the transport knows.

    `orchestrated` is set only by Loregarden's own supervised agent CLI runs,
    so it is a real signal of an agent. Anything else (an operator's terminal,
    `curl`, an external orchestrator, the in-process CLI) cannot be told apart
    and is recorded as unknown rather than guessed.

    `run_id` rides beside it (`mcp/caller.py`) and becomes the `origin_ref`
    only when it names a real run: a reference to a run that does not exist
    would send the operator looking for it. An unknown id is still an agent
    write, with the reference left unknown and the mismatch logged.
    """
    if not orchestrated:
        return None, None
    if not run_id:
        return MemoryOriginKind.AGENT, None
    if session.get(AgentRun, run_id) is None:
        logger.warning("memory write names unknown run %r; origin_ref left unset", run_id)
        return MemoryOriginKind.AGENT, None
    return MemoryOriginKind.AGENT, run_id


def execute_memory_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any],
    *,
    orchestrated: bool = False,
    run_id: str = "",
) -> str | None:
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

    if name == "loregarden_append_learning":
        origin_kind, origin_ref = _write_origin(session, orchestrated=orchestrated, run_id=run_id)
        result = memory.append_learning(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            content=arguments["content"],
            tags=arguments.get("tags"),
            title=arguments.get("title", ""),
            aliases=arguments.get("aliases"),
            origin_kind=origin_kind,
            origin_ref=origin_ref,
        )
        return json.dumps(result, indent=2)

    if name == "loregarden_upsert_memory":
        # A change to the flag needs a reason this tool does not carry
        # (`OperationId.MEMORY_DISCREDIT`); restating it passes, unchanged.
        refuse_unreasoned_discredit(
            memory,
            node_id=arguments.get("node_id", ""),
            workspace_slug=arguments["workspace_slug"],
            discredited=arguments.get("discredited"),
        )
        origin_kind, origin_ref = _write_origin(session, orchestrated=orchestrated, run_id=run_id)
        result = memory.upsert_memory(
            node_id=arguments.get("node_id", ""),
            title=arguments["title"],
            body=arguments.get("body", ""),
            tags=arguments.get("tags"),
            ticket_id=arguments.get("ticket_id", ""),
            workspace_slug=arguments["workspace_slug"],
            origin_kind=origin_kind,
            origin_ref=origin_ref,
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
        slug = _resolved_slug(session, arguments.get("workspace_slug", ""))
        result = memory.search(
            arguments["query"],
            workspace_slug=slug,
            limit=int(arguments.get("limit") or 20),
        )
        return json.dumps(_search_envelope(result), indent=2)

    raise ValueError(f"{name} is a memory tool with no handler")
