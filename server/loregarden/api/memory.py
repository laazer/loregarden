from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import LearningOutcomeRung, MemoryRelationType
from loregarden.services import memory_curation, memory_graph_health
from loregarden.services.learning_confidence import LearningConfidence
from loregarden.services.learning_outcomes import confidence_for, ladder_counts
from loregarden.services.memory_briefing_telemetry import MemoryBriefingStats, briefing_stats
from loregarden.services.memory_config import (
    apply_memory_config,
    current_memory_config,
    memory_config_defaults,
)
from loregarden.services.memory_curation import MergeConflictError, Proposal
from loregarden.services.memory_graph_health import GraphHealthReading, GraphHealthReport
from loregarden.services.memory_links import MemoryRelationError
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryNodeNotFoundError,
    MemoryWriteNotAppliedError,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryConfigBody(BaseModel):
    icloud_root: str = ""
    obsidian_vault_dir: str = ""
    obsidian_memory_subdir: str = Field(default="Loregarden/Memory", min_length=1)
    obsidian_learnings_subdir: str = Field(default="Loregarden/Learnings", min_length=1)
    obsidian_blogposts_subdir: str = Field(default="Loregarden/BlogPosts", min_length=1)
    obsidian_checkpoints_subdir: str = Field(default="Loregarden/Checkpoints", min_length=1)
    memory_sqlite_url: str = ""
    database_url: str = ""


def _memory_config_response() -> dict:
    service = AgentMemoryService.from_settings()
    return {
        "config": current_memory_config(),
        "status": service.status(),
        "defaults": memory_config_defaults(),
    }


@router.get("/status")
def memory_status(workspace_slug: str = "") -> dict:
    return AgentMemoryService.from_settings().status(workspace_slug=workspace_slug)


