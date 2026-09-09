"""LM Studio model discovery — chat models only, embeddings skipped."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from loregarden.services.lmstudio_discovery import (
    FAILURE_CACHE_TTL_SECONDS,
    PROBE_BUDGET_SECONDS,
    LmStudioChatModel,
    is_chat_lmstudio_model,
    list_lmstudio_chat_model_ids,
    list_lmstudio_chat_models,
    lmstudio_model_options,
    reset_model_cache,
)


@pytest.fixture(autouse=True)
def _no_memoized_catalog():
    """The catalog is process-wide; one test's answer must not serve the next."""
    reset_model_cache()
    yield
    reset_model_cache()


def test_is_chat_lmstudio_model_filters_embeddings():
    assert is_chat_lmstudio_model("qwen/qwen3.5-9b")
    assert is_chat_lmstudio_model("llama3.1-8b")
    assert not is_chat_lmstudio_model("text-embedding-nomic-embed-text-v1.5")
    assert not is_chat_lmstudio_model("nomic-embed-text")
    assert not is_chat_lmstudio_model("")


def _mock_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.return_value = response
    return client


def test_list_lmstudio_chat_models_uses_native_state():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": [
            {
                "id": "text-embedding-nomic-embed-text-v1.5",
                "type": "embeddings",
                "state": "loaded",
            },
            {"id": "llama3.1-8b", "type": "llm", "state": "not-loaded"},
            {"id": "qwen/qwen3.5-9b", "type": "vlm", "state": "loaded"},
        ]
    }
    client = _mock_client(response)

    with patch("loregarden.services.lmstudio_discovery.httpx.Client", return_value=client):
        models = list_lmstudio_chat_models("http://lm.test/v1")

    assert [(m.id, m.loaded) for m in models] == [
        ("qwen/qwen3.5-9b", True),
        ("llama3.1-8b", False),
    ]
    client.get.assert_called_once_with("http://lm.test/api/v0/models")


def test_list_lmstudio_chat_model_ids_skips_embeddings():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": [
            {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings", "state": "loaded"},
            {"id": "qwen/qwen3.5-9b", "type": "vlm", "state": "loaded"},
            {"id": "llama3.1-8b", "type": "llm", "state": "not-loaded"},
        ]
    }
    client = _mock_client(response)

    with patch("loregarden.services.lmstudio_discovery.httpx.Client", return_value=client):
        assert list_lmstudio_chat_model_ids("http://lm.test/v1") == [
            "qwen/qwen3.5-9b",
            "llama3.1-8b",
        ]


def test_list_lmstudio_chat_models_falls_back_to_openai_compat():
    native = MagicMock()
    native.raise_for_status.side_effect = ConnectionError("no native")
    openai = MagicMock()
    openai.raise_for_status = MagicMock()
    openai.json.return_value = {
        "data": [
            {"id": "text-embedding-nomic-embed-text-v1.5"},
            {"id": "qwen/qwen3.5-9b"},
        ]
    }
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.side_effect = [native, openai]

    with patch("loregarden.services.lmstudio_discovery.httpx.Client", return_value=client):
        models = list_lmstudio_chat_models("http://lm.test/v1")

    assert [(m.id, m.loaded) for m in models] == [("qwen/qwen3.5-9b", False)]
    assert client.get.call_args_list[0].args == ("http://lm.test/api/v0/models",)
    assert client.get.call_args_list[1].args == ("http://lm.test/v1/models",)


def test_list_lmstudio_chat_model_ids_empty_when_unreachable():
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=ConnectionError("down"),
    ):
        assert list_lmstudio_chat_model_ids() == []


def test_lmstudio_model_options_marks_loaded_state():
    with patch(
        "loregarden.services.lmstudio_discovery.list_lmstudio_chat_models",
        return_value=[
            LmStudioChatModel(id="qwen/qwen3.5-9b", loaded=True),
            LmStudioChatModel(id="llama3.1-8b", loaded=False),
        ],
    ):
        options = lmstudio_model_options()
    assert options[0] == {"id": "", "label": "Auto (first loaded chat model)"}
    assert options[1] == {"id": "qwen/qwen3.5-9b", "label": "qwen/qwen3.5-9b · loaded"}
    assert options[2] == {"id": "llama3.1-8b", "label": "llama3.1-8b · not loaded"}


def test_runtime_options_payload_includes_lmstudio_models():
    from loregarden.services.cli_settings import runtime_options_payload

    with patch(
        "loregarden.services.lmstudio_discovery.list_lmstudio_chat_models",
        return_value=[LmStudioChatModel(id="local-a", loaded=True)],
    ):
        payload = runtime_options_payload()
    assert payload["lmstudio_models"][1]["id"] == "local-a"
    assert payload["lmstudio_models"][1]["label"] == "local-a · loaded"


def test_the_server_is_probed_once_and_the_catalog_is_reused():
    response = MagicMock()
    response.json.return_value = {"data": [{"id": "qwen/qwen3.5-9b", "state": "loaded"}]}
    client = _mock_client(response)

    with patch("httpx.Client", return_value=client) as httpx_client:
        assert list_lmstudio_chat_model_ids("http://127.0.0.1:1234/v1") == ["qwen/qwen3.5-9b"]
        assert list_lmstudio_chat_model_ids("http://127.0.0.1:1234/v1") == ["qwen/qwen3.5-9b"]

    # Two HTTP round trips per runtime-options request, inside a request holding
    # a database connection, is what this replaces.
    assert httpx_client.call_count == 1


def test_each_server_is_cached_separately():
    def answer_for(model_id):
        response = MagicMock()
        response.json.return_value = {"data": [{"id": model_id, "state": "loaded"}]}
        return _mock_client(response)

    with patch("httpx.Client", side_effect=[answer_for("a/one"), answer_for("b/two")]):
        assert list_lmstudio_chat_model_ids("http://127.0.0.1:1234/v1") == ["a/one"]
        assert list_lmstudio_chat_model_ids("http://127.0.0.1:9999/v1") == ["b/two"]

    # A trailing slash is the same server, so it must hit the same entry.
    with patch("httpx.Client", side_effect=AssertionError("re-probed a cached server")):
        assert list_lmstudio_chat_model_ids("http://127.0.0.1:1234/v1/") == ["a/one"]


def test_a_caller_is_not_made_to_wait_for_an_in_flight_probe():
    probing = threading.Event()
    release = threading.Event()

    def hanging_client(*args, **kwargs):
        probing.set()
        assert release.wait(timeout=10), "the second caller never returned"
        response = MagicMock()
        response.json.return_value = {"data": [{"id": "qwen/qwen3.5-9b", "state": "loaded"}]}
        return _mock_client(response)

    with patch("httpx.Client", side_effect=hanging_client):
        prober = threading.Thread(
            target=lambda: list_lmstudio_chat_models("http://127.0.0.1:1234/v1")
        )
        prober.start()
        try:
            assert probing.wait(timeout=10), "the probe never reached the server"

            started = time.monotonic()
            assert list_lmstudio_chat_models("http://127.0.0.1:1234/v1") == []
            waited = time.monotonic() - started
        finally:
            release.set()
            prober.join(timeout=10)

    assert waited < 1.0, f"the second caller waited {waited:.2f}s behind the probe"


def test_an_unreachable_server_cannot_be_probed_back_to_back():
    # Unreachable costs both attempts, native then OpenAI-compat.
    assert FAILURE_CACHE_TTL_SECONDS > PROBE_BUDGET_SECONDS
