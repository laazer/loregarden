"""The durable claim is a triple, and an unreadable boot stamp raises.

lg-external-harness-1026, R2/R3/R5. `process_identity` already answers "is pid
4821 still the process I recorded?" with a pid plus a start-time fingerprint.
A worktree's claim is durable in a way a run's is not — it survives a reboot,
on a row in the database — so it carries a third component scoped to the boot.

**The rationale, restated from our own mechanism, not copied from the ticket.**
The filed text says "birth is monotonic since boot", and that is false here:
`identify` records `ps -o lstart=`, an absolute wall-clock date at one-second
granularity, chosen because this control plane runs on macOS where `/proc` does
not exist. So the post-reboot pid+birth collision the filed sentence describes
does not arise by that mechanism. What a boot-scoped component defeats in *this*
tree is a clock step — NTP, suspend/resume, a VM restore — making two processes'
`lstart` strings agree, plus a same-second pid reuse. Writing the filed sentence
into the code would ship a comment false about its own function, which is the
failure `lg-machine-resource-854` records.

**And the comparison rule has a direction.** A boot mismatch on its own is never
"the claimant is gone". It defers to `liveness(pid, birth)`, so boot-stamp drift
can only ever keep a tree, never release one. That asymmetry is the test at the
bottom of this module, and it is the property worth having.

Tests stub `process_identity.boot_identity`, never `subprocess.run` — the read
goes through one function precisely so that one function is the seam.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    ProcessState,
    RunStatus,
    Ticket,
    Workspace,
    Worktree,
)
from loregarden.services import process_identity
from loregarden.services.process_identity import (
    BootIdentityUnavailable,
    ClaimIdentity,
    boot_identity,
    claim_fields,
    claim_is_live,
    liveness,
    parse_claim,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session, select
from tests.worktree_helpers import make_repo

DARWIN_BOOTTIME = "{ sec = 1785351732, usec = 726777 } Wed Jul 29 15:02:12 2026\n"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _tree_row(session, workspace, external_id="LG-1") -> Worktree:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id.lower()}",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-{external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    resolve_execution_root(session, run, ticket, workspace)
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


# ---------------------------------------------------------------------------
# AC4 — boot_identity reads the kernel, and raises when it cannot
# ---------------------------------------------------------------------------


def test_the_boot_identity_of_this_machine_is_a_non_empty_stable_string():
    first = boot_identity()
    assert first
    assert first == boot_identity(), "the boot identity must not move while the box is up"


def test_darwin_parses_the_sec_field_alone(monkeypatch):
    """Both the other fields in `kern.boottime` move under clock correction.

    Measured on this box 2026-10-16: `sysctl -n kern.boottime` answers
    `{ sec = 1785351732, usec = 726777 } Wed Jul 29 15:02:12 2026`. The `usec`
    field and the trailing formatted date both shift when the clock is stepped,
    so an identity built from either changes without a reboot — and a changing
    boot identity would read every live agent's tree as claimed by a dead boot.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    with mock.patch.object(
        process_identity.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, DARWIN_BOOTTIME, ""),
    ):
        assert boot_identity() == "1785351732"

    drifted = DARWIN_BOOTTIME.replace("usec = 726777", "usec = 991204").replace(
        "Wed Jul 29 15:02:12 2026", "Wed Jul 29 15:02:13 2026"
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    with mock.patch.object(
        process_identity.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, drifted, ""),
    ):
        assert boot_identity() == "1785351732", "only `sec` may decide the identity"


