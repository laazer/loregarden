"""Maintenance for the memory graph: proposals from code, decisions from people.

The second-brain-os curator's rule, adopted whole: **mechanical findings are
reported, semantic changes are proposed, and nothing is merged, renamed or
withdrawn without a person saying yes.** An aggressive curator that merges on
its own judgement destroys work; one that only lists things produces a list
nobody acts on. So this module has two halves that never call each other:

`proposals` reads the graph and says what looks wrong — a learning still
wearing its ticket id as a title, two learnings that read as the same idea, a
contradiction nobody resolved, a learning that has gone cold. It writes
nothing, and it is deterministic: the same graph yields the same list.

`retitle` and `merge` act, only when an operator asks, each through the same
copy-on-write path every other write takes (180), each recording who and why.

Merging is the careful one. It keeps every claim — the absorbed body is
appended unless the survivor already contains it — keeps the absorbed title as
an alias so recall and old references still find it, re-points the absorbed
node's edges, records `survivor supersedes absorbed`, and withdraws the
absorbed node with the reason attached. Its observed outcomes (178) move to the
survivor: the runs were briefed with the same idea. Two learnings joined by a
`contradicts` edge are refused: if they disagree, they are not duplicates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from loregarden.models.domain import LearningApplication, MemoryRelationType, utcnow
from loregarden.services import term_overlap
from loregarden.services.memory_export import export_node_to_vault
from loregarden.services.memory_history import ChangeAttribution, NodeContent, record_superseded
from loregarden.services.memory_links import (
    insert_relation,
    is_generic_title,
    learning_title,
)
from loregarden.services.memory_store import (
    RECALL_CANDIDATE_CAP,
    AgentMemoryService,
    MemoryGraphStore,
    MemoryNodeNotFoundError,
    MemoryWriteNotAppliedError,
    clean_aliases,
)
from pydantic import BaseModel
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Term-set Jaccard at or above which two learnings are proposed as duplicates.
DUPLICATE_SIMILARITY = 0.6
_MAX_DUPLICATE_PAIRS = 20
COLD_AFTER_DAYS = 90
_MERGE_SEPARATOR = "\n\n— merged from “{title}” —\n\n"


class ProposalKind(str, Enum):
    GENERIC_TITLE = "generic_title"
    NEAR_DUPLICATE = "near_duplicate"
    CONTESTED = "contested"
    COLD = "cold"


class Proposal(BaseModel):
    kind: ProposalKind
    node_ids: list[str]
    titles: list[str]
    #: What the operator is being asked to decide, in a sentence.
    reason: str
    suggested_title: str | None = None
    similarity: float | None = None


class MergeConflictError(ValueError):
    """A merge that must not happen as asked. The message says why."""


def _terms(node: dict[str, Any]) -> set[str]:
    return term_overlap.terms(
        " ".join([node["title"], *node.get("aliases", []), node["body"][:600]])
    )


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def proposals(
    session: Session,
    memory: AgentMemoryService,
    workspace_slug: str,
    *,
    now: datetime | None = None,
) -> list[Proposal]:
    """Everything worth a person's decision in one workspace graph. Writes nothing."""
    graph = memory.require_graph(workspace_slug)
    nodes = graph.list_nodes(workspace_slug=workspace_slug, limit=RECALL_CANDIDATE_CAP)
    by_id = {node["id"]: node for node in nodes}
    with graph.connection() as conn:
        edges = [
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT source_id, target_id, relation_type FROM memory_relations"
            )
            if row[0] in by_id and row[1] in by_id
        ]
    found: list[Proposal] = []
    found += _generic_titles(nodes)
    found += _contested(edges, by_id)
    found += _duplicates(nodes, edges)
    found += _cold(session, nodes, edges, now or utcnow())
    return found


