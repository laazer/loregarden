"""Memoized, single-flight, non-blocking caching for model-discovery probes.

Every adapter discovers its model catalog the same way: shell out to a CLI, or
call a local server, and hope it answers. Four properties matter, and all four
are easy to get wrong once per module:

1. **The probe is memoized.** ``codex debug models`` and the LM Studio endpoints
   were called afresh on every ``runtime-options`` request, so opening Settings
   paid for the discovery of every adapter, every time.
2. **The lock is never held across the probe.** Holding it makes concurrent
   callers queue behind a subprocess. ``runtime_options_payload`` used to run
   inside a request holding a database connection, so a queue of callers is a
   queue of held connections — fifteen of them exhausted the pool and the API
   stopped answering, ``/health`` included.
3. **A failure is held for longer than the probe's own budget.** A hung CLI
   burns its whole timeout before failing, so a shorter failure TTL lets the
   next caller start probing the instant the last one gave up: a probe is then
   permanently in flight rather than retried now and then.
4. **The probe never runs on the calling thread.** Handing the connection back
   saved the pool but not the request: ``get()`` ran the probe inline and the
   three probes were called in sequence, so a cold cache with three hanging CLIs
   cost 45s + 12s + 2x2s. The probe now runs on a background daemon thread that
   the caller joins for a bounded ``join_timeout_seconds``.

The third is enforced at construction rather than documented, because it is an
ordering between two numbers that live in different modules and drift apart.

Making the probe non-blocking creates a second defect in the same move: a cold
key has no catalogue to return, and an empty list is byte-identical to "the CLI
is not installed". So a probe reports a :class:`ProbeOutcome` rather than a bare
list, the cache answers with a :class:`ProbeAnswer` carrying a
:class:`DiscoveryStatus`, and "it failed" and "there is nothing" stay different
answers all the way out to the picker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Generic, TypeVar

from loregarden.models.domain.enums import DiscoveryStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: ``detail`` is rendered verbatim inside a form hint beneath the model field, so
#: an unbounded ``str(exc)`` — a ``CalledProcessError`` carries its whole argv —
#: is a layout failure in the surface this exists to serve.
MAX_DETAIL_CHARS = 200

#: A thread name is what an operator reads in a stack dump. The lmstudio cache
#: key is a caller-supplied URL, so it has to be clamped to stay readable.
MAX_THREAD_NAME_CHARS = 120

#: Three sequential bounded joins total 0.75s against runtime-options' 2s budget.
#: Retune it with a measurement, not a guess.
DEFAULT_JOIN_TIMEOUT_SECONDS = 0.25


def clamp_detail(text: str) -> str:
    """One line, no stack trace, short enough to render in a field hint."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= MAX_DETAIL_CHARS:
        return collapsed
    return collapsed[: MAX_DETAIL_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True)
class ProbeOutcome(Generic[T]):
    """What a probe callable reports: what it found, and whether it could look.

    ``ok=False`` means the probe could not answer and ``detail`` says why, in a
    complete sentence the operator reads. ``ok=True`` with an empty ``found`` is
    a legitimate, nameable state — OpenCode authenticated to nothing, LM Studio
    running with no model loaded, codex with only a profile default.
    """

    found: list[T] = field(default_factory=list)
    ok: bool = True
    detail: str = ""
    target: str = ""


@dataclass(frozen=True)
class ProbeAnswer(Generic[T]):
    """What a caller is given: the catalogue, and which of four states it is in."""

    found: list[T]
    status: DiscoveryStatus
    detail: str
    target: str

    def as_discovery_block(self) -> dict[str, object]:
        """This answer as ``runtime-options``' ``model_discovery[provider]`` object.

        One definition, because all three discovery modules render the same
        three keys and a fourth provider would otherwise copy them again.
        """
        return {"status": self.status, "detail": self.detail, "target": self.target}


@dataclass(frozen=True)
class _Entry(Generic[T]):
    found: list[T]
    ok: bool
    detail: str
    target: str
    expires_at: float


