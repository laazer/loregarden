"""The memoization every model-discovery probe shares.

The bug the first half of these cover: a discovery probe that hangs took the API
down with it. ``runtime_options_payload`` ran inside a request holding a database
connection, so callers queued behind a probe held one each — fifteen of them
exhausted the pool (5 + 10 overflow, 30s checkout) and every endpoint began
failing, including ``/health``, which shares the bounded threadpool that sync
endpoints run in.

The second half covers what is left after the connection was handed back
(PR #322-#324): the *request* still blocked, because ``get()`` ran the probe on
the calling thread and the three probes were called in sequence — 45s + 12s +
2x2s on a cold cache with three hanging CLIs. The probe now runs on a background
daemon thread the caller joins for a bounded time.

Making it non-blocking creates the defect the other half of these tests pin: a
cold key answers with an empty list, which was byte-identical to "the CLI is not
installed". ``ProbeAnswer`` carries a ``DiscoveryStatus`` so those two are
different answers — the collapse CLAUDE.md's *No silent failures* forbids by
name, and the regression this ticket's own history records.

Criteria covered here: B1 (the two result types), B2 (background thread, bounded
join, a raising probe does not wedge the key), B3 (every row of the status
table), B4 (``join_probes``), and A0b/A0c re-verified.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from unittest import mock

import pytest
from loregarden.models.domain.enums import DiscoveryStatus
from loregarden.services.discovery_cache import (
    ProbeAnswer,
    ProbeCache,
    ProbeOutcome,
    join_probes,
)

# Short enough that a test measuring the bounded join finishes quickly, long
# enough that a probe which returns at once wins the race deterministically.
QUICK_JOIN = 0.02
PATIENT_JOIN = 2.0


def _cache(**overrides) -> ProbeCache[str]:
    kwargs = {
        "label": "test",
        "probe_budget_seconds": 5.0,
        "success_ttl_seconds": 300.0,
        "failure_ttl_seconds": 60.0,
        "join_timeout_seconds": PATIENT_JOIN,
    }
    kwargs.update(overrides)
    return ProbeCache(**kwargs)


def _ok(*found: str, target: str = "the-thing-probed") -> ProbeOutcome[str]:
    return ProbeOutcome(found=list(found), ok=True, detail="", target=target)


def _failed(detail: str, *, target: str = "the-thing-probed") -> ProbeOutcome[str]:
    return ProbeOutcome(found=[], ok=False, detail=detail, target=target)


class _HangingProbe:
    """A probe that parks until released, so "in flight" is not a timing guess."""

    def __init__(self, outcome: ProbeOutcome[str] | None = None) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self._outcome = outcome if outcome is not None else _ok("answered")

    def __call__(self) -> ProbeOutcome[str]:
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=20), "the probe was never released"
        return self._outcome

    def finish(self) -> None:
        self.release.set()


@pytest.fixture(autouse=True)
def _no_probe_thread_outlives_this_test():
    """B4 in miniature: a probe thread reading a patched binary that no longer
    exists is how one test's subprocess reached the next."""
    yield
    join_probes(timeout=5.0)


def _live_probe_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("discovery-probe-")]


# --------------------------------------------------------------------------
# A0c — the construction-time invariant (re-verified, not re-implemented)
# --------------------------------------------------------------------------


def test_a_failure_ttl_shorter_than_the_probe_budget_is_refused():
    # A hung CLI burns the whole budget before failing. Holding that failure for
    # less time than the budget means the next caller starts probing as this one
    # gives up, so a probe is always in flight and never merely retried.
    with pytest.raises(ValueError, match="must outlast"):
        _cache(probe_budget_seconds=45.0, failure_ttl_seconds=30.0)

    with pytest.raises(ValueError, match="must outlast"):
        _cache(probe_budget_seconds=12.0, failure_ttl_seconds=12.0)


def test_a_cache_must_be_labelled():
    # The label names the probe thread, which is the only way an operator reading
    # a stack dump can tell which CLI is hanging.
    with pytest.raises(TypeError):
        ProbeCache(  # type: ignore[call-arg]
            probe_budget_seconds=5.0,
            success_ttl_seconds=300.0,
            failure_ttl_seconds=60.0,
        )


# --------------------------------------------------------------------------
# B1 — the probe returns an outcome, the caller gets an answer
# --------------------------------------------------------------------------


def test_the_probe_runs_once_and_the_answer_is_reused():
    cache = _cache()
    calls = []

    def probe() -> ProbeOutcome[str]:
        calls.append(1)
        return _ok("a")

    first = cache.get("k", probe)
    assert isinstance(first, ProbeAnswer)
    assert first.found == ["a"]
    assert first.status == DiscoveryStatus.FRESH

    assert cache.get("k", probe).found == ["a"]
    assert len(calls) == 1