def _generic_titles(nodes: list[dict[str, Any]]) -> list[Proposal]:
    out = []
    for node in nodes:
        if not is_generic_title(node["title"]) or not node["body"].strip():
            continue
        suggestion, _ = learning_title("", node["body"])
        out.append(
            Proposal(
                kind=ProposalKind.GENERIC_TITLE,
                node_ids=[node["id"]],
                titles=[node["title"]],
                reason="Titled by its ticket, not by what it says. Recall and the related-"
                "learnings digest show this title to agents.",
                suggested_title=suggestion,
            )
        )
    return out


def _superseded(edges: list[tuple[str, str, str]]) -> set[str]:
    return {target for _, target, kind in edges if kind == MemoryRelationType.SUPERSEDES}


def _contested(edges: list[tuple[str, str, str]], by_id: dict[str, dict]) -> list[Proposal]:
    superseded = _superseded(edges)
    return [
        Proposal(
            kind=ProposalKind.CONTESTED,
            node_ids=[source, target],
            titles=[by_id[source]["title"], by_id[target]["title"]],
            reason="These contradict each other and neither is marked superseded. Agents are "
            "briefed with both. Supersede one if the evidence settled it; leave it if not.",
        )
        for source, target, kind in edges
        if kind == MemoryRelationType.CONTRADICTS and not {source, target} & superseded
    ]


def _duplicates(nodes: list[dict[str, Any]], edges: list[tuple[str, str, str]]) -> list[Proposal]:
    superseded = _superseded(edges)
    disagree = {
        frozenset((source, target))
        for source, target, kind in edges
        if kind == MemoryRelationType.CONTRADICTS
    }
    candidates = [node for node in nodes if node["id"] not in superseded]
    terms = {node["id"]: _terms(node) for node in candidates}
    pairs = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if frozenset((left["id"], right["id"])) in disagree:
                continue
            similarity = _jaccard(terms[left["id"]], terms[right["id"]])
            if similarity >= DUPLICATE_SIMILARITY:
                pairs.append((similarity, left, right))
    pairs.sort(key=lambda item: (-item[0], item[1]["id"], item[2]["id"]))
    return [
        Proposal(
            kind=ProposalKind.NEAR_DUPLICATE,
            node_ids=[left["id"], right["id"]],
            titles=[left["title"], right["title"]],
            reason="These read as the same idea. Merging keeps every claim and both names; "
            "check they are not a general case and a specific one first.",
            similarity=round(similarity, 2),
        )
        for similarity, left, right in pairs[:_MAX_DUPLICATE_PAIRS]
    ]


def _cold(
    session: Session,
    nodes: list[dict[str, Any]],
    edges: list[tuple[str, str, str]],
    now: datetime,
) -> list[Proposal]:
    linked = {node for source, target, _ in edges for node in (source, target)}
    cutoff = (now - timedelta(days=COLD_AFTER_DAYS)).replace(microsecond=0).isoformat()
    stale = [n for n in nodes if n["id"] not in linked and n["updated_at"] < cutoff]
    if not stale:
        return []
    surfaced = set(
        session.exec(
            select(LearningApplication.node_id).where(
                col(LearningApplication.node_id).in_([n["id"] for n in stale])
            )
        ).all()
    )
    return [
        Proposal(
            kind=ProposalKind.COLD,
            node_ids=[node["id"]],
            titles=[node["title"]],
            reason=f"Unlinked, untouched for {COLD_AFTER_DAYS}+ days, and never briefed into a "
            "run. Link it, update it, or withdraw it (discredit is restorable).",
        )
        for node in stale
        if node["id"] not in surfaced
    ]


# ---------------------------------------------------------------------------
# Actions — each on explicit operator request only.
# ---------------------------------------------------------------------------


