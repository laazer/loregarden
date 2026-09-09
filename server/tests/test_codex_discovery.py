"""Codex model discovery — live catalog from ``codex debug models``."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from loregarden.services.codex_discovery import (
    DISCOVERY_TIMEOUT_SECONDS,
    FAILURE_CACHE_TTL_SECONDS,
    CodexModel,
    _parse_models_payload,
    codex_model_options,
    list_codex_models,
    reset_model_cache,
)


@pytest.fixture(autouse=True)
def _no_memoized_catalog():
    """The catalog is process-wide; one test's answer must not serve the next."""
    reset_model_cache()
    yield
    reset_model_cache()


CATALOG = {
    "models": [
        {
            "slug": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "visibility": "list",
            "priority": 1,
        },
        {
            "slug": "gpt-5.5",
            "display_name": "GPT-5.5",
            "visibility": "list",
            "priority": 7,
        },
        {
            "slug": "gpt-5.4",
            "display_name": "GPT-5.4",
            "visibility": "hide",
            "priority": 16,
        },
        {
            "slug": "codex-auto-review",
            "display_name": "Codex Auto Review",
            "visibility": "hide",
            "priority": 43,
        },
    ]
}


def test_parse_models_payload_skips_hidden_and_sorts_by_priority():
    models = _parse_models_payload(CATALOG)
    assert [(m.slug, m.display_name) for m in models] == [
        ("gpt-5.6-sol", "GPT-5.6-Sol"),
        ("gpt-5.5", "GPT-5.5"),
    ]


def test_list_codex_models_prefers_cli_catalog():
    completed = MagicMock(
        returncode=0,
        stdout="WARNING: noise\n" + json.dumps(CATALOG),
        stderr="",
    )
    with (
        patch("loregarden.services.codex_discovery.resolve_codex_binary", return_value="codex"),
        patch("loregarden.services.codex_discovery.subprocess.run", return_value=completed) as run,
        patch("loregarden.services.codex_discovery._list_from_cache") as cache,
    ):
        models = list_codex_models()

    assert [m.slug for m in models] == ["gpt-5.6-sol", "gpt-5.5"]
    run.assert_called_once()
    assert run.call_args.args[0][:3] == ["codex", "debug", "models"]
    cache.assert_not_called()


def test_list_codex_models_falls_back_to_cache(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with (
        patch("loregarden.services.codex_discovery.resolve_codex_binary", return_value=None),
    ):
        models = list_codex_models()

    assert [m.slug for m in models] == ["gpt-5.6-sol", "gpt-5.5"]


def test_codex_model_options_prepends_default():
    with patch(
        "loregarden.services.codex_discovery.list_codex_models",
        return_value=[
            CodexModel(slug="gpt-5.5", display_name="GPT-5.5", priority=7),
        ],
    ):
        options = codex_model_options()

    assert options[0] == {"id": "", "label": "Default (Codex profile)"}
    assert options[1] == {"id": "gpt-5.5", "label": "GPT-5.5"}
    assert "gpt-5" not in {opt["id"] for opt in options}


def test_the_cli_runs_once_and_the_catalog_is_reused():
    completed = MagicMock(stdout=json.dumps(CATALOG), stderr="", returncode=0)
    with patch("loregarden.services.codex_discovery.subprocess.run", return_value=completed) as run:
        with patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ):
            assert [m.slug for m in list_codex_models()] == ["gpt-5.6-sol", "gpt-5.5"]
            assert [m.slug for m in list_codex_models()] == ["gpt-5.6-sol", "gpt-5.5"]

    # It used to run on every runtime-options request, inside a request holding
    # a database connection.
    assert run.call_count == 1


def test_a_caller_is_not_made_to_wait_for_an_in_flight_probe():
    probing = threading.Event()
    release = threading.Event()

    def hanging_cli(*args, **kwargs):
        probing.set()
        assert release.wait(timeout=10), "the second caller never returned"
        return MagicMock(stdout=json.dumps(CATALOG), stderr="", returncode=0)

    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch("loregarden.services.codex_discovery.subprocess.run", side_effect=hanging_cli),
        patch("loregarden.services.codex_discovery._list_from_cache", return_value=[]),
    ):
        prober = threading.Thread(target=list_codex_models)
        prober.start()
        try:
            assert probing.wait(timeout=10), "the probe never reached the CLI"

            started = time.monotonic()
            assert list_codex_models() == []
            waited = time.monotonic() - started
        finally:
            release.set()
            prober.join(timeout=10)

    assert waited < 1.0, f"the second caller waited {waited:.2f}s behind the probe"


def test_a_hung_cli_cannot_be_probed_back_to_back():
    assert FAILURE_CACHE_TTL_SECONDS > DISCOVERY_TIMEOUT_SECONDS
