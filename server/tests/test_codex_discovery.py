"""Codex model discovery — live catalog from ``codex debug models``.

Criteria covered: B1 (``ProbeOutcome``/``ProbeAnswer``), B2 (bounded join),
B8 (the exact ``detail`` sentence per failure), B9 (the budget stays 12s), and
A0b/A0c re-verified.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from loregarden.models.domain.enums import DiscoveryStatus
from loregarden.services.codex_discovery import (
    DISCOVERY_TIMEOUT_SECONDS,
    FAILURE_CACHE_TTL_SECONDS,
    CodexModel,
    _parse_models_payload,
    codex_home,
    codex_model_options,
    list_codex_models,
    reset_model_cache,
)
from loregarden.services.discovery_cache import ProbeAnswer, ProbeCache, join_probes


@pytest.fixture(autouse=True)
def _no_memoized_catalog():
    """The catalog is process-wide; one test's answer must not serve the next."""
    reset_model_cache()
    yield
    join_probes(timeout=5.0)
    reset_model_cache()


def _impatient_cache() -> ProbeCache[CodexModel]:
    return ProbeCache(
        label="codex",
        probe_budget_seconds=DISCOVERY_TIMEOUT_SECONDS,
        success_ttl_seconds=300.0,
        failure_ttl_seconds=FAILURE_CACHE_TTL_SECONDS,
        join_timeout_seconds=0.02,
    )


def _answer(found: list[CodexModel]) -> ProbeAnswer[CodexModel]:
    """A completed probe's answer, so a stubbed `list_codex_models` returns the
    same shape the real one does."""
    return ProbeAnswer(
        found=found,
        status=DiscoveryStatus.FRESH,
        detail="",
        target="/bin/codex",
    )


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
        answer = list_codex_models()

    assert [m.slug for m in answer.found] == ["gpt-5.6-sol", "gpt-5.5"]
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.detail == ""
    assert answer.target == "codex"
    run.assert_called_once()
    assert run.call_args.args[0][:3] == ["codex", "debug", "models"]
    cache.assert_not_called()


def test_list_codex_models_falls_back_to_cache(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with patch("loregarden.services.codex_discovery.resolve_codex_binary", return_value=None):
        answer = list_codex_models()

    assert [m.slug for m in answer.found] == ["gpt-5.6-sol", "gpt-5.5"]
    # The cache file answered, so this is not a failure — there is nothing for
    # the operator to fix and nothing to tell them.
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.detail == ""
    assert answer.target == str(cache_path)


def test_codex_model_options_prepends_default():
    with patch(
        "loregarden.services.codex_discovery.list_codex_models",
        return_value=_answer([CodexModel(slug="gpt-5.5", display_name="GPT-5.5", priority=7)]),
    ):
        options, discovery = codex_model_options()

    assert options[0] == {"id": "", "label": "Default (Codex profile)"}
    assert options[1] == {"id": "gpt-5.5", "label": "GPT-5.5"}
    assert "gpt-5" not in {opt["id"] for opt in options}
    assert discovery == {
        "status": DiscoveryStatus.FRESH,
        "detail": "",
        "target": "/bin/codex",
    }


def test_the_cli_runs_once_and_the_catalog_is_reused():
    completed = MagicMock(stdout=json.dumps(CATALOG), stderr="", returncode=0)
    with (
        patch("loregarden.services.codex_discovery.subprocess.run", return_value=completed) as run,
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
    ):
        assert [m.slug for m in list_codex_models().found] == ["gpt-5.6-sol", "gpt-5.5"]
        assert [m.slug for m in list_codex_models().found] == ["gpt-5.6-sol", "gpt-5.5"]

    # It used to run on every runtime-options request, inside a request holding
    # a database connection.
    assert run.call_count == 1


# ---------------------------------------------------------------------------
# B8 — the exact sentence each failure reports, because the UI renders it
# ---------------------------------------------------------------------------


def test_no_cli_and_an_unreadable_cache_names_both(monkeypatch):
    # A short $CODEX_HOME on purpose: the sentence is pinned verbatim *and*
    # bounded at 200 chars, and pytest's own `tmp_path` is 180 characters deep,
    # so the two assertions can only both be meaningful against a realistic
    # path. The over-long case is covered by
    # `test_a_deep_codex_home_still_fits_the_field_hint` below.
    monkeypatch.setenv("CODEX_HOME", "/tmp/lg-codex-home-absent")
    expected_path = codex_home() / "models_cache.json"

    with patch("loregarden.services.codex_discovery.resolve_codex_binary", return_value=None):
        answer = list_codex_models()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == (
        f"Codex CLI not found and {expected_path} is unreadable. "
        "Install Codex, or set LOREGARDEN_CODEX_BIN."
    )
    assert answer.target == str(expected_path)
    assert len(answer.detail) <= 200


def test_a_deep_codex_home_still_fits_the_field_hint(tmp_path, monkeypatch):
    """B8's bound on the one pinned sentence that interpolates a path."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ("deep/" * 40) / "nothing-here"))

    with patch("loregarden.services.codex_discovery.resolve_codex_binary", return_value=None):
        answer = list_codex_models()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert len(answer.detail) <= 200
    # Elided from the front, so the instruction at the end survives.
    assert answer.detail.endswith("is unreadable. Install Codex, or set LOREGARDEN_CODEX_BIN.")
    assert answer.detail.startswith("Codex CLI not found and ")


def test_a_cli_that_times_out_names_its_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch(
            "loregarden.services.codex_discovery.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=12.0),
        ),
    ):
        answer = list_codex_models()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == "`codex debug models` did not answer within 12s."
    assert answer.target == "/bin/codex"


def test_a_cli_that_times_out_but_has_a_readable_cache_is_not_a_failure(tmp_path, monkeypatch):
    (tmp_path / "models_cache.json").write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch(
            "loregarden.services.codex_discovery.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=12.0),
        ),
    ):
        answer = list_codex_models()

    # The fallback is the point of having it: a catalogue was produced, so the
    # picker is not in an error state.
    assert [m.slug for m in answer.found] == ["gpt-5.6-sol", "gpt-5.5"]
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.detail == ""


def test_a_cli_that_cannot_be_run_reports_why(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch(
            "loregarden.services.codex_discovery.subprocess.run",
            side_effect=OSError("Permission denied"),
        ),
    ):
        answer = list_codex_models()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == "`codex debug models` could not be run: Permission denied."


def test_a_cli_that_printed_no_json_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch(
            "loregarden.services.codex_discovery.subprocess.run",
            return_value=MagicMock(stdout="not json at all", stderr="", returncode=0),
        ),
    ):
        answer = list_codex_models()

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == "`codex debug models` printed no model catalog."


def test_a_cli_that_listed_an_empty_catalog_is_not_a_failure(tmp_path, monkeypatch):
    """A3. A codex account with only a profile default is a nameable state."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch(
            "loregarden.services.codex_discovery.subprocess.run",
            return_value=MagicMock(stdout=json.dumps({"models": []}), stderr="", returncode=0),
        ),
    ):
        options, discovery = codex_model_options()

    assert len(options) == 1
    assert discovery["status"] == DiscoveryStatus.FRESH
    assert discovery["detail"] == ""