def retitle(
    memory: AgentMemoryService,
    *,
    node_id: str,
    workspace_slug: str,
    title: str,
    aliases: Sequence[str] | None,
    reason: str,
    writer: str,
) -> dict[str, Any]:
    """Rename a node. The old title becomes an alias, so nothing that found it
    by that name stops finding it."""
    graph = memory.require_graph(workspace_slug)
    node = graph.get_node(node_id)
    if node is None:
        raise MemoryNodeNotFoundError(node_id)
    keep = list(node["aliases"] if aliases is None else aliases)
    graph.upsert_node(
        node_id=node_id,
        title=title,
        body=node["body"],
        tags=node["tags"],
        ticket_id=node["ticket_id"],
        workspace_slug=node["workspace_slug"],
        node_type=node["node_type"],
        aliases=[*keep, node["title"]],
        attribution=ChangeAttribution(writer=writer, note=reason),
    )
    stored = graph.get_node(node_id)
    if stored is None or stored["title"] != title:
        raise MemoryWriteNotAppliedError("the new title did not persist")
    export_node_to_vault(memory, node=stored)
    return stored


def _merged_body(survivor: dict[str, Any], absorbed: dict[str, Any]) -> str:
    extra = absorbed["body"].strip()
    if not extra or extra in survivor["body"]:
        return survivor["body"]
    return survivor["body"].rstrip() + _MERGE_SEPARATOR.format(title=absorbed["title"]) + extra


def _repoint_edges(conn: sqlite3.Connection, *, absorbed: str, survivor: str, now: str) -> int:
    """Move the absorbed node's edges onto the survivor. Returns edges moved."""
    moved = 0
    rows = conn.execute(
        "SELECT id, source_id, target_id, relation_type FROM memory_relations "
        "WHERE source_id = ? OR target_id = ?",
        (absorbed, absorbed),
    ).fetchall()
    for relation_id, source, target, kind in rows:
        conn.execute("DELETE FROM memory_relations WHERE id = ?", (relation_id,))
        source = survivor if source == absorbed else source
        target = survivor if target == absorbed else target
        if source == target:
            continue
        insert_relation(
            conn,
            source_id=source,
            target_id=target,
            relation_type=MemoryRelationType(kind),
            created_at=now,
        )
        moved += 1
    return moved


def _rekey_applications(session: Session, *, absorbed: str, survivor: str) -> int:
    """Carry observed outcomes to the survivor, skipping runs it already has."""
    have = set(
        session.exec(
            select(LearningApplication.run_id).where(LearningApplication.node_id == survivor)
        ).all()
    )
    moved = 0
    for row in session.exec(
        select(LearningApplication).where(LearningApplication.node_id == absorbed)
    ).all():
        if row.run_id in have:
            continue
        row.node_id = survivor
        session.add(row)
        moved += 1
    return moved


def merge(
    session: Session,
    memory: AgentMemoryService,
    *,
    survivor_id: str,
    absorbed_id: str,
    workspace_slug: str,
    reason: str,
    writer: str,
) -> dict[str, Any]:
    """Fold `absorbed_id` into `survivor_id`. See the module docstring for what is kept."""
    if survivor_id == absorbed_id:
        raise MergeConflictError("A learning cannot be merged into itself.")
    graph = memory.require_graph(workspace_slug)
    survivor = _live_node(graph.get_node(survivor_id), survivor_id)
    absorbed = _live_node(graph.get_node(absorbed_id), absorbed_id)

    outcomes_moved = _rekey_applications(session, absorbed=absorbed_id, survivor=survivor_id)
    now = utcnow().replace(microsecond=0).isoformat()
    body = _merged_body(survivor, absorbed)
    aliases = clean_aliases(
        [*survivor["aliases"], absorbed["title"], *absorbed["aliases"]], survivor["title"]
    )
    tags = list(dict.fromkeys([*survivor["tags"], *absorbed["tags"]]))
    note = f"merged “{absorbed['title']}” into “{survivor['title']}”: {reason}"
    attribution = ChangeAttribution(writer=writer, note=note)
    try:
        edges_moved = _merge_in_shard(
            graph,
            survivor=survivor,
            absorbed=absorbed,
            merged=NodeContent(
                title=survivor["title"],
                body=body,
                tags_json=json.dumps(tags),
                discredited=False,
                aliases_json=json.dumps(aliases),
            ),
            now=now,
            attribution=attribution,
        )
    except Exception:
        # Nothing in the shard was committed (its transaction rolled back), so
        # the outcome re-key must not be either.
        session.rollback()
        raise
    # The shard is committed; the outcome re-key commits after it. A failure
    # here leaves the merge done and the outcomes on the absorbed node, which is
    # survivable — and it is logged and raised, not swallowed.
    try:
        session.commit()
    except Exception:
        logger.exception("memory merge: outcome re-key failed after %s was merged", absorbed_id)
        raise
    merged = graph.get_node(survivor_id)
    withdrawn = graph.get_node(absorbed_id)
    if merged is None or withdrawn is None or not withdrawn["discredited"]:
        raise MemoryWriteNotAppliedError("the merge did not persist")
    export_node_to_vault(memory, node=merged)
    export_node_to_vault(memory, node=withdrawn)
    return {
        "survivor": merged,
        "absorbed_id": absorbed_id,
        "aliases_added": [a for a in aliases if a not in survivor["aliases"]],
        "edges_moved": edges_moved,
        "outcomes_moved": outcomes_moved,
    }