#: Every live probe thread in the process, so a test fixture can drain them: a
#: thread outliving its test reads a `monkeypatch`ed binary that no longer exists
#: and leaks its subprocess into whatever runs next.
_PROBE_THREADS: list[threading.Thread] = []
_REGISTRY_LOCK = threading.Lock()


def _register_probe_thread(thread: threading.Thread) -> None:
    with _REGISTRY_LOCK:
        _PROBE_THREADS[:] = [live for live in _PROBE_THREADS if live.is_alive()]
        _PROBE_THREADS.append(thread)


def join_probes(timeout: float = 5.0) -> None:
    """Wait, at most ``timeout`` in total, for every in-flight discovery probe.

    Bounded on purpose: a genuinely stuck CLI must not hang the suite. Callers
    get no signal about which probes were still running, because every one of
    them is a daemon thread that cannot keep the process alive.
    """
    with _REGISTRY_LOCK:
        threads = list(_PROBE_THREADS)
    deadline = time.monotonic() + timeout
    for thread in threads:
        # ``ident`` is set by ``start()``. A registered thread that never
        # started — ``start()`` raised — would make ``join`` raise, turning a
        # test fixture's drain into an error about an unrelated failure.
        if thread.ident is None:
            continue
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    with _REGISTRY_LOCK:
        _PROBE_THREADS[:] = [live for live in _PROBE_THREADS if live.is_alive()]


