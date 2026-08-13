"""Load workspace orchestration profiles from agent_context/orchestration/*.yaml.

Orchestration profiles are loregarden's own configuration for how it drives a
workspace (gates, driver, orchestrator skill) — not something that lives in
the target repo being orchestrated. They're always resolved from loregarden's
own repo tree, keyed by workspace slug, the same way workflow templates are
already synced from loregarden's own agent_context/workflows/*.yaml regardless
of which workspace uses them.
"""

from __future__ import annotations

import io
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import OrchestrationDriver, Workspace
from pydantic import BaseModel, Field
from ruamel.yaml import YAML


class OrchestratorConfig(BaseModel):
    skill: str = "autopilot"
    pipeline: str = "agents/common_assets/pipeline_stages_v1.md"


class GatesConfig(BaseModel):
    enabled: bool = False
    commands: list[str] = Field(default_factory=list)
    transition_script: str = ""
    # When a transition gate (lint/format/typecheck) fails, try to repair it
    # automatically before pulling in a human. `autofix_commands` are mechanical
    # fixers (e.g. `ruff check --fix server/`, `ruff format server/`,
    # `oxlint --fix client/`) run best-effort in the workspace root; the gate is
    # then re-run. If it still fails and `autofix_agent_fallback` is on, the
    # stage is rerouted back to its own agent — with the gate errors in its
    # context — for up to `autofix_max_agent_attempts` inline retries before
    # falling back to blocking for a human.
    autofix_commands: list[str] = Field(default_factory=list)
    autofix_agent_fallback: bool = True
    autofix_max_agent_attempts: int = 2


class GitAutomationConfig(BaseModel):
    """What a run is allowed to do with git once its work is done.

    Each step is its own switch because each one is a different amount of
    trust. The pipeline runs them in the order below and stops at the first
    switch that is off — pushing without committing is meaningless, and
    merging without a PR has nothing to merge — so turning off an early step
    disables everything after it regardless of its own flag.

    ``worktree`` is separate from the rest: it isolates the run's checkout so
    parallel runs stop fighting over one working tree. Everything else is
    about what happens to that work afterwards.
    """

    #: Run in a dedicated git worktree instead of the shared workspace root.
    worktree: bool = True
    commit: bool = False
    push: bool = False
    open_pr: bool = False
    #: `gh pr merge --auto`, so the PR lands when its checks pass.
    auto_merge: bool = False
    #: On a merge conflict, hand the conflicted files to an implementer agent
    #: rather than blocking. Independent of auto_merge: a conflict can surface
    #: from the pre-merge check even when nothing is auto-merging.
    auto_resolve_conflicts: bool = False
    #: How many times the resolver agent may try before the ticket blocks.
    max_conflict_resolve_attempts: int = 2
    #: Branch PRs target, and the branch a worktree is cut from.
    base_branch: str = "main"


class BoundaryConfig(BaseModel):
    """Whether a boundary mismatch stops a stage or is merely recorded.

    Off by default, and deliberately so for its first milestone. The verdict is
    written for every dispatch either way, so switching this on later is a
    decision made against a measured mismatch rate rather than a guess. Turning a
    brand-new precondition into a hard stop across every stage in every workspace
    on the day it ships converts a diagnostic into an outage.
    """

    enforce: bool = False


class RetryBudgetConfig(BaseModel):
    enabled: bool = True
    max_attempts_per_stage: int = 5


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
    git: GitAutomationConfig = Field(default_factory=GitAutomationConfig)
    boundary: BoundaryConfig = Field(default_factory=BoundaryConfig)
    retry_budget: RetryBudgetConfig = Field(default_factory=RetryBudgetConfig)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    callbacks: CallbacksConfig = Field(default_factory=CallbacksConfig)
    max_stages_per_run: int = 0
    # Subtree-wide cap on stages completed across a top-level auto_approve run
    # AND every descendant it recurses into (ticket 164) — unlike
    # max_stages_per_run, which resets per nested execute() call and so cannot
    # bound a whole unattended subtree run. 0 = unlimited, same convention.
    max_subtree_stages_per_run: int = 0


def orchestration_dir() -> Path:
    return settings.repo_root / "agent_context" / "orchestration"


def _round_trip_yaml() -> YAML:
    """A loader that keeps comments and formatting attached to the parsed data.

    PyYAML's loader produces a plain dict, so the comments never survive to the
    dumper — saving a profile from the UI erased the prose explaining why its
    gate policy is what it is. The indent settings match the hand-written files
    so a save is not also a whitespace reflow of every list in them.
    """
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    return yaml_rt


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return _round_trip_yaml().load(f) or {}


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
    buffer = io.StringIO()
    _round_trip_yaml().dump(data, buffer)
    tmp.write_text(buffer.getvalue(), encoding="utf-8")
    tmp.replace(path)


def _merge_block(raw: dict, key: str, values: dict) -> None:
    """Update `raw[key]`'s entries in place, rather than replacing the block.

    This is the half of comment preservation that the loader cannot do for you.
    ruamel attaches each comment to the node it precedes, so assigning
    ``raw["gates"] = {...}`` throws that node away and takes every comment
    inside the block with it — round-trip loader or not. Updating the existing
    mapping's keys leaves the nodes, and their comments, in place.
    """
    block = raw.get(key)
    if not isinstance(block, dict):
        raw[key] = dict(values)
        return
    for name, value in values.items():
        block[name] = value


def update_gates_config(workspace: Workspace, gates: GatesConfig) -> OrchestrationProfile:
    """Persist `gates` into the workspace's orchestration profile YAML, leaving
    every other field in that file untouched (or creating a minimal file with
    just slug + gates if none existed yet)."""
    path = _profile_path_for_write(workspace)
    raw = _load_yaml(path) if path.is_file() else {}
    raw.setdefault("slug", workspace.orchestration_profile_slug or workspace.slug)
    existing_gates = raw.get("gates") or {}
    new_gates = gates.model_dump(mode="json")
    # The Gates editor only manages enabled/commands/transition_script; preserve
    # any autofix_* settings already in the file so saving from the UI doesn't
    # silently wipe a hand-configured self-fix policy.
    for key in ("autofix_commands", "autofix_agent_fallback", "autofix_max_agent_attempts"):
        if key in existing_gates:
            new_gates[key] = existing_gates[key]
    _merge_block(raw, "gates", new_gates)
    _write_yaml_atomic(path, raw)
    return load_profile_from_path(path)


def update_git_config(workspace: Workspace, git: GitAutomationConfig) -> OrchestrationProfile:
    """Persist `git` into the workspace's orchestration profile YAML, leaving
    every other field in that file untouched."""
    path = _profile_path_for_write(workspace)
    raw = _load_yaml(path) if path.is_file() else {}
    raw.setdefault("slug", workspace.orchestration_profile_slug or workspace.slug)
    _merge_block(raw, "git", git.model_dump(mode="json"))
    _write_yaml_atomic(path, raw)
    return load_profile_from_path(path)
