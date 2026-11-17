"""The memory layer's status and a typed edge between two nodes, as operations.

Both were written twice, as MCP tools and as routes under `/api/memory/`. The
status read was the same on each. The edge differed only in how its type
arrived: the page's request model requires one of `MemoryRelationType`, while
an agent may leave it out and get `related`. The args model keeps the agent's
default; the route's own request model still requires the type, so the page
cannot send one without it.

The memory store is its own SQLite file, not the control plane's session, so
neither operation writes through `session`.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import MemoryRelationType
from loregarden.services.memory_links import parse_relation_type
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.operations.spec import Caller, Effect, Operation, OperationId
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore")


class MemoryStatusQuery(BaseModel):
    model_config = _LENIENT

    workspace_slug: str = ""


class MemoryRelationEdge(BaseModel):
    model_config = _LENIENT

    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    #: Text, parsed by `parse_relation_type`, so an unknown label is refused
    #: naming the vocabulary rather than with a bare enum error.
    relation_type: str = MemoryRelationType.RELATED.value
    workspace_slug: str = ""


def _status(_session: Session, query: MemoryStatusQuery, _caller: Caller) -> dict[str, Any]:
    return AgentMemoryService.from_settings().status(workspace_slug=query.workspace_slug)


def _create_relation(
    _session: Session, edge: MemoryRelationEdge, _caller: Caller
) -> dict[str, Any]:
    """Both nodes must exist, no self-edges, and restating an edge returns it."""
    return AgentMemoryService.from_settings().create_relation(
        source_id=edge.source_id,
        target_id=edge.target_id,
        relation_type=parse_relation_type(edge.relation_type),
        workspace_slug=edge.workspace_slug,
    )


MEMORY_OPERATIONS = (
    Operation(
        id=OperationId.MEMORY_STATUS,
        effect=Effect.READ,
        args=MemoryStatusQuery,
        handler=_status,
    ),
    Operation(
        id=OperationId.MEMORY_RELATION_CREATE,
        effect=Effect.WRITE,
        args=MemoryRelationEdge,
        handler=_create_relation,
    ),
)
