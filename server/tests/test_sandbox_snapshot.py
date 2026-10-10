"""`task sandbox` copies the database and every memory graph, keeping their layout."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

from loregarden.cli.main import main as cli_main
from loregarden.services import sandbox_snapshot
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
