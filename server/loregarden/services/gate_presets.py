"""Gate commands for the toolchains a workspace uses, offered when setting it up.

The fallback profile gives every workspace loregarden's own guardrails, which
detect Python and TypeScript layout but run none of the repo's own tools. This
looks at the repository — its root and up to two levels down, where a
``server/`` or ``asset_generation/`` usually holds a project — and offers the
lint, format, typecheck and test commands each toolchain it finds would run.

Two constraints from how gates execute (``gate_runner._run_command``) shape
every command here:

- **No shell.** A command is ``shlex.split`` and exec'd, so ``cd x && y`` does
  not work. A project in a subdirectory is reached with the tool's own flag —
  ``uv run --directory``, ``npm --prefix``, ``cargo --manifest-path``,
  ``go -C`` — never by changing directory.
- **cwd is the run's checkout root**, a worktree during a run. Paths are
  relative to it, so one command works in every worktree.

Tests are offered but off by default: gates run on every stage transition, with
a 300-second budget each, and a suite that fits neither turns every transition
into a timeout. Toolchains nothing was detected for are still listed, with
generic commands, so a brand-new repository can pick its stack up front.
"""

from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import tomllib
from pydantic import BaseModel, Field, RootModel, ValidationError

logger = logging.getLogger(__name__)

#: Two levels covers ``server/``, ``packages/web/`` and ``tools/asset_generation/``.
MAX_DEPTH = 2
#: Directories that hold dependencies or build output, never a project of the repo's own.
#: Dot-directories (.git, .venv, .claude worktrees…) are skipped as a class.
SKIPPED_DIRS = frozenset({"venv", "node_modules", "target", "dist", "build", "__pycache__"})
#: The distribution name at the head of a PEP 508 requirement string.
_REQUIREMENT_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class GateKind(StrEnum):
    LINT = "lint"
    FORMAT = "format"
    TYPECHECK = "typecheck"
    TEST = "test"


#: Fast enough to run on every transition; tests are opted into.
_ON_BY_DEFAULT = frozenset({GateKind.LINT, GateKind.FORMAT, GateKind.TYPECHECK})


class ToolchainKey(StrEnum):
    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    GO = "go"
    GODOT = "godot"


@dataclass(frozen=True)
class PresetCommand:
    command: str
    label: str
    kind: GateKind
    #: Whether the picker starts with it ticked, for a detected toolchain.
    default_on: bool


@dataclass(frozen=True)
class ToolchainPreset:
    key: ToolchainKey
    label: str
    #: Relative to the repository root, POSIX-style; "." for the root itself.
    directory: str
    detected: bool
    commands: list[PresetCommand] = field(default_factory=list)
    #: What the commands assume is installed, or why one was left out.
    note: str = ""


def _arg(path: str) -> str:
    """A path as one argument of a gate command: shell-quoted for ``shlex.split``,
    braces doubled for the ``str.format`` that fills placeholders first."""
    return shlex.quote(path).replace("{", "{{").replace("}", "}}")


def _cmd(command: str, label: str, kind: GateKind, *, detected: bool) -> PresetCommand:
    return PresetCommand(command, label, kind, default_on=detected and kind in _ON_BY_DEFAULT)


# --- Python ---------------------------------------------------------------


class _Requirement(RootModel[str]):
    """A PEP 508 requirement string; its distribution name, lower-cased."""

    @property
    def name(self) -> str | None:
        match = _REQUIREMENT_NAME.match(self.root)
        return match.group(1).lower() if match else None


class _IncludeGroup(BaseModel):
    """A PEP 735 ``{include-group = "…"}`` entry: names another group, requires nothing itself."""

    include_group: str = Field(alias="include-group")

    @property
    def name(self) -> str | None:
        return None


class _ProjectTable(BaseModel):
    dependencies: list[_Requirement] = []
    optional_dependencies: dict[str, list[_Requirement]] = Field(
        default={}, alias="optional-dependencies"
    )


class _PyProject(BaseModel):
    """The parts of pyproject.toml that say which tools a project uses; the rest is ignored."""

    project: _ProjectTable = _ProjectTable()
    tool: dict[str, object] = {}
    dependency_groups: dict[str, list[_Requirement | _IncludeGroup]] = Field(
        default={}, alias="dependency-groups"
    )

    def tools(self) -> set[str]:
        """Its [tool.*] tables and every dependency it names."""
        entries = [
            *self.project.dependencies,
            *(r for group in self.project.optional_dependencies.values() for r in group),
            *(r for group in self.dependency_groups.values() for r in group),
        ]
        return set(self.tool) | {name for entry in entries if (name := entry.name)}


class _PackageJson(BaseModel):
    scripts: dict[str, str] = {}


