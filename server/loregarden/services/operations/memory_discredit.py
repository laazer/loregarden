"""Discredit a memory node, or restore it: always with a reason, always in its history.

Written twice before. The Memory page's route required a reason and recorded it,
with the writer, in `memory_node_versions`. `loregarden_upsert_memory` took a
bare `discredited` flag beside the body and changed it with no reason and no
writer, so a node could drop out of every agent's briefing with nothing to say
why. The stricter rule holds on every transport: the upsert tool carries no
reason, so it refuses a change to the flag (`refuse_unreasoned_discredit`), and
this operation is the one way to make one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loregarden.services.memory_browser import MemoryNodeRecord, node_record
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

#: Where an agent is sent when it asks the upsert tool to change the flag.
DISCREDIT_ROUTE = "PUT /api/memory/nodes/{node_id}/discredited"


class DiscreditCall(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    node_id: str = Field(min_length=1)
    workspace_slug: str = ""
    discredited: bool
    #: Required: this changes what every future agent run is briefed with, and
    #: a change nobody can explain later is the thing history exists to prevent.
    reason: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class Discredited:
    node_id: str
    workspace_slug: str
    node: dict[str, Any]


def _writer(caller: Caller) -> str:
    """Who changed the flag, as the node's history records it."""
    return "operator" if caller.transport is Transport.REST else "agent"


def _discredit(session: Session, call: DiscreditCall, caller: Caller) -> Discredited:
    del session  # the graph lives in its own store, not the control-plane database
    node = AgentMemoryService.from_settings().set_discredited(
        node_id=call.node_id,
        workspace_slug=call.workspace_slug,
        discredited=call.discredited,
        reason=call.reason,
        writer=_writer(caller),
    )
    return Discredited(node_id=call.node_id, workspace_slug=call.workspace_slug, node=node)


def _page_reply(session: Session, done: Discredited) -> MemoryNodeRecord:
    """The full node record, read back, as every node endpoint answers."""
    detail = AgentMemoryService.from_settings().node_detail(
        node_id=done.node_id, workspace_slug=done.workspace_slug
    )
    return node_record(session, detail)


def refuse_unreasoned_discredit(
    memory: AgentMemoryService, *, node_id: str, workspace_slug: str, discredited: bool | None
) -> None:
    """Refuse an upsert that would change a node's `discredited` flag.

    The upsert carries no reason, and a change to the flag needs one. An absent
    flag, or restating the one a node already has (`false` on a new node),
    changes nothing, so it passes; the caller then upserts without it.
    """
    if discredited is None:
        return
    stored = memory.require_graph(workspace_slug).get_node(node_id) if node_id else None
    current = bool(stored and stored["discredited"])
    if discredited == current:
        return
    verb = "Discrediting" if discredited else "Restoring"
    raise ValueError(
        f"{verb} a memory node needs a reason, recorded in its history, and "
        "loregarden_upsert_memory carries none, so it does not change the flag. "
        f"An operator does it from the Memory page ({DISCREDIT_ROUTE}, with a reason). "
        "To correct what the node says, upsert it with the corrected body instead."
    )


MEMORY_DISCREDIT = (
    Operation(
        id=OperationId.MEMORY_DISCREDIT,
        effect=Effect.WRITE,
        args=DiscreditCall,
        handler=_discredit,
        unavailable_on={
            Transport.MCP: "no MCP tool carries the reason a change to the flag needs; "
            "loregarden_upsert_memory refuses one and names the Memory page",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
        replies={Transport.REST: _page_reply},
    ),
)
