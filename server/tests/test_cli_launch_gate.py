"""One `cursor-agent` at a time may clear authentication.

Every cursor launch re-reads its saved login from the macOS keychain. Parallel
stage lanes start within milliseconds of each other, and concurrent access to the
same keychain item killed the losing lanes with `errSecDuplicateItem` /
"couldn't find your saved login" before they reached a model — which the
orchestrator then had to interpret with no stage report to go on. The slot is held
only until a process produces output, so lanes still do their work in parallel.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.launch_gate import acquire_launch_slot
from sqlmodel import Session


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


def test_cursor_launches_wait_for_the_held_slot():
    held = acquire_launch_slot("cursor")
    try:
        acquired, thread, slots = _acquire_in_thread("cursor")
        assert not acquired.wait(timeout=0.5)  # blocked while the slot is held
    finally:
        held.release()

    assert acquired.wait(timeout=5)
    slots[0].release()
    thread.join(timeout=5)


def test_non_keychain_adapters_are_not_serialized():
    """claude authenticates from a config file; local/lmstudio not at all."""
    held = acquire_launch_slot("claude")
    try:
        acquired, thread, slots = _acquire_in_thread("claude")
        assert acquired.wait(timeout=5)
    finally:
        held.release()
    slots[0].release()
    thread.join(timeout=5)


def test_release_is_idempotent():
    """Callers release on first output and again in `finally`."""
    slot = acquire_launch_slot("cursor")
    slot.release()
    slot.release()

    # The second release must not have left the lock free for two holders.
    other = acquire_launch_slot("cursor")
    acquired, thread, slots = _acquire_in_thread("cursor")
    try:
        assert not acquired.wait(timeout=0.5)
    finally:
        other.release()
    assert acquired.wait(timeout=5)
    slots[0].release()
    thread.join(timeout=5)


def test_print_mode_frees_the_slot_once_the_process_emits(db_session: Session):
    """A lane that has started streaming no longer blocks its siblings, so the
    launches serialize but the runs themselves stay parallel.
    """
    # Print immediately, then stay alive well past the assertion below.
    script = 'import time\nprint(\'{"type":"system"}\', flush=True)\ntime.sleep(3)\n'
    invocation = SimpleNamespace(
        argv=[sys.executable, "-u", "-c", script],
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
                timeout=10,
                streamer=streamer,
                run_id="test-launch-gate-run",
            )
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while not streamer.lines and time.time() < deadline:
        time.sleep(0.05)
    assert streamer.lines, "child never streamed"

    acquired, sibling_thread, slots = _acquire_in_thread("cursor")
    assert acquired.wait(timeout=2), "streaming lane still held the launch slot"
    assert thread.is_alive(), "child exited before the sibling could launch"
    slots[0].release()
    sibling_thread.join(timeout=5)

    thread.join(timeout=15)
    assert result, "run did not finish"