def test_a_probe_that_answers_with_nothing_is_not_a_failure():
    # A3: OpenCode authenticated to no provider, LM Studio running with no model
    # loaded, codex with only a profile default. The CLI answered; there is
    # nothing to offer. That is a nameable state, not an error.
    cache = _cache()

    answer = cache.get("k", lambda: _ok())

    assert answer.found == []
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.detail == ""


def test_a_probe_that_could_not_answer_carries_a_detail_sentence():
    cache = _cache()

    answer = cache.get("k", lambda: _failed("LM Studio was not listening."))

    assert answer.found == []
    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == "LM Studio was not listening."


def test_a_caller_cannot_mutate_the_cached_answer():
    cache = _cache()

    first = cache.get("k", lambda: _ok("a"))
    first.found.append("injected")

    assert cache.get("k", lambda: _ok("a")).found == ["a"]


def test_reset_drops_the_memoized_answer():
    cache = _cache()
    calls = []

    def probe() -> ProbeOutcome[str]:
        calls.append(1)
        return _ok("a")

    assert cache.get("k", probe).found == ["a"]
    cache.reset()
    assert cache.get("k", probe).found == ["a"]
    assert len(calls) == 2


# --------------------------------------------------------------------------
# B1 / A0c — which TTL an answer is filed under
# --------------------------------------------------------------------------


def test_an_empty_answer_is_retried_sooner_than_a_full_one():
    # The invariant forbids a zero failure TTL against a real budget, so shrink
    # both: what is under test is which TTL an empty answer is filed under.
    cache = _cache(probe_budget_seconds=0.0, success_ttl_seconds=300.0, failure_ttl_seconds=0.01)
    empty_calls = []
    full_calls = []

    def empty_probe() -> ProbeOutcome[str]:
        empty_calls.append(1)
        return _ok()

    def full_probe() -> ProbeOutcome[str]:
        full_calls.append(1)
        return _ok("a")

    assert cache.get("empty", empty_probe).found == []
    time.sleep(0.05)
    assert cache.get("empty", empty_probe).found == []
    # An empty-but-ok answer is still retried sooner than a real catalog is
    # refreshed, because the operator is about to fix what made it empty.
    assert len(empty_calls) == 2

    assert cache.get("full", full_probe).found == ["a"]
    time.sleep(0.05)
    assert cache.get("full", full_probe).found == ["a"]
    assert len(full_calls) == 1


def test_a_failed_answer_is_held_on_the_failure_ttl_too():
    cache = _cache(probe_budget_seconds=0.0, success_ttl_seconds=300.0, failure_ttl_seconds=0.01)
    calls = []

    def failing_probe() -> ProbeOutcome[str]:
        calls.append(1)
        return _failed("the CLI is not installed.")

    assert cache.get("k", failing_probe).status == DiscoveryStatus.UNAVAILABLE
    assert cache.get("k", failing_probe).status == DiscoveryStatus.UNAVAILABLE
    assert len(calls) == 1, "a held failure must not be re-probed within its TTL"

    time.sleep(0.05)
    assert cache.get("k", failing_probe).status in {
        DiscoveryStatus.UNAVAILABLE,
        DiscoveryStatus.PROBING,
    }
    assert len(calls) == 2


# --------------------------------------------------------------------------
# B2 — the probe runs on a named background daemon thread
# --------------------------------------------------------------------------


def test_the_probe_runs_on_a_daemon_thread_named_for_the_cache_and_key():
    cache = _cache(label="opencode")
    seen: dict[str, object] = {}

    def probe() -> ProbeOutcome[str]:
        current = threading.current_thread()
        seen["name"] = current.name
        seen["daemon"] = current.daemon
        seen["is_main"] = current is threading.main_thread()
        return _ok("a")

    assert cache.get("some-key", probe).found == ["a"]

    assert seen["is_main"] is False, "the probe must not run on the calling thread"
    assert seen["name"] == "discovery-probe-opencode-some-key"
    # A probe still running at shutdown must not keep the process alive.
    assert seen["daemon"] is True


def test_the_keyless_cache_names_its_thread_default():
    # opencode and codex hold one catalog, so one entry, keyed by the empty string.
    cache = _cache(label="codex")
    seen: list[str] = []

    cache.get("", lambda: seen.append(threading.current_thread().name) or _ok("a"))

    assert seen == ["discovery-probe-codex-default"]


