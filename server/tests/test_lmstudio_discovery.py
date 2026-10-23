"""LM Studio model discovery — chat models only, embeddings skipped.

Criteria covered: B1, B2, B8 (the exact ``detail`` sentence per failure class),
B9 (the budget stays 2s), and A0b/A0c re-verified.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from loregarden.models.domain.enums import DiscoveryStatus
from loregarden.services.discovery_cache import ProbeAnswer, ProbeCache, join_probes
from loregarden.services.lmstudio_discovery import (
    CACHE_TTL_SECONDS,
    DISCOVERY_TIMEOUT_SECONDS,
    FAILURE_CACHE_TTL_SECONDS,
    PROBE_BUDGET_SECONDS,
    LmStudioChatModel,
    is_chat_lmstudio_model,
    list_lmstudio_chat_model_ids,
    list_lmstudio_chat_models,
    lmstudio_model_options,
    normalize_lmstudio_base_url,
)

LM = "http://lm.test/v1"


#: How long a test's cold call waits for its mocked probe. The module's own
#: 0.25s join is a real UX budget; on a loaded pre-push box a mocked probe
#: missed it and the test read the cold empty answer as the result
#: (test_list_lmstudio_chat_model_ids_skips_embeddings, 2026-10-23, after 22
#: minutes queued for capacity). `test_discovery_cache` already joins patiently.
PATIENT_JOIN = 5.0


def _cache(join_timeout_seconds: float) -> ProbeCache[LmStudioChatModel]:
    return ProbeCache(
        label="lmstudio",
        probe_budget_seconds=PROBE_BUDGET_SECONDS,
        success_ttl_seconds=CACHE_TTL_SECONDS,
        failure_ttl_seconds=FAILURE_CACHE_TTL_SECONDS,
        join_timeout_seconds=join_timeout_seconds,
    )


@pytest.fixture(autouse=True)
def _no_memoized_catalog(monkeypatch):
    """The catalog is process-wide; one test's answer must not serve the next.

    Each test gets its own cache that waits for its probe, so an answer is the
    probe's, never the cold default because the machine was slow. A test about
    impatience swaps in `_impatient_cache()` itself.

    `monkeypatch`, not `unittest.mock.patch`: those tests swap with
    `monkeypatch.setattr`, and one undo stack is the only way the two swaps
    unwind in order. With `patch`, this fixture restored the real cache first
    and monkeypatch then restored *this* one, which leaked a 5s join into
    test_runtime_effort.
    """
    monkeypatch.setattr("loregarden.services.lmstudio_discovery._CACHE", _cache(PATIENT_JOIN))
    yield
    join_probes(timeout=5.0)


def _impatient_cache() -> ProbeCache[LmStudioChatModel]:
    return _cache(0.02)


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


def _failing_client(exc: BaseException) -> MagicMock:
    """A client whose every request raises — both attempts, native then compat."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.side_effect = exc
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
        answer = list_lmstudio_chat_models(LM)

    assert [(m.id, m.loaded) for m in answer.found] == [
        ("qwen/qwen3.5-9b", True),
        ("llama3.1-8b", False),
    ]
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.detail == ""
    # Always the resolved base URL, because the empty-state copy names it.
    assert answer.target == LM
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
        assert list_lmstudio_chat_model_ids(LM) == [
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
        answer = list_lmstudio_chat_models(LM)

    assert [(m.id, m.loaded) for m in answer.found] == [("qwen/qwen3.5-9b", False)]
    # The fallback answered, so the native attempt failing is not an error the
    # operator has to see.
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.detail == ""
    assert client.get.call_args_list[0].args == ("http://lm.test/api/v0/models",)
    assert client.get.call_args_list[1].args == ("http://lm.test/v1/models",)


def test_a_server_with_no_chat_model_loaded_is_not_a_failure():
    """A3. LM Studio running with nothing loaded is a nameable state."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": [{"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings"}]
    }

    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        return_value=_mock_client(response),
    ):
        options, discovery = lmstudio_model_options(LM)

    assert options == [{"id": "", "label": "Auto (first loaded chat model)"}]
    assert discovery == {"status": DiscoveryStatus.FRESH, "detail": "", "target": LM}


def test_lmstudio_model_options_marks_loaded_state():
    answer = ProbeAnswer(
        found=[
            LmStudioChatModel(id="qwen/qwen3.5-9b", loaded=True),
            LmStudioChatModel(id="llama3.1-8b", loaded=False),
        ],
        status=DiscoveryStatus.FRESH,
        detail="",
        target="http://127.0.0.1:1234/v1",
    )
    with patch(
        "loregarden.services.lmstudio_discovery.list_lmstudio_chat_models",
        return_value=answer,
    ):
        options, discovery = lmstudio_model_options()

    assert options[0] == {"id": "", "label": "Auto (first loaded chat model)"}
    assert options[1] == {"id": "qwen/qwen3.5-9b", "label": "qwen/qwen3.5-9b · loaded"}
    assert options[2] == {"id": "llama3.1-8b", "label": "llama3.1-8b · not loaded"}
    assert discovery["status"] == DiscoveryStatus.FRESH


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


# ---------------------------------------------------------------------------
# B8 — each failure class names its own reason, because the UI renders it
# ---------------------------------------------------------------------------


def test_a_refused_connection_says_so():
    request = httpx.Request("GET", f"{LM}/models")
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        return_value=_failing_client(httpx.ConnectError("refused", request=request)),
    ):
        answer = list_lmstudio_chat_models(LM)

    assert answer.found == []
    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == f"Could not reach LM Studio at {LM} — connection refused."
    assert answer.target == LM
    assert len(answer.detail) <= 200


def test_a_timeout_names_the_budget_it_waited():
    request = httpx.Request("GET", f"{LM}/models")
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        return_value=_failing_client(httpx.TimeoutException("slow", request=request)),
    ):
        answer = list_lmstudio_chat_models(LM)

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == f"Could not reach LM Studio at {LM} — timed out after 2s."


def test_an_http_error_names_its_status_code():
    request = httpx.Request("GET", f"{LM}/models")
    error = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        return_value=_failing_client(error),
    ):
        answer = list_lmstudio_chat_models(LM)

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == f"Could not reach LM Studio at {LM} — HTTP 503."


def test_an_unclassified_failure_still_reports_the_exception():
    """The broad catch must classify what it caught, never discard it."""
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=RuntimeError("the socket went away"),
    ):
        answer = list_lmstudio_chat_models(LM)

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == f"Could not reach LM Studio at {LM} — the socket went away."


def test_an_unreachable_server_still_names_the_url_in_its_target():
    """U6's empty-state copy and U7's error copy both name the base URL, and the
    client cannot reconstruct which one was probed from a workspace row."""
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=RuntimeError("down"),
    ):
        _options, discovery = lmstudio_model_options("http://127.0.0.1:9999/v1/")

    assert discovery["target"] == "http://127.0.0.1:9999/v1"
    assert discovery["target"] == normalize_lmstudio_base_url("http://127.0.0.1:9999/v1/")
    assert discovery["status"] == DiscoveryStatus.UNAVAILABLE


def test_list_lmstudio_chat_model_ids_empty_when_unreachable():
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=ConnectionError("down"),
    ):
        assert list_lmstudio_chat_model_ids() == []


# ---------------------------------------------------------------------------
# B2 / B3 / A0b / A0c
# ---------------------------------------------------------------------------


def test_a_cold_probe_reports_probing_with_the_url_it_is_probing(monkeypatch):
    monkeypatch.setattr("loregarden.services.lmstudio_discovery._CACHE", _impatient_cache())
    probing = threading.Event()
    release = threading.Event()

    def hanging_client(*args, **kwargs):
        probing.set()
        assert release.wait(timeout=20), "the probe was never released"
        response = MagicMock()
        response.json.return_value = {"data": [{"id": "qwen/qwen3.5-9b", "state": "loaded"}]}
        return _mock_client(response)

    with patch("httpx.Client", side_effect=hanging_client):
        try:
            started = time.monotonic()
            options, discovery = lmstudio_model_options(LM)
            waited = time.monotonic() - started
            assert probing.wait(timeout=5), "the probe never reached the server"
        finally:
            release.set()

    assert waited < 1.0, f"a cold render waited {waited:.2f}s for the server"
    assert discovery == {"status": DiscoveryStatus.PROBING, "detail": "", "target": LM}
    assert options == [{"id": "", "label": "Auto (first loaded chat model)"}]


def test_a_caller_is_not_made_to_wait_for_an_in_flight_probe(monkeypatch):
    """A0b, re-verified for lmstudio."""
    monkeypatch.setattr("loregarden.services.lmstudio_discovery._CACHE", _impatient_cache())
    probing = threading.Event()
    release = threading.Event()

    def hanging_client(*args, **kwargs):
        probing.set()
        assert release.wait(timeout=20), "the second caller never returned"
        response = MagicMock()
        response.json.return_value = {"data": [{"id": "qwen/qwen3.5-9b", "state": "loaded"}]}
        return _mock_client(response)

    with patch("httpx.Client", side_effect=hanging_client):
        list_lmstudio_chat_models("http://127.0.0.1:1234/v1")
        try:
            assert probing.wait(timeout=5), "the probe never reached the server"

            started = time.monotonic()
            answer = list_lmstudio_chat_models("http://127.0.0.1:1234/v1")
            waited = time.monotonic() - started
        finally:
            release.set()

    assert waited < 1.0, f"the second caller waited {waited:.2f}s behind the probe"
    assert answer.status == DiscoveryStatus.PROBING


def test_an_unreachable_server_cannot_be_probed_back_to_back():
    # A0c, re-verified. Unreachable costs both attempts, native then OpenAI-compat.
    assert FAILURE_CACHE_TTL_SECONDS > PROBE_BUDGET_SECONDS


def test_the_discovery_budget_stays_where_it_was_measured():
    # B9. Out of scope for this ticket; it needs its own measurement.
    assert DISCOVERY_TIMEOUT_SECONDS == 2.0
    assert PROBE_BUDGET_SECONDS == 4.0


# ==========================================================================
# Adversarial coverage (test-break stage).
#
# The three failure classes above are the ones the spec names, and each is
# raised as a clean, well-formed exception. What reaches this handler on a real
# machine is messier: an exception whose `str()` is empty, one whose `str()` is
# a kilobyte of argv, two different failures across the two attempts, and a
# server that answers 200 with a payload nobody designed. The detail string
# reaches the operator's field hint verbatim (U7), so each of those is a
# user-visible defect and not an internal one.
# ==========================================================================


def _settled_answer(base_url: str = LM) -> ProbeAnswer[LmStudioChatModel]:
    """The answer once the probe has landed, rather than whichever side of the
    bounded join this machine happened to be on.

    A cold key answers `probing` if the probe misses the join window, so a test
    asserting a `detail` has to read the settled entry or it is a coin flip on a
    loaded box.
    """
    list_lmstudio_chat_models(base_url)
    join_probes(timeout=5.0)
    return list_lmstudio_chat_models(base_url)


def test_an_unclassified_failures_detail_stays_short_enough_for_the_field_hint():
    """B8's 200-character bound, on the one branch that interpolates `str(exc)`.

    Weakness: the fallthrough `str(exc)` is the only unbounded reason of the
    four. `httpx` errors on long URLs, and a wrapped `CalledProcessError`
    carrying its whole argv, both arrive here. The pinned classes are short by
    construction; this one is short only if someone bounded it.
    """
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=RuntimeError("x" * 5_000),
    ):
        answer = _settled_answer()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert len(answer.detail) <= 200, f"the field hint was {len(answer.detail)} characters"
    assert "\n" not in answer.detail
    assert "Traceback" not in answer.detail
    # Shortened, but still the sentence the UI expects, still naming the server.
    assert answer.detail.startswith(f"Could not reach LM Studio at {LM}")
    assert answer.target == LM


def test_a_failure_whose_exception_says_nothing_still_reads_as_a_sentence():
    """Weakness: `{reason}` substituted with an empty string.

    `RuntimeError()`, `KeyError()` and `httpx.ConnectError("")` all stringify to
    nothing. The template then renders "Could not reach LM Studio at … — ." and
    that half-sentence is the whole explanation the operator is given, in the one
    state that exists to explain itself.
    """
    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        side_effect=RuntimeError(),
    ):
        answer = _settled_answer()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail.startswith(f"Could not reach LM Studio at {LM}")
    reason = answer.detail[len(f"Could not reach LM Studio at {LM}") :]
    assert reason.strip(" —.") != "", (
        f"the reason half of the sentence was empty: {answer.detail!r}"
    )


def test_both_attempts_failing_differently_still_classifies_one_of_them():
    """Weakness: two attempts, one detail.

    The native endpoint is absent on older builds (one failure) and the server is
    also down (a different failure), so the two attempts genuinely raise
    different classes. The reason shown must still be one of the classified ones
    rather than a repr of whichever was caught last.
    """
    request = httpx.Request("GET", f"{LM}/models")
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.side_effect = [
        httpx.ConnectError("native refused", request=request),
        httpx.TimeoutException("compat slow", request=request),
    ]

    with patch("loregarden.services.lmstudio_discovery.httpx.Client", return_value=client):
        list_lmstudio_chat_models(LM)
        join_probes(timeout=5.0)
        answer = list_lmstudio_chat_models(LM)

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail in {
        f"Could not reach LM Studio at {LM} — connection refused.",
        f"Could not reach LM Studio at {LM} — timed out after 2s.",
    }, f"neither attempt was classified: {answer.detail!r}"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": "qwen/qwen3.5-9b"},
        {"data": [None, 7, "a-bare-string"]},
        {"data": [{}, {"id": None}, {"id": ""}, {"name": "   "}]},
        [],
        "not json at all",
        None,
    ],
)
def test_a_payload_nobody_designed_is_an_answer_and_not_a_failure(payload):
    """A3 against corrupt input. Weakness: the parser raising inside the probe.

    A reachable LM Studio that answers 200 with an unexpected shape (an older
    build, a reverse proxy's error page rendered as JSON, a half-written
    response) is "the server answered and there is nothing usable" — `ok=True`
    and empty. A `TypeError` escaping the parser instead reports *unavailable*
    with a Python error as the operator's hint, which blames the network for a
    schema mismatch and sends them to restart a server that is running.
    """
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload

    with patch(
        "loregarden.services.lmstudio_discovery.httpx.Client",
        return_value=_mock_client(response),
    ):
        answer = _settled_answer()

    assert answer.found == []
    assert answer.status == DiscoveryStatus.FRESH, (
        f"a reachable server answering {payload!r} was reported as unreachable: {answer.detail!r}"
    )
    assert answer.detail == ""
    assert answer.target == LM


def test_urls_that_normalize_to_one_server_are_probed_once():
    """Weakness: a caller-controlled cache key normalized inconsistently.

    `normalize_lmstudio_base_url` strips whitespace and trailing slashes, and the
    base URL arrives from a query parameter or a workspace row. If the key is
    taken before normalization, `"…/v1"`, `"…/v1/"` and `" …/v1 "` are three
    entries, three probe threads and three subprocess-free but still concurrent
    HTTP probes for one server — and the picker's `target` disagrees with the
    key it was cached under.
    """
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"data": [{"id": "qwen/qwen3.5-9b", "state": "loaded"}]}
    client = _mock_client(response)

    with patch("loregarden.services.lmstudio_discovery.httpx.Client", return_value=client) as made:
        _settled_answer()
        for spelling in ("http://lm.test/v1", "http://lm.test/v1/", "  http://lm.test/v1  "):
            answer = list_lmstudio_chat_models(spelling)
            assert [model.id for model in answer.found] == ["qwen/qwen3.5-9b"], spelling
            assert answer.target == LM, spelling

    assert made.call_count == 1, (
        f"one server was probed {made.call_count} times because three spellings of its URL "
        "became three cache keys"
    )
