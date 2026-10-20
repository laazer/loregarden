"""A configured gate command is recognised as the preset it duplicates, whatever its spelling."""

import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Workspace
from loregarden.services.gate_equivalence import CanonicalCommand, canonical, running_equivalents
from sqlmodel import Session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    (tmp_path / "client").mkdir()
    (tmp_path / "client" / "package.json").write_text(
        json.dumps({"scripts": {"lint": "oxlint", "test": "jest --ci", "both": "oxlint && jest"}})
    )
    (tmp_path / "server").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # The shell wrapper loregarden's own profile uses.
        ('bash -c "cd server && uv run ruff check ."', ("server", ("ruff", "check"))),
        ("uv run --directory server ruff check", ("server", ("ruff", "check"))),
        ("sh -c 'cd {workspace_root}/server && ruff check'", ("server", ("ruff", "check"))),
        ("uvx ruff format --check server", (".", ("ruff", "format", "--check", "server"))),
        # An npm script is what it runs.
        ("npm --prefix client run lint", ("client", ("oxlint",))),
        ('bash -c "cd client && npx --no-install oxlint ."', ("client", ("oxlint",))),
        ("pnpm --dir client run test -- --runInBand", ("client", ("jest", "--ci", "--runInBand"))),
        # npx --prefix chooses node_modules, not the working directory.
        ("npx --prefix client tsc --build client", (".", ("tsc", "--build", "client"))),
        (
            "cargo clippy --manifest-path src-tauri/Cargo.toml -- -D warnings",
            ("src-tauri", ("cargo", "clippy", "--", "-D", "warnings")),
        ),
        ("go -C api vet ./...", ("api", ("go", "vet", "./..."))),
    ],
)
def test_reduces_each_spelling_to_what_runs_and_where(repo, command, expected):
    directory, argv = expected
    assert canonical(command, repo) == CanonicalCommand(directory, argv)


@pytest.mark.parametrize(
    "command",
    [
        # Anything beyond `cd DIR && COMMAND` is shell the rules do not model.
        'bash -c "cd server; ruff check"',
        'bash -c "ruff check | tee out"',
        'bash -c "cd server && ruff check && pytest"',
        # A script that is itself a pipeline, or one that does not exist.
        "npm --prefix client run both",
        "npm --prefix client run missing",
        "npm --prefix nowhere run lint",
        # Placeholders and paths outside the repository.
        "bash {loregarden_root}/.lefthook/scripts/workspace-gates.sh --repo {workspace_root}",
        'bash -c "cd ../elsewhere && ruff check"',
        "uv run --directory /abs/path ruff check",
        'bash -c "unterminated',
    ],
)
def test_anything_outside_the_rules_matches_nothing(repo, command):
    assert canonical(command, repo) is None


def test_running_equivalents_prefers_an_exact_match_and_skips_what_does_not_run(repo):
    presets = [
        "uv run --directory server ruff check",
        "uv run --directory server ruff format --check",
        "npm --prefix client run lint",
        "npm --prefix client run test",
    ]
    configured = [
        'bash -c "cd server && uv run ruff check ."',
        "uv run --directory server ruff format --check",
        'bash -c "cd client && npx --no-install oxlint ."',
        "bash {loregarden_root}/.lefthook/scripts/workspace-gates.sh",
    ]
    assert running_equivalents(presets, configured, repo) == {
        presets[0]: configured[0],
        presets[1]: presets[1],
        presets[2]: configured[2],
    }


def test_a_check_in_another_directory_is_not_the_same_check(repo):
    assert (
        running_equivalents(
            ["uv run --directory server ruff check"],
            ['bash -c "cd client && uv run ruff check ."'],
            repo,
        )
        == {}
    )


def test_workspace_presets_report_what_already_runs(client, isolated_db, repo):
    (repo / "server" / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[dependency-groups]\ndev = ["ruff"]\n'
    )
    (repo / "server" / "uv.lock").write_text("")
    with Session(isolated_db) as session:
        session.add(Workspace(slug="eq", name="eq", repo_path=str(repo)))
        session.commit()
    profile = mock.Mock()
    profile.gates.commands = ['bash -c "cd server && uv run ruff check ."']
    with mock.patch(
        "loregarden.api.gate_presets.resolve_orchestration_profile", return_value=profile
    ):
        body = client.get("/api/gate-presets/workspaces/eq").json()

    commands = {c["command"]: c["running_as"] for t in body["toolchains"] for c in t["commands"]}
    assert (
        commands["uv run --directory server ruff check"]
        == 'bash -c "cd server && uv run ruff check ."'
    )
    assert commands["uv run --directory server ruff format --check"] is None
