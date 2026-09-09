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
from loregarden.services.discovery_cache import ProbeCache

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 12.0
# ``codex debug models`` used to run on every runtime-options request, inside a
# request holding a database connection. Hold the catalog instead: it changes
# when OpenAI ships a model or the operator signs in, not between two keystrokes
# in the settings modal.
CACHE_TTL_SECONDS = 300.0
# Must outlast DISCOVERY_TIMEOUT_SECONDS — ProbeCache refuses to be built
# otherwise. A CLI that hangs burns the whole budget before it fails, and a
# shorter failure TTL would let the next caller start probing as this one
# gives up.
FAILURE_CACHE_TTL_SECONDS = 60.0
DEFAULT_OPTION = {"id": "", "label": "Default (Codex profile)"}


@dataclass(frozen=True)
class CodexModel:
    slug: str
    display_name: str
    priority: int = 0


_CACHE: ProbeCache[CodexModel] = ProbeCache(
    probe_budget_seconds=DISCOVERY_TIMEOUT_SECONDS,
    success_ttl_seconds=CACHE_TTL_SECONDS,
    failure_ttl_seconds=FAILURE_CACHE_TTL_SECONDS,
)
#: One catalog, so one entry.
_CACHE_KEY = ""


def resolve_codex_binary() -> str | None:
    """Absolute path or PATH name for the Codex CLI, if it can be spawned."""
    name, env_key = ADAPTER_BINARIES["codex"]
    override = (os.environ.get(env_key) or "").strip()
    if override:
        return override if os.path.exists(override) else None
    from shutil import which

    return which(name)


def codex_home() -> Path:
    """Root of the local Codex CLI state directory (``$CODEX_HOME`` or ``~/.codex``)."""
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
            # silent-ok: priority only orders the picker; a malformed value sorts
            # the model first and it is still listed and selectable
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
        logger.warning(
            "Codex model discovery failed running %s; falling back to the on-disk models cache: %s",
            binary,
            exc,
        )
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
    path = codex_home() / "models_cache.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Codex models cache unreadable at %s and the CLI listed nothing, so "
            "the picker offers only the profile default: %s",
            path,
            exc,
        )
        return []
    return _parse_models_payload(payload)


def _discover() -> list[CodexModel]:
    models = _list_from_cli()
    if models:
        return models
    return _list_from_cache()


def reset_model_cache() -> None:
    """Drop the memoized catalog so the next call re-runs discovery."""
    _CACHE.reset()


def list_codex_models() -> list[CodexModel]:
    """Return listable Codex models for the signed-in account, or [].

    Memoized, and single-flighted: one caller probes while the rest are served
    the last known catalog. A queue of callers behind a 12s subprocess is a
    queue of held database connections.
    """
    return _CACHE.get(_CACHE_KEY, _discover)


def codex_model_options() -> list[dict[str, str]]:
    """Runtime-options shaped list for the Codex model picker."""
    options: list[dict[str, str]] = [dict(DEFAULT_OPTION)]
    for model in list_codex_models():
        options.append({"id": model.slug, "label": model.display_name})
    return options
