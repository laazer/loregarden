"""Discover OpenCode model pins from the local OpenCode CLI catalog.

``opencode models`` prints one ``provider/model`` id per line, and the list
depends on which providers the operator has authenticated — an OpenCode install
signed in to Zen sees different ids than one wired to a local LM Studio. No
static list would be right for both, so there is nothing to fall back to when
the CLI is missing: the picker shows only the "use the profile default" row and
the run uses whatever OpenCode itself selects.

Which is why the *reason* the row is alone has to travel with it. "OpenCode is
still being asked" and "OpenCode is not installed" both used to render an empty
picker that asserted the second.
"""

from __future__ import annotations

import logging
import os
import subprocess
from shutil import which

from loregarden.services.cli_settings import ADAPTER_BINARIES
from loregarden.services.discovery_cache import (
    ProbeAnswer,
    ProbeCache,
    ProbeOutcome,
    clamp_detail,
)

logger = logging.getLogger(__name__)

# ``opencode models`` refreshes each authenticated provider's catalog over the
# network before it prints, which measures ~15s on a warm install — the old 12s
# budget expired every single time and the picker was permanently empty.
DISCOVERY_TIMEOUT_SECONDS = 45.0
# Paying that cost on every runtime-options request would stall the settings
# modal, so hold the answer. A failure is held briefly instead: the usual causes
# (CLI absent, provider not yet authenticated) are fixed by the operator, who
# should not wait five minutes to see the result.
CACHE_TTL_SECONDS = 300.0
# Longer than the discovery budget on purpose, and ProbeCache refuses to be built
# otherwise: a CLI that hangs burns the full timeout before it fails, so a
# shorter failure TTL lets the next request start probing the moment the last one
# gave up.
FAILURE_CACHE_TTL_SECONDS = 60.0
DEFAULT_OPTION = {"id": "", "label": "Default (OpenCode profile)"}

_CACHE: ProbeCache[str] = ProbeCache(
    label="opencode",
    probe_budget_seconds=DISCOVERY_TIMEOUT_SECONDS,
    success_ttl_seconds=CACHE_TTL_SECONDS,
    failure_ttl_seconds=FAILURE_CACHE_TTL_SECONDS,
)
#: One catalog, so one entry.
_CACHE_KEY = ""


def resolve_opencode_binary() -> str | None:
    """Absolute path or PATH name for the OpenCode CLI, if it can be spawned."""
    name, env_key = ADAPTER_BINARIES["opencode"]
    override = (os.environ.get(env_key) or "").strip()
    if override:
        return override if os.path.exists(override) else None
    return which(name)


def discovery_target() -> str:
    """What the probe is (or would be) asking — resolvable without probing.

    A cold key has no outcome to read a target from, and the picker's own copy
    names the thing it is waiting on, so the module answers for it.
    """
    return resolve_opencode_binary() or ADAPTER_BINARIES["opencode"][0]


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


def _list_from_cli() -> ProbeOutcome[str]:
    binary = resolve_opencode_binary()
    if not binary:
        return ProbeOutcome(
            found=[],
            ok=False,
            detail="OpenCode CLI not found. Install OpenCode, or set LOREGARDEN_OPENCODE_BIN.",
            target=ADAPTER_BINARIES["opencode"][0],
        )
    try:
        completed = subprocess.run(
            [binary, "models"],
            check=False,
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        detail = f"`opencode models` did not answer within {DISCOVERY_TIMEOUT_SECONDS:g}s."
        # Warning, not debug: this is the only signal that an empty picker is a
        # discovery failure rather than an OpenCode install with no providers.
        logger.warning("OpenCode model discovery timed out running %s", binary)
        return ProbeOutcome(found=[], ok=False, detail=detail, target=binary)
    except OSError as exc:
        logger.warning("OpenCode model discovery could not run %s: %s", binary, exc)
        return ProbeOutcome(
            found=[],
            ok=False,
            detail=clamp_detail(f"`opencode models` could not be run: {exc}."),
            target=binary,
        )
    models = _parse_models_output(completed.stdout)
    if not models:
        # The CLI answered, so this is not a failure: OpenCode signed in to no
        # provider has an empty catalogue rather than a broken one.
        logger.warning(
            "OpenCode model discovery listed nothing (exit %s): %r",
            completed.returncode,
            (completed.stderr or completed.stdout or "")[:200],
        )
    return ProbeOutcome(found=models, ok=True, target=binary)


def reset_model_cache() -> None:
    """Drop the memoized catalog so the next call re-runs the CLI."""
    _CACHE.reset()


def list_opencode_models() -> ProbeAnswer[str]:
    """The local OpenCode CLI's model ids, and which discovery state they are in."""
    return _CACHE.get(_CACHE_KEY, _list_from_cli, target=discovery_target())


def opencode_model_options() -> tuple[list[dict[str, str]], dict[str, object]]:
    """Runtime-options shaped list for the OpenCode model picker, and its state."""
    answer = list_opencode_models()
    options: list[dict[str, str]] = [dict(DEFAULT_OPTION)]
    for model_id in answer.found:
        options.append({"id": model_id, "label": model_id})
    return options, answer.as_discovery_block()