class ProbeCache(Generic[T]):
    """Holds one discovery probe's results, one entry per key."""

    def __init__(
        self,
        *,
        label: str,
        probe_budget_seconds: float,
        success_ttl_seconds: float,
        failure_ttl_seconds: float,
        join_timeout_seconds: float = DEFAULT_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        if failure_ttl_seconds <= probe_budget_seconds:
            raise ValueError(
                "failure_ttl_seconds must outlast probe_budget_seconds "
                f"({failure_ttl_seconds} <= {probe_budget_seconds}); otherwise a probe that "
                "hangs for its whole budget is retried the moment it gives up, and a broken "
                "CLI keeps a probe permanently in flight."
            )
        self._label = label
        self._probe_budget_seconds = probe_budget_seconds
        self._success_ttl_seconds = success_ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._join_timeout_seconds = join_timeout_seconds
        #: Guards the fields below, and nothing else. Never held across a probe.
        self._state_lock = threading.Lock()
        self._entries: dict[str, _Entry[T]] = {}
        self._probe_locks: dict[str, threading.Lock] = {}

    # -- internals ---------------------------------------------------------

    def _probe_lock_for(self, key: str) -> threading.Lock:
        with self._state_lock:
            lock = self._probe_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._probe_locks[key] = lock
            return lock

    def _read(self, key: str) -> tuple[_Entry[T] | None, bool]:
        """The memoized entry for ``key``, and whether it is still fresh."""
        with self._state_lock:
            entry = self._entries.get(key)
        if entry is None:
            return None, False
        return entry, time.monotonic() < entry.expires_at

    def _store(self, key: str, outcome: ProbeOutcome[T]) -> None:
        # An empty answer is retried sooner whether or not the probe succeeded:
        # the operator is about to authenticate a provider or load a model.
        ttl = self._success_ttl_seconds if outcome.found else self._failure_ttl_seconds
        with self._state_lock:
            self._entries[key] = _Entry(
                # A copy: a probe that keeps sorting or appending to its own
                # working list would otherwise edit the cache from its thread.
                found=list(outcome.found),
                ok=outcome.ok,
                detail=outcome.detail,
                target=outcome.target,
                expires_at=time.monotonic() + ttl,
            )

    def _thread_name(self, key: str) -> str:
        name = f"discovery-probe-{self._label}-{key or 'default'}"
        return name[:MAX_THREAD_NAME_CHARS]

    @staticmethod
    def _answer_from_outcome(outcome: ProbeOutcome[T]) -> ProbeAnswer[T]:
        """A completed probe reports its own status, never ``probing``."""
        if outcome.ok:
            return ProbeAnswer(
                found=list(outcome.found),
                status=DiscoveryStatus.FRESH,
                detail="",
                target=outcome.target,
            )
        return ProbeAnswer(
            found=[],
            status=DiscoveryStatus.UNAVAILABLE,
            detail=outcome.detail,
            target=outcome.target,
        )

    @staticmethod
    def _answer_from_entry(entry: _Entry[T]) -> ProbeAnswer[T]:
        if entry.ok:
            return ProbeAnswer(
                found=list(entry.found),
                status=DiscoveryStatus.FRESH,
                detail="",
                target=entry.target,
            )
        return ProbeAnswer(
            found=[],
            status=DiscoveryStatus.UNAVAILABLE,
            detail=entry.detail,
            target=entry.target,
        )

    @staticmethod
    def _in_flight_answer(entry: _Entry[T] | None) -> ProbeAnswer[T]:
        """What to say while a probe for this key is running.

        A catalogue that has merely expired beats both blocking and emptiness, so
        it is served as ``stale``. A held failure is not: it may well be fixed by
        the probe that is running, and showing its reason as though it were
        current is how a recovered CLI keeps reporting the old error.
        """
        if entry is not None and entry.ok and entry.found:
            return ProbeAnswer(
                found=list(entry.found),
                status=DiscoveryStatus.STALE,
                detail="",
                target=entry.target,
            )
        return ProbeAnswer(
            found=[],
            status=DiscoveryStatus.PROBING,
            detail="",
            target=entry.target if entry is not None else "",
        )

    def _recorded_outcome(self, key: str, probe: Callable[[], ProbeOutcome[T]]) -> ProbeOutcome[T]:
        """Run ``probe`` and turn whatever it did into an outcome worth filing."""
        last_target = ""
        entry, _fresh = self._read(key)
        if entry is not None:
            last_target = entry.target
        try:
            outcome = probe()
        except Exception as exc:  # noqa: BLE001 — classified below, never discarded
            # Warning, not debug: this is the only signal that an empty picker is
            # a discovery failure rather than a CLI with nothing to offer. The
            # full text stays here; only the operator's field hint is clamped.
            logger.warning(
                "Discovery probe %s/%s raised: %s",
                self._label,
                key or "default",
                exc,
            )
            reason = " ".join(str(exc).split()) or type(exc).__name__
            return ProbeOutcome(
                found=[],
                ok=False,
                detail=clamp_detail(f"The discovery probe failed: {reason}."),
                target=last_target,
            )
        # A probe is a foreign callable and its return value is unvalidated here.
        # Filing a non-outcome — a half-migrated module still returning `list[T]`,
        # or `None` — as "the CLI answered with nothing" is the exact collapse
        # this module exists to remove, and it would carry no log line either.
        if not isinstance(outcome, ProbeOutcome):  # py-org: allow-isinstance
            logger.warning(
                "Discovery probe %s/%s returned %r rather than a ProbeOutcome",
                self._label,
                key or "default",
                type(outcome).__name__,
            )
            return ProbeOutcome(
                found=[],
                ok=False,
                detail=clamp_detail(
                    "The discovery probe returned no outcome "
                    f"({type(outcome).__name__}), so nothing could be discovered."
                ),
                target=last_target,
            )
        return outcome

    def _undispatched_outcome(self, key: str, exc: BaseException) -> ProbeOutcome[T]:
        """A probe that could not even be started is a failure like any other.

        ``Thread.start`` raises ``RuntimeError`` on a process that has run out
        of threads. Letting that escape ``get`` would do two things at once:
        turn a decorative model catalogue into a 500 on ``runtime-options``,
        and leave this key's probe lock held for the lifetime of the process —
        so every later call answers ``probing`` forever, never re-probing even
        after the failure TTL, while the client polls it every 1500ms. The
        caller is told ``ok=False`` instead, and the failure TTL retries it.
        """
        logger.warning(
            "Discovery probe %s/%s could not be dispatched: %s",
            self._label,
            key or "default",
            exc,
        )
        reason = " ".join(str(exc).split()) or type(exc).__name__
        entry, _fresh = self._read(key)
        return ProbeOutcome(
            found=[],
            ok=False,
            detail=clamp_detail(f"The discovery probe could not be started: {reason}."),
            target=entry.target if entry is not None else "",
        )

    def _probe_on_thread(
        self,
        key: str,
        probe: Callable[[], ProbeOutcome[T]],
        lock: threading.Lock,
        done: threading.Event,
        holder: list[ProbeOutcome[T] | None],
    ) -> None:
        try:
            outcome = self._recorded_outcome(key, probe)
            self._store(key, outcome)
            holder[0] = outcome
        finally:
            # Both in a `finally`, and in this order: a probe that raised past
            # `_recorded_outcome` must not leave the key wedged for the lifetime
            # of the process, with a lock nobody holds a reference to and a
            # caller waiting on an event nobody will set.
            lock.release()
            done.set()

    # -- the one entry point -----------------------------------------------

    def get(
        self,
        key: str,
        probe: Callable[[], ProbeOutcome[T]],
        *,
        target: str = "",
    ) -> ProbeAnswer[T]:
        """Return the answer for ``key``, probing on a background thread if stale.

        At most one probe per key runs at a time, and no caller waits longer than
        ``join_timeout_seconds`` for it. A model catalogue decorates a picker; no
        request may block for the length of a subprocess to render one.

        ``target`` is what the caller knows is being probed without probing it —
        a resolved binary path, a base URL. A cold key has no outcome to read one
        from, and the picker's copy names the thing it is waiting on, so the
        answer is backfilled with it when the probe has not supplied one yet.
        """
        answer = self._answer(key, probe)
        if answer.target or not target:
            return answer
        return replace(answer, target=target)

    def _answer(self, key: str, probe: Callable[[], ProbeOutcome[T]]) -> ProbeAnswer[T]:
        entry, fresh = self._read(key)
        if fresh and entry is not None:
            return self._answer_from_entry(entry)

        lock = self._probe_lock_for(key)
        if not lock.acquire(blocking=False):
            # Somebody else is probing this key. Serve what there is, now.
            return self._in_flight_answer(entry)

        released = False
        try:
            # The holder may have finished between the read above and the acquire.
            entry, fresh = self._read(key)
            if fresh and entry is not None:
                return self._answer_from_entry(entry)

            done = threading.Event()
            holder: list[ProbeOutcome[T] | None] = [None]
            thread = threading.Thread(
                target=self._probe_on_thread,
                args=(key, probe, lock, done, holder),
                name=self._thread_name(key),
                daemon=True,
            )
            _register_probe_thread(thread)
            try:
                thread.start()
            except Exception as exc:  # noqa: BLE001 — recorded ok=False below, never discarded
                # `released` is still False, so the `finally` hands the lock
                # back. Marking it handed off *before* `start()` is what wedged
                # the key: the thread that would have released it never ran.
                outcome = self._undispatched_outcome(key, exc)
                self._store(key, outcome)
                return self._answer_from_outcome(outcome)
            # The thread owns the lock from here: it releases it in its `finally`.
            released = True
        finally:
            if not released:
                lock.release()

        done.wait(self._join_timeout_seconds)
        landed = holder[0]
        if done.is_set() and landed is not None:
            return self._answer_from_outcome(landed)
        return self._in_flight_answer(entry)

    def reset(self) -> None:
        """Drop every memoized answer so the next call re-probes.

        The probe locks are deliberately kept: one of them may be held by a
        probe that is still running, and replacing it would let a second caller
        into a probe this cache thinks nobody is running.
        """
        with self._state_lock:
            self._entries.clear()
