"""One `cursor-agent` at a time may clear authentication.

Every cursor launch re-reads its saved login from the macOS keychain. Parallel
stage lanes start within milliseconds of each other, and concurrent access to the
same keychain item killed the losing lanes with `errSecDuplicateItem` /
"couldn't find your saved login" before they reached a model — which the
orchestrator then had to interpret with no stage report to go on. The slot is held
only until a process produces output, so lanes still do their work in parallel.

The slot is a module-global lock, so a test that fails while holding it used to
hold it for the rest of the worker process and stall every later cursor launch
by `MAX_WAIT_SECONDS`. Acquire through `with` here, never bare (799).
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from loregarden.agents.executors import launch_gate
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.launch_gate import acquire_launch_slot
from sqlmodel import Session


@pytest.fixture(autouse=True)
def _no_leaked_launch_slot():
    """Fail the test that leaks the slot, rather than the one that waits on it.

    Without this the cost of a leak lands on whichever unrelated test runs next
    in this worker — it blocks for `MAX_WAIT_SECONDS` and dies on the suite
    timeout, naming itself. That is how a wall-clock race in this file was
    reported as a timeout in `test_auto_mode_subtree` (799).
    """
    yield
    # A sibling thread may still be a few instructions from its release.
    deadline = time.time() + 2
    while launch_gate._launch_lock.locked() and time.time() < deadline:
        time.sleep(0.01)
    if launch_gate._launch_lock.locked():
        launch_gate._launch_lock.release()
        pytest.fail("test finished still holding the launch slot")


class _CollectingStreamer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_stream_line(self, line: str) -> None:
        self.lines.append(line)


def _acquire_in_thread(adapter: str) -> tuple[threading.Event, threading.Thread, list]:
    """Try to take a slot off-thread; the event fires once it is held."""
    acquired = threading.Event()
    slots: list = []

    def _worker() -> None:
        slots.append(acquire_launch_slot(adapter))
        acquired.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return acquired, thread, slots


def _release_all(slots: list, thread: threading.Thread) -> None:
    """Release whatever the off-thread waiter managed to take, then join it."""
    for slot in slots:
        slot.release()
    thread.join(timeout=5)


def test_cursor_launches_wait_for_the_held_slot():
    # Order matters: this thread must hold the slot before the waiter asks for
    # it, or the waiter wins the race and *this* thread blocks MAX_WAIT_SECONDS.
    thread: threading.Thread | None = None
    slots: list = []
    try:
        with acquire_launch_slot("cursor"):
            acquired, thread, slots = _acquire_in_thread("cursor")
            assert not acquired.wait(timeout=0.5)  # blocked while the slot is held
        assert acquired.wait(timeout=5)
    finally:
        if thread is not None:
            _release_all(slots, thread)


def test_non_keychain_adapters_are_not_serialized():
    """claude authenticates from a config file; local/lmstudio not at all."""
    thread: threading.Thread | None = None
    slots: list = []
    try:
        with acquire_launch_slot("claude"):
            acquired, thread, slots = _acquire_in_thread("claude")
            assert acquired.wait(timeout=5)
    finally:
        if thread is not None:
            _release_all(slots, thread)


def test_release_is_idempotent():
    """Callers release on first output and again in `finally`."""
    slot = acquire_launch_slot("cursor")
    slot.release()
    slot.release()

    # The second release must not have left the lock free for two holders.
    thread: threading.Thread | None = None
    slots: list = []
    try:
        with acquire_launch_slot("cursor"):
            acquired, thread, slots = _acquire_in_thread("cursor")
            assert not acquired.wait(timeout=0.5)
        assert acquired.wait(timeout=5)
    finally:
        if thread is not None:
            _release_all(slots, thread)


def test_raising_inside_the_slot_still_frees_it():
    """The regression 799 was filed for: a leak costs the *next* caller, not this one."""
    with pytest.raises(RuntimeError):
        with acquire_launch_slot("cursor"):
            raise RuntimeError("boom")

    # Free, and takeable without waiting out MAX_WAIT_SECONDS.
    acquired, thread, slots = _acquire_in_thread("cursor")
    try:
        assert acquired.wait(timeout=5), "a raise inside the slot left the lock held"
    finally:
        _release_all(slots, thread)


def test_print_mode_frees_the_slot_once_the_process_emits(db_session: Session, tmp_path):
    """A lane that has started streaming no longer blocks its siblings, so the
    launches serialize but the runs themselves stay parallel.

    The child outlives the assertions by waiting on a sentinel this test
    creates, rather than by sleeping. It used to sleep 3s while up to 7s of
    polling ran ahead of the liveness check, so under a loaded parallel run the
    child was legitimately gone and the assertion failed on wall clock (799).
    """
    sentinel = tmp_path / "let-the-child-exit"
    script = (
        "import os, sys, time\n"
        'print(\'{"type":"system"}\', flush=True)\n'
        "deadline = time.time() + 30\n"
        "while not os.path.exists(sys.argv[1]) and time.time() < deadline:\n"
        "    time.sleep(0.02)\n"
    )
    invocation = SimpleNamespace(
        argv=[sys.executable, "-u", "-c", script, str(sentinel)],
        cwd=None,
        stdin_prompt=None,
        interactive=False,
        adapter="cursor",
        env={},
    )
    streamer = _CollectingStreamer()
    executor = CliAgentExecutor(db_session)
    result: list = []

    def _run() -> None:
        result.append(
            executor._run_print_mode(
                invocation=invocation,
                repo_root=Path.cwd(),
                # Generous: the idle timer starts at the child's one line, and
                # must not fire while this test is still asserting.
                timeout=60,
                streamer=streamer,
                run_id="test-launch-gate-run",
            )
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    acquired, sibling_thread, slots = None, None, []
    try:
        deadline = time.time() + 10
        while not streamer.lines and time.time() < deadline:
            time.sleep(0.05)
        assert streamer.lines, "child never streamed"

        acquired, sibling_thread, slots = _acquire_in_thread("cursor")
        assert acquired.wait(timeout=5), "streaming lane still held the launch slot"
        # Meaningful because the child is waiting on the sentinel below, not on
        # a sleep that may already have elapsed.
        assert thread.is_alive(), "child exited before the sibling could launch"
    finally:
        if sibling_thread is not None:
            _release_all(slots, sibling_thread)
        sentinel.touch()

    thread.join(timeout=30)
    assert result, "run did not finish"