def test_the_dispatching_caller_waits_no_longer_than_the_join_timeout():
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe()

    started = time.monotonic()
    answer = cache.get("k", probe)
    waited = time.monotonic() - started
    probe.finish()

    # This is the whole AC5 mechanism: whatever the CLI does, the request returns.
    assert waited < 1.0, f"the caller waited {waited:.2f}s for a hanging probe"
    assert answer.status == DiscoveryStatus.PROBING
    assert answer.found == []


def test_a_bounded_join_that_completes_in_time_reports_the_probes_own_status():
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)

    answer = cache.get("k", lambda: _ok("a", "b"))

    # Not `probing`: the answer arrived inside the join window, so reporting
    # "still probing" would make the client poll for something it already has.
    assert answer.status == DiscoveryStatus.FRESH
    assert answer.found == ["a", "b"]


def test_a_bounded_join_that_completes_on_a_failure_reports_unavailable():
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)

    answer = cache.get("k", lambda: _failed("Codex CLI not found."))

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == "Codex CLI not found."


def test_only_one_probe_thread_runs_per_key():
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe()

    try:
        cache.get("k", probe)
        assert probe.started.wait(timeout=5), "the probe never started"

        for _ in range(3):
            assert cache.get("k", probe).status == DiscoveryStatus.PROBING

        assert probe.calls == 1, "the per-key lock is still the single-flight bound"
        assert len(_live_probe_threads()) == 1
    finally:
        probe.finish()


def test_a_probe_for_one_key_does_not_block_another():
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    slow = _HangingProbe()

    try:
        cache.get("first", slow)
        assert slow.started.wait(timeout=5)

        # Two LM Studio servers are two probes; one being slow says nothing
        # about the other.
        answer = cache.get("second", lambda: _ok("b"))
        assert answer.found == ["b"]
        assert answer.status == DiscoveryStatus.FRESH
    finally:
        slow.finish()


def test_a_caller_is_not_made_to_wait_for_an_in_flight_probe():
    """A0b, re-verified: the *second* caller never queues behind the first."""
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe(_ok("a"))

    try:
        cache.get("k", probe)
        assert probe.started.wait(timeout=5), "the probe never started"

        started = time.monotonic()
        answer = cache.get("k", probe)
        waited = time.monotonic() - started
    finally:
        probe.finish()

    assert waited < 1.0, f"the second caller waited {waited:.2f}s behind the probe"
    assert answer.status == DiscoveryStatus.PROBING

    join_probes(timeout=5.0)
    assert cache.get("k", probe).found == ["a"]


# --------------------------------------------------------------------------
# B2 — a probe that raises is recorded, logged, and does not wedge the key
# --------------------------------------------------------------------------


def test_a_raising_probe_is_recorded_unavailable_and_logged_at_warning(caplog):
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)

    def exploding_probe() -> ProbeOutcome[str]:
        raise RuntimeError("the socket went away")

    with caplog.at_level(logging.WARNING, logger="loregarden.services.discovery_cache"):
        answer = cache.get("k", exploding_probe)

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.found == []
    # Never swallowed, and never recorded as "nothing found".
    assert "the socket went away" in answer.detail
    assert any(
        "the socket went away" in record.getMessage() and record.levelno >= logging.WARNING
        for record in caplog.records
    ), "a probe that raised must be visible in the log, not only in the answer"


def test_a_raising_probe_does_not_wedge_its_key():
    # If the lock is not released, or the event never set, the key is dead for
    # the lifetime of the process and the picker is permanently empty.
    cache = _cache(
        probe_budget_seconds=0.0,
        failure_ttl_seconds=0.01,
        join_timeout_seconds=PATIENT_JOIN,
    )
    calls: list[str] = []

    def exploding_probe() -> ProbeOutcome[str]:
        calls.append("boom")
        raise RuntimeError("boom")

    def healthy_probe() -> ProbeOutcome[str]:
        calls.append("ok")
        return _ok("a")

    assert cache.get("k", exploding_probe).status == DiscoveryStatus.UNAVAILABLE
    time.sleep(0.05)
    assert cache.get("k", healthy_probe).found == ["a"]
    assert calls == ["boom", "ok"]


