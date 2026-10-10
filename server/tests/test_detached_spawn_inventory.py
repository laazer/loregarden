"""Which spawn sites detach, and which surfaces this ticket deliberately leaves
alone (AC6, AC23, AC36, AC37).

The scope of this ticket is print-mode runs. That is a *measured* boundary, not
a convenience: over 312 runs in a fortnight, 203 were `[external-harness]` (no
local pid to identify), 87 were print mode and carried a pid plus an identity,
and the approval-gated permission bridge carried neither.

The bridge stays out because the server IS its approval return channel — it
writes approvals and steering into the child's stdin. Detaching it without a
durable permission transport produces a live agent blocked on a prompt no
restarted server can answer, holding a worktree. That is strictly worse than
today.

So the inventory is a test rather than a comment. The bridge's row asserts its
CURRENT behaviour, with the scope decision as its reason: a boundary marker,
not an xfail. If anyone later detaches the bridge without a durable permission
channel, this file says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "loregarden"


def _source(relative: str) -> str:
    return (SERVER / relative).read_text(encoding="utf-8")


#: (label, module, detaches the child, records a process identity)
SPAWN_SITES = [
    ("agent_spawn.spawn_agent", "agents/executors/agent_spawn.py", True, False),
    ("print_mode.run_print_mode", "agents/executors/print_mode.py", False, True),
    ("permission_bridge", "agents/executors/permission_bridge.py", False, False),
    ("external_harness", "services/external_harness.py", False, False),
]


@pytest.mark.parametrize(("label", "module", "detaches", "records_identity"), SPAWN_SITES)
def test_the_spawn_inventory_is_what_this_ticket_says_it_is(
    label, module, detaches, records_identity
):
    """AC37. One row per site; the reasons are in this module's docstring.

    `agent_spawn` owns the detachment (`start_new_session=True`) and
    `print_mode` owns the identity record, which is why the two rows differ:
    the spawn moved out so `print_mode` keeps only the budget loop and the
    result shaping.

    `external_harness` has no local spawn at all — its agent runs on someone
    else's machine, so there is no pid here to detach or to fingerprint.
    """
    body = _source(module)

    assert ("start_new_session=True" in body) is detaches, label
    assert ("record_process_identity" in body) is records_identity, label


def test_the_permission_bridge_is_unchanged_and_that_is_the_decision():
    """AC37/AC10. Its spawn writes approvals into the child's stdin, so the
    server is its return channel. A detached bridge run would wait forever on a
    prompt nobody can answer. The durable permission transport is a follow-up."""
    body = _source("agents/executors/permission_bridge.py")

    assert "start_new_session" not in body
    assert "run_output_files" not in body
    assert "AgentTransport" not in body


# --- AC36: deliberately unchanged --------------------------------------------

UNTOUCHED_BY_TRANSPORT = [
    "services/terminal_session.py",
    "services/orchestration_recovery.py",
    "services/run_interruption.py",
    "services/cli_agent_runner.py",
    "services/cli_output.py",
    "agents/executors/permission_bridge.py",
    "services/run_detached_stop.py",
]


@pytest.mark.parametrize("module", UNTOUCHED_BY_TRANSPORT)
def test_transport_is_a_per_run_process_detail_and_does_not_spread(module):
    """AC36, so no later stage adds this as "completeness".

    `run_detached_stop` is on the list only because AC17's `pgid == pid` check
    holds on both transports. If it ever does not, per-transport dispatch there
    is the sanctioned remedy and this row is the one to change — never a tmux
    branch in `print_mode` or `agent_spawn`.
    """
    assert "AgentTransport" not in _source(module), module


def test_terminal_session_is_not_reused():
    """AC36/S0, overriding the original description's "watch out for".

    It spawns `$SHELL -l`, pumps for a websocket, and `close()` SIGHUPs a
    process group; its own docstring says it is deliberately not bound to a
    run's lifetime. The only reusable part was `pty.openpty()`, and no pty is
    built here at all — a pty's master fd lives in whichever process opened it,
    which reproduces the PIPE defect this ticket exists to fix.
    """
    for module in ("agents/executors/agent_spawn.py", "services/run_output_files.py"):
        body = _source(module)
        assert "terminal_session" not in body, module
        # "pty" as a substring also matches "empty"; check the real spellings.
        assert "import pty" not in body, module
        assert "openpty" not in body, module


def test_the_print_mode_path_does_not_reach_for_the_pipe_reader():
    """AC6. `SubprocessLineReader.readline` returns None for both "nothing
    ready" and "writer closed", and on an empty read flushes a buffered partial
    as a terminated line. On a regular file that corrupts exactly the
    `stream_event` partials AC7 protects."""
    assert "SubprocessLineReader" not in _source("agents/executors/print_mode.py")
    assert "SubprocessLineReader" not in _source("services/run_resupervise.py")
    assert "RunOutputTail" not in _source("services/subprocess_lines.py")


# --- AC23: exactly three user-facing surfaces --------------------------------

CLIENT = Path(__file__).resolve().parents[2] / "client" / "src"

TRANSPORT_SURFACES = [
    "components/RunLogModal.tsx",
    "components/logs/LaneLogView.tsx",
    "components/RunSteerComposer.tsx",
]

NO_TRANSPORT_SURFACES = [
    "components/RunLedgerPanel.tsx",
    "components/logs/LogLineRow.tsx",
]


@pytest.mark.parametrize("surface", TRANSPORT_SURFACES)
def test_each_named_surface_reads_the_new_payload(surface):
    """AC23. S1 the modal, S2 the lane log, S3 the stop/steer control."""
    path = CLIENT / surface
    assert path.exists(), surface
    body = path.read_text(encoding="utf-8")

    assert "transport" in body or "cancel_requested_at" in body, surface


@pytest.mark.parametrize("surface", NO_TRANSPORT_SURFACES)
def test_no_other_surface_grows_a_transport_field(surface):
    """AC36. Ticket cards, the kanban, hive and the usage panes get nothing:
    transport is a per-run process detail, and AC18's "visible in the run
    record" is satisfied by the run log modal alone."""
    path = CLIENT / surface
    if not path.exists():
        pytest.skip(f"{surface} does not exist in this tree")

    assert "transport" not in path.read_text(encoding="utf-8"), surface


def test_the_log_line_row_gains_no_new_tag_or_css():
    """AC29. `SYS` already exists (1,412 rows) and already maps to the `info`
    variant, so the restart marker needs no new tag, no new CSS, and no change
    to the memoised row that keeps a 1,667-line feed from re-parsing every 2s."""
    body = (CLIENT / "components/logs/LogLineRow.tsx").read_text(encoding="utf-8")

    assert "reattached" not in body
    assert "memo(" in body, "the memo is what makes a 1,667-line feed pollable"
