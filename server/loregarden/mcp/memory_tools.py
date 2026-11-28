"""Dispatch for the memory, learning, blog and checkpoint tools.

This is one of the two agent-facing readers of durable memory, so it carries
the grounding verdict (lg-code-knowledge-368). On the way in, an `anchors`
argument is resolved against the RUN's own checkout BEFORE the write and the
whole write is refused if it cannot be: a claim stored ungrounded when its
author asked for grounding is the silent failure that ticket exists to
remove. On the way out, a `loregarden_search_memory` hit that carries anchors
gains a `grounding` object; one that does not gains nothing at all.

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
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from loregarden.mcp.shaped_errors import UnknownWorkspaceSlug
from loregarden.models.domain import (
    AgentRun,
    GroundingVerdict,
    MemoryOriginKind,
    Ticket,
    Workspace,
)
from loregarden.models.domain.enums import MemoryStoreState
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.operations.memory_discredit import refuse_unreasoned_discredit
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.retrieval_basis import envelope_partial
from loregarden.services.symbol_map.grounding import (
    UNGROUNDED_FALLBACK,
    ClaimGrounding,
    SymbolMapUnavailableError,
    anchor_payloads,
    claim_verdicts,
    resolve_anchors,
)
from loregarden.services.ticket_worktree import resolve_ticket_root

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


#: What S2 says when the call carries no run. The verdict is a comparison
#: against a CHECKOUT, and with no run there is none — saying MISSING would
#: invent a fact out of a missing argument.
_NO_RUN_REASON = "this call carries no run, so there is no checkout to verify against"


def _run_checkout(session: Session, run_id: str) -> tuple[str, Path] | None:
    """The workspace and the tree a run is standing in, or None (D8).

    `AgentRun` -> `ticket_id`/`workspace_id` -> `resolve_ticket_root`. A
    workspace-scoped run (Home chat, `ticket_id` None) falls back to the
    workspace root. Anchors resolve against THIS tree and not the primary
    checkout: a run working in a linked worktree writes about the code in
    front of it.
    """
    run = session.get(AgentRun, run_id) if run_id else None
    if run is None:
        return None
    workspace = session.get(Workspace, run.workspace_id)
    if workspace is None:
        return None
    ticket = session.get(Ticket, run.ticket_id) if run.ticket_id else None
    root = (
        resolve_ticket_root(session, ticket, workspace)
        if ticket is not None
        else Path(workspace.repo_path)
    )
    return workspace.id, root


def _resolved_anchors(
    session: Session, arguments: dict[str, Any], run_id: str
) -> list[dict[str, Any]] | None:
    """The anchor records to store, or None when the write asked for none.

    Runs BEFORE the write, and every failure raises rather than downgrading:
    there is no partial record and no silent anchor-stripped row.
    """
    names = list(arguments.get("anchors") or [])
    if not names:
        return None
    checkout = _run_checkout(session, run_id)
    if checkout is None:
        raise SymbolMapUnavailableError(
            "this call names no run that can be resolved to a checkout, so there is "
            "no tree to resolve an anchor against. To record the claim as it "
            f"stands, {UNGROUNDED_FALLBACK}"
        )
    workspace_id, root = checkout
    return anchor_payloads(resolve_anchors(session, workspace_id, root, names))


def _grounding_block(claim: ClaimGrounding | None, anchors: list[dict[str, Any]]) -> dict[str, Any]:
    """One hit's `grounding` object: the verdict, every anchor, and the reason.

    UNVERIFIABLE always carries a human-readable reason naming which cause it
    was. A verdict of UNVERIFIABLE with no reason is a defect, not a terse
    reply, so the no-run case states its own.
    """
    if claim is None:
        return {
            "verdict": GroundingVerdict.UNVERIFIABLE.value,
            "anchors": [
                {
                    "qualified_name": anchor["qualified_name"],
                    "status": GroundingVerdict.UNVERIFIABLE.value,
                    "location": "",
                }
                for anchor in anchors
            ],
            "reason": _NO_RUN_REASON,
        }
    return {
        "verdict": claim.verdict.value,
        "anchors": [
            {
                "qualified_name": check.qualified_name,
                "status": check.verdict.value,
                "location": check.location,
            }
            for check in claim.checks
        ],
        "reason": claim.reason,
    }


def _annotate_search(session: Session, result: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Add `grounding` to every ANCHORED graph hit, and to nothing else.

    An unanchored node's JSON is byte-identical to what it was, so no existing
    reader of this reply is touched. The verdicts are one batched call for the
    whole page: at 20 hits, a lookup per hit is twenty derivations of the same
    partition.
    """
    hits = result.get("graph") or []
    anchored = [(hit["id"], hit["anchors"]) for hit in hits if hit.get("anchors")]
    if not anchored:
        return result
    checkout = _run_checkout(session, run_id)
    graded: dict[str, ClaimGrounding] = {}
    if checkout is not None:
        workspace_id, root = checkout
        graded = dict(
            claim_verdicts(
                session,
                workspace_id,
                root,
                [(node_id, json.dumps(anchors)) for node_id, anchors in anchored],
            )
        )
    annotated = {
        node_id: _grounding_block(graded.get(node_id), anchors) for node_id, anchors in anchored
    }
    return {
        **result,
        "graph": [
            {**hit, "grounding": annotated[hit["id"]]} if hit["id"] in annotated else hit
            for hit in hits
        ],
    }


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
        anchors = _resolved_anchors(session, arguments, run_id)
        result = memory.append_learning(
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            content=arguments["content"],
            tags=arguments.get("tags"),
            title=arguments.get("title", ""),
            aliases=arguments.get("aliases"),
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            anchors=anchors,
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
        anchors = _resolved_anchors(session, arguments, run_id)
        result = memory.upsert_memory(
            node_id=arguments.get("node_id", ""),
            title=arguments["title"],
            body=arguments.get("body", ""),
            tags=arguments.get("tags"),
            ticket_id=arguments.get("ticket_id", ""),
            workspace_slug=arguments["workspace_slug"],
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            anchors=anchors,
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
        return json.dumps(_annotate_search(session, _search_envelope(result), run_id), indent=2)

    raise ValueError(f"{name} is a memory tool with no handler")