def test_a_probe_that_cannot_be_dispatched_does_not_wedge_its_key():
    # The sibling above drives the failure through the *probe*; this one drives
    # it through the *dispatch*. `Thread.start` raises RuntimeError on a process
    # that has run out of threads, and the first fix marked the lock handed off
    # before calling it — so the thread that would have released it never ran,
    # the RuntimeError escaped `get()` as a 500 on /api/workspaces/runtime-options,
    # and every later call for the key answered `probing` forever, past the
    # failure TTL, while the client polled it every 1500ms.
    cache = _cache(
        probe_budget_seconds=0.0,
        failure_ttl_seconds=0.01,
        join_timeout_seconds=QUICK_JOIN,
    )
    calls: list[str] = []

    def probe() -> ProbeOutcome[str]:
        calls.append("probed")
        return _ok("a")

    with mock.patch.object(
        threading.Thread, "start", side_effect=RuntimeError("can't start new thread")
    ):
        answer = cache.get("k", probe)

    # Served an answer, not an exception, and one that says what went wrong.
    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.found == []
    assert "can't start new thread" in answer.detail
    assert calls == [], "the probe never ran, so it must not be recorded as having answered"

    # The lock is back, so once the failure TTL expires the key is probed again
    # rather than answering `probing` forever with nobody probing it.
    time.sleep(0.05)
    assert cache.get("k", probe).found == ["a"]
    assert calls == ["probed"]


def test_a_probe_that_cannot_be_dispatched_leaves_no_unstarted_thread_to_join():
    # `join_probes` must survive a thread registered but never started: `join`
    # on one raises, which would surface in the autouse fixture of whatever
    # test ran next rather than here.
    cache = _cache(join_timeout_seconds=QUICK_JOIN)

    with mock.patch.object(
        threading.Thread, "start", side_effect=RuntimeError("can't start new thread")
    ):
        cache.get("k", lambda: _ok("a"))

    join_probes(timeout=1.0)
    assert _live_probe_threads() == []


def test_a_raising_probe_leaves_no_live_thread():
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)

    def exploding_probe() -> ProbeOutcome[str]:
        raise RuntimeError("boom")

    cache.get("k", exploding_probe)
    join_probes(timeout=5.0)

    assert _live_probe_threads() == []


# --------------------------------------------------------------------------
# B3 — the status table, one test per row
# --------------------------------------------------------------------------


def test_no_cache_entry_with_a_probe_in_flight_is_probing():
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe()

    try:
        answer = cache.get("k", probe)
    finally:
        probe.finish()

    assert answer.status == DiscoveryStatus.PROBING
    assert answer.found == []
    # `detail` is non-empty only for `unavailable`; there is nothing to report yet.
    assert answer.detail == ""


def test_an_entry_within_its_ttl_and_ok_is_fresh():
    cache = _cache()
    cache.get("k", lambda: _ok("a"))

    answer = cache.get("k", lambda: pytest.fail("a fresh entry must not re-probe"))

    assert answer.status == DiscoveryStatus.FRESH
    assert answer.found == ["a"]
    assert answer.detail == ""


def test_an_entry_within_its_ttl_and_not_ok_is_unavailable():
    cache = _cache()
    cache.get("k", lambda: _failed("Codex CLI not found."))

    answer = cache.get("k", lambda: pytest.fail("a held failure must not re-probe"))

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail == "Codex CLI not found."


def test_an_expired_ok_entry_with_models_is_stale_while_it_refreshes():
    cache = _cache(success_ttl_seconds=0.0, join_timeout_seconds=QUICK_JOIN)
    assert cache.get("k", lambda: _ok("first")).found == ["first"]

    refresh = _HangingProbe(_ok("second"))
    try:
        answer = cache.get("k", refresh)
    finally:
        refresh.finish()

    # Stale beats blocking, and stale beats empty: the picker shows the catalogue
    # it had while the refresh runs behind it.
    assert answer.status == DiscoveryStatus.STALE
    assert answer.found == ["first"]
    assert answer.detail == ""


def test_an_expired_ok_entry_with_nothing_in_it_is_probing():
    cache = _cache(
        probe_budget_seconds=0.0,
        failure_ttl_seconds=0.01,
        join_timeout_seconds=QUICK_JOIN,
    )
    assert cache.get("k", lambda: _ok()).found == []
    time.sleep(0.05)

    refresh = _HangingProbe()
    try:
        answer = cache.get("k", refresh)
    finally:
        refresh.finish()

    # There is no cached catalogue to call stale, so the honest answer is that a
    # probe is running.
    assert answer.status == DiscoveryStatus.PROBING
    assert answer.found == []


def test_an_expired_failed_entry_is_probing_not_unavailable():
    cache = _cache(
        probe_budget_seconds=0.0,
        failure_ttl_seconds=0.01,
        join_timeout_seconds=QUICK_JOIN,
    )
    assert cache.get("k", lambda: _failed("LM Studio refused the connection.")).detail
    time.sleep(0.05)

    refresh = _HangingProbe()
    try:
        answer = cache.get("k", refresh)
    finally:
        refresh.finish()

    assert answer.status == DiscoveryStatus.PROBING
    # The stale reason must not be shown as though it were current: a probe is
    # in flight and may well succeed.
    assert answer.detail == ""


