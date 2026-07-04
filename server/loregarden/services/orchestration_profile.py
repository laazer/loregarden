"""Load workspace orchestration profiles from agent_context/orchestration/*.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from loregarden.config import settings
from loregarden.models.domain import OrchestrationDriver, Workspace
from loregarden.services.workspace_paths import resolve_agent_context_dir


class OrchestratorConfig(BaseModel):
    skill: str = "autopilot"
    pipeline: str = "agents/common_assets/pipeline_stages_v1.md"


class GatesConfig(BaseModel):
    enabled: bool = False
    commands: list[str] = Field(default_factory=list)


class SubagentsConfig(BaseModel):
    spawn_via: str = "cli"


class CallbacksConfig(BaseModel):
    mode: str = "api"


class OrchestrationProfile(BaseModel):
    slug: str
    name: str = ""
    driver: OrchestrationDriver = OrchestrationDriver.BUILTIN_AUTOPILOT
    workflow_template: str = "loregarden-tdd"
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    callbacks: CallbacksConfig = Field(default_factory=CallbacksConfig)
    max_stages_per_run: int = 0


def orchestration_dir(workspace: Workspace) -> Path:
    return resolve_agent_context_dir(workspace) / "orchestration"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_profile_from_path(path: Path) -> OrchestrationProfile:
    raw = _load_yaml(path)
    if "slug" not in raw:
        raw["slug"] = path.stem
    return OrchestrationProfile.model_validate(raw)


def resolve_orchestration_profile(workspace: Workspace) -> OrchestrationProfile:
    root = orchestration_dir(workspace)
    candidates: list[Path] = []
    if workspace.orchestration_profile_slug:
        candidates.append(root / f"{workspace.orchestration_profile_slug}.yaml")
    candidates.append(root / f"{workspace.slug}.yaml")
    candidates.append(root / "default.yaml")

    for path in candidates:
        if path.is_file():
            return load_profile_from_path(path)

    fallback = settings.repo_root / "agent_context" / "orchestration" / "loregarden.yaml"
    if fallback.is_file():
        return load_profile_from_path(fallback)

    return OrchestrationProfile(slug="default", name="Default Builtin Autopilot")


def list_profiles(workspace: Workspace) -> list[OrchestrationProfile]:
    root = orchestration_dir(workspace)
    profiles: list[OrchestrationProfile] = []
    if root.is_dir():
        for path in sorted(root.glob("*.yaml")):
            profiles.append(load_profile_from_path(path))
    if not profiles:
        profiles.append(resolve_orchestration_profile(workspace))
    return profiles
