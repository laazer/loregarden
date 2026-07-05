"""Workspace file editor: browse, read/write, and git ref switching."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from loregarden.models.domain import Workspace
from loregarden.services.git_branch import validate_branch_name
from loregarden.services.path_browser import assert_browse_allowed, parent_browse_path, to_workspace_repo_path
from loregarden.services.workspace_paths import resolve_workspace_root

MAX_FILE_BYTES = 512_000
BLOCKED_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
}
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".css",
    ".html",
    ".sql",
    ".sh",
    ".toml",
    ".ini",
    ".env",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".xml",
    ".csv",
    ".graphql",
    ".dockerfile",
}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists() or _git(path, "rev-parse", "--git-dir").returncode == 0


def _parse_worktrees(repo_root: Path) -> list[dict[str, str]]:
    proc = _git(repo_root, "worktree", "list", "--porcelain")
    if proc.returncode != 0:
        return [{"path": str(repo_root.resolve()), "branch": _current_branch(repo_root), "label": "workspace"}]

    worktrees: list[dict[str, str]] = []
    current_path = ""
    current_branch = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            current_path = line.removeprefix("worktree ").strip()
        elif line.startswith("branch "):
            current_branch = line.removeprefix("branch refs/heads/").strip()
        elif line == "detached":
            current_branch = "(detached)"
        elif line == "" and current_path:
            label = current_branch or current_path
            if current_path == str(repo_root.resolve()):
                label = f"{current_branch or 'workspace'} (main)"
            worktrees.append(
                {
                    "path": current_path,
                    "branch": current_branch,
                    "label": label,
                }
            )
            current_path = ""
            current_branch = ""
    if current_path:
        label = current_branch or current_path
        if current_path == str(repo_root.resolve()):
            label = f"{current_branch or 'workspace'} (main)"
        worktrees.append({"path": current_path, "branch": current_branch, "label": label})
    return worktrees


def _list_branches(repo_root: Path) -> list[str]:
    proc = _git(repo_root, "branch", "--format=%(refname:short)")
    if proc.returncode != 0:
        return []
    branches = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    return sorted(set(branches), key=str.lower)


def _current_branch(repo_root: Path) -> str:
    proc = _git(repo_root, "branch", "--show-current")
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _allowed_worktree_paths(workspace_root: Path) -> set[str]:
    return {item["path"] for item in _parse_worktrees(workspace_root)}


def resolve_editor_root(workspace: Workspace, context_root: str | None = None) -> Path:
    workspace_root = resolve_workspace_root(workspace)
    raw = (context_root or ".").strip() or "."
    if raw == ".":
        return assert_browse_allowed(workspace_root)

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (workspace_root / raw).resolve()
    else:
        candidate = candidate.resolve()

    assert_browse_allowed(candidate)
    allowed = _allowed_worktree_paths(workspace_root)
    workspace_resolved = str(workspace_root.resolve())
    if str(candidate) not in allowed and str(candidate) != workspace_resolved:
        raise ValueError("Editor context is not a workspace root or registered git worktree")
    if not candidate.is_dir():
        raise ValueError("Editor context is not a directory")
    return candidate


def resolve_editor_file(root: Path, rel_path: str) -> Path:
    text = (rel_path or "").strip()
    if not text or text.startswith("/") or ".." in Path(text).parts:
        raise ValueError("Invalid file path")
    target = (root / text).resolve()
    assert_browse_allowed(target)
    if not str(target).startswith(str(root.resolve())):
        raise ValueError("Path escapes editor root")
    return target


def _is_text_file(name: str) -> bool:
    lowered = name.lower()
    if lowered in {"dockerfile", "makefile", "license", "readme"}:
        return True
    return Path(name).suffix.lower() in TEXT_SUFFIXES


def list_editor_refs(workspace: Workspace, *, context_root: str | None = None) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    editor_root = resolve_editor_root(workspace, context_root)
    branches = _list_branches(workspace_root) if _is_git_repo(workspace_root) else []
    worktrees = _parse_worktrees(workspace_root) if _is_git_repo(workspace_root) else []
    current_branch = _current_branch(editor_root) if _is_git_repo(editor_root) else ""
    return {
        "workspace_root": str(workspace_root.resolve()),
        "context_root": to_workspace_repo_path(editor_root, repo_root=workspace_root),
        "context_path": str(editor_root.resolve()),
        "current_branch": current_branch,
        "branches": [
            {"name": name, "current": name == current_branch and editor_root.resolve() == workspace_root.resolve()}
            for name in branches
        ],
        "worktrees": [
            {
                **item,
                "current": item["path"] == str(editor_root.resolve()),
                "repo_path": to_workspace_repo_path(Path(item["path"]), repo_root=workspace_root)
                if Path(item["path"]).resolve() != workspace_root.resolve()
                else ".",
            }
            for item in worktrees
        ],
    }


def checkout_editor_branch(workspace: Workspace, branch: str) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    if not _is_git_repo(workspace_root):
        raise ValueError("Workspace is not a git repository")
    validate_branch_name(branch)
    proc = _git(workspace_root, "checkout", branch)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "checkout failed").strip()
        raise ValueError(detail)
    return list_editor_refs(workspace, context_root=".")


def list_editor_browse(workspace: Workspace, path: str | None = None, *, context_root: str | None = None) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    editor_root = resolve_editor_root(workspace, context_root)
    rel = (path or ".").strip() or "."
    if rel == ".":
        current = editor_root
    else:
        current = resolve_editor_file(editor_root, rel)
    if not current.is_dir():
        raise ValueError("Not a directory")

    entries: list[dict[str, str]] = []
    try:
        with os.scandir(current) as scan:
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                if entry.name in BLOCKED_DIR_NAMES:
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if not is_dir and not (is_file and _is_text_file(entry.name)):
                    continue
                child = Path(entry.path).resolve()
                assert_browse_allowed(child)
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(child),
                        "repo_path": to_workspace_repo_path(child, repo_root=editor_root),
                        "kind": "directory" if is_dir else "file",
                    }
                )
    except PermissionError as exc:
        raise ValueError(f"Permission denied: {current}") from exc

    entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].lower()))
    parent_repo_path = None
    parent = parent_browse_path(current)
    if parent:
        parent_repo_path = to_workspace_repo_path(Path(parent), repo_root=editor_root)
    return {
        "current_path": str(current),
        "repo_path": to_workspace_repo_path(current, repo_root=editor_root),
        "parent_path": parent,
        "parent_repo_path": parent_repo_path,
        "context_root": to_workspace_repo_path(editor_root, repo_root=workspace_root),
        "context_path": str(editor_root.resolve()),
        "entries": entries,
    }


def read_editor_file(workspace: Workspace, path: str, *, context_root: str | None = None) -> dict:
    editor_root = resolve_editor_root(workspace, context_root)
    target = resolve_editor_file(editor_root, path)
    if not target.is_file():
        raise ValueError("Not a file")
    if not _is_text_file(target.name):
        raise ValueError(f"Unsupported file type: {target.name}")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"File too large to edit ({size} bytes)")
    content = target.read_text(encoding="utf-8")
    return {
        "path": to_workspace_repo_path(target, repo_root=editor_root),
        "content": content,
        "language": detect_language(target.name),
        "size": size,
    }


def write_editor_file(
    workspace: Workspace,
    path: str,
    content: str,
    *,
    context_root: str | None = None,
) -> dict:
    editor_root = resolve_editor_root(workspace, context_root)
    target = resolve_editor_file(editor_root, path)
    if target.is_dir():
        raise ValueError("Cannot write to a directory")
    if not _is_text_file(target.name):
        raise ValueError(f"Unsupported file type: {target.name}")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Content exceeds maximum editable file size")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": to_workspace_repo_path(target, repo_root=editor_root),
        "saved": True,
        "size": target.stat().st_size,
    }


def detect_language(name: str) -> str:
    lowered = name.lower()
    suffix = Path(name).suffix.lower()
    mapping = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".css": "css",
        ".html": "html",
        ".sql": "sql",
        ".sh": "shell",
        ".toml": "toml",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".kt": "kotlin",
        ".rb": "ruby",
        ".php": "php",
        ".xml": "xml",
        ".graphql": "graphql",
    }
    if lowered == "dockerfile":
        return "dockerfile"
    return mapping.get(suffix, "plaintext")
