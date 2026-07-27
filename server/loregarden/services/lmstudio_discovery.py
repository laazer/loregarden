"""Discover chat models from a running LM Studio OpenAI-compatible server."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from loregarden.config import settings

logger = logging.getLogger(__name__)

DEFAULT_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DISCOVERY_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class LmStudioChatModel:
    id: str
    loaded: bool


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


def list_lmstudio_chat_models(base_url: str = "") -> list[LmStudioChatModel]:
    """Return chat models from LM Studio (prefer native API with load state)."""
    native_url = _native_models_url(base_url)
    openai_url = _openai_models_url(base_url)
    try:
        with httpx.Client(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
            try:
                response = client.get(native_url)
                response.raise_for_status()
                return _parse_native_models(response.json())
            except Exception as native_exc:  # noqa: BLE001 — try OpenAI fallback
                logger.debug(
                    "LM Studio native discovery failed at %s: %s; trying %s",
                    native_url,
                    native_exc,
                    openai_url,
                )
                response = client.get(openai_url)
                response.raise_for_status()
                return _parse_openai_models(response.json())
    except Exception as exc:  # noqa: BLE001 — discovery must never break Settings
        logger.debug("LM Studio model discovery failed at %s: %s", openai_url, exc)
        return []


def list_lmstudio_chat_model_ids(base_url: str = "") -> list[str]:
    """Return chat model ids from LM Studio, or [] if unreachable."""
    return [model.id for model in list_lmstudio_chat_models(base_url)]


def lmstudio_model_options(base_url: str = "") -> list[dict[str, str]]:
    """Runtime-options shaped list for the LM Studio model picker."""
    options: list[dict[str, str]] = [
        {"id": "", "label": "Auto (first loaded chat model)"},
    ]
    for model in list_lmstudio_chat_models(base_url):
        suffix = "loaded" if model.loaded else "not loaded"
        options.append({"id": model.id, "label": f"{model.id} · {suffix}"})
    return options
