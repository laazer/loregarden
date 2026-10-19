"""Gate commands to offer for a workspace's toolchains — read-only.

Two ways in: by workspace, for its setup card, and by path, for the add dialog
before the workspace exists. Saving the chosen commands goes through the
profile's existing gates endpoint (`PUT /orchestration/workspaces/{slug}/profile/gates`),
and trying them first through its dry run, so there is one write path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from loregarden.db.session import get_session
from loregarden.services.gate_equivalence import running_equivalents
from loregarden.services.gate_presets import GateKind, ToolchainKey, ToolchainPreset, gate_presets
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.organization_gate_service import UnknownWorkspaceError, workspace_for_slug
from loregarden.services.workspace_paths import resolve_repo_path, resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/gate-presets", tags=["orchestration"])


class PresetCommandView(BaseModel):
    command: str
    label: str
    kind: GateKind
    default_on: bool
    #: The workspace's configured command that already does this, which may be
    #: spelled differently (`gate_equivalence`); None when it does not run.
    #: Always None for a path that is not a workspace yet.
    running_as: str | None = None


class ToolchainPresetView(BaseModel):
    key: ToolchainKey
    label: str
    directory: str
    detected: bool
    commands: list[PresetCommandView]
    note: str


class GatePresetsView(BaseModel):
    repo_root: str
    toolchains: list[ToolchainPresetView]


def _view(
    root: Path, presets: list[ToolchainPreset], running: dict[str, str] | None = None
) -> GatePresetsView:
    running = running or {}
    return GatePresetsView(
        repo_root=str(root),
        toolchains=[
            ToolchainPresetView(
                key=p.key,
                label=p.label,
                directory=p.directory,
                detected=p.detected,
                commands=[
                    PresetCommandView(
                        command=c.command,
                        label=c.label,
                        kind=c.kind,
                        default_on=c.default_on,
                        running_as=running.get(c.command),
                    )
                    for c in p.commands
                ],
                note=p.note,
            )
            for p in presets
        ],
    )


@router.get("", response_model=GatePresetsView)
def presets_for_path(path: str) -> GatePresetsView:
    """For a would-be workspace: what its path holds now, or every toolchain if nothing yet."""
    root = resolve_repo_path(path)
    return _view(root, gate_presets(root))


@router.get("/workspaces/{slug}", response_model=GatePresetsView)
def presets_for_workspace(slug: str, session: Session = Depends(get_session)) -> GatePresetsView:
    try:
        workspace = workspace_for_slug(session, slug)
    except UnknownWorkspaceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    root = resolve_workspace_root(workspace)
    presets = gate_presets(root)
    running = running_equivalents(
        (c.command for p in presets for c in p.commands),
        resolve_orchestration_profile(workspace).gates.commands,
        root,
    )
    return _view(root, presets, running)
