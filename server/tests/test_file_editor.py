import subprocess
from pathlib import Path

import pytest
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.file_editor import (
    checkout_editor_branch,
    list_editor_browse,
    list_editor_refs,
    read_editor_file,
    resolve_editor_root,
    write_editor_file,
)


def _init_repo(path: Path) -> None:
    # Force the initial branch to "main" so tests don't depend on the host git's
    # default (older git / some CI images still default to "master").
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def editor_repo(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    _init_repo(repo)
    (repo / "client").mkdir()
    (repo / "client" / "app.ts").write_text("export const app = 1;\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add client"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "branch", "feature/editor"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo.resolve())
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    return repo


@pytest.fixture
def editor_workspace(editor_repo):
    return Workspace(slug="demo", name="Demo", repo_path=".")


def test_list_editor_browse_files_and_dirs(editor_workspace, editor_repo):
    payload = list_editor_browse(editor_workspace, ".")
    kinds = {entry["name"]: entry["kind"] for entry in payload["entries"]}
    assert kinds == {"client": "directory", "README.md": "file"}


def test_read_and_write_editor_file(editor_workspace, editor_repo):
    read_payload = read_editor_file(editor_workspace, "client/app.ts")
    assert "export const app" in read_payload["content"]
    assert read_payload["language"] == "typescript"

    write_editor_file(editor_workspace, "client/app.ts", "export const app = 2;\n")
    updated = read_editor_file(editor_workspace, "client/app.ts")
    assert updated["content"] == "export const app = 2;\n"


def test_checkout_branch_updates_context(editor_workspace, editor_repo):
    (editor_repo / "README.md").write_text("# on main\n", encoding="utf-8")
    subprocess.run(
        ["git", "checkout", "feature/editor"], cwd=editor_repo, check=True, capture_output=True
    )
    (editor_repo / "README.md").write_text("# on feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=editor_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feature change"], cwd=editor_repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=editor_repo, check=True, capture_output=True)

    refs = checkout_editor_branch(editor_workspace, "feature/editor")
    assert refs["current_branch"] == "feature/editor"
    content = read_editor_file(editor_workspace, "README.md")["content"]
    assert "# on feature" in content


def test_resolve_editor_root_rejects_unknown_path(editor_workspace, editor_repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="not a workspace root or registered git worktree"):
        resolve_editor_root(editor_workspace, str(outside))


def test_list_editor_refs_includes_branches_and_worktrees(editor_workspace, editor_repo):
    refs = list_editor_refs(editor_workspace)
    branch_names = {item["name"] for item in refs["branches"]}
    assert "main" in branch_names or "master" in branch_names
    assert "feature/editor" in branch_names
    assert len(refs["worktrees"]) >= 1


def test_editor_api_round_trip(client, editor_repo, db_session):
    ws = db_session.exec(
        __import__("sqlmodel").select(Workspace).where(Workspace.slug == "loregarden")
    ).first()
    assert ws is not None
    # The client fixture repoints the seeded workspace at its own throwaway repo;
    # restore the relative repo_path so it resolves against editor_repo instead.
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    browse = client.get(f"/api/workspaces/{ws.slug}/editor/browse")
    assert browse.status_code == 200
    assert any(entry["name"] == "client" for entry in browse.json()["entries"])

    read_res = client.get(f"/api/workspaces/{ws.slug}/editor/file", params={"path": "README.md"})
    assert read_res.status_code == 200
    assert "# test" in read_res.json()["content"]

    write_res = client.put(
        f"/api/workspaces/{ws.slug}/editor/file",
        json={"path": "README.md", "content": "# updated\n"},
    )
    assert write_res.status_code == 200
    assert write_res.json()["saved"] is True

    refs = client.get(f"/api/workspaces/{ws.slug}/editor/refs")
    assert refs.status_code == 200
    assert "branches" in refs.json()

    checkout = client.post(
        f"/api/workspaces/{ws.slug}/editor/checkout",
        json={"branch": "feature/editor"},
    )
    assert checkout.status_code == 200
    assert checkout.json()["current_branch"] == "feature/editor"
