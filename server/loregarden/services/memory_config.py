"""Persist and apply iCloud / Obsidian memory settings from data/memory.local.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loregarden.config import settings
from loregarden.services.path_resolve import (
    detect_icloud_root,
    detect_mobile_documents_root,
    detect_obsidian_documents_dir,
    detect_obsidian_icloud_dir,
    expand_path,
    resolve_sqlite_path,
)

MEMORY_CONFIG_FILENAME = "memory.local.json"
CONFIG_KEYS = (
    "icloud_root",
    "obsidian_vault_dir",
    "obsidian_memory_subdir",
    "obsidian_learnings_subdir",
    "obsidian_blogposts_subdir",
    "memory_sqlite_url",
    "database_url",
)


def memory_config_path(repo_root: Path | None = None) -> Path:
    root = (repo_root or settings.repo_root).resolve()
    return root / "data" / MEMORY_CONFIG_FILENAME


def read_local_memory_config(repo_root: Path | None = None) -> dict[str, str]:
    path = memory_config_path(repo_root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = value.strip()
    if not out.get("obsidian_blogposts_subdir"):
        out["obsidian_blogposts_subdir"] = settings.obsidian_blogposts_subdir
    return out


def write_local_memory_config(payload: dict[str, str], repo_root: Path | None = None) -> Path:
    path = memory_config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {key: payload.get(key, "").strip() for key in CONFIG_KEYS}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def current_memory_config() -> dict[str, str]:
    out = {key: str(getattr(settings, key, "") or "").strip() for key in CONFIG_KEYS}
    if not out.get("obsidian_blogposts_subdir"):
        out["obsidian_blogposts_subdir"] = "Loregarden/BlogPosts"
    return out


def apply_memory_config(payload: dict[str, Any], *, persist: bool = True) -> dict[str, str]:
    cleaned = _validate_memory_config(payload)
    for key, value in cleaned.items():
        setattr(settings, key, value)
    if persist:
        write_local_memory_config(cleaned)
    return cleaned


def load_local_memory_config_into_settings() -> bool:
    overrides = read_local_memory_config()
    if not overrides:
        return False
    apply_memory_config(overrides, persist=False)
    return True


def _validate_memory_config(payload: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key in CONFIG_KEYS:
        raw = payload.get(key, "")
        cleaned[key] = raw.strip() if isinstance(raw, str) else str(raw or "").strip()

    if cleaned["icloud_root"]:
        path = expand_path(cleaned["icloud_root"], repo_root=settings.repo_root)
        if not path.is_dir():
            raise ValueError(f"iCloud root is not a directory: {cleaned['icloud_root']}")

    if cleaned["obsidian_vault_dir"]:
        path = expand_path(cleaned["obsidian_vault_dir"], repo_root=settings.repo_root)
        if not path.is_dir():
            raise ValueError(f"Obsidian vault is not a directory: {cleaned['obsidian_vault_dir']}")

    for url_key in ("memory_sqlite_url", "database_url"):
        url = cleaned[url_key]
        if url:
            resolve_sqlite_path(url, settings.repo_root)

    for subdir_key in (
        "obsidian_memory_subdir",
        "obsidian_learnings_subdir",
        "obsidian_blogposts_subdir",
    ):
        subdir = cleaned[subdir_key]
        if not subdir:
            raise ValueError(f"{subdir_key} is required")

    return cleaned


def memory_config_defaults() -> dict[str, str | None]:
    detected = detect_icloud_root()
    mobile = detect_mobile_documents_root()
    obsidian_icloud = detect_obsidian_icloud_dir()
    obsidian_documents = detect_obsidian_documents_dir()
    return {
        "icloud_root": str(detected) if detected else None,
        "mobile_documents_dir": str(mobile) if mobile else None,
        "obsidian_icloud_dir": str(obsidian_icloud) if obsidian_icloud else None,
        "obsidian_documents_dir": str(obsidian_documents) if obsidian_documents else None,
    }