def test_the_probed_target_is_carried_across_every_status():
    """``target`` names the thing probed, and survives the answer it came with.

    A cold key has no outcome yet, so the *module* supplies the target for a
    first `probing` answer (the lmstudio empty-state copy names the base URL) —
    see the per-module tests. What this pins is that once the cache has an
    outcome, every answer derived from it keeps that outcome's target.
    """
    cache = _cache(success_ttl_seconds=0.0, join_timeout_seconds=QUICK_JOIN)

    fresh = cache.get("k", lambda: _ok("a", target="/opt/homebrew/bin/codex"))
    assert fresh.target == "/opt/homebrew/bin/codex"

    refresh = _HangingProbe(_ok("a"))
    try:
        stale = cache.get("k", refresh)
    finally:
        refresh.finish()
    assert stale.status == DiscoveryStatus.STALE
    assert stale.target == "/opt/homebrew/bin/codex"

    join_probes(timeout=5.0)
    failed = _cache().get("other", lambda: _failed("nope.", target="http://127.0.0.1:1234/v1"))
    assert failed.target == "http://127.0.0.1:1234/v1"


def test_a_detail_is_only_set_on_the_unavailable_status():
    """The one rule the UI copy depends on: a non-empty detail means "it failed"."""
    cache = _cache(success_ttl_seconds=0.0, join_timeout_seconds=QUICK_JOIN)

    assert cache.get("ok", lambda: _ok("a")).detail == ""
    assert cache.get("empty", lambda: _ok()).detail == ""
    assert cache.get("bad", lambda: _failed("it broke.")).detail == "it broke."

    refresh = _HangingProbe(_ok("b"))
    try:
        assert cache.get("ok", refresh).detail == ""
    finally:
        refresh.finish()


# --------------------------------------------------------------------------
# B4 — join_probes, and the lock reset() deliberately keeps
# --------------------------------------------------------------------------


def test_join_probes_waits_for_a_probe_that_has_finished_its_work():
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe(_ok("a"))

    cache.get("k", probe)
    assert probe.started.wait(timeout=5)
    assert len(_live_probe_threads()) == 1

    probe.finish()
    join_probes(timeout=5.0)

    assert _live_probe_threads() == []
    # And the answer the thread stored is there to be read.
    assert cache.get("k", probe).found == ["a"]


def test_join_probes_is_bounded_and_does_not_raise_on_a_probe_still_in_flight():
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe()

    try:
        cache.get("k", probe)
        assert probe.started.wait(timeout=5)

        started = time.monotonic()
        join_probes(timeout=0.1)
        waited = time.monotonic() - started
    finally:
        probe.finish()

    # A test fixture must not hang the suite on a genuinely stuck CLI.
    assert waited < 2.0


def test_join_probes_returns_at_once_when_nothing_is_probing():
    started = time.monotonic()
    join_probes(timeout=5.0)
    assert time.monotonic() - started < 1.0


def test_reset_keeps_the_probe_lock_of_a_running_probe():
    """R1-r4: replacing a held lock lets a second caller into a live probe."""
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    running = _HangingProbe()
    second_calls: list[int] = []

    try:
        cache.get("k", running)
        assert running.started.wait(timeout=5)

        cache.reset()
        answer = cache.get("k", lambda: second_calls.append(1) or _ok("second"))

        assert second_calls == [], "reset must not admit a second probe for a live key"
        assert answer.status == DiscoveryStatus.PROBING
    finally:
        running.finish()


# ==========================================================================
# Adversarial coverage (test-break stage).
#
# Everything above drives one caller at a time through a pinned state table.
# The defect this ticket exists to close was a *concurrency* defect — twenty
# callers behind one subprocess — and the fix replaces a calling-thread probe
# with a background thread, a lock, an event and a mutable cache entry. That is
# four shared-state primitives whose failure modes only appear under
# simultaneous callers, a mutated clock, an aliased list, or an exception that
# says nothing.
#
# Each test below names the weakness it exposes and the observable damage.
# ==========================================================================


