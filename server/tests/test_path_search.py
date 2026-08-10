"""Ranking for the composer's `@` reference picker."""

import pytest
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.path_search import search_workspace_paths


@pytest.fixture
def search_repo(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    (repo / "client" / "src" / "components").mkdir(parents=True)
    (repo / "client" / "src" / "components" / "AppActionBar.tsx").write_text("x", encoding="utf-8")
    (repo / "client" / "src" / "components" / "CopilotDock.tsx").write_text("x", encoding="utf-8")
    (repo / "server" / "loregarden").mkdir(parents=True)
    (repo / "server" / "loregarden" / "main.py").write_text("x", encoding="utf-8")
    (repo / "node_modules" / "junk").mkdir(parents=True)
    (repo / "node_modules" / "junk" / "AppActionBar.tsx").write_text("x", encoding="utf-8")
    (repo / ".hidden").write_text("x", encoding="utf-8")
    (repo / "README.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo.resolve())
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    return repo


@pytest.fixture
def search_workspace(search_repo):
    return Workspace(slug="demo", name="Demo", repo_path=".")


def test_empty_query_returns_top_level_dirs_first(search_workspace):
    results = search_workspace_paths(search_workspace, "")
    assert [entry["name"] for entry in results] == ["client", "server", "README.md"]


def test_subsequence_match_finds_nested_file(search_workspace):
    results = search_workspace_paths(search_workspace, "appact")
    assert results[0]["repo_path"] == "client/src/components/AppActionBar.tsx"
    assert results[0]["kind"] == "file"


def test_blocked_and_hidden_paths_are_never_offered(search_workspace):
    paths = {entry["repo_path"] for entry in search_workspace_paths(search_workspace, "a")}
    assert not any(path.startswith("node_modules") for path in paths)
    assert ".hidden" not in paths


def test_directories_are_matchable(search_workspace):
    results = search_workspace_paths(search_workspace, "components")
    assert results[0]["repo_path"] == "client/src/components"
    assert results[0]["kind"] == "directory"


def test_limit_is_capped_and_respected(search_workspace):
    assert len(search_workspace_paths(search_workspace, "", limit=2)) == 2
