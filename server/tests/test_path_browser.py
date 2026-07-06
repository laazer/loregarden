from pathlib import Path

import pytest
from loregarden.config import settings
from loregarden.services.path_browser import (
    assert_browse_allowed,
    list_browse,
    list_import_browse,
    normalize_browse_target,
    read_import_files,
    resolve_browse_target,
    to_workspace_repo_path,
)


def test_to_workspace_repo_path_relative_and_absolute(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    sibling = tmp_path / "blobert"
    repo.mkdir()
    sibling.mkdir()
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))

    assert to_workspace_repo_path(repo, repo_root=repo) == "."
    assert to_workspace_repo_path(sibling, repo_root=repo) == str(sibling.resolve())


def test_list_browse_lists_sibling_directories(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    sibling = tmp_path / "blobert"
    repo.mkdir()
    sibling.mkdir()
    (repo / "client").mkdir()
    (repo / ".hidden").mkdir()
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))

    payload = list_browse(tmp_path, repo_root=repo)
    names = {entry["name"] for entry in payload["entries"]}
    assert names == {"blobert", "loregarden"}
    assert payload["repo_path"] == str(tmp_path.resolve())
    assert payload["parent_path"] is None

    loregarden_payload = list_browse(repo, repo_root=repo)
    assert loregarden_payload["repo_path"] == "."
    assert [entry["name"] for entry in loregarden_payload["entries"]] == ["client"]
    assert loregarden_payload["entries"][0]["repo_path"] == "client"


def test_resolve_browse_target_defaults_to_repo_root(tmp_path):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    assert resolve_browse_target(None, repo_root=repo) == repo.resolve()
    assert resolve_browse_target("client", repo_root=repo) == (repo / "client").resolve()


def test_assert_browse_allowed_blocks_outside_home(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    outside = Path("/tmp")
    monkeypatch.setattr(settings, "browse_root", str(repo))

    assert_browse_allowed(repo)
    with pytest.raises(ValueError, match="outside the allowed browse scope"):
        assert_browse_allowed(outside)


def test_browse_import_lists_files_and_directories(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    board = repo / "project_board"
    board.mkdir()
    (board / "ticket.md").write_text("# TICKET: x\nTitle: Test\n", encoding="utf-8")
    (board / "notes.txt").write_text("ignore me", encoding="utf-8")
    (board / "nested").mkdir()
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))

    payload = list_import_browse(board, repo_root=repo)
    kinds = {entry["name"]: entry["kind"] for entry in payload["entries"]}
    assert kinds == {"nested": "directory", "ticket.md": "file"}
    assert "notes.txt" not in kinds


def test_read_import_files(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    ticket = repo / "ticket.json"
    ticket.write_text('{"title":"Hello"}', encoding="utf-8")
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))

    files = read_import_files([str(ticket)])
    assert files == [("ticket.json", '{"title":"Hello"}')]


def test_normalize_browse_target_falls_back_for_sqlite_url(tmp_path):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    assert normalize_browse_target("sqlite:///data/loregarden.db", repo_root=repo) == repo.resolve()


def test_normalize_browse_target_unescapes_shell_spaces(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    vault = tmp_path / "Project Vault"
    repo.mkdir()
    vault.mkdir()
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    escaped = str(vault).replace(" ", r"\ ")
    assert normalize_browse_target(escaped, repo_root=repo) == vault.resolve()


def test_normalize_browse_target_uses_parent_for_file_seed(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    db_file = repo / "memory.db"
    db_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    assert normalize_browse_target(str(db_file), repo_root=repo) == repo.resolve()


def test_normalize_browse_target_walks_up_missing_path(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    (repo / "vault").mkdir()
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    missing = repo / "vault" / "missing" / "nested"
    assert normalize_browse_target(str(missing), repo_root=repo) == (repo / "vault").resolve()


def test_browse_directory_api_accepts_sqlite_url_seed(client, tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    sibling = tmp_path / "blobert"
    repo.mkdir()
    sibling.mkdir()
    (repo / "server").mkdir()
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo.resolve())
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))

    res = client.get("/api/system/browse")
    assert res.status_code == 200
    body = res.json()
    assert body["repo_path"] == "."
    assert {entry["name"] for entry in body["entries"]} == {"server"}

    sibling_res = client.get("/api/system/browse", params={"path": str(sibling)})
    assert sibling_res.status_code == 200
    assert sibling_res.json()["repo_path"] == str(sibling.resolve())

    blocked = client.get("/api/system/browse", params={"path": "/etc"})
    assert blocked.status_code == 400

    sqlite_seed = client.get("/api/system/browse", params={"path": "sqlite:///data/loregarden.db"})
    assert sqlite_seed.status_code == 200
    assert sqlite_seed.json()["repo_path"] == "."