def _pyproject_tools(project: Path) -> set[str]:
    path = project / "pyproject.toml"
    try:
        return _PyProject.model_validate(tomllib.loads(path.read_text(encoding="utf-8"))).tools()
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        # Offered anyway: ruff through uvx needs nothing from the pyproject.
        logger.warning("cannot read %s for gate presets: %s", path, exc)
        return set()


def _python(project: Path, directory: str, *, detected: bool) -> ToolchainPreset:
    tools = _pyproject_tools(project) if detected else {"ruff", "pytest"}
    uv = not detected or (project / "uv.lock").is_file()
    d = _arg(directory)
    where = "" if directory == "." else f" --directory {d}"
    commands: list[PresetCommand] = []
    if uv and "ruff" in tools:
        commands += [
            _cmd(f"uv run{where} ruff check", "Ruff lint", GateKind.LINT, detected=detected),
            _cmd(
                f"uv run{where} ruff format --check",
                "Ruff format",
                GateKind.FORMAT,
                detected=detected,
            ),
        ]
    else:
        # uvx runs ruff without it being a dependency; it reads the project's own config.
        commands += [
            _cmd(f"uvx ruff check {d}", "Ruff lint", GateKind.LINT, detected=detected),
            _cmd(
                f"uvx ruff format --check {d}",
                "Ruff format",
                GateKind.FORMAT,
                detected=detected,
            ),
        ]
    if uv and "mypy" in tools:
        commands.append(
            _cmd(f"uv run{where} mypy .", "mypy", GateKind.TYPECHECK, detected=detected)
        )
    if uv and "pytest" in tools:
        commands.append(
            _cmd(f"uv run{where} pytest -q", "pytest", GateKind.TEST, detected=detected)
        )
    note = (
        ""
        if uv
        else "No uv.lock: only ruff (through uvx) is offered, since the project's environment is unknown."
    )
    return ToolchainPreset(ToolchainKey.PYTHON, "Python", directory, detected, commands, note)


# --- Node -----------------------------------------------------------------

_SCRIPT_KINDS: list[tuple[str, GateKind]] = [
    ("lint", GateKind.LINT),
    ("format:check", GateKind.FORMAT),
    ("check-format", GateKind.FORMAT),
    ("typecheck", GateKind.TYPECHECK),
    ("type-check", GateKind.TYPECHECK),
    ("test", GateKind.TEST),
]


def _node_runner(project: Path, directory: str) -> tuple[str, Callable[[str], str]]:
    """The package manager the lockfile names, and how it runs a script in ``directory``."""
    d = _arg(directory)
    if (project / "pnpm-lock.yaml").is_file():
        return "pnpm", lambda script: f"pnpm --dir {d} run {_arg(script)}"
    if (project / "yarn.lock").is_file():
        return "yarn", lambda script: f"yarn --cwd {d} run {_arg(script)}"
    if (project / "bun.lockb").is_file() or (project / "bun.lock").is_file():
        return "bun", lambda script: f"bun --cwd {d} run {_arg(script)}"
    return "npm", lambda script: f"npm --prefix {d} run {_arg(script)}"


def _package_scripts(project: Path) -> dict[str, str]:
    path = project / "package.json"
    try:
        return _PackageJson.model_validate_json(path.read_text(encoding="utf-8")).scripts
    except (OSError, ValidationError) as exc:
        # The toolchain is still listed, with a note that it offers nothing to run.
        logger.warning("cannot read %s for gate presets: %s", path, exc)
        return {}


def _tsc_command(project: Path, directory: str) -> str:
    """``tsc -b`` for a references stub — ``--noEmit`` on one checks zero files."""
    try:
        config = (project / "tsconfig.json").read_text(encoding="utf-8")
    except OSError:
        config = ""
    d = _arg(directory)
    if '"references"' in config:
        return f"npx --prefix {d} tsc --build {d}"
    return f"npx --prefix {d} tsc --noEmit -p {d}"


def _node(project: Path, directory: str, *, detected: bool) -> ToolchainPreset:
    manager, run = _node_runner(project, directory)
    scripts = _package_scripts(project) if detected else {"lint": "", "typecheck": "", "test": ""}
    commands = [
        _cmd(run(script), f"{manager} run {script}", kind, detected=detected)
        for script, kind in _SCRIPT_KINDS
        if script in scripts
    ]
    if (
        detected
        and (project / "tsconfig.json").is_file()
        and not any(c.kind is GateKind.TYPECHECK for c in commands)
    ):
        commands.append(
            _cmd(_tsc_command(project, directory), "tsc", GateKind.TYPECHECK, detected=detected)
        )
    note = "" if commands else "package.json defines no lint, typecheck or test script to run."
    return ToolchainPreset(
        ToolchainKey.NODE, "Node / TypeScript", directory, detected, commands, note
    )


