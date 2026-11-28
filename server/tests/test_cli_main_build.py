"""A write this build is too old for runs on main's build instead of stopping there.

The refusal itself is `test_cli_stale_write_guard`'s; these cover what follows
it: `mcp call` hands the call to a checkout of `origin/main`, kept in the
primary checkout's `data/`, and says so.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.cli import main_build, mcp_tools
from loregarden.cli.errors import UsageError
from loregarden.mcp.tool_ids import McpTool

AHEAD = ["20261027_drop_studio_agents_adapter"]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} in {cwd}: {result.stderr.strip()}"
    return result.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(f"{name}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(name="primary")
def primary_fixture(tmp_path) -> Path:
    """A checkout whose `origin/main` is its first commit; `data/` ignored, as in loregarden."""
    repo = tmp_path / "primary"
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text("/data/\n")
    sha = _commit(repo, "seed.txt")
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo


def _move_main(primary: Path, name: str) -> str:
    sha = _commit(primary, name)
    git(primary, "update-ref", "refs/remotes/origin/main", sha)
    return sha


def _build(primary: Path) -> main_build.MainBuild:
    with main_build._ready(primary) as build:
        return build


def test_main_build_is_a_checkout_of_origin_main_in_the_primary_data_dir(primary):
    build = _build(primary)
    assert build.path == primary / "data" / "cli-main-build"
    assert git(build.path, "rev-parse", "HEAD") == git(primary, "rev-parse", "origin/main")
    assert git(primary, "status", "--porcelain") == "", "data/ is ignored; the primary stays clean"


def test_main_build_follows_origin_main_when_it_moves(primary):
    first = _build(primary)
    moved = _move_main(primary, "later.txt")

    again = _build(primary)

    assert again.path == first.path
    assert again.sha == moved
    assert git(again.path, "rev-parse", "HEAD") == moved
    assert (again.path / "later.txt").is_file()


def test_main_build_with_local_changes_is_never_discarded(primary):
    build = _build(primary)
    (build.path / "seed.txt").write_text("somebody's edit\n")
    _move_main(primary, "later.txt")

    with pytest.raises(UsageError, match="has local changes"):
        _build(primary)

    assert (build.path / "seed.txt").read_text() == "somebody's edit\n"


def test_a_build_directory_removed_by_hand_is_made_again(primary):
    build = _build(primary)
    subprocess.run(["rm", "-rf", str(build.path)], check=True)

    assert _build(primary).path == build.path
    assert (build.path / "seed.txt").is_file()


def test_the_call_goes_to_main_builds_cli_with_its_arguments_on_stdin(primary, capsys, monkeypatch):
    # This process runs under `uv run`, whose venv is this build's, not main's.
    monkeypatch.setenv("VIRTUAL_ENV", str(primary / "server" / ".venv"))
    ran = subprocess.CompletedProcess([], 0, stdout='{"ok": true}\n', stderr="")
    with mock.patch.object(main_build, "_run_cli", return_value=ran) as run:
        out = main_build.run_on_main_build(
            primary,
            McpTool.UPDATE_TICKET,
            {"ticket_id": "lg-x-1", "state": "done"},
            orchestrated=True,
            why="Refusing to run it: this build does not know 1 migration(s).",
        )

    assert out == '{"ok": true}'
    command = run.call_args.args[0]
    build = primary / "data" / "cli-main-build"
    assert command == [
        str(build / "scripts" / "loregarden-cli.sh"),
        "mcp",
        "call",
        McpTool.UPDATE_TICKET,
        "--json",
        "-",
        "--orchestrated",
    ]
    assert json.loads(run.call_args.kwargs["stdin"]) == {"ticket_id": "lg-x-1", "state": "done"}
    env = run.call_args.kwargs["env"]
    assert env[main_build.ON_MAIN_BUILD_ENV] == "1"
    assert env["LOREGARDEN_REPO_ROOT"] == str(primary)
    assert "VIRTUAL_ENV" not in env
    notice = capsys.readouterr().err
    assert "this build does not know 1 migration(s)" in notice
    assert (
        f"main's build instead (origin/main at {git(primary, 'rev-parse', 'HEAD')[:12]}" in notice
    )


def test_main_builds_failure_is_an_error_carrying_its_own_words(primary, capsys):
    ran = subprocess.CompletedProcess([], 2, stdout="", stderr="UsageError: no such ticket\n")
    with mock.patch.object(main_build, "_run_cli", return_value=ran):
        with pytest.raises(UsageError, match="exited 2"):
            main_build.run_on_main_build(
                primary, McpTool.UPDATE_TICKET, {}, orchestrated=False, why="stale."
            )
    assert "UsageError: no such ticket" in capsys.readouterr().err


def test_main_build_never_hands_a_refused_call_on_again(primary, monkeypatch):
    """Main's build refusing too means the database is ahead of origin/main: say so, stop."""
    monkeypatch.setenv(main_build.ON_MAIN_BUILD_ENV, "1")
    with mock.patch.object(main_build, "_run_cli") as run:
        with pytest.raises(UsageError, match="This is main's build already"):
            main_build.run_on_main_build(
                primary, McpTool.UPDATE_TICKET, {}, orchestrated=False, why="stale."
            )
    run.assert_not_called()
    assert not (primary / "data" / "cli-main-build").exists()


# -- `mcp call` --------------------------------------------------------------------


def _call(*, allow_stale: bool, orchestrated: bool, ahead: list[str]):
    with (
        mock.patch.object(mcp_tools.db_session, "init_db"),
        mock.patch.object(mcp_tools, "unknown_migration_ids", return_value=ahead),
        mock.patch.object(mcp_tools, "run_on_main_build", return_value="from main") as handed,
        mock.patch.object(mcp_tools, "execute_tool", return_value="from here") as here,
        mock.patch.object(mcp_tools, "Session"),
    ):
        out = mcp_tools._run_tool(
            McpTool.UPDATE_TICKET,
            {"ticket_id": "lg-x-1"},
            orchestrated=orchestrated,
            run_id="",
            allow_stale=allow_stale,
        )
    return out, handed, here


@pytest.mark.parametrize("orchestrated", [False, True])
def test_a_stale_write_runs_on_main_build(orchestrated):
    out, handed, here = _call(allow_stale=False, orchestrated=orchestrated, ahead=AHEAD)

    assert out == "from main"
    here.assert_not_called()
    (primary, tool, arguments), kwargs = handed.call_args
    assert (tool, arguments) == (McpTool.UPDATE_TICKET, {"ticket_id": "lg-x-1"})
    assert kwargs["orchestrated"] is orchestrated
    assert AHEAD[0] in kwargs["why"]


def test_a_current_build_writes_itself():
    out, handed, here = _call(allow_stale=False, orchestrated=False, ahead=[])
    assert out == "from here"
    handed.assert_not_called()


def test_a_persons_waiver_still_writes_from_this_build():
    out, handed, _here = _call(allow_stale=True, orchestrated=False, ahead=AHEAD)
    assert out == "from here"
    handed.assert_not_called()