def _concurrent(callers: int, work) -> list:
    """Run ``work`` on ``callers`` threads released together, and return results.

    A barrier rather than a stagger: the window these tests probe is between the
    cache read and the single-flight acquire, and a stagger closes it.
    """
    barrier = threading.Barrier(callers)
    results: list = [None] * callers
    errors: list[BaseException] = []

    def run(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = work()
        except BaseException as exc:  # noqa: BLE001 — re-raised in the main thread below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "a caller thread never returned"
    assert not errors, f"a caller raised: {errors[0]!r}"
    return results


def test_twenty_simultaneous_callers_on_a_cold_key_run_one_probe():
    """The ticket's own shape: twenty callers, one cold key, one CLI.

    Weakness: single-flight that only holds when callers arrive one at a time.
    Checking the cache, finding nothing, and *then* taking the probe lock is two
    steps, and every caller can be between them at once. A cache that dispatches
    per caller spawns twenty `opencode models` subprocesses, which is exactly the
    contention measured on this machine (2.9s solo, 26.8s at twenty-way) and the
    reason the pool died.

    Damage if broken: twenty subprocesses and twenty daemon threads per cold
    request, and the thing the fix was supposed to remove is back behind a
    different queue.
    """
    cache = _cache(join_timeout_seconds=QUICK_JOIN)
    probe = _HangingProbe(_ok("a"))

    try:
        answers = _concurrent(20, lambda: cache.get("k", probe))
        assert probe.started.wait(timeout=5), "the probe never started"

        assert probe.calls == 1, f"{probe.calls} probes ran for one cold key"
        assert len(_live_probe_threads()) == 1, "one key, one probe thread"
    finally:
        probe.finish()

    assert all(isinstance(answer, ProbeAnswer) for answer in answers)
    # Nobody waited for the CLI, and nobody was handed a status that claims an
    # answer the probe has not produced.
    assert {answer.status for answer in answers} == {DiscoveryStatus.PROBING}
    assert all(answer.found == [] for answer in answers)


def test_simultaneous_callers_on_an_expired_catalogue_are_never_handed_an_empty_list():
    """Weakness: the refresh path losing the cached catalogue it is refreshing.

    `stale` is the one row of the table where a caller holds an answer *and* a
    probe is in flight, so it is the row where a read/dispatch ordering mistake
    is invisible with one caller and obvious with ten: whoever dispatches drops
    to the fresh-entry-less branch and returns ``[]``.

    Damage if broken: the picker a person is looking at empties itself every time
    the TTL rolls over — "what left the picker permanently empty before".
    """
    cache = _cache(success_ttl_seconds=0.0, join_timeout_seconds=QUICK_JOIN)
    assert cache.get("k", lambda: _ok("first", target="/bin/thing")).found == ["first"]

    refresh = _HangingProbe(_ok("second"))
    try:
        answers = _concurrent(10, lambda: cache.get("k", refresh))
        assert refresh.started.wait(timeout=5)
        assert refresh.calls == 1, f"{refresh.calls} refreshes ran for one expired key"
    finally:
        refresh.finish()

    for answer in answers:
        assert answer.found == ["first"], "a caller lost the catalogue mid-refresh"
        assert answer.status == DiscoveryStatus.STALE
        assert answer.target == "/bin/thing"
        assert answer.detail == ""


def test_simultaneous_callers_on_distinct_keys_each_get_their_own_probe():
    """Weakness: a per-key lock that is really a per-cache lock.

    The LM Studio key is caller-controlled, so distinct keys are the normal case
    and not an exotic one. One global lock would make two workspaces with
    different base URLs serialize, and the second's answer would be the first's.
    """
    cache = _cache(label="lmstudio", join_timeout_seconds=PATIENT_JOIN)
    keys = [f"http://host-{i}:1234/v1" for i in range(8)]
    answers: dict[str, ProbeAnswer[str]] = {}
    barrier = threading.Barrier(len(keys))

    def fetch(key: str) -> None:
        barrier.wait(timeout=10)
        answers[key] = cache.get(key, lambda: _ok(key, target=key))

    threads = [threading.Thread(target=fetch, args=(key,), daemon=True) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "a caller thread never returned"

    assert set(answers) == set(keys)
    for key, answer in answers.items():
        assert answer.found == [key], f"{key} was served another key's catalogue"
        assert answer.target == key
        assert answer.status == DiscoveryStatus.FRESH


# --- type and structure mutations -----------------------------------------


def test_both_answer_types_are_frozen():
    """Weakness: a mutable answer shared between the cache and every caller.

    The spec says frozen for both, and the cache hands the same object to every
    caller of a fresh key. One caller writing `answer.status = FRESH` — or a
    render helper normalizing `detail` in place — would rewrite what the next
    request reads, from another thread, with no lock.
    """
    answer = ProbeAnswer(found=["a"], status=DiscoveryStatus.FRESH, detail="", target="t")
    outcome = _ok("a")

    with pytest.raises(dataclasses.FrozenInstanceError):
        answer.status = DiscoveryStatus.UNAVAILABLE  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        answer.detail = "rewritten"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.ok = False  # type: ignore[misc]


def test_a_probe_that_keeps_mutating_its_own_list_cannot_reach_the_cache():
    """Weakness: `frozen=True` freezes the field, not the list behind it.

    `_parse_native_models` builds a list and sorts it; a probe that returned its
    working list and kept appending would edit the cache from a daemon thread
    while a request reads it. The existing mutation test mutates the *answer*,
    which a `list(...)` on the way out already defends; this mutates the
    *outcome*, which only a copy on the way in defends.
    """
    cache = _cache()
    retained: list[str] = ["a"]

    cache.get("k", lambda: ProbeOutcome(found=retained, ok=True, detail="", target="t"))
    # The probe's own list, edited after the cache filed it — a sort, an append,
    # a clear-and-refill are all things a probe body does to its working list.
    retained.append("injected-after-the-fact")

    assert cache.get("k", lambda: pytest.fail("a fresh entry must not re-probe")).found == ["a"]


def test_a_probe_that_answers_with_nothing_at_all_is_not_recorded_as_an_empty_catalogue():
    """Weakness: a probe returning `None` (or a bare list, as all three did
    before this change) being filed as "the CLI answered with no models".

    The three discovery modules are migrating their return type in this same
    change, so a half-migrated module returning the old `list[T]` is the likely
    mistake rather than an exotic one. Filing that as `fresh` + `[]` is the
    collapse this ticket exists to remove, with no log line to find it by.
    """
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)

    answer = cache.get("none", lambda: None)  # type: ignore[arg-type,return-value]

    assert answer.status is not DiscoveryStatus.FRESH, (
        "a probe that did not return an outcome was recorded as a successful "
        "empty catalogue — 'it failed' and 'there is nothing' collapsed again"
    )
    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail, "a non-answer must carry a reason"


# --- the clock, the detail, and the boundary values ------------------------


def test_a_wall_clock_jump_does_not_expire_a_fresh_entry():
    """R1-r2. Weakness: TTL arithmetic on `time.time()`.

    A keyless module-global cache plus a wall clock has produced CI flakes in
    this repo before, and ntp stepping a laptop's clock forward is not
    hypothetical. `time.monotonic()` is immune; `time.time()` would expire every
    entry at once and re-probe three CLIs on the next request.
    """
    cache = _cache()
    calls: list[int] = []

    def probe() -> ProbeOutcome[str]:
        calls.append(1)
        return _ok("a")

    assert cache.get("k", probe).status == DiscoveryStatus.FRESH

    with mock.patch("time.time", return_value=time.time() + 10_000_000):
        answer = cache.get("k", probe)

    assert answer.status == DiscoveryStatus.FRESH
    assert answer.found == ["a"]
    assert len(calls) == 1, "the TTL is measured against the wall clock, not the monotonic one"


def test_a_probe_whose_exception_says_nothing_still_carries_a_reason():
    """Weakness: `detail=str(exc)` on an exception whose `str()` is empty.

    `raise RuntimeError()`, `KeyError()` and a bare `httpx.ConnectError("")` all
    stringify to "" or near it. The UI renders `detail` verbatim for
    `unavailable` (U7), so an empty detail is a `role="status"` line with nothing
    in it — the blank pane, reached through the one state that is supposed to
    explain itself.
    """
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)

    answer = cache.get("k", lambda: (_ for _ in ()).throw(RuntimeError()))

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert answer.detail.strip(), "an unavailable answer with an empty detail renders a blank hint"


