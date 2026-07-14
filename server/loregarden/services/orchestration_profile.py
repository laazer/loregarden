"""Load workspace orchestration profiles from agent_context/orchestration/*.yaml.

Orchestration profiles are loregarden's own configuration for how it drives a
workspace (gates, driver, orchestrator skill) — not something that lives in
the target repo being orchestrated. They're always resolved from loregarden's
own repo tree, keyed by workspace slug, the same way workflow templates are
already synced from loregarden's own agent_context/workflows/*.yaml regardless
of which workspace uses them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from loregarden.config import settings
from loregarden.models.domain import OrchestrationDriver, Workspace
from pydantic import BaseModel, Field


class OrchestratorConfig(BaseModel):
    skill: str = "autopilot"
    pipeline: str = "agents/common_assets/pipeline_stages_v1.md"


class GatesConfig(BaseModel):
    enabled: bool = False
    commands: list[str] = Field(default_factory=list)
    transition_script: str = ""


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


def orchestration_dir() -> Path:
    return settings.repo_root / "agent_context" / "orchestration"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_profile_from_path(path: Path) -> OrchestrationProfile:
    raw = _load_yaml(path)
    if "slug" not in raw:
        raw["slug"] = path.stem
    return OrchestrationProfile.model_validate(raw)


def resolve_orchestration_profile(workspace: Workspace) -> OrchestrationProfile:
    root = orchestration_dir()
    candidates: list[Path] = []
    if workspace.orchestration_profile_slug:
        candidates.append(root / f"{workspace.orchestration_profile_slug}.yaml")
    candidates.append(root / f"{workspace.slug}.yaml")
    candidates.append(root / "default.yaml")

    for path in candidates:
        if path.is_file():
            return load_profile_from_path(path)

    return OrchestrationProfile(slug="default", name="Default Builtin Autopilot")


def list_profiles(workspace: Workspace) -> list[OrchestrationProfile]:
    root = orchestration_dir()
    profiles: list[OrchestrationProfile] = []
    if root.is_dir():
        for path in sorted(root.glob("*.yaml")):
            profiles.append(load_profile_from_path(path))
    if not profiles:
        profiles.append(resolve_orchestration_profile(workspace))
    return profiles


def _profile_path_for_write(workspace: Workspace) -> Path:
    """The file a write should target — the same file resolve_orchestration_profile
    would have read, or a sensible default slug if none exists yet."""
    root = orchestration_dir()
    candidates = [
        root / f"{workspace.orchestration_profile_slug}.yaml"
        if workspace.orchestration_profile_slug
        else None,
        root / f"{workspace.slug}.yaml",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return candidates[0] or candidates[1]


def _write_yaml_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def update_gates_config(workspace: Workspace, gates: GatesConfig) -> OrchestrationProfile:
    """Persist `gates` into the workspace's orchestration profile YAML, leaving
    every other field in that file untouched (or creating a minimal file with
    just slug + gates if none existed yet)."""
    path = _profile_path_for_write(workspace)
    raw = _load_yaml(path) if path.is_file() else {}
    raw.setdefault("slug", workspace.orchestration_profile_slug or workspace.slug)
    raw["gates"] = gates.model_dump(mode="json")
    _write_yaml_atomic(path, raw)
    return load_profile_from_path(path)
