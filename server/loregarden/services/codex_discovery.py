"""Discover Codex model pins from the local Codex CLI catalog.

The static ``CODEX_MODEL_OPTIONS`` list went stale the moment OpenAI renamed
models — pinning ``gpt-5`` against a ChatGPT-signed-in Codex account fails with
a 400. Prefer the live catalog Codex itself ships:

1. ``codex debug models`` (refreshes against the signed-in account)
2. ``~/.codex/models_cache.json`` if the CLI is missing or times out
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loregarden.services.cli_settings import ADAPTER_BINARIES

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 12.0
DEFAULT_OPTION = {"id": "", "label": "Default (Codex profile)"}


@dataclass(frozen=True)
class CodexModel:
    slug: str
    display_name: str
    priority: int = 0


def resolve_codex_binary() -> str | None:
    """Absolute path or PATH name for the Codex CLI, if it can be spawned."""
    name, env_key = ADAPTER_BINARIES["codex"]
    override = (os.environ.get(env_key) or "").strip()
    if override:
        return override if os.path.exists(override) else None
    from shutil import which

    return which(name)


def _codex_home() -> Path:
    override = (os.environ.get("CODEX_HOME") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".codex"


def _parse_models_payload(payload: object) -> list[CodexModel]:
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []

    found: list[CodexModel] = []
    seen: set[str] = set()
    for entry in models:
        if not isinstance(entry, dict):
            continue
        # Codex hides retired / internal ids from its own picker; match that.
        visibility = str(entry.get("visibility") or "list").lower()
        if visibility == "hide":
            continue
        slug = str(entry.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        display = str(entry.get("display_name") or slug).strip() or slug
        try:
            priority = int(entry.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        found.append(CodexModel(slug=slug, display_name=display, priority=priority))
    found.sort(key=lambda m: (m.priority, m.slug.lower()))
    return found


def _json_object_from_cli_output(raw: str) -> object | None:
    """Codex may print warnings before the JSON payload."""
    text = (raw or "").strip()
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def _list_from_cli() -> list[CodexModel]:
    binary = resolve_codex_binary()
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "debug", "models"],
            check=False,
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Codex model discovery CLI failed: %s", exc)
        return []

    payload = _json_object_from_cli_output(completed.stdout) or _json_object_from_cli_output(
        completed.stderr
    )
    if payload is None:
        logger.debug(
            "Codex model discovery returned no JSON (exit %s)",
            completed.returncode,
        )
        return []
    return _parse_models_payload(payload)


def _list_from_cache() -> list[CodexModel]:
    path = _codex_home() / "models_cache.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Codex models cache unreadable at %s: %s", path, exc)
        return []
    return _parse_models_payload(payload)


def list_codex_models() -> list[CodexModel]:
    """Return listable Codex models for the signed-in account, or []."""
    models = _list_from_cli()
    if models:
        return models
    return _list_from_cache()


def codex_model_options() -> list[dict[str, str]]:
    """Runtime-options shaped list for the Codex model picker."""
    options: list[dict[str, str]] = [dict(DEFAULT_OPTION)]
    for model in list_codex_models():
        options.append({"id": model.slug, "label": model.display_name})
    return options