@router.get("/briefings")
def memory_briefings(
    window_days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> MemoryBriefingStats:
    """Briefing health over recent runs — built / empty / errored / absent.

    Denominated over `agent_runs`, not over briefing rows, so a recording seam
    that silently stopped writing reads as holes rather than as the last healthy
    numbers forever. `started_at IS NOT NULL` excludes runs that never reached
    prompt assembly; it is not exact — a run that died between `started_at` and
    the prompt build still counts as a hole, which errs toward reporting a hole
    that is not one, the safe direction for a health signal.

    An empty window is zeros with null timestamps, never a 404.
    """
    return briefing_stats(session, window_days=window_days)


@router.get("/config")
def get_memory_config() -> dict:
    return _memory_config_response()


@router.put("/config")
def put_memory_config(body: MemoryConfigBody) -> dict:
    try:
        apply_memory_config(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _memory_config_response()


class ConfidenceView(BaseModel):
    """A learning's Beta posterior (178). `observations == 0` means never observed."""

    mean: float
    lower_bound: float
    observations: int
    trusted: bool

    @classmethod
    def of(cls, confidence: LearningConfidence) -> "ConfidenceView":
        return cls(
            mean=confidence.mean,
            lower_bound=confidence.lower_bound,
            observations=confidence.observations,
            trusted=confidence.trusted,
        )


class DiscreditBody(BaseModel):
    # Stripped before validation, so a whitespace-only reason fails `min_length`.
    model_config = ConfigDict(str_strip_whitespace=True)

    workspace_slug: str = ""
    discredited: bool
    #: Required: this changes what every future agent run is briefed with, and
    #: a change nobody can explain later is the thing history exists to prevent.
    reason: str = Field(min_length=1, max_length=2000)


def _graph_call(action):
    """Run a graph operation, mapping its failures onto honest HTTP statuses."""
    try:
        return action()
    except MemoryNodeNotFoundError as exc:
        raise HTTPException(404, f"No memory node {exc.args[0]!r} in this workspace") from exc
    except MemoryRelationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except MergeConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MemoryWriteNotAppliedError as exc:
        raise HTTPException(500, f"The change was not applied: {exc}") from exc
    except ValueError as exc:
        # Graph not configured: a state of this installation, not a bad request.
        raise HTTPException(409, str(exc)) from exc


def _with_outcomes(session: Session, node: dict[str, Any]) -> dict[str, Any]:
    confidence = confidence_for(session, [node["id"]])[node["id"]]
    counts = ladder_counts(session, node["id"])
    return {
        **node,
        "confidence": ConfidenceView.of(confidence).model_dump(),
        "ladder": {rung.value: counts[rung] for rung in LearningOutcomeRung},
    }


@router.get("/nodes")
def memory_nodes(
    workspace_slug: str = "",
    include_discredited: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    """Graph nodes for the operator. `include_discredited` is explicit, never a
    default: every agent read path depends on discredited rows staying hidden."""
    service = AgentMemoryService.from_settings()
    nodes = _graph_call(
        lambda: service.list_graph_nodes(
            workspace_slug=workspace_slug,
            limit=limit,
            include_discredited=include_discredited,
        )
    )
    confidence = confidence_for(session, [node["id"] for node in nodes])
    return {
        "workspace_slug": workspace_slug,
        "include_discredited": include_discredited,
        "nodes": [
            {**node, "confidence": ConfidenceView.of(confidence[node["id"]]).model_dump()}
            for node in nodes
        ],
    }


@router.get("/nodes/{node_id}")
def memory_node(
    node_id: str, workspace_slug: str = "", session: Session = Depends(get_session)
) -> dict:
    """One node — discredited or not — with its version history and outcomes."""
    service = AgentMemoryService.from_settings()
    node = _graph_call(lambda: service.node_detail(node_id=node_id, workspace_slug=workspace_slug))
    return _with_outcomes(session, node)


@router.get("/nodes/{node_id}/lineage")
def memory_node_lineage(node_id: str, workspace_slug: str = "") -> dict:
    """What this learning replaced and what replaced it, oldest first."""
    service = AgentMemoryService.from_settings()
    steps = _graph_call(lambda: service.lineage(node_id=node_id, workspace_slug=workspace_slug))
    return {"node_id": node_id, "steps": steps}


@router.put("/nodes/{node_id}/discredited")
def set_memory_node_discredited(
    node_id: str, body: DiscreditBody, session: Session = Depends(get_session)
) -> dict:
    """Mark a node wrong, or restore it. The reason is stored with the change."""
    service = AgentMemoryService.from_settings()
    node = _graph_call(
        lambda: service.set_discredited(
            node_id=node_id,
            workspace_slug=body.workspace_slug,
            discredited=body.discredited,
            reason=body.reason,
            writer="operator",
        )
    )
    return _with_outcomes(session, node)


class WorkspaceBody(BaseModel):
    workspace_slug: str = ""


class RetitleBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    workspace_slug: str = ""
    title: str = Field(min_length=1, max_length=200)
    #: None keeps the node's aliases; the old title is added either way.
    aliases: list[str] | None = None
    reason: str = Field(min_length=1, max_length=2000)


class MergeBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    workspace_slug: str = ""
    absorbed_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)


def _health_report(session: Session, workspace_slug: str, *, record: bool) -> GraphHealthReport:
    service = AgentMemoryService.from_settings()
    current = _graph_call(lambda: memory_graph_health.measure(session, service, workspace_slug))
    history = memory_graph_health.snapshots(session, workspace_slug, limit=1)
    if record:
        memory_graph_health.record_snapshot(session, current)
    return memory_graph_health.compare(current, history[0] if history else None)


@router.get("/graph-health")
def memory_graph_health_report(
    workspace_slug: str = "", session: Session = Depends(get_session)
) -> GraphHealthReport:
    """The graph's shape now, against the last recorded snapshot. Records nothing."""
    return _health_report(session, workspace_slug, record=False)


@router.post("/graph-health/snapshots")
def record_memory_graph_health(
    body: WorkspaceBody, session: Session = Depends(get_session)
) -> GraphHealthReport:
    """Measure, append the reading to the dated log, and compare with the one before."""
    return _health_report(session, body.workspace_slug, record=True)


@router.get("/graph-health/snapshots")
def memory_graph_health_history(
    workspace_slug: str = "",
    limit: int = Query(default=24, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[GraphHealthReading]:
    return memory_graph_health.snapshots(session, workspace_slug, limit=limit)


@router.get("/proposals")
def memory_proposals(
    workspace_slug: str = "", session: Session = Depends(get_session)
) -> list[Proposal]:
    """What looks wrong in the graph, for a person to decide. Changes nothing."""
    service = AgentMemoryService.from_settings()
    return _graph_call(lambda: memory_curation.proposals(session, service, workspace_slug))


@router.put("/nodes/{node_id}/title")
def retitle_memory_node(
    node_id: str, body: RetitleBody, session: Session = Depends(get_session)
) -> dict:
    """Rename a learning; the old title is kept as an alias."""
    service = AgentMemoryService.from_settings()
    _graph_call(
        lambda: memory_curation.retitle(
            service,
            node_id=node_id,
            workspace_slug=body.workspace_slug,
            title=body.title,
            aliases=body.aliases,
            reason=body.reason,
            writer="operator",
        )
    )
    node = _graph_call(
        lambda: service.node_detail(node_id=node_id, workspace_slug=body.workspace_slug)
    )
    return _with_outcomes(session, node)


@router.post("/nodes/{node_id}/merge")
def merge_memory_nodes(
    node_id: str, body: MergeBody, session: Session = Depends(get_session)
) -> dict:
    """Fold another learning into this one, keeping every claim and name."""
    service = AgentMemoryService.from_settings()
    return _graph_call(
        lambda: memory_curation.merge(
            session,
            service,
            survivor_id=node_id,
            absorbed_id=body.absorbed_id,
            workspace_slug=body.workspace_slug,
            reason=body.reason,
            writer="operator",
        )
    )


class RelationBody(BaseModel):
    workspace_slug: str = ""
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    relation_type: MemoryRelationType


@router.post("/relations")
def create_memory_relation(body: RelationBody) -> dict:
    """Assert a typed edge — how an operator resolves a contradiction by marking
    one side superseded. Same rules as the MCP tool: both nodes must exist, no
    self-edges, and restating an edge returns it."""
    service = AgentMemoryService.from_settings()
    return _graph_call(
        lambda: service.create_relation(
            source_id=body.source_id,
            target_id=body.target_id,
            relation_type=body.relation_type,
            workspace_slug=body.workspace_slug,
        )
    )