def test_a_failure_detail_never_carries_an_unbounded_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch(
            "loregarden.services.codex_discovery.subprocess.run",
            side_effect=OSError("y" * 500),
        ),
    ):
        answer = list_codex_models()

    assert len(answer.detail) <= 200
    assert "Traceback" not in answer.detail


# ---------------------------------------------------------------------------
# B2 / B3 / A0b / A0c
# ---------------------------------------------------------------------------


def test_a_cold_probe_reports_probing_with_the_binary_it_is_probing(monkeypatch):
    monkeypatch.setattr("loregarden.services.codex_discovery._CACHE", _impatient_cache())
    probing = threading.Event()
    release = threading.Event()

    def hanging_cli(*args, **kwargs):
        probing.set()
        assert release.wait(timeout=20), "the probe was never released"
        return MagicMock(stdout=json.dumps(CATALOG), stderr="", returncode=0)

    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch("loregarden.services.codex_discovery.subprocess.run", side_effect=hanging_cli),
    ):
        try:
            started = time.monotonic()
            options, discovery = codex_model_options()
            waited = time.monotonic() - started
            assert probing.wait(timeout=5), "the probe never reached the CLI"
        finally:
            release.set()

    assert waited < 1.0, f"a cold render waited {waited:.2f}s for the CLI"
    assert discovery["status"] == DiscoveryStatus.PROBING
    assert discovery["detail"] == ""
    assert discovery["target"] == "/bin/codex"
    assert len(options) == 1


def test_a_caller_is_not_made_to_wait_for_an_in_flight_probe(monkeypatch):
    """A0b, re-verified for codex."""
    monkeypatch.setattr("loregarden.services.codex_discovery._CACHE", _impatient_cache())
    probing = threading.Event()
    release = threading.Event()

    def hanging_cli(*args, **kwargs):
        probing.set()
        assert release.wait(timeout=20), "the second caller never returned"
        return MagicMock(stdout=json.dumps(CATALOG), stderr="", returncode=0)

    with (
        patch(
            "loregarden.services.codex_discovery.resolve_codex_binary",
            return_value="/bin/codex",
        ),
        patch("loregarden.services.codex_discovery.subprocess.run", side_effect=hanging_cli),
        patch("loregarden.services.codex_discovery._list_from_cache", return_value=[]),
    ):
        list_codex_models()
        try:
            assert probing.wait(timeout=5), "the probe never reached the CLI"

            started = time.monotonic()
            answer = list_codex_models()
            waited = time.monotonic() - started
        finally:
            release.set()

    assert waited < 1.0, f"the second caller waited {waited:.2f}s behind the probe"
    assert answer.status == DiscoveryStatus.PROBING


def test_a_hung_cli_cannot_be_probed_back_to_back():
    # A0c, re-verified.
    assert FAILURE_CACHE_TTL_SECONDS > DISCOVERY_TIMEOUT_SECONDS


def test_the_discovery_budget_stays_where_it_was_measured():
    # B9. Explicitly out of scope for this ticket: it needs a deliberate
    # re-measurement, and lowering it on the old evidence is the named
    # regression that left the picker permanently empty.
    assert DISCOVERY_TIMEOUT_SECONDS == 12.0
