"""`task sandbox` copies the database and every memory graph, keeping their layout."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.cli.main import main as cli_main
from loregarden.config import settings
from loregarden.services import sandbox_snapshot
from loregarden.services.review_kit import capture_path, snapshot_captures
from loregarden.services.sandbox_snapshot import take_snapshot


def _db(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (marker,))
    return path


def _read(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT v FROM t").fetchone()[0]


def _fake_main_db(source: Path):
    def snapshot(target: Path) -> None:
        with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
            src.backup(dst)

    return snapshot


def test_copies_the_database_and_each_workspace_graph(tmp_path):
    main_db = _db(tmp_path / "main" / "loregarden.db", "main")
    memory = _db(tmp_path / "vault" / "memory.db", "base")
    _db(tmp_path / "vault" / "loregarden" / "memory.db", "lg")
    _db(tmp_path / "vault" / "blobert" / "memory.db", "blob")
    (tmp_path / "vault" / "Learnings").mkdir()  # a vault dir with no graph is skipped
    out = tmp_path / "sandbox"

    with (
        patch.object(sandbox_snapshot, "snapshot_database", _fake_main_db(main_db)),
        patch.object(sandbox_snapshot, "resolved_memory_sqlite_path", return_value=memory),
    ):
        snap = take_snapshot(out)

    assert _read(snap.database) == "main"
    assert snap.memory_sqlite == out / "memory" / "memory.db"
    assert _read(snap.memory_sqlite) == "base"
    assert snap.memory_workspaces == ["blobert", "loregarden"]
    assert _read(out / "memory" / "loregarden" / "memory.db") == "lg"


def test_no_memory_graph_means_no_memory_copy(tmp_path):
    main_db = _db(tmp_path / "loregarden.db", "main")
    with (
        patch.object(sandbox_snapshot, "snapshot_database", _fake_main_db(main_db)),
        patch.object(sandbox_snapshot, "resolved_memory_sqlite_path", return_value=None),
    ):
        snap = take_snapshot(tmp_path / "sandbox")

    assert snap.memory_sqlite is None
    assert snap.memory_workspaces == []


def test_the_cli_prints_a_sandboxed_env_that_blanks_the_vault(tmp_path, capsys):
    main_db = _db(tmp_path / "loregarden.db", "main")
    with (
        patch.object(sandbox_snapshot, "snapshot_database", _fake_main_db(main_db)),
        patch.object(sandbox_snapshot, "resolved_memory_sqlite_path", return_value=None),
    ):
        assert cli_main(["sandbox", "snapshot", "--into", str(tmp_path / "sb")]) == 0

    out = capsys.readouterr().out
    assert "export LOREGARDEN_SANDBOX=1" in out
    assert "export LOREGARDEN_OBSIDIAN_VAULT_DIR=''" in out
    assert "export LOREGARDEN_MEMORY_SQLITE_URL=''" in out
    assert "loregarden.db" in out.split("LOREGARDEN_DATABASE_URL=")[1].splitlines()[0]


def test_the_screenshots_the_database_names_come_with_it(tmp_path, capsys):
    main_db = _db(tmp_path / "loregarden.db", "main")
    ticket, capture = "18d6d081-268a-4b1f-ab84-d955c3f71a26", f"{'a' * 32}.png"
    live = tmp_path / "live-captures"
    (live / ticket).mkdir(parents=True)
    (live / ticket / capture).write_bytes(b"png")
    into = tmp_path / "sb"
    stale = into / "review-captures" / "gone-ticket"
    stale.mkdir(parents=True)

    with (
        patch.object(sandbox_snapshot, "snapshot_database", _fake_main_db(main_db)),
        patch.object(sandbox_snapshot, "resolved_memory_sqlite_path", return_value=None),
        patch.object(settings, "review_captures_dir", live),
    ):
        assert cli_main(["sandbox", "snapshot", "--into", str(into)]) == 0

    exported = capsys.readouterr().out.split("LOREGARDEN_REVIEW_CAPTURES_DIR=")[1].splitlines()[0]
    with patch.object(settings, "review_captures_dir", Path(exported)):
        found = capture_path(ticket, capture)
    assert found == into / "review-captures" / ticket / capture
    assert found.read_bytes() == b"png"
    assert not stale.exists()
    assert (live / ticket / capture).exists()


def test_no_screenshots_still_gives_the_sandbox_a_directory_of_its_own(tmp_path):
    with patch.object(settings, "review_captures_dir", tmp_path / "none"):
        copied = snapshot_captures(tmp_path / "sb" / "review-captures")

    assert copied.is_dir()
    assert list(copied.iterdir()) == []


@pytest.mark.parametrize("target", ["live", "live/inside", "."])
def test_a_copy_that_would_delete_the_live_screenshots_is_refused(tmp_path, target):
    live = tmp_path / "live"
    live.mkdir()
    (live / "shot.png").write_bytes(b"png")

    with (
        patch.object(settings, "review_captures_dir", live),
        pytest.raises(ValueError, match="overlaps the live captures"),
    ):
        snapshot_captures(tmp_path / target)

    assert (live / "shot.png").read_bytes() == b"png"
