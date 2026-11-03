"""A pid the control plane can trust, and a process it cannot signal by accident.

317 lets an agent run outlive the server that spawned it. The moment that is
true, "is pid 4821 alive?" stops being a useful question: pids are recycled, and
on a busy machine the number that named an agent an hour ago may name a shell, a
test runner, or nothing.

The two directions are not symmetric. A wrong "orphaned" costs one re-run. A
wrong "still mine" means the control plane adopts, reports on, and eventually
signals a process it does not own — so everything here fails toward orphaned.

A third asymmetry is about these tests rather than the code. A property about
what `ps` said can only be checked on a reading that came back, so every probe of
a real pid goes through `tests.process_probe`, which asks again once and skips
rather than failing when `ps` still will not answer (lg-run-durability-679). The
patched tests below are the ones that hold the coverage: they drive `identify`'s
ALIVE, GONE and UNKNOWN branches with no subprocess at all, and cannot skip.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest
from loregarden.models.domain import AgentRun, ProcessState, RunStatus, Ticket, WorkItemType
from loregarden.services.process_identity import (
    IdentityReading,
    identify,
    liveness,
    record_process_identity,
    session_of,
)
from loregarden.services.ticket_service import TicketService
from tests.process_probe import _PS_ATTEMPTS, decided_liveness, decided_reading, stamp_of


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="process identity",
        work_item_type=WorkItemType.MILESTONE,
    )


@pytest.fixture(name="run")
def run_fixture(db_session, ticket) -> AgentRun:
    run = AgentRun(
        run_code="rc_pid",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


# ---- the fingerprint ----------------------------------------------------


def test_a_live_process_has_an_identity():
    """This interpreter is the most convenient live process available."""
    reading = decided_reading(os.getpid())
    assert reading.state is ProcessState.ALIVE
    assert reading.stamp


def test_the_same_process_fingerprints_the_same_twice():
    """Otherwise every comparison would read as pid reuse."""
    assert decided_reading(os.getpid()) == decided_reading(os.getpid())


def test_a_dead_process_has_no_identity():
    """A pid that is nobody answers GONE, not a stale value."""
    assert decided_reading(2147483000) == IdentityReading(ProcessState.GONE)


def test_a_zombie_has_no_identity(zombie_pid):
    """Exited but unreaped still holds the pid, and `ps` still prints its start
    time. A tmux pane its server never reaped read as a live agent forever."""
    assert identify(zombie_pid) == IdentityReading(ProcessState.GONE)


def test_two_processes_do_not_share_a_fingerprint():
    """The property the whole module rests on: start time separates them."""
    child = subprocess.Popen(["sleep", "30"])
    try:
        assert stamp_of(child.pid) != stamp_of(os.getpid())
    finally:
        child.kill()
        child.wait()


def test_a_ps_that_answers_with_a_start_time_is_alive():
    """The ALIVE branch, with no subprocess and no way to skip.

    Every other ALIVE assertion in this module runs a real `ps`, which means it
    can be skipped on a machine too loaded to answer. This one holds the parse:
    a zero exit plus a start time is ALIVE, and the stamp is that line stripped.
    What it deliberately cannot check is the `ps -o lstart=` contract itself —
    that is what the real-`ps` tests are for, and what their skip reports.
    """
    answered = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Ss   Mon Sep  7 11:02:13 2026\n", stderr=""
    )
    with patch("loregarden.services.process_identity.subprocess.run", return_value=answered):
        assert identify(os.getpid()) == IdentityReading(
            ProcessState.ALIVE, "Mon Sep  7 11:02:13 2026"
        )


# ---- the comparison, and which way it fails -----------------------------


def test_a_matching_identity_is_still_running():
    assert decided_liveness(os.getpid(), stamp_of(os.getpid())) is ProcessState.ALIVE


def test_a_reused_pid_is_not_still_running():
    """The case this exists for.

    A live pid whose fingerprint does not match is a *different* process wearing
    a familiar number. Adopting it would be the control plane reporting on, and
    later signalling, something it does not own.
    """
    assert decided_liveness(os.getpid(), "Thu Jan  1 00:00:00 1970") is ProcessState.GONE


def test_a_run_with_no_recorded_identity_is_not_adopted(db_session):
    """Fails closed, deliberately.

    A run from before identity was recorded has a pid nobody can vouch for.
    Falling back to a bare liveness check there is exactly the adoption mistake
    this module prevents, so absence of identity means "do not adopt".

    Not routed through `process_probe`: these three short-circuit in `liveness`
    before any `ps` runs, so there is nothing to retry and a retry would hide
    the short-circuit this test is about.
    """
    assert liveness(os.getpid(), "") is ProcessState.GONE
    assert liveness(os.getpid(), None) is ProcessState.GONE
    assert liveness(None, "anything") is ProcessState.GONE


def test_a_ps_that_cannot_answer_is_neither_alive_nor_gone():
    """`ps` failing is not evidence the process is ours — nor that it is dead.

    lg-run-durability-862: both used to be one `None`, so a slow `ps` at boot
    read a live agent as dead and the reaper failed its run.
    """

    def times_out(args, *rest, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with patch("loregarden.services.process_identity.subprocess.run", times_out):
        assert identify(os.getpid()) == IdentityReading(ProcessState.UNKNOWN)
        assert liveness(os.getpid(), "something") is ProcessState.UNKNOWN


def test_a_ps_that_runs_but_prints_nothing_usable_is_unknown():
    """A zero exit with no stamp is not a process that is gone."""
    empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("loregarden.services.process_identity.subprocess.run", return_value=empty):
        assert identify(os.getpid()).state is ProcessState.UNKNOWN


# ---- the probe the other pid tests are built on -------------------------
#
# A helper that skips every time is green and proves nothing, so its own two
# outcomes are pinned here. Neither of these tests can skip.


def test_an_unanswerable_probe_skips_the_test_rather_than_failing_it():
    """The point of the helper. A `ps` that never answers is not a false
    property — it is a property this run could not check."""

    def times_out(args, *rest, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with (
        patch("loregarden.services.process_identity.subprocess.run", times_out),
        pytest.raises(pytest.skip.Exception) as skipped,
    ):
        decided_reading(os.getpid())

    assert str(os.getpid()) in str(skipped.value)


def test_a_probe_that_answers_returns_the_reading_it_got():
    """The other half, and what makes the test above meaningful: the helper
    skips only when `ps` will not answer, never as a matter of course."""
    answered = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Ss   Mon Sep  7 11:02:13 2026\n", stderr=""
    )
    with patch("loregarden.services.process_identity.subprocess.run", return_value=answered):
        assert decided_reading(os.getpid()) == IdentityReading(
            ProcessState.ALIVE, "Mon Sep  7 11:02:13 2026"
        )
        assert stamp_of(os.getpid()) == "Mon Sep  7 11:02:13 2026"
        assert decided_liveness(os.getpid(), "Mon Sep  7 11:02:13 2026") is ProcessState.ALIVE


def test_a_probe_asks_again_when_the_first_ps_does_not_answer():
    """The retry *is* the helper, and nothing else here would notice its loss.

    Both tests above pass with `_PS_ATTEMPTS = 1` — one skips because `ps` never
    answers, the other returns because it always does. Neither distinguishes
    "asked again" from "asked once", so a helper that does not retry would read
    as green while turning every real-`ps` test into a skip on exactly the
    loaded machine this ticket is about. A skipped suite is not a passing one.
    """
    answered = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Ss   Mon Sep  7 11:02:13 2026\n", stderr=""
    )
    with patch(
        "loregarden.services.process_identity.subprocess.run",
        side_effect=[subprocess.TimeoutExpired(["ps"], 5), answered],
    ) as ps:
        try:
            reading = decided_reading(os.getpid())
        except pytest.skip.Exception as gave_up:
            # A skip is green, so catching it here is what makes this test able
            # to fail at all: with `_PS_ATTEMPTS = 1` the helper skips, and an
            # uncaught skip would report this very test as skipped.
            pytest.fail(
                f"gave up after {ps.call_count} attempt(s) instead of asking again: {gave_up}"
            )

    assert reading == IdentityReading(ProcessState.ALIVE, "Mon Sep  7 11:02:13 2026")
    assert ps.call_count == 2, "the second attempt is the whole point of the helper"


def test_a_liveness_probe_asks_again_when_the_first_ps_does_not_answer():
    """`liveness` runs its own fresh `identify`, so it needs its own retry.

    A reading that came back in setup says nothing about the next `ps`, and that
    is the asymmetry `decided_liveness` exists for rather than reusing
    `decided_reading`.
    """
    answered = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Ss   Mon Sep  7 11:02:13 2026\n", stderr=""
    )
    with patch(
        "loregarden.services.process_identity.subprocess.run",
        side_effect=[subprocess.TimeoutExpired(["ps"], 5), answered],
    ) as ps:
        try:
            state = decided_liveness(os.getpid(), "Mon Sep  7 11:02:13 2026")
        except pytest.skip.Exception as gave_up:
            pytest.fail(
                f"gave up after {ps.call_count} attempt(s) instead of asking again: {gave_up}"
            )

    assert state is ProcessState.ALIVE
    assert ps.call_count == 2


def test_an_unanswerable_liveness_probe_skips_rather_than_returning_unknown():
    """`decided_liveness` must never hand UNKNOWN back to a caller.

    Its callers assert ALIVE or GONE, so an UNKNOWN return would fail their
    assertion — the original defect, relocated into the helper meant to end it.
    Only the skip path keeps that promise, and only on a loaded machine would
    anything else have caught its loss.
    """

    def times_out(args, *rest, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with (
        patch("loregarden.services.process_identity.subprocess.run", times_out),
        pytest.raises(pytest.skip.Exception) as skipped,
    ):
        decided_liveness(os.getpid(), "Mon Sep  7 11:02:13 2026")

    assert str(os.getpid()) in str(skipped.value)
    assert str(_PS_ATTEMPTS) in str(skipped.value), "the reason states how many times it asked"


def test_a_liveness_probe_with_no_recorded_identity_answers_without_running_ps():
    """The short-circuit is not laundered into a skip.

    A run with no stored identity is GONE in `liveness` before any `ps` runs, so
    the helper reports that verdict unchanged rather than retrying nothing twice
    and skipping. This matters because an empty stored identity is the write-side
    hole of this ticket: it must stay visible as GONE, which is what the xfail
    repro in `test_open_defect_repros.py` tracks, rather than disappearing into a
    skip that reads as "this machine was busy".
    """
    with patch("loregarden.services.process_identity.subprocess.run") as ps:
        assert decided_liveness(os.getpid(), "") is ProcessState.GONE
        assert decided_liveness(os.getpid(), None) is ProcessState.GONE
        assert decided_liveness(None, "anything") is ProcessState.GONE

    assert ps.call_count == 0


def test_a_stamp_for_a_pid_that_is_gone_fails_loudly_instead_of_returning_empty():
    """`stamp_of` returning `""` would reproduce the bug it was written to stop.

    `""` reaches `liveness` as a falsy identity, which answers GONE without
    probing, and the test that stored it then asserts ALIVE and fails — the
    2026-09-07 shape, now in the setup line. So a decided-but-GONE reading is a
    failure that names the pid, never a silent empty stamp.
    """
    gone = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with (
        patch("loregarden.services.process_identity.subprocess.run", return_value=gone),
        pytest.raises(AssertionError) as failed,
    ):
        stamp_of(2147483000)

    assert "2147483000" in str(failed.value)


def test_a_stamp_that_ps_could_not_answer_for_skips_instead_of_returning_empty():
    """The other way `stamp_of` could produce `""`: an UNKNOWN reading."""

    def times_out(args, *rest, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with (
        patch("loregarden.services.process_identity.subprocess.run", times_out),
        pytest.raises(pytest.skip.Exception),
    ):
        stamp_of(os.getpid())


# ---- `identify`'s own branches, with no subprocess ----------------------
#
# Routing the real-`ps` tests through the probe means each of them can skip, so
# every branch of `identify` needs one patched test that cannot. ALIVE and the
# zero-exit-with-no-stamp UNKNOWN have one above; these three did not.


def test_a_ps_that_says_nothing_holds_the_pid_is_gone():
    """GONE was asserted only through a real `ps`, in
    `test_a_dead_process_has_no_identity`, which the probe can now skip. `ps -p`
    exits non-zero and prints nothing when no process matches, and that — alone
    — is the evidence for GONE."""
    gone = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with patch("loregarden.services.process_identity.subprocess.run", return_value=gone):
        assert identify(2147483000) == IdentityReading(ProcessState.GONE)


def test_a_ps_that_fails_while_printing_something_is_unknown_not_gone():
    """A non-zero exit *with* output is not "no process matched".

    It is a `ps` that was called wrongly, refused, or truncated — a machine that
    could not answer. Reading it as GONE is the collapse this ticket exists to
    undo, and nothing drove this branch before.
    """
    noisy = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="usage: ps [-AaCcEefhjlMmrSTvwXx]\n", stderr=""
    )
    with patch("loregarden.services.process_identity.subprocess.run", return_value=noisy):
        assert identify(os.getpid()) == IdentityReading(ProcessState.UNKNOWN)


def test_a_ps_that_cannot_be_executed_at_all_is_unknown():
    """The OSError half of the handler. A `ps` that is missing or not executable
    says nothing about the pid, and only `TimeoutExpired` was driven before."""
    with patch(
        "loregarden.services.process_identity.subprocess.run",
        side_effect=OSError("[Errno 2] No such file or directory: 'ps'"),
    ):
        assert identify(os.getpid()) == IdentityReading(ProcessState.UNKNOWN)
        assert liveness(os.getpid(), "something") is ProcessState.UNKNOWN


# ---- recorded on the run ------------------------------------------------


def test_the_run_records_both_the_pid_and_its_identity(db_session, run):
    """A pid stored alone is a number a later process can wear."""
    stamp_of(os.getpid())  # precondition: this host can answer about itself at all
    record_process_identity(run.id, os.getpid())

    db_session.expire_all()
    stored = db_session.get(AgentRun, run.id)
    assert stored.agent_pid == os.getpid()
    if not stored.agent_pid_identity:
        pytest.skip(
            "`ps` answered for the precondition and not for the write: "
            'record_process_identity stores identify(pid).stamp, which is "" on an '
            "UNKNOWN reading — the write-side hole of lg-run-durability-679, covered by "
            "its repro in test_open_defect_repros.py"
        )
    assert decided_liveness(stored.agent_pid, stored.agent_pid_identity) is ProcessState.ALIVE


def test_recording_never_fails_the_run(db_session, run):
    """Bookkeeping must not take down the work it is describing."""
    with patch("loregarden.services.process_identity.Session", side_effect=RuntimeError("db gone")):
        record_process_identity(run.id, os.getpid())  # must not raise


def test_recording_against_a_missing_run_is_a_no_op(db_session):
    record_process_identity("no-such-run", os.getpid())  # must not raise


def test_session_of_a_live_process_is_its_sid():
    """R2.6. Sibling of identify(); does not change identify()'s fingerprint."""
    assert session_of(os.getpid()) == os.getsid(os.getpid())


def test_session_of_a_dead_pid_is_none():
    assert session_of(2147483000) is None


def test_a_detached_child_has_a_different_session_than_the_server():
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert session_of(child.pid) != os.getsid(os.getpid())
        assert session_of(child.pid) == os.getsid(child.pid)
    finally:
        child.kill()
        child.wait()


def test_a_same_session_child_shares_the_server_sid():
    child = subprocess.Popen(["sleep", "30"], start_new_session=False)
    try:
        assert session_of(child.pid) == os.getsid(os.getpid())
    finally:
        child.kill()
        child.wait()


def test_session_of_does_not_change_identify_fingerprint():
    before = identify(os.getpid())
    session_of(os.getpid())
    assert identify(os.getpid()) == before


def test_session_of_timeout_is_none_not_a_guessed_sid():
    """R2.6. Same 5s timeout as identify(); an unanswered ps cannot prove detach."""

    def times_out(args, *rest, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 5))

    with patch("loregarden.services.process_identity.subprocess.run", times_out):
        assert session_of(os.getpid()) is None
    assert identify(os.getpid()).state is ProcessState.ALIVE
