"""LM Studio model discovery — chat models only, embeddings skipped."""

from unittest.mock import MagicMock, patch

from loregarden.services.lmstudio_discovery import (
    is_chat_lmstudio_model,
    list_lmstudio_chat_model_ids,
    lmstudio_model_options,
)


def test_is_chat_lmstudio_model_filters_embeddings():
    assert is_chat_lmstudio_model("qwen/qwen3.5-9b")
    assert is_chat_lmstudio_model("llama3.1-8b")
    assert not is_chat_lmstudio_model("text-embedding-nomic-embed-text-v1.5")
    assert not is_chat_lmstudio_model("nomic-embed-text")
    assert not is_chat_lmstudio_model("")


def test_list_lmstudio_chat_model_ids_skips_embeddings():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": [
            {"id": "text-embedding-nomic-embed-text-v1.5"},
            {"id": "qwen/qwen3.5-9b"},
            {"id": "llama3.1-8b"},
        ]
    }
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.return_value = response

    with patch("loregarden.services.lmstudio_discovery.httpx.Client", return_value=client):
        assert list_lmstudio_chat_model_ids("http://lm.test/v1") == [
            "qwen/qwen3.5-9b",
            "llama3.1-8b",
        ]


def test_list_lmstudio_chat_model_ids_empty_when_unreachable():
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=ConnectionError("down"),
    ):
        assert list_lmstudio_chat_model_ids() == []


def test_lmstudio_model_options_includes_auto_and_discovered():
    with patch(
        "loregarden.services.lmstudio_discovery.list_lmstudio_chat_model_ids",
        return_value=["qwen/qwen3.5-9b"],
    ):
        options = lmstudio_model_options()
    assert options[0] == {"id": "", "label": "Auto (first loaded chat model)"}
    assert options[1] == {"id": "qwen/qwen3.5-9b", "label": "qwen/qwen3.5-9b"}


def test_runtime_options_payload_includes_lmstudio_models():
    from loregarden.services.cli_settings import runtime_options_payload

    with patch(
        "loregarden.services.lmstudio_discovery.list_lmstudio_chat_model_ids",
        return_value=["local-a"],
    ):
        payload = runtime_options_payload()
    assert payload["lmstudio_models"][1]["id"] == "local-a"
