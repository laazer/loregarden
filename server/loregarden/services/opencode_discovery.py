"""Discover OpenCode model pins from the local OpenCode CLI catalog.

``opencode models`` prints one ``provider/model`` id per line, and the list
depends on which providers the operator has authenticated — an OpenCode install
signed in to Zen sees different ids than one wired to a local LM Studio. No
static list would be right for both, so there is nothing to fall back to when
the CLI is missing: the picker shows only the "use the profile default" row and
the run uses whatever OpenCode itself selects.
"""

from __future__ import annotations

import logging
import os
import subprocess
from shutil import which

from loregarden.services.cli_settings import ADAPTER_BINARIES

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 12.0
DEFAULT_OPTION = {"id": "", "label": "Default (OpenCode profile)"}


def resolve_opencode_binary() -> str | None:
    """Absolute path or PATH name for the OpenCode CLI, if it can be spawned."""
    name, env_key = ADAPTER_BINARIES["opencode"]
    override = (os.environ.get(env_key) or "").strip()
    if override:
        return override if os.path.exists(override) else None
    return which(name)


def _parse_models_output(raw: str) -> list[str]:
    """Model ids from ``opencode models`` stdout, in the order the CLI printed them.

    The CLI decorates other commands with box-drawing output; ``models`` is a
    plain list today, so anything without a provider separator is treated as
    chrome rather than a pin.
    """
    seen: set[str] = set()
    ids: list[str] = []
    for line in (raw or "").splitlines():
        candidate = line.strip()
        if "/" not in candidate or " " in candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        ids.append(candidate)
    return ids


def list_opencode_models() -> list[str]:
    """Return the local OpenCode CLI's model ids, or []."""
    binary = resolve_opencode_binary()
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "models"],
            check=False,
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("OpenCode model discovery CLI failed: %s", exc)
        return []
    return _parse_models_output(completed.stdout)


def opencode_model_options() -> list[dict[str, str]]:
    """Runtime-options shaped list for the OpenCode model picker."""
    options: list[dict[str, str]] = [dict(DEFAULT_OPTION)]
    for model_id in list_opencode_models():
        options.append({"id": model_id, "label": model_id})
    return options
