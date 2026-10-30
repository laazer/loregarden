"""The claim's edges: a platform nobody read, and a column somebody corrupted.

lg-external-harness-1026, AC4/AC5/AC7/AC11. `test_worktree_claim_identity.py`
pins the two platforms the ticket names, the asymmetry of the comparison, and
the raise on an unreadable stamp. What it does not reach:

1. **A third platform.** AC4 names linux and darwin. A box that is neither
   reaches whatever the function does *after* those two branches, and the one
   answer forbidden there is `""` — because an unset column reads back as `""`
   and the two would compare equal, which is the whole reason this function
   raises instead of defaulting.
2. **A boot id file that is there and says nothing.** Only the *missing* linux
   file is pinned. An empty `/proc/sys/kernel/random/boot_id`, or one holding a
   newline, takes the "I read it successfully" path and hands `""` back.
3. **A read that fails for a reason other than absence.** AC11 makes
   `BootIdentityUnavailable` the one exception the decision site catches. An
   `OSError` from the file read — EACCES, EIO, a container with `/proc`
   mounted read-restricted — escapes as itself, and the caller that does not
   catch it is `reconcile_worktrees`: the crash-in-a-sweep failure, from the
   claim side rather than the stat side.
4. **`claim_pid` is a TEXT column and nothing validates it.** `int("")` raises
   and so does `int("4821 ")`'s nastier cousins. A parser that raises on a
   corrupt row kills the sweep; a parser that treats corruption as *absence*
   routes a garbled claim into AC7's deliberate fail-open, which was bounded
   for empty rows and nothing else. Neither is acceptable, and the third answer
   — uncertainty, which keeps ownership — is the one AC11 already has a member
   for (`CLAIM_UNPROVEN`).

(4) is the one place this module asserts something the spec does not spell out.
AC7 grants the fail-open to an **empty** claim, naming the reason: every
checkout that existed at deploy time would otherwise be permanently
unretirable. A half-written claim is not that case — it is a row nobody can
read, which AC11 calls uncertainty and requires to keep ownership. Recorded as
a checkpoint rather than assumed silently.
"""

from __future__ import annotations

import errno
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
)
from loregarden.services import process_identity, worktree_lifecycle
from loregarden.services.process_identity import (
    BootIdentityUnavailable,
    boot_identity,
    parse_claim,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import KeepReason, retire_worktree
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo


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


# ---------------------------------------------------------------------------
# AC4 — there is no platform on which this function returns a default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["win32", "freebsd13", "emscripten", ""])
def test_a_platform_the_function_was_never_written_for_raises(monkeypatch, platform: str):
    """AC4 names two kernels. The answer on a third must be the raise.

    Falling off the end of two `if`s returns `None` in Python and `""` in the
    version somebody writes to satisfy the `-> str` annotation, and `""` is the
    one value AC4 forbids by name: it compares equal to an unstamped column, so
    every pre-migration row would read as claimed by *this* boot.
    """
    monkeypatch.setattr(sys, "platform", platform)

    with pytest.raises(BootIdentityUnavailable):
        boot_identity()


@pytest.mark.parametrize("contents", ["", "\n", "   \n\n"], ids=["empty", "newline", "blank"])
def test_a_linux_boot_id_file_that_says_nothing_raises(monkeypatch, tmp_path: Path, contents: str):
    """The file is there, the read succeeds, and the kernel told you nothing.

    Only the *missing* file is pinned elsewhere, and missing is the easy half:
    it raises on its own. An empty file takes the success path, and a stripped
    empty string is still an empty string.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    boot_id_file = tmp_path / "boot_id"
    boot_id_file.write_text(contents)
    monkeypatch.setattr(process_identity, "_LINUX_BOOT_ID_PATH", boot_id_file)

    with pytest.raises(BootIdentityUnavailable):
        boot_identity()


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError(errno.EACCES, "Permission denied"),
        OSError(errno.EIO, "Input/output error"),
        IsADirectoryError(errno.EISDIR, "Is a directory"),
    ],
    ids=["eacces", "eio", "eisdir"],
)
def test_a_boot_id_read_that_fails_for_any_reason_raises_the_one_catchable_error(
    monkeypatch, tmp_path: Path, failure: OSError
):
    """AC11 gives the decision site exactly one exception to catch from here.

    `BootIdentityUnavailable` is what `worktree_lifecycle` converts to
    `CLAIM_UNPROVEN`. Anything else — an EACCES on `/proc` inside a hardened
    container, an EIO off a failing read — escapes `retire_worktree` as itself
    and ends `reconcile_worktrees`, so every tree after this one goes unvisited.
    A missing-file case alone does not pin this, because `FileNotFoundError` is
    the one `OSError` the obvious implementation remembers.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(process_identity, "_LINUX_BOOT_ID_PATH", tmp_path / "boot_id")

    with mock.patch("pathlib.Path.read_text", side_effect=failure):
        with pytest.raises(BootIdentityUnavailable):
            boot_identity()


# ---------------------------------------------------------------------------
# AC5/AC11 — a claim column holds text, and text can be anything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pid",
    ["abc", "4821abc", "0x12d5", "4821\n9", "-4821", "0", " ", "9" * 30],
    ids=["letters", "mixed", "hex", "two-lines", "negative", "zero", "space", "enormous"],
)
def test_a_corrupt_pid_column_never_raises_out_of_the_parser(pid: str):
    """The parser is read inside a sweep, so it does not get to raise.

    `int("abc")` is a `ValueError`, which is neither `BootIdentityUnavailable`
    nor `OSError`, so the decision site's two handlers (AC11) do not catch it
    and the pass ends on the first garbled row. A row the parser cannot read is
    a row nobody can prove ownership of; what it must not be is an exception
    nobody expected.
    """
    answer = parse_claim(claim_pid=pid, claim_birth="Wed Oct 15 09:11:02 2026", claim_boot_id="b")

    assert answer is None or answer.pid > 0, f"{pid!r} parsed into a claim: {answer!r}"


def test_a_claim_nobody_can_read_is_uncertainty_and_not_an_absence(session, workspace):
    """Where (4) lands at the decision site, and why it is not AC7's fail-open.

    AC7's fail-open is for an **empty** claim, and it says why: every checkout
    that existed when the migration ran would otherwise be unretirable forever.
    A half-written claim is a different row — three columns, one of them
    garbage, written by something that got part way. Reading it as "no
    claimant" hands it the fail-open that was granted to the empty case, and
    the tree of a live agent whose pid column got mangled is then removed on
    the strength of a receipt the agent has since moved past.

    `CLAIM_UNPROVEN` is the member AC11 already has for this, so the refusal
    costs nothing to spell: the tree stays, the row records why, and the next
    sweep asks again.
    """
    ticket = Ticket(
        external_id="LG-CORRUPT",
        workspace_id=workspace.id,
        title="Add the thing",
        branch="loregarden/lg-corrupt",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code="run-corrupt",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = resolve_execution_root(session, run, ticket, workspace)
    (path / "work.txt").write_text("real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    grant_receipt(session, ticket, Path(workspace.repo_path))
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    row.claim_pid = "not-a-pid"
    session.add(row)
    session.commit()

    outcome = retire_worktree(session, worktree_lifecycle._service_for(session, workspace.id), row)

    assert outcome.refusal is KeepReason.CLAIM_UNPROVEN, (
        "a claim nobody can read was treated as a claim nobody made"
    )
    assert outcome.deleted_nothing is True
    assert path.is_dir()
    assert (
        session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id))
        .one()
        .unresolved_reason
    )
