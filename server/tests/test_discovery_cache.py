"""The memoization every model-discovery probe shares.

The bug these cover: a discovery probe that hangs took the API down with it.
``runtime_options_payload`` runs inside a request holding a database connection,
so callers queued behind a probe hold one each — fifteen of them exhausted the
pool (5 + 10 overflow, 30s checkout) and every endpoint began failing, including
``/health``, which shares the bounded threadpool that sync endpoints run in.
"""

from __future__ import annotations

import threading
import time

import pytest
from loregarden.services.discovery_cache import ProbeCache


def _cache(**overrides) -> ProbeCache[str]:
    kwargs = {
        "probe_budget_seconds": 5.0,
        "success_ttl_seconds": 300.0,
        "failure_ttl_seconds": 60.0,
    }
    kwargs.update(overrides)
    return ProbeCache(**kwargs)


def test_a_failure_ttl_shorter_than_the_probe_budget_is_refused():
    # A hung CLI burns the whole budget before failing. Holding that failure for
    # less time than the budget means the next caller starts probing as this one
    # gives up, so a probe is always in flight and never merely retried.
    with pytest.raises(ValueError, match="must outlast"):
        _cache(probe_budget_seconds=45.0, failure_ttl_seconds=30.0)

    with pytest.raises(ValueError, match="must outlast"):
        _cache(probe_budget_seconds=12.0, failure_ttl_seconds=12.0)


def test_the_probe_runs_once_and_the_answer_is_reused():
    cache = _cache()
    calls = []

    def probe() -> list[str]:
        calls.append(1)
        return ["a"]

    assert cache.get("k", probe) == ["a"]
    assert cache.get("k", probe) == ["a"]
    assert len(calls) == 1


def test_a_caller_is_not_made_to_wait_for_an_in_flight_probe():
    cache = _cache()
    probing = threading.Event()
    release = threading.Event()

    def hanging_probe() -> list[str]:
        probing.set()
        assert release.wait(timeout=10), "the second caller never returned"
        return ["a"]

    prober = threading.Thread(target=lambda: cache.get("k", hanging_probe))
    prober.start()
    try:
        assert probing.wait(timeout=10), "the probe never started"

        started = time.monotonic()
        # Nothing cached yet, so there is nothing to serve — but the caller must
        # come back with it rather than queue behind the probe.
        assert cache.get("k", hanging_probe) == []
        waited = time.monotonic() - started
    finally:
        release.set()
        prober.join(timeout=10)

    assert waited < 1.0, f"the second caller waited {waited:.2f}s behind the probe"
    assert cache.get("k", hanging_probe) == ["a"]


def test_a_stale_entry_is_served_while_another_caller_refreshes_it():
    cache = _cache(success_ttl_seconds=0.0)
    probing = threading.Event()
    release = threading.Event()

    assert cache.get("k", lambda: ["first"]) == ["first"]

    def hanging_probe() -> list[str]:
        probing.set()
        assert release.wait(timeout=10)
        return ["second"]

    prober = threading.Thread(target=lambda: cache.get("k", hanging_probe))
    prober.start()
    try:
        assert probing.wait(timeout=10)
        # Stale beats blocking: the picker showing a slightly old catalog is not
        # worth a held database connection.
        assert cache.get("k", hanging_probe) == ["first"]
    finally:
        release.set()
        prober.join(timeout=10)


def test_a_probe_for_one_key_does_not_block_another():
    cache = _cache()
    probing = threading.Event()
    release = threading.Event()

    def hanging_probe() -> list[str]:
        probing.set()
        assert release.wait(timeout=10)
        return ["a"]

    prober = threading.Thread(target=lambda: cache.get("first", hanging_probe))
    prober.start()
    try:
        assert probing.wait(timeout=10)
        # Two LM Studio servers are two probes; one being slow says nothing
        # about the other.
        assert cache.get("second", lambda: ["b"]) == ["b"]
    finally:
        release.set()
        prober.join(timeout=10)


def test_an_empty_answer_is_retried_sooner_than_a_full_one():
    # The invariant forbids a zero failure TTL against a real budget, so shrink
    # both: what is under test is which TTL an empty answer is filed under.
    cache = _cache(probe_budget_seconds=0.0, success_ttl_seconds=300.0, failure_ttl_seconds=0.01)
    empty_calls = []
    full_calls = []

    def empty_probe() -> list[str]:
        empty_calls.append(1)
        return []

    def full_probe() -> list[str]:
        full_calls.append(1)
        return ["a"]

    assert cache.get("empty", empty_probe) == []
    time.sleep(0.05)
    assert cache.get("empty", empty_probe) == []
    # "The CLI answered with nothing" is the same non-answer as "the CLI is
    # missing" — both are retried on the failure TTL, not held for five minutes.
    assert len(empty_calls) == 2

    assert cache.get("full", full_probe) == ["a"]
    time.sleep(0.05)
    assert cache.get("full", full_probe) == ["a"]
    assert len(full_calls) == 1


def test_a_caller_cannot_mutate_the_cached_answer():
    cache = _cache()

    first = cache.get("k", lambda: ["a"])
    first.append("injected")

    assert cache.get("k", lambda: ["a"]) == ["a"]


def test_reset_drops_the_memoized_answer():
    cache = _cache()
    calls = []

    def probe() -> list[str]:
        calls.append(1)
        return ["a"]

    assert cache.get("k", probe) == ["a"]
    cache.reset()
    assert cache.get("k", probe) == ["a"]
    assert len(calls) == 2
