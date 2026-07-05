"""Filesystem path helpers — tilde expansion, repo-relative paths, iCloud detection."""

from __future__ import annotations

import os
from pathlib import Path

ICLOUD_DRIVE_CONTAINER = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"


def expand_path(raw: str | Path, *, repo_root: Path | None = None) -> Path:
    text = str(raw).strip()
    if not text:
        raise ValueError("path is required")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute() and repo_root is not None:
        path = repo_root / path
    return path.resolve(strict=False)


def detect_icloud_root() -> Path | None:
    if ICLOUD_DRIVE_CONTAINER.is_dir():
        return ICLOUD_DRIVE_CONTAINER.resolve()
    return None


def resolve_icloud_root(override: str = "") -> Path | None:
    text = override.strip()
    if text:
        path = expand_path(text)
        return path if path.is_dir() else None
    return detect_icloud_root()


def is_under_icloud(path: Path, icloud_root: Path | None = None) -> bool:
    root = icloud_root or detect_icloud_root()
    if not root:
        return False
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_sqlite_path(database_url: str, repo_root: Path) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError(f"expected sqlite URL, got: {database_url}")
    raw = database_url.removeprefix("sqlite:///")
    return expand_path(raw, repo_root=repo_root)


def sqlite_url_for_path(db_path: Path) -> str:
    return f"sqlite:///{db_path.resolve()}"
