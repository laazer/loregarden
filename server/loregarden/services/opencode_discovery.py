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
import threading
import time
from shutil import which

from loregarden.services.cli_settings import ADAPTER_BINARIES

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
# Longer than the discovery budget on purpose. A CLI that hangs burns the full
# timeout before it fails, so a failure TTL shorter than that budget lets the
# next request start probing the moment the last one gave up — a hung binary
# then keeps a probe in flight permanently instead of being retried now and
# then. ``test_a_hung_cli_cannot_be_probed_back_to_back`` pins the ordering.
FAILURE_CACHE_TTL_SECONDS = 60.0
DEFAULT_OPTION = {"id": "", "label": "Default (OpenCode profile)"}

#: Guards the cache fields below, and nothing else. It is never held across the
#: CLI probe: doing so made every concurrent caller queue behind a subprocess
#: that can take the better part of a minute.
_cache_lock = threading.Lock()
#: Single-flight. One caller probes; the rest are served the last answer rather
#: than piling up. ``runtime_options_payload`` runs inside a request holding a
#: database connection, so a probe that blocks N callers takes N connections out
#: of the pool for its whole duration — 15 of them exhausted the pool outright
#: and the API stopped answering, ``/health`` included.
_probe_lock = threading.Lock()
_cached_models: list[str] = []
_cache_expires_at = 0.0


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


def _list_from_cli() -> list[str]:
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
        # Warning, not debug: this is the only signal that an empty picker is a
        # discovery failure rather than an OpenCode install with no providers.
        logger.warning("OpenCode model discovery CLI failed: %s", exc)
        return []
    models = _parse_models_output(completed.stdout)
    if not models:
        logger.warning(
            "OpenCode model discovery listed nothing (exit %s): %r",
            completed.returncode,
            (completed.stderr or completed.stdout or "")[:200],
        )
    return models


def reset_model_cache() -> None:
    """Drop the memoized catalog so the next call re-runs the CLI."""
    global _cached_models, _cache_expires_at
    with _cache_lock:
        _cached_models = []
        _cache_expires_at = 0.0


def _read_cache() -> tuple[list[str], bool]:
    """The memoized catalog, and whether it is still fresh."""
    with _cache_lock:
        return list(_cached_models), time.monotonic() < _cache_expires_at


def _store_cache(models: list[str]) -> None:
    global _cached_models, _cache_expires_at
    with _cache_lock:
        _cached_models = list(models)
        _cache_expires_at = time.monotonic() + (
            CACHE_TTL_SECONDS if models else FAILURE_CACHE_TTL_SECONDS
        )


def list_opencode_models() -> list[str]:
    """Return the local OpenCode CLI's model ids, or []. Memoized for a few minutes.

    At most one caller probes the CLI at a time. Anyone arriving while a probe is
    in flight gets the last known answer immediately instead of waiting: the
    catalog is decoration for a picker, and no request should hold a database
    connection for the length of a subprocess to render it.
    """
    cached, fresh = _read_cache()
    if fresh:
        return cached

    if not _probe_lock.acquire(blocking=False):
        return cached

    try:
        # The holder may have finished between the read above and the acquire.
        cached, fresh = _read_cache()
        if fresh:
            return cached
        models = _list_from_cli()
        _store_cache(models)
        return list(models)
    finally:
        _probe_lock.release()


def opencode_model_options() -> list[dict[str, str]]:
    """Runtime-options shaped list for the OpenCode model picker."""
    options: list[dict[str, str]] = [dict(DEFAULT_OPTION)]
    for model_id in list_opencode_models():
        options.append({"id": model_id, "label": model_id})
    return options
