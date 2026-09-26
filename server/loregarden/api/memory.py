from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import LearningOutcomeRung
from loregarden.services.learning_confidence import LearningConfidence
from loregarden.services.learning_outcomes import confidence_for, ladder_counts
from loregarden.services.memory_briefing_telemetry import MemoryBriefingStats, briefing_stats
from loregarden.services.memory_config import (
    apply_memory_config,
    current_memory_config,
    memory_config_defaults,
)
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