def test_linux_reads_the_kernel_s_own_boot_id(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    boot_id_file = tmp_path / "boot_id"
    boot_id_file.write_text("3f6a1d2e-77bb-4c0a-9a2f-5b1d9e4c0a11\n")
    monkeypatch.setattr(process_identity, "_LINUX_BOOT_ID_PATH", boot_id_file)

    assert boot_identity() == "3f6a1d2e-77bb-4c0a-9a2f-5b1d9e4c0a11"


@pytest.mark.parametrize(
    "output",
    ["", "   \n", "{ sec = , usec = 0 }\n", "nothing like a boottime\n", "{ sec = abc }\n"],
    ids=["empty", "blank", "no-sec", "garbage", "non-numeric"],
)
def test_an_unreadable_boot_stamp_raises_and_never_defaults(monkeypatch, output: str):
    """The single most important line in this module.

    An unset column reads back as `""`. If `boot_identity()` ever returned `""`
    too, a stamped claim and an unstamped row would compare **equal** — every
    pre-migration checkout would look like it belonged to this boot, which is
    the fail-*closed* direction turned inside out. So there is no default, no
    sentinel, and no empty string: it raises.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    with mock.patch.object(
        process_identity.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, output, ""),
    ):
        with pytest.raises(BootIdentityUnavailable):
            boot_identity()


def test_a_sysctl_that_will_not_run_raises_rather_than_answering(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    for failure in (OSError("no sysctl"), subprocess.TimeoutExpired("sysctl", 5)):
        with mock.patch.object(process_identity.subprocess, "run", side_effect=failure):
            with pytest.raises(BootIdentityUnavailable):
                boot_identity()


def test_a_missing_linux_boot_id_file_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(process_identity, "_LINUX_BOOT_ID_PATH", tmp_path / "absent")

    with pytest.raises(BootIdentityUnavailable):
        boot_identity()


# ---------------------------------------------------------------------------
# AC5 — the durable claim is a triple; current ownership stays a pair
# ---------------------------------------------------------------------------


def test_the_claim_is_a_frozen_triple_that_round_trips_through_three_columns():
    claim = ClaimIdentity(pid=4821, birth="Wed Oct 15 09:11:02 2026", boot_id="1785351732")

    with pytest.raises(Exception):
        claim.pid = 9  # frozen: a claim nobody can edit after the fact

    fields = claim_fields(claim)
    assert set(fields) == {"claim_pid", "claim_birth", "claim_boot_id"}
    assert all(isinstance(value, str) for value in fields.values())
    assert parse_claim(**fields) == claim


def test_current_reads_this_process_and_this_boot():
    import os

    claim = ClaimIdentity.current()

    assert claim.pid == os.getpid()
    assert claim.birth
    assert claim.boot_id == boot_identity()


@pytest.mark.parametrize(
    ("pid", "birth", "boot_id"),
    [("", "", ""), ("4821", "", "1785351732"), ("", "birth", "1785351732"), ("4821", "b", "")],
    ids=["all-empty", "no-birth", "no-pid", "no-boot"],
)
def test_a_partly_written_claim_parses_as_no_claim_at_all(pid, birth, boot_id):
    """Two of the three is not a claim. A half-written row must not be treated
    as one, and must not be treated as *this* boot's either."""
    assert parse_claim(claim_pid=pid, claim_birth=birth, claim_boot_id=boot_id) is None


def test_liveness_is_untouched_and_never_consults_the_boot_stamp():
    """AC5 is explicit: current ownership stays a pair.

    `liveness` decides whether a *running* agent's process is still there, and
    that question does not survive a reboot — nothing it guards does. Adding a
    boot id to it would change what every lock and lease in the tree compares,
    and would make a box that cannot read its own boot stamp unable to answer
    "is this agent alive?" at all. So: same two parameters, and a
    `boot_identity` that raises changes nothing about its answer.
    """
    import inspect
    import os

    assert list(inspect.signature(liveness).parameters) == ["pid", "identity"]

    reading = process_identity.identify(os.getpid())
    with mock.patch.object(
        process_identity, "boot_identity", side_effect=BootIdentityUnavailable("no sysctl")
    ):
        assert liveness(os.getpid(), reading.stamp) is ProcessState.ALIVE
        assert liveness(os.getpid(), "not this process's start time") is ProcessState.GONE


def test_a_matching_boot_and_a_live_pid_is_a_live_claimant():
    claim = ClaimIdentity.current()

    with mock.patch.object(process_identity, "liveness", return_value=ProcessState.ALIVE):
        assert claim_is_live(claim) is ProcessState.ALIVE


def test_a_boot_mismatch_with_a_live_matching_pid_is_still_live(monkeypatch):
    """The pinned asymmetry, half one.

    A clock step or a restored VM can move the boot stamp under a process that
    never died. Reading that as "the claimant is gone" would hand a live
    agent's checkout to the reaper, which is the single worst thing this gate
    could do.
    """
    claim = ClaimIdentity(pid=4821, birth="Wed Oct 15 09:11:02 2026", boot_id="an-older-boot")

    with mock.patch.object(process_identity, "boot_identity", return_value="a-newer-boot"):
        with mock.patch.object(process_identity, "liveness", return_value=ProcessState.ALIVE):
            assert claim_is_live(claim) is ProcessState.ALIVE


def test_a_boot_mismatch_with_a_pid_that_is_gone_is_gone(monkeypatch):
    """The pinned asymmetry, half two. Deferring to `liveness` means deferring
    to it in both directions; the boot component adds nothing of its own."""
    claim = ClaimIdentity(pid=4821, birth="Wed Oct 15 09:11:02 2026", boot_id="an-older-boot")

    with mock.patch.object(process_identity, "boot_identity", return_value="a-newer-boot"):
        with mock.patch.object(process_identity, "liveness", return_value=ProcessState.GONE):
            assert claim_is_live(claim) is ProcessState.GONE


@pytest.mark.parametrize("state", list(ProcessState), ids=lambda s: s.name)
def test_the_boot_component_never_makes_a_tree_more_deletable_than_liveness_alone(state):
    """The invariant underneath both halves above, stated once.

    For every answer `liveness` can give, and for a boot stamp that matches and
    one that does not, `claim_is_live` must never answer GONE where `liveness`
    did not. A boot id may only ever be more conservative. Any future edit that
    tries to make a boot mismatch *mean* something fails here.
    """
    claim = ClaimIdentity(pid=4821, birth="Wed Oct 15 09:11:02 2026", boot_id="recorded-boot")
    for stamp in ("recorded-boot", "some-other-boot"):
        with mock.patch.object(process_identity, "boot_identity", return_value=stamp):
            with mock.patch.object(process_identity, "liveness", return_value=state):
                answer = claim_is_live(claim)
        if state is not ProcessState.GONE:
            assert answer is not ProcessState.GONE, (
                f"a boot stamp of {stamp!r} turned {state.name} into GONE"
            )


def test_an_unreadable_boot_stamp_propagates_out_of_claim_is_live():
    """It is caught at the decision site in `worktree_lifecycle`, never here.

    Swallowing it inside this module means picking a value to return, and every
    value available recreates the empty-string-compares-equal bug the raise
    exists to prevent.
    """
    claim = ClaimIdentity(pid=4821, birth="b", boot_id="recorded-boot")

    with mock.patch.object(
        process_identity, "boot_identity", side_effect=BootIdentityUnavailable("no sysctl")
    ):
        with pytest.raises(BootIdentityUnavailable):
            claim_is_live(claim)


# ---------------------------------------------------------------------------
# AC7 — the claim is stamped, and an absent claim fails open, bounded
# ---------------------------------------------------------------------------


def test_creating_a_worktree_stamps_the_claim_on_the_row(session, workspace):
    row = _tree_row(session, workspace)

    claim = parse_claim(
        claim_pid=row.claim_pid, claim_birth=row.claim_birth, claim_boot_id=row.claim_boot_id
    )
    assert claim is not None, "a freshly cut worktree carries a claim"
    assert claim == ClaimIdentity.current()


def test_reusing_a_ticket_s_worktree_restamps_the_claim(session, workspace):
    """A ticket's stages share one tree across processes. The claim names the
    process using it *now*, or the next sweep reads a finished stage's pid."""
    row = _tree_row(session, workspace)
    row.claim_pid = "999999"
    row.claim_birth = "Mon Jan  1 00:00:00 2001"
    row.claim_boot_id = "a-boot-long-gone"
    session.add(row)
    session.commit()

    ticket = session.get(Ticket, row.ticket_id)
    run = AgentRun(
        run_code="run-second-stage",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="verifier",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    resolve_execution_root(session, run, ticket, workspace)
    session.refresh(row)

    assert (
        parse_claim(
            claim_pid=row.claim_pid, claim_birth=row.claim_birth, claim_boot_id=row.claim_boot_id
        )
        == ClaimIdentity.current()
    )


def test_an_empty_claim_reads_as_no_claimant_and_not_as_uncertainty(session, workspace):
    """The one deliberate fail-open in this change (plan D-simplest-2).

    Every `worktrees` row written before the migration carries no claim. Reading
    that as "unresolved" would make every checkout existing at deploy time
    permanently unretirable, which is a worse outage than the one being fixed.
    So an empty claim is "nobody claims this" — and the bound on that is the
    test immediately below.
    """
    row = _tree_row(session, workspace)
    row.claim_pid = ""
    row.claim_birth = ""
    row.claim_boot_id = ""
    session.add(row)
    session.commit()

    assert (
        parse_claim(
            claim_pid=row.claim_pid, claim_birth=row.claim_birth, claim_boot_id=row.claim_boot_id
        )
        is None
    )