def test_a_raising_probes_detail_stays_short_enough_for_a_field_hint(caplog):
    """R2-r1. Weakness: an unbounded `str(exc)` reaching the operator's field.

    A `CalledProcessError` carries the whole argv, and an httpx error on a long
    URL is longer still. The detail is rendered inside a form hint, so a
    multi-kilobyte string is a layout failure in the surface this ticket adds;
    a traceback in it would also leak this machine's paths.
    """
    cache = _cache(join_timeout_seconds=PATIENT_JOIN)
    noise = "x" * 5_000

    with caplog.at_level(logging.WARNING, logger="loregarden.services.discovery_cache"):
        answer = cache.get("k", lambda: (_ for _ in ()).throw(RuntimeError(noise)))

    assert answer.status == DiscoveryStatus.UNAVAILABLE
    assert len(answer.detail) <= 200, f"the field hint was {len(answer.detail)} chars"
    assert "Traceback" not in answer.detail
    assert "\n" not in answer.detail
    # Truncated for the field, not lost: the full text is still in the log.
    assert any(noise in record.getMessage() for record in caplog.records), (
        "the exception was shortened for the UI and nowhere else — the whole "
        "reason is now unrecoverable"
    )


def test_a_zero_join_timeout_dispatches_without_waiting_and_still_lands_its_answer():
    """Boundary: `join_timeout_seconds=0.0`, which an implementer tuning the
    0.25s default on a loaded box will reach for.

    Weakness: `Event.wait(0)` treated as "no timeout" rather than "do not wait",
    or a dispatch skipped entirely when there is no time to wait for it — which
    would make the key permanently `probing` and the picker permanently empty.
    """
    cache = _cache(join_timeout_seconds=0.0)
    probe_ran = threading.Event()

    def probe() -> ProbeOutcome[str]:
        probe_ran.set()
        return _ok("a")

    started = time.monotonic()
    answer = cache.get("k", probe)
    waited = time.monotonic() - started

    assert waited < 1.0, f"a zero join still waited {waited:.2f}s"
    # Either answer is honest — whether a probe that returns immediately wins a
    # zero-length race is a scheduling question, and pinning it would be a flake.
    # What must not happen is a key that never gets probed at all.
    assert answer.status in {DiscoveryStatus.PROBING, DiscoveryStatus.FRESH}
    if answer.status == DiscoveryStatus.FRESH:
        assert answer.found == ["a"]
    assert probe_ran.wait(timeout=5), "no probe was dispatched at all"

    join_probes(timeout=5.0)
    assert cache.get("k", probe).found == ["a"], "the fire-and-forget answer was never stored"


