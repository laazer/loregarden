"""Read models for browsing the memory graph (766), and the one node record.

Two responses, both typed:

- `KnowledgeGraph` — a window of a workspace graph for the knowledge browser:
  the nodes, the recorded edges *between those nodes only*, counts, and which
  reader produced them. "The graph is not configured" is its own answer
  (`configured=False`), not an empty list: a browser that renders it as "no
  memories yet" sends the operator looking for learnings that were never
  going to be written.
- `MemoryNodeRecord` — one node in full, as every node endpoint returns it:
  body, provenance, history, edges, successors and observed confidence. One
  model for all of them, so a response missing a field is a server error
  rather than a client that crashes reading it.

Code groundings (lg-code-knowledge-368), drift states (369), dual timelines
(661) and decision lineage (lg-improved-memory-309) will add fields here when
those records exist; nothing below stands in for them.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import (
    KnowledgeGraphSource,
    LearningOutcomeRung,
    MemoryNodeType,
    MemoryOriginKind,
    RelationDirection,
    utcnow,
)
from loregarden.services.learning_confidence import LearningConfidence
from loregarden.services.learning_outcomes import confidence_for, ladder_counts
from loregarden.services.memory_store import AgentMemoryService
from pydantic import BaseModel
from sqlmodel import Session

_EXCERPT_CHARS = 160


class ConfidenceView(BaseModel):
    """A learning's Beta posterior (178). `observations == 0` means never observed."""

    mean: float
    lower_bound: float
    observations: int
    trusted: bool

    @classmethod
    def of(cls, confidence: LearningConfidence) -> ConfidenceView:
        return cls(
            mean=confidence.mean,
            lower_bound=confidence.lower_bound,
            observations=confidence.observations,
            trusted=confidence.trusted,
        )


class GraphNode(BaseModel):
    id: str
    title: str
    excerpt: str
    node_type: MemoryNodeType
    tags: list[str]
    ticket_id: str
    discredited: bool
    created_at: str
    updated_at: str
    origin_kind: MemoryOriginKind | None
    origin_ref: str | None


class GraphRelation(BaseModel):
    id: str
    source_id: str
    target_id: str
    relation_type: str
    created_at: str


class GraphCounts(BaseModel):
    entities: int
    links: int


class KnowledgeGraph(BaseModel):
    workspace_slug: str
    #: False when no memory graph is configured for this workspace at all.
    configured: bool
    source: KnowledgeGraphSource
    query: str
    node_type: MemoryNodeType | None
    #: Whether discredited nodes were asked for. Off by default, as on every read path.
    include_discredited: bool
    nodes: list[GraphNode]
    #: Only edges whose source and target are both in `nodes`.
    relations: list[GraphRelation]
    counts: GraphCounts
    #: Live nodes per type across the whole workspace, for the type chips.
    type_counts: dict[str, int]
    #: True when `limit` cut the window short.
    truncated: bool
    checked_at: datetime


class NodeVersion(BaseModel):
    version: int
    title: str
    body: str
    discredited: bool
    aliases: list[str] | None
    became_current_at: str
    superseded_at: str
    superseded_by: str | None
    change_note: str | None


class NodeRelation(BaseModel):
    id: str
    relation_type: str
    direction: RelationDirection
    node_id: str
    title: str
    discredited: bool


class NodeRef(BaseModel):
    id: str
    title: str


class MemoryNodeRecord(BaseModel):
    id: str
    title: str
    body: str
    tags: list[str]
    aliases: list[str]
    ticket_id: str
    workspace_slug: str
    node_type: MemoryNodeType
    discredited: bool
    created_at: str
    updated_at: str
    origin_kind: MemoryOriginKind | None
    origin_ref: str | None
    versions: list[NodeVersion]
    relations: list[NodeRelation]
    superseded_by: list[NodeRef]
    confidence: ConfidenceView
    ladder: dict[LearningOutcomeRung, int]


def _graph_node(row: dict) -> GraphNode:
    excerpt = " ".join(row["body"].split())[:_EXCERPT_CHARS]
    return GraphNode(excerpt=excerpt, **{k: row[k] for k in GraphNode.model_fields if k in row})


def knowledge_graph(
    memory: AgentMemoryService,
    *,
    workspace_slug: str,
    node_type: MemoryNodeType | None,
    query: str,
    limit: int,
    include_discredited: bool = False,
) -> KnowledgeGraph:
    """A window of one workspace graph. Never creates a graph file to answer."""
    source = KnowledgeGraphSource.SEARCH if query.strip() else KnowledgeGraphSource.LIST
    empty = KnowledgeGraph(
        workspace_slug=workspace_slug,
        configured=True,
        source=source,
        query=query,
        node_type=node_type,
        include_discredited=include_discredited,
        nodes=[],
        relations=[],
        counts=GraphCounts(entities=0, links=0),
        type_counts={},
        truncated=False,
        checked_at=utcnow(),
    )
    path = memory.graph_path(workspace_slug)
    if path is None:
        return empty.model_copy(update={"configured": False})
    if not path.is_file():
        # Configured, never written. Opening it would create it as a side effect
        # of a read; there is nothing in it either way.
        return empty
    graph = memory.require_graph(workspace_slug)
    if source is KnowledgeGraphSource.SEARCH:
        rows = graph.search(
            query,
            workspace_slug=workspace_slug,
            limit=limit,
            node_type=node_type,
            include_discredited=include_discredited,
        )
    else:
        rows = graph.list_nodes(
            workspace_slug=workspace_slug,
            limit=limit,
            node_type=node_type,
            include_discredited=include_discredited,
        )
    nodes = [_graph_node(row) for row in rows]
    relations = [GraphRelation(**edge) for edge in graph.list_relations([n.id for n in nodes])]
    return empty.model_copy(
        update={
            "nodes": nodes,
            "relations": relations,
            "counts": GraphCounts(entities=len(nodes), links=len(relations)),
            "type_counts": graph.node_type_counts(workspace_slug),
            "truncated": len(nodes) >= limit,
        }
    )


def node_record(session: Session, detail: dict) -> MemoryNodeRecord:
    """`AgentMemoryService.node_detail` output, with observed outcomes, as the record."""
    node_id = detail["id"]
    counts = ladder_counts(session, node_id)
    return MemoryNodeRecord.model_validate(
        {
            **detail,
            "confidence": ConfidenceView.of(confidence_for(session, [node_id])[node_id]),
            "ladder": counts,
        }
    )
