"""Directory browsing for workspace repo_path selection."""

from __future__ import annotations

import os
from pathlib import Path

from loregarden.config import settings
from loregarden.services.path_resolve import expand_path

BROWSE_CEILING = Path.home().resolve()
IMPORTABLE_SUFFIXES = {".md", ".json", ".yaml", ".yml"}


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return path.resolve() == root.resolve()


def resolve_browse_target(raw: str | None, *, repo_root: Path | None = None) -> Path:
    root = (repo_root or settings.repo_root).resolve()
    text = (raw or "").strip()
    if not text or text == ".":
        return root
    return expand_path(text, repo_root=root).resolve()


def normalize_browse_target(raw: str | None, *, repo_root: Path | None = None) -> Path:
    """Resolve a browse path, falling back when the seed is a file, URL, or missing path."""
    root = (repo_root or settings.repo_root).resolve()
    text = (raw or "").strip()
    if not text or text == ".":
        return root
    if text.lower().startswith("sqlite:///"):
        return root

    try:
        target = assert_browse_allowed(resolve_browse_target(text, repo_root=root))
    except ValueError as exc:
        if "outside the allowed browse scope" in str(exc):
            raise
        return root

    if target.is_file():
        target = target.parent
    if target.is_dir():
        return target

    cursor = target
    for _ in range(32):
        parent = cursor.parent
        if parent == cursor:
            break
        try:
            parent = assert_browse_allowed(parent)
        except ValueError:
            break
        if parent.is_dir():
            return parent
        cursor = parent

    if root.is_dir():
        return root
    raise ValueError(f"Not a directory: {target}")


def assert_browse_allowed(path: Path) -> Path:
    resolved = path.resolve()
    if not is_under(resolved, BROWSE_CEILING):
        raise ValueError("Path is outside the allowed browse scope")
    return resolved


def to_workspace_repo_path(target: Path, *, repo_root: Path) -> str:
    target = target.resolve()
    root = repo_root.resolve()
    if target == root:
        return "."
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return str(target)


def parent_browse_path(path: Path) -> str | None:
    resolved = path.resolve()
    parent = resolved.parent
    if parent == resolved or not is_under(parent, BROWSE_CEILING):
        return None
    return str(parent)


def list_browse(path: Path, *, repo_root: Path | None = None) -> dict:
    root = (repo_root or settings.repo_root).resolve()
    current = assert_browse_allowed(path)
    if not current.is_dir():
        raise ValueError("Not a directory")

    entries: list[dict[str, str]] = []
    try:
        with os.scandir(current) as scan:
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                child = Path(entry.path).resolve()
                if not is_under(child, BROWSE_CEILING):
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(child),
                        "repo_path": to_workspace_repo_path(child, repo_root=root),
                    }
                )
    except PermissionError as exc:
        raise ValueError(f"Permission denied: {current}") from exc

    entries.sort(key=lambda item: item["name"].lower())
    return {
        "current_path": str(current),
        "repo_path": to_workspace_repo_path(current, repo_root=root),
        "parent_path": parent_browse_path(current),
        "repo_root": str(root),
        "entries": entries,
    }


def _is_importable_file(name: str) -> bool:
    return Path(name).suffix.lower() in IMPORTABLE_SUFFIXES


def list_import_browse(path: Path, *, repo_root: Path | None = None) -> dict:
    root = (repo_root or settings.repo_root).resolve()
    current = assert_browse_allowed(path)
    if not current.is_dir():
        raise ValueError("Not a directory")

    entries: list[dict[str, str]] = []
    try:
        with os.scandir(current) as scan:
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if not is_dir and not (is_file and _is_importable_file(entry.name)):
                    continue
                child = Path(entry.path).resolve()
                if not is_under(child, BROWSE_CEILING):
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(child),
                        "repo_path": to_workspace_repo_path(child, repo_root=root),
                        "kind": "directory" if is_dir else "file",
                    }
                )
    except PermissionError as exc:
        raise ValueError(f"Permission denied: {current}") from exc

    entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].lower()))
    return {
        "current_path": str(current),
        "repo_path": to_workspace_repo_path(current, repo_root=root),
        "parent_path": parent_browse_path(current),
        "repo_root": str(root),
        "entries": entries,
    }


def read_import_files(paths: list[str]) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for raw in paths:
        text = raw.strip()
        if not text:
            continue
        target = assert_browse_allowed(Path(text))
        if not target.is_file():
            raise ValueError(f"Not a file: {text}")
        if not _is_importable_file(target.name):
            raise ValueError(f"Unsupported import file type: {target.name}")
        files.append((target.name, target.read_text(encoding="utf-8")))
    return files
