"""Which gate commands are offered for a repository's toolchains, and that they run as gates run them.

Repositories are built in ``tmp_path`` from marker files; the commands are
asserted as strings because the string *is* the contract — gate_runner splits
and execs it with no shell, from the checkout root.
"""

import json
import shlex
from pathlib import Path

from fastapi.testclient import TestClient
from loregarden.services.gate_presets import gate_presets
from loregarden.services.gate_runner import format_gate_command


def sample_context() -> dict[str, str]:
    return {"workspace_root": "/w", "loregarden_root": "/lg", "ticket_id": "t"}


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _by_place(root: Path) -> dict[tuple[str, str], list[tuple[str, bool]]]:
    return {
        (p.key.value, p.directory): [(c.command, c.default_on) for c in p.commands]
        for p in gate_presets(root)
        if p.detected
    }


def test_a_uv_project_in_a_subdirectory_is_reached_with_directory_not_cd(tmp_path: Path) -> None:
    _write(
        tmp_path / "server" / "pyproject.toml",
        '[project]\nname = "s"\ndependencies = ["pytest>=8"]\n'
        '[dependency-groups]\ndev = ["mypy", {include-group = "x"}]\n[tool.ruff]\n',
    )
    _write(tmp_path / "server" / "uv.lock")
    assert _by_place(tmp_path)[("python", "server")] == [
        ("uv run --directory server ruff check", True),
        ("uv run --directory server ruff format --check", True),
        ("uv run --directory server mypy .", True),
        ("uv run --directory server pytest -q", False),
    ]


def test_python_without_uv_offers_only_ruff_through_uvx(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "s"\ndependencies = ["pytest"]\n')
    assert _by_place(tmp_path)[("python", ".")] == [
        ("uvx ruff check .", True),
        ("uvx ruff format --check .", True),
    ]


def test_node_runs_the_scripts_package_json_defines_with_its_lockfile_manager(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "web" / "package.json",
        json.dumps({"scripts": {"lint": "x", "test": "y", "build": "z"}}),
    )
    _write(tmp_path / "web" / "pnpm-lock.yaml")
    _write(tmp_path / "web" / "tsconfig.json", '{"files": [], "references": [{"path": "a"}]}')
    assert _by_place(tmp_path)[("node", "web")] == [
        ("pnpm --dir web run lint", True),
        ("pnpm --dir web run test", False),
        # A references stub: --noEmit would check zero files.
        ("npx --prefix web tsc --build web", True),
    ]


def test_a_project_with_commands_hides_vendored_copies_beneath_it(tmp_path: Path) -> None:
    _write(tmp_path / "project.godot")
    _write(tmp_path / "reference" / "kit" / "project.godot")
    _write(tmp_path / "package.json", json.dumps({"workspaces": ["client"]}))
    _write(tmp_path / "client" / "package.json", json.dumps({"scripts": {"lint": "x"}}))
    places = set(_by_place(tmp_path))
    assert ("godot", ".") in places
    assert ("godot", "reference/kit") not in places
    # The root package.json offers nothing, so it hides nothing.
    assert ("node", "client") in places
    assert ("node", ".") not in places


def test_dependency_and_dot_directories_are_not_projects(tmp_path: Path) -> None:
    _write(
        tmp_path / "node_modules" / "dep" / "package.json", json.dumps({"scripts": {"lint": "x"}})
    )
    _write(tmp_path / ".venv" / "lib" / "pyproject.toml", '[project]\nname = "v"\n')
    assert _by_place(tmp_path) == {}


def test_a_missing_path_offers_every_toolchain_undetected_and_unticked(tmp_path: Path) -> None:
    presets = gate_presets(tmp_path / "not-yet")
    assert {p.key.value for p in presets} == {"python", "node", "rust", "go", "godot"}
    assert not any(p.detected for p in presets)
    assert not any(c.default_on for p in presets for c in p.commands)
    assert all(p.commands for p in presets)


def test_odd_directory_names_survive_placeholder_filling_and_splitting(tmp_path: Path) -> None:
    """gate_runner fills placeholders with str.format, then shlex.splits; a path must stay one argument."""
    odd = "a b{x}"
    _write(tmp_path / odd / "Cargo.toml")
    _write(tmp_path / odd / "go.mod")
    _write(tmp_path / odd / "package.json", json.dumps({"scripts": {"lint": "x"}}))
    _write(tmp_path / odd / "pyproject.toml", '[project]\nname = "s"\n')
    commands = [c.command for p in gate_presets(tmp_path) if p.detected for c in p.commands]
    assert len(commands) == 8
    for command in commands:
        argv = shlex.split(format_gate_command(command, sample_context()))
        assert any(odd in arg for arg in argv), argv
        assert not any(arg in {"a", "b{x}"} for arg in argv), argv


def test_the_api_serves_presets_by_path_and_by_workspace(
    client: TestClient, tmp_path: Path
) -> None:
    _write(tmp_path / "Cargo.toml")
    by_path = client.get("/api/gate-presets", params={"path": str(tmp_path)})
    assert by_path.status_code == 200, by_path.text
    rust = next(t for t in by_path.json()["toolchains"] if t["key"] == "rust")
    assert rust["detected"] and rust["directory"] == "."

    created = client.post(
        "/api/workspaces", json={"slug": "rs", "name": "Rs", "repo_path": str(tmp_path)}
    )
    assert created.status_code == 201, created.text
    by_slug = client.get("/api/gate-presets/workspaces/rs")
    assert by_slug.json() == by_path.json()
    assert client.get("/api/gate-presets/workspaces/nope").status_code == 404
