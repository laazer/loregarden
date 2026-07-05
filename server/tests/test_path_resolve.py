from pathlib import Path

import pytest

from loregarden.services.path_resolve import (
    detect_obsidian_documents_dir,
    expand_path,
    is_under_icloud,
    resolve_icloud_root,
    resolve_sqlite_path,
    sqlite_url_for_path,
    unescape_shell_path,
)


def test_expand_path_tilde_and_repo_relative(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    assert expand_path("nested/file.db", repo_root=tmp_path) == (nested / "file.db").resolve()


def test_expand_path_absolute_ignores_repo_root(tmp_path):
    abs_path = tmp_path / "abs.db"
    assert expand_path(str(abs_path), repo_root=tmp_path / "ignored") == abs_path.resolve()


def test_resolve_sqlite_path_relative_to_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(tmp_path))
    path = resolve_sqlite_path("sqlite:///data/loregarden.db", tmp_path)
    assert path == (tmp_path / "data" / "loregarden.db").resolve()


def test_resolve_sqlite_path_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = resolve_sqlite_path("sqlite:///~/Loregarden/loregarden.db", tmp_path)
    assert path == (tmp_path / "Loregarden" / "loregarden.db").resolve()


def test_is_under_icloud_detects_nested_path(tmp_path):
    icloud = tmp_path / "icloud"
    icloud.mkdir()
    db = icloud / "Loregarden" / "memory.db"
    db.parent.mkdir(parents=True)
    assert is_under_icloud(db, icloud) is True
    assert is_under_icloud(tmp_path / "local.db", icloud) is False


def test_resolve_icloud_root_override(tmp_path):
    custom = tmp_path / "custom-icloud"
    custom.mkdir()
    assert resolve_icloud_root(str(custom)) == custom.resolve()


def test_sqlite_url_for_path_absolute():
    path = Path("/tmp/test.db")
    assert sqlite_url_for_path(path) == f"sqlite:///{path.resolve()}"


def test_unescape_shell_path():
    raw = r"/Users/me/Library/Mobile\ Documents/iCloud\~md\~obsidian/Documents/Project\ Vault"
    assert unescape_shell_path(raw) == (
        "/Users/me/Library/Mobile Documents/iCloud~md~obsidian/Documents/Project Vault"
    )


def test_expand_path_unescapes_shell_spaces(tmp_path):
    target = tmp_path / "Project Vault"
    target.mkdir()
    escaped = str(target).replace(" ", r"\ ")
    assert expand_path(escaped) == target.resolve()


def test_detect_obsidian_documents_dir_when_present(monkeypatch, tmp_path):
    docs = tmp_path / "iCloud~md~obsidian" / "Documents"
    docs.mkdir(parents=True)
    monkeypatch.setattr(
        "loregarden.services.path_resolve.OBSIDIAN_ICLOUD_CONTAINER",
        tmp_path / "iCloud~md~obsidian",
    )
    assert detect_obsidian_documents_dir() == docs.resolve()