def test_a_long_and_punctuated_key_still_names_a_thread():
    """Weakness: the thread name built from a caller-controlled key.

    The lmstudio key is a URL (`normalize_lmstudio_base_url`), so colons and
    slashes are the normal case, and the base URL comes from a query parameter —
    a 500-character one is a request away. The name is what an operator reads in
    a stack dump; it must exist and it must not be the whole URL's length in a
    thread name.
    """
    cache = _cache(label="lmstudio", join_timeout_seconds=PATIENT_JOIN)
    key = "http://" + ("a" * 400) + ":1234/v1"
    seen: list[str] = []

    cache.get(key, lambda: seen.append(threading.current_thread().name) or _ok("a"))

    assert seen, "the probe never ran"
    assert seen[0].startswith("discovery-probe-lmstudio-")
    assert len(seen[0]) <= 120, f"the thread name is {len(seen[0])} characters long"


def test_two_caches_do_not_share_entries_and_join_probes_covers_both():
    """Weakness: module-level state that is really per-process.

    Three caches exist in the tree (opencode, codex, lmstudio) and the registry
    `join_probes` walks is module-level. A registry that only holds the most
    recently dispatched thread — a reassignment where an append belonged, or a
    single-slot "current probe" — leaves the other thread running past the
    autouse fixture, which is how one test's subprocess reached the next (R1-r1).

    The probes keep working for a measurable moment *after* they are released, so
    a `join_probes` that skipped one is visible rather than merely lucky.
    """

    class _SlowFinishProbe:
        """Released, then still busy: the window a dropped registry entry hides in."""

        def __init__(self, outcome: ProbeOutcome[str], *, finish_seconds: float) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.returned = threading.Event()
            self._outcome = outcome
            self._finish_seconds = finish_seconds

        def __call__(self) -> ProbeOutcome[str]:
            self.started.set()
            assert self.release.wait(timeout=20), "the probe was never released"
            time.sleep(self._finish_seconds)
            self.returned.set()
            return self._outcome

    first = _cache(label="one", join_timeout_seconds=QUICK_JOIN)
    second = _cache(label="two", join_timeout_seconds=QUICK_JOIN)
    probe_one = _SlowFinishProbe(_ok("from-one"), finish_seconds=1.2)
    probe_two = _SlowFinishProbe(_ok("from-two"), finish_seconds=0.0)

    try:
        assert first.get("same-key", probe_one).status == DiscoveryStatus.PROBING
        assert second.get("same-key", probe_two).status == DiscoveryStatus.PROBING
        assert probe_one.started.wait(timeout=5)
        assert probe_two.started.wait(timeout=5)
        assert len(_live_probe_threads()) == 2
    finally:
        probe_one.release.set()
        probe_two.release.set()

    join_probes(timeout=5.0)

    assert _live_probe_threads() == [], "a probe thread survived join_probes"
    assert probe_one.returned.is_set() and probe_two.returned.is_set(), (
        "join_probes returned while a probe was still working — the registry is "
        "not holding every live thread, so the autouse fixture cannot drain them"
    )
    # Two caches, one key name, two different catalogues: no shared entry table.
    assert first.get("same-key", probe_one).found == ["from-one"]
    assert second.get("same-key", probe_two).found == ["from-two"]