def _live_node(node: dict[str, Any] | None, node_id: str) -> dict[str, Any]:
    if node is None:
        raise MemoryNodeNotFoundError(node_id)
    if node["discredited"]:
        raise MergeConflictError(f"“{node['title']}” is discredited; restore it before merging.")
    return node


def _merge_in_shard(
    graph: MemoryGraphStore,
    *,
    survivor: dict[str, Any],
    absorbed: dict[str, Any],
    merged: NodeContent,
    now: str,
    attribution: ChangeAttribution,
) -> int:
    """The shard half of a merge, in one transaction. Returns edges moved."""
    survivor_id, absorbed_id = survivor["id"], absorbed["id"]
    with graph.connection() as conn:
        between = conn.execute(
            "SELECT 1 FROM memory_relations WHERE relation_type = ? AND "
            "((source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?))",
            (
                MemoryRelationType.CONTRADICTS.value,
                survivor_id,
                absorbed_id,
                absorbed_id,
                survivor_id,
            ),
        ).fetchone()
        if between:
            raise MergeConflictError(
                "These two contradict each other, so they are not duplicates. Resolve the "
                "contradiction (supersede one) instead of merging."
            )
        prior = {
            node_id: conn.execute(
                "SELECT title, body, tags_json, updated_at, discredited, aliases_json "
                "FROM memory_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            for node_id in (survivor_id, absorbed_id)
        }
        record_superseded(
            conn,
            node_id=survivor_id,
            prior=prior[survivor_id],
            incoming=merged,
            superseded_at=now,
            attribution=attribution,
        )
        conn.execute(
            "UPDATE memory_nodes SET body = ?, tags_json = ?, aliases_json = ?, updated_at = ? "
            "WHERE id = ?",
            (merged.body, merged.tags_json, merged.aliases_json, now, survivor_id),
        )
        edges_moved = _repoint_edges(conn, absorbed=absorbed_id, survivor=survivor_id, now=now)
        insert_relation(
            conn,
            source_id=survivor_id,
            target_id=absorbed_id,
            relation_type=MemoryRelationType.SUPERSEDES,
            created_at=now,
        )
        record_superseded(
            conn,
            node_id=absorbed_id,
            prior=prior[absorbed_id],
            incoming=NodeContent(
                title=absorbed["title"],
                body=absorbed["body"],
                tags_json=prior[absorbed_id]["tags_json"],
                discredited=True,
                aliases_json=prior[absorbed_id]["aliases_json"],
            ),
            superseded_at=now,
            attribution=attribution,
        )
        conn.execute(
            "UPDATE memory_nodes SET discredited = 1, updated_at = ? WHERE id = ?",
            (now, absorbed_id),
        )
    return edges_moved
