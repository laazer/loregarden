"""What a workspace can run for a reviewer, and which of it fits a ticket.

``.loregarden/review.yaml`` names the things worth running and the paths each
one shows:

    targets:
      - name: game
        paths: ["scenes/*", "scripts/*", "project.godot"]
        prepare: ["godot", "--headless", "--import", "--path", "{worktree}"]
        command: ["godot", "--path", "{worktree}"]
        scene_command: ["godot", "--path", "{worktree}", "res://{scene}"]
      - name: backend tests
        kind: check
        paths: ["backend/*"]
        cwd: backend
        command: ["uv", "run", "pytest", "-q"]

A repository holds more than one thing to look at. One command per workspace
started the game for a ticket that only changed a web backend, which told the
reviewer nothing about the ticket. So a ticket is offered the targets whose
``paths`` match a file it changes. ``paths`` are globs over repository-relative
paths, and ``*`` crosses directories: ``backend/*`` covers everything under it.

``launch:`` is one target with no ``paths``, named ``build``. It is offered for
changes no named target claims, so a workspace with one thing to run needs
nothing else.

A ``check`` is a command whose exit code is the answer (a test suite); an
``app`` runs until it is closed, and exits however it exits.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import yaml
from loregarden.models.domain import Workspace
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(".loregarden") / "review.yaml"
#: The name ``launch:`` is offered under.
DEFAULT_TARGET = "build"


class LaunchError(ValueError):
    """The launch cannot start; the message says why and what to fix."""


class LaunchKind(StrEnum):
    APP = "app"
    CHECK = "check"


class LaunchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LaunchKind = LaunchKind.APP
    #: Where both commands run, relative to the review checkout.
    cwd: str = ""
    prepare: list[str] = Field(default_factory=list)
    command: list[str] = Field(min_length=1)
    scene_command: list[str] = Field(default_factory=list)

    @field_validator("cwd")
    @classmethod
    def _inside_the_checkout(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"cwd {value!r} must be a path inside the repository")
        return value


class LaunchTarget(LaunchSpec):
    name: str = Field(min_length=1)
    paths: list[str] = Field(min_length=1)

    def covers(self, files: list[str]) -> bool:
        return any(fnmatchcase(name, glob) for name in files for glob in self.paths)


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch: LaunchSpec | None = None
    targets: list[LaunchTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names_something_to_run(self) -> ReviewConfig:
        if self.launch is None and not self.targets:
            raise ValueError("name something to run: `launch` or `targets`")
        names = [target.name for target in self.targets]
        if self.launch is not None:
            names.append(DEFAULT_TARGET)
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"target names repeat: {', '.join(repeated)}")
        return self

    def specs(self) -> dict[str, LaunchSpec]:
        """Every target by name, in file order, ``launch`` last."""
        by_name: dict[str, LaunchSpec] = {target.name: target for target in self.targets}
        if self.launch is not None:
            by_name[DEFAULT_TARGET] = self.launch
        return by_name


class LaunchMatch(StrEnum):
    #: ``targets`` run what the ticket changes.
    MATCHED = "matched"
    #: The ticket's changes are known and nothing here runs them.
    NO_MATCH = "no_match"
    #: The ticket's changes could not be read, so every target is offered.
    UNKNOWN = "unknown"
    #: The workspace has no readable config; its launch status says why.
    UNCONFIGURED = "unconfigured"


class LaunchOffer(BaseModel):
    """What a reviewer can run to see one ticket."""

    match: LaunchMatch
    targets: list[str] = Field(default_factory=list)


class TargetView(BaseModel):
    name: str
    kind: LaunchKind


class ConfigStatus(BaseModel):
    """Whether a workspace can launch from the review page, and why not."""

    configured: bool
    path: str
    error: str = ""
    targets: list[TargetView] = Field(default_factory=list)


def _config_path(workspace: Workspace) -> Path:
    return resolve_workspace_root(workspace) / CONFIG_FILE


def load_review_config(workspace: Workspace) -> ReviewConfig | None:
    """The workspace's launch config, None when it has none. Raises on a bad file."""
    path = _config_path(workspace)
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ReviewConfig.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise LaunchError(f"{path} is not a valid review config: {exc}") from exc


def config_status(workspace: Workspace) -> ConfigStatus:
    path = _config_path(workspace)
    try:
        config = load_review_config(workspace)
    except LaunchError as exc:
        return ConfigStatus(configured=False, path=str(path), error=str(exc))
    if config is None:
        return ConfigStatus(configured=False, path=str(path))
    return ConfigStatus(
        configured=True,
        path=str(path),
        targets=[TargetView(name=name, kind=spec.kind) for name, spec in config.specs().items()],
    )


def readable_config(workspace: Workspace) -> ReviewConfig | None:
    """``load_review_config``, with a broken file reported rather than raised.

    For the queue: one workspace's bad file must not take the page down. The
    page asks for that workspace's launch status too, and shows the error.
    """
    try:
        return load_review_config(workspace)
    except LaunchError as exc:
        logger.warning("review config for %s: %s", workspace.slug, exc)
        return None


def launch_offer(config: ReviewConfig | None, files: list[str] | None) -> LaunchOffer:
    """The targets that show a change to ``files``.

    No files reads as unknown, not as "nothing to run": a branch whose changes
    already reached the base diffs empty, and its ticket did change something.
    """
    if config is None:
        return LaunchOffer(match=LaunchMatch.UNCONFIGURED)
    if not files:
        return LaunchOffer(match=LaunchMatch.UNKNOWN, targets=list(config.specs()))
    names = [target.name for target in config.targets if target.covers(files)]
    if not names and config.launch is not None:
        names = [DEFAULT_TARGET]
    if not names:
        return LaunchOffer(match=LaunchMatch.NO_MATCH)
    return LaunchOffer(match=LaunchMatch.MATCHED, targets=names)


def choose_target(config: ReviewConfig, target: str, scene: str) -> tuple[str, LaunchSpec]:
    """The named target; unnamed, the one that opens scenes, or the only one."""
    specs = config.specs()
    if target:
        if target not in specs:
            raise LaunchError(f"{CONFIG_FILE} has no target {target!r}; it has {', '.join(specs)}")
        chosen = target
    elif scene:
        chosen = next((name for name, spec in specs.items() if spec.scene_command), "")
    elif len(specs) == 1:
        chosen = next(iter(specs))
    else:
        raise LaunchError(f"name a target to launch: {', '.join(specs)}")
    if scene and not (chosen and specs[chosen].scene_command):
        raise LaunchError(f"{CONFIG_FILE} has no scene_command to open one scene")
    return chosen, specs[chosen]
