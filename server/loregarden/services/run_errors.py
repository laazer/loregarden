"""Shared run failure messages."""

from __future__ import annotations

import re

_LEGACY_TIMEOUT_SUFFIX = re.compile(
    r"^(Agent timed out after \d+s): Command .* timed out after \d+ seconds?$",
    re.DOTALL,
)


def agent_timeout_message(timeout_seconds: int | float) -> str:
    seconds = int(timeout_seconds)
    return f"Agent timed out after {seconds}s"


def normalize_timeout_stderr(
    stderr: str,
    *,
    timeout_seconds: int | float | None = None,
) -> str:
    """Strip misleading subprocess TimeoutExpired suffixes from legacy messages."""
    cleaned = stderr.strip()
    legacy = _LEGACY_TIMEOUT_SUFFIX.match(cleaned)
    if legacy:
        return legacy.group(1)
    if timeout_seconds is not None:
        prefix = agent_timeout_message(timeout_seconds)
        if cleaned.startswith(prefix):
            return prefix
    return cleaned
