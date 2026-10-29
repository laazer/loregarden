"""Discover chat models from a running LM Studio OpenAI-compatible server.

An unreachable server is the normal case on a machine with no LM Studio, and it
is also the one state whose copy has to name the URL that was asked — which the
client cannot reconstruct from a workspace row. So the probe classifies what it
caught rather than collapsing every failure into an empty list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from loregarden.config import settings
from loregarden.services.discovery_cache import (
    ProbeAnswer,
    ProbeCache,
    ProbeOutcome,
    clamp_detail,
)

logger = logging.getLogger(__name__)

DEFAULT_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DISCOVERY_TIMEOUT_SECONDS = 2.0
# An unreachable server costs both attempts — native, then the OpenAI-compat
# fallback — so the budget a caller can actually wait is twice the per-request
# timeout, and that is the number the failure TTL has to outlast.
PROBE_BUDGET_SECONDS = DISCOVERY_TIMEOUT_SECONDS * 2
# Short, because the picker reports which models are *loaded* and the operator
# changes that in LM Studio while the settings modal is open. A reachable server
# answers in milliseconds, so re-probing this often is nearly free; it is the
# unreachable case that is slow, and that is held by the failure TTL below.
CACHE_TTL_SECONDS = 30.0
FAILURE_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class LmStudioChatModel:
    id: str
    loaded: bool


_CACHE: ProbeCache[LmStudioChatModel] = ProbeCache(
    label="lmstudio",
    probe_budget_seconds=PROBE_BUDGET_SECONDS,
    success_ttl_seconds=CACHE_TTL_SECONDS,
    failure_ttl_seconds=FAILURE_CACHE_TTL_SECONDS,
)


def is_chat_lmstudio_model(model_id: str) -> bool:
    """Skip embedding / reranker ids — they are not usable as agent chat models."""
    lowered = (model_id or "").lower()
    if not lowered:
        return False
    banned = ("embed", "embedding", "rerank", "whisper", "tts", "stt")
    return not any(token in lowered for token in banned)


def normalize_lmstudio_base_url(base_url: str = "") -> str:
    raw = (base_url or "").strip() or settings.lmstudio_base_url or DEFAULT_LMSTUDIO_BASE_URL
    return raw.rstrip("/")


def _native_models_url(openai_base_url: str) -> str:
    """Map OpenAI-compat base (.../v1) to LM Studio native list (.../api/v0/models)."""
    base = normalize_lmstudio_base_url(openai_base_url)
    root = base[:-3] if base.endswith("/v1") else base
    return f"{root.rstrip('/')}/api/v0/models"


def _openai_models_url(openai_base_url: str) -> str:
    return f"{normalize_lmstudio_base_url(openai_base_url)}/models"


def _parse_native_models(payload: object) -> list[LmStudioChatModel]:
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []

    found: list[LmStudioChatModel] = []
    seen: set[str] = set()
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_type = str(entry.get("type") or "").lower()
        if model_type in {"embeddings", "embedding"}:
            continue
        model_id = str(entry.get("id") or entry.get("name") or "").strip()
        if not model_id or not is_chat_lmstudio_model(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        state = str(entry.get("state") or "").lower()
        found.append(LmStudioChatModel(id=model_id, loaded=state == "loaded"))
    # Loaded first so the picker puts ready models at the top.
    found.sort(key=lambda m: (not m.loaded, m.id.lower()))
    return found


def _parse_openai_models(payload: object) -> list[LmStudioChatModel]:
    """Fallback when native /api/v0 is unavailable — load state unknown."""
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []

    found: list[LmStudioChatModel] = []
    seen: set[str] = set()
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or entry.get("name") or "").strip()
        if not model_id or not is_chat_lmstudio_model(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        # OpenAI-compat /v1/models does not report load state; treat as not loaded
        # so we do not imply readiness we cannot verify.
        found.append(LmStudioChatModel(id=model_id, loaded=False))
    return found


def reset_model_cache() -> None:
    """Drop every memoized catalog so the next call re-probes."""
    _CACHE.reset()


def list_lmstudio_chat_models(base_url: str = "") -> ProbeAnswer[LmStudioChatModel]:
    """Chat models from LM Studio (native API first, for load state), and their state.

    Memoized per server, single-flighted, and probed on a background thread: no
    caller waits for the server, and an unreachable one costs both attempts.
    """
    # A cold key has no outcome to read a target from, and the empty-state copy
    # names the base URL, so the module answers for it.
    target = normalize_lmstudio_base_url(base_url)
    return _CACHE.get(target, lambda: _probe(base_url), target=target)


def _fetch_models(base_url: str, target: str) -> ProbeOutcome[LmStudioChatModel]:
    """One reachable-server answer, native API first so load state is known."""
    native_url = _native_models_url(base_url)
    openai_url = _openai_models_url(base_url)
    with httpx.Client(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
        try:
            response = client.get(native_url)
            response.raise_for_status()
            return ProbeOutcome(found=_parse_native_models(response.json()), ok=True, target=target)
        except Exception as native_exc:  # noqa: BLE001 — try OpenAI fallback
            # silent-ok: /api/v0 is absent on older LM Studio builds; the
            # OpenAI-compat fallback runs on the next line and `_probe`'s
            # handlers classify and report it when that one fails too
            logger.debug(
                "LM Studio native discovery failed at %s: %s; trying %s",
                native_url,
                native_exc,
                openai_url,
            )
            response = client.get(openai_url)
            response.raise_for_status()
            return ProbeOutcome(found=_parse_openai_models(response.json()), ok=True, target=target)


def _probe(base_url: str) -> ProbeOutcome[LmStudioChatModel]:
    """Ask LM Studio, and say which way it failed if it did.

    The failure classes are separate `except` clauses rather than one handler
    that inspects what it caught: the reason reaches the operator's field hint
    verbatim, so "connection refused" and "timed out after 2s" are different
    answers and only the fallthrough interpolates an exception at all.
    """
    target = normalize_lmstudio_base_url(base_url)
    try:
        return _fetch_models(base_url, target)
    except httpx.TimeoutException as exc:
        failure, reason = exc, f"timed out after {DISCOVERY_TIMEOUT_SECONDS:g}s"
    except httpx.ConnectError as exc:
        failure, reason = exc, "connection refused"
    except httpx.HTTPStatusError as exc:
        failure, reason = exc, f"HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001 — reported below, never discarded
        # An exception whose `str()` is empty still has to read as a sentence:
        # `RuntimeError()` would otherwise render "… — ." as the whole
        # explanation, in the one state that exists to explain itself.
        failure, reason = exc, " ".join(str(exc).split()) or type(exc).__name__

    logger.warning(
        "LM Studio model discovery failed at %s; the picker falls back to "
        "'Auto (first loaded chat model)': %s",
        _openai_models_url(base_url),
        failure,
    )
    return ProbeOutcome(
        found=[],
        ok=False,
        detail=clamp_detail(f"Could not reach LM Studio at {target} — {reason}."),
        target=target,
    )


def list_lmstudio_chat_model_ids(base_url: str = "") -> list[str]:
    """Chat model ids from LM Studio, or [] if it had nothing or was unreachable."""
    return [model.id for model in list_lmstudio_chat_models(base_url).found]


def lmstudio_model_options(
    base_url: str = "",
) -> tuple[list[dict[str, str]], dict[str, object]]:
    """Runtime-options shaped list for the LM Studio model picker, and its state."""
    answer = list_lmstudio_chat_models(base_url)
    options: list[dict[str, str]] = [
        {"id": "", "label": "Auto (first loaded chat model)"},
    ]
    for model in answer.found:
        suffix = "loaded" if model.loaded else "not loaded"
        options.append({"id": model.id, "label": f"{model.id} · {suffix}"})
    return options, answer.as_discovery_block()