# --- Rust, Go, Godot ------------------------------------------------------


def _rust(_project: Path, directory: str, *, detected: bool) -> ToolchainPreset:
    manifest = _arg("Cargo.toml" if directory == "." else f"{directory}/Cargo.toml")
    commands = [
        _cmd(
            f"cargo fmt --check --manifest-path {manifest}",
            "cargo fmt",
            GateKind.FORMAT,
            detected=detected,
        ),
        _cmd(
            f"cargo clippy --manifest-path {manifest} -- -D warnings",
            "clippy",
            GateKind.LINT,
            detected=detected,
        ),
        _cmd(
            f"cargo test --manifest-path {manifest}", "cargo test", GateKind.TEST, detected=detected
        ),
    ]
    return ToolchainPreset(ToolchainKey.RUST, "Rust", directory, detected, commands)


def _go(_project: Path, directory: str, *, detected: bool) -> ToolchainPreset:
    commands = [
        _cmd(f"go -C {_arg(directory)} vet ./...", "go vet", GateKind.LINT, detected=detected),
        _cmd(f"go -C {_arg(directory)} test ./...", "go test", GateKind.TEST, detected=detected),
    ]
    note = "gofmt needs a shell to fail on output, which gates do not have; it is not offered."
    return ToolchainPreset(ToolchainKey.GO, "Go", directory, detected, commands, note)


def _godot(_project: Path, directory: str, *, detected: bool) -> ToolchainPreset:
    commands = [
        PresetCommand(f"gdlint {_arg(directory)}", "gdlint", GateKind.LINT, default_on=False),
        PresetCommand(
            f"gdformat --check {_arg(directory)}", "gdformat", GateKind.FORMAT, default_on=False
        ),
    ]
    note = (
        "Needs gdtoolkit (pip install gdtoolkit) on loregarden's PATH; off until you confirm it is."
    )
    return ToolchainPreset(
        ToolchainKey.GODOT, "Godot (GDScript)", directory, detected, commands, note
    )


@dataclass(frozen=True)
class _Toolchain:
    key: ToolchainKey
    marker: str
    build: Callable[..., ToolchainPreset]


_TOOLCHAINS: list[_Toolchain] = [
    _Toolchain(ToolchainKey.PYTHON, "pyproject.toml", _python),
    _Toolchain(ToolchainKey.NODE, "package.json", _node),
    _Toolchain(ToolchainKey.RUST, "Cargo.toml", _rust),
    _Toolchain(ToolchainKey.GO, "go.mod", _go),
    _Toolchain(ToolchainKey.GODOT, "project.godot", _godot),
]


def _project_dirs(root: Path) -> Iterator[Path]:
    """The root and its subdirectories to ``MAX_DEPTH``, skipping dependency and build trees."""
    frontier = [root]
    for _ in range(MAX_DEPTH + 1):
        yield from frontier
        children: list[Path] = []
        for directory in frontier:
            try:
                entries = sorted(directory.iterdir())
            except OSError as exc:
                logger.warning("cannot list %s for gate presets: %s", directory, exc)
                continue
            children += [
                p
                for p in entries
                if p.is_dir() and p.name not in SKIPPED_DIRS and not p.name.startswith(".")
            ]
        frontier = children


def gate_presets(root: Path) -> list[ToolchainPreset]:
    """Every toolchain's gate commands: detected projects first, then the rest with generic commands.

    A path with nothing at it yet detects nothing and offers every toolchain undetected.
    """
    detected: list[ToolchainPreset] = []
    # A project with commands covers its subtree for its toolchain: what is below it is
    # vendored (blobert's reference Godot projects) or already in its own lint run.
    # One without commands covers nothing — a root package.json that only holds
    # workspaces must not hide the client/ beneath it.
    covering: dict[ToolchainKey, list[Path]] = {}
    found: set[ToolchainKey] = set()
    if root.is_dir():
        for project in _project_dirs(root):
            directory = project.relative_to(root).as_posix()
            for toolchain in _TOOLCHAINS:
                if not (project / toolchain.marker).is_file():
                    continue
                if any(project.is_relative_to(above) for above in covering.get(toolchain.key, [])):
                    continue
                found.add(toolchain.key)
                preset = toolchain.build(project, directory, detected=True)
                if preset.commands:
                    detected.append(preset)
                    covering.setdefault(toolchain.key, []).append(project)
    undetected = [
        toolchain.build(root, ".", detected=False)
        for toolchain in _TOOLCHAINS
        if toolchain.key not in found
    ]
    return detected + undetected
