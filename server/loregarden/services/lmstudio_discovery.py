"""Discover chat models from a running LM Studio OpenAI-compatible server."""

from __future__ import annotations

import logging

import httpx
from loregarden.config import settings

logger = logging.getLogger(__name__)

DEFAULT_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DISCOVERY_TIMEOUT_SECONDS = 2.0


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


def list_lmstudio_chat_model_ids(base_url: str = "") -> list[str]:
    """Return loaded chat model ids from LM Studio, or [] if unreachable."""
    url = normalize_lmstudio_base_url(base_url)
    try:
        with httpx.Client(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
            response = client.get(f"{url}/models")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 — discovery must never break Settings
        logger.debug("LM Studio model discovery failed at %s: %s", url, exc)
        return []

    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []

    ids: list[str] = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or entry.get("name") or "").strip()
        if model_id and is_chat_lmstudio_model(model_id) and model_id not in ids:
            ids.append(model_id)
    return ids


def lmstudio_model_options(base_url: str = "") -> list[dict[str, str]]:
    """Runtime-options shaped list for the LM Studio model picker."""
    options: list[dict[str, str]] = [
        {"id": "", "label": "Auto (first loaded chat model)"},
    ]
    for model_id in list_lmstudio_chat_model_ids(base_url):
        options.append({"id": model_id, "label": model_id})
    return options
