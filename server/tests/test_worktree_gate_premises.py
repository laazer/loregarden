"""What the gate passes to the seam, and what every refusal has to carry.

lg-external-harness-1026, AC7/AC9/AC11/AC12. The sibling modules check the seam
in isolation (`test_directory_release.py` is handed an `inside` and a
`park_root` by a fixture) and the gate in isolation (`test_worktree_lifecycle.py`
checks three refusal members by hand). Between the two is a gap wide enough to
drive the whole atomicity argument through:

* **Nothing asserts where the production call's `park_root` comes from.** Every
  release test receives `repo.parent / ".worktrees-releasing"` from a fixture.
  A gate that passes `tempfile.gettempdir()` instead passes all of them, and
  every real release then answers EXDEV — the cross-device rename AC9 spends a
  sentence forbidding, which is not atomic and is not a claim.
* **Nothing asserts that `inside` is the base.** AC9 replaces a string-prefix
  containment check specifically so that `.worktrees-old/x` is refused against
  `.worktrees`. Passing `repo.parent` as the containment root restores exactly
  the hole the segment-aware check was written to close, and no test sees it.
* **AC12's "every refusal answers `deleted_nothing=True`" and AC11's "and is
  recorded" are each pinned for a handful of members.** A member added later —
  or a branch that forgets one — is the failure mode, so both are parametrized
  over the vocabulary rather than over the examples someone thought of.
* **AC7 says the claim is stamped "wherever a worktree is created or reused".**
  The claim tests cover the ticket path twice and the chat path not at all.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    BaxterChatSession,
    ProcessState,
    TicketState,
    Workspace,
    Worktree,
)
from loregarden.services import process_identity, worktree_lifecycle, worktree_service
from loregarden.services.process_identity import ClaimIdentity, IdentityReading
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import (
    KeepReason,
    RemovalFailure,
    retire_worktree,
)
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo, make_run
from tests.worktree_helpers import make_ticket_on_id_branch as _ticket

#: A pid this test suite never runs under, with a birth stamp that is not a
#: date any `ps` would print: the claim has to name ANOTHER process, because a
#: self-claim is deliberately not a live claimant (the process that cuts a tree
#: is the one that retires it).
_FOREIGN_PID = 99999
_FOREIGN_BIRTH = "Mon Oct 20 11:02:03 2026"
_BOOT_UNDER_TEST = "boot-under-test"

UNCERTAINTY = (
    KeepReason.NO_RECEIPT,
    KeepReason.RECEIPT_DOES_NOT_COVER,
    KeepReason.CLAIM_UNPROVEN,
    KeepReason.UNREADABLE,
)


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


def _tree_for(session, workspace, ticket) -> Path:
    return resolve_execution_root(
        session,
        make_run(session, workspace, ticket, f"run-{ticket.external_id}"),
        ticket,
        workspace,
    )


def _commit_in(path: Path, name: str = "work.txt") -> None:
    (path / name).write_text("real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")


def _active_row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


def _service(session, workspace):
    return worktree_lifecycle._service_for(session, workspace.id)


def _finished(session, ticket):
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    return ticket


def _retirable(session, workspace):
    """A tree with a receipt and nothing else standing in the way."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))
    _finished(session, ticket)
    return ticket, path


# ---------------------------------------------------------------------------
# AC9 — the two paths the gate hands the seam, which only the gate can get wrong
# ---------------------------------------------------------------------------


def test_the_gate_parks_in_the_base_s_own_sibling_on_the_same_device(session, workspace):
    """The atomicity argument, asserted where it is actually decided.

    `os.replace` is a rename only within one filesystem; across two it answers
    EXDEV, and the seam correctly refuses that. But the seam does not *choose*
    the park root — the gate does, and nothing until now read what it chose. A
    park root under `/tmp` (a separate volume on macOS, which is what this
    control plane runs on) would make every real release fail at the rename,
    with the whole suite still green because every release test supplies its
    own.

    So: the sibling under the same parent, and `st_dev` to prove the claim the
    name is only shorthand for.
    """
    ticket, _path = _retirable(session, workspace)
    service = _service(session, workspace)
    seen: dict[str, Path] = {}

    def capture(path, *, inside, park_root):
        seen["path"] = Path(path)
        seen["inside"] = Path(inside)
        seen["park_root"] = Path(park_root)

    with mock.patch.object(worktree_service, "release_directory", side_effect=capture):
        retire_worktree(session, service, _active_row(session, ticket))

    repo_parent = Path(workspace.repo_path).resolve().parent
    assert seen, "the gate never reached the seam"
    assert seen["park_root"].resolve() == (repo_parent / ".worktrees-releasing").resolve()
    assert seen["park_root"].resolve().parent == service.worktree_base.resolve().parent, (
        "the park root must be a SIBLING of the base, not somewhere else entirely"
    )

    seen["park_root"].mkdir(parents=True, exist_ok=True)
    assert seen["park_root"].stat().st_dev == service.worktree_base.stat().st_dev, (
        "a cross-device park root makes os.replace EXDEV, and EXDEV is not atomic"
    )


def test_the_containment_root_the_gate_passes_is_the_base_and_not_its_parent(session, workspace):
    """A wider `inside` is the string-prefix bug wearing a type.

    AC9 replaces `str(path).startswith(str(self.worktree_base))` with
    `Path.is_relative_to` so that a sibling sharing a prefix —
    `.worktrees-old/x` against a base of `.worktrees` — is refused. Handing
    the seam `repo.parent` as the containment root makes `is_relative_to`
    answer True for `.worktrees-old/x`, for the repository checkout itself,
    and for the park root: the segment-aware check is intact and pointed at
    the wrong thing.
    """
    ticket, _path = _retirable(session, workspace)
    service = _service(session, workspace)
    seen: dict[str, Path] = {}

    def capture(path, *, inside, park_root):
        seen["inside"] = Path(inside)
        del path, park_root

    with mock.patch.object(worktree_service, "release_directory", side_effect=capture):
        retire_worktree(session, service, _active_row(session, ticket))

    assert seen["inside"].resolve() == service.worktree_base.resolve()
    sibling = service.worktree_base.resolve().parent / ".worktrees-old" / "x"
    assert not sibling.is_relative_to(seen["inside"].resolve()), (
        "the containment root is wide enough to accept a prefix-sharing sibling"
    )
    assert not Path(workspace.repo_path).resolve().is_relative_to(seen["inside"].resolve()), (
        "the containment root is wide enough to accept the repository checkout itself"
    )


# ---------------------------------------------------------------------------
# AC12 / AC11 — over the vocabulary, not over the examples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "refusal", list(KeepReason) + list(RemovalFailure), ids=lambda r: f"{type(r).__name__}.{r.name}"
)
def test_every_refusal_in_either_vocabulary_answers_deleted_nothing(session, workspace, refusal):
    """`deleted_nothing` is the field a caller reads to decide whether to tell
    the operator their work is gone. AC12 says every refusal sets it, and the
    existing suite pins four members by hand — so a fifth, or a sixth added
    next quarter, inherits whatever the fallthrough does.
    """
    ticket = _finished(session, _ticket(session, workspace))
    _tree_for(session, workspace, ticket)
    row = _active_row(session, ticket)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=refusal):
        outcome = retire_worktree(session, service, row)

    assert outcome.refusal is refusal
    assert outcome.deleted_nothing is True, f"{refusal.name} did not say it deleted nothing"


@pytest.mark.parametrize("reason", UNCERTAINTY, ids=lambda r: r.name)
def test_every_uncertainty_premise_records_itself_on_the_row(session, workspace, reason):
    """ "Evidence records uncertainty; it never releases ownership" — both halves.

    A refusal nobody can see afterwards is a tree that sits on disk for a month
    with no answer to "why is this still here?". The row is the only place that
    answer survives the sweep that produced it, and AC11 names three injections
    that must write it. The vocabulary is what the operator meets, so the
    vocabulary is what is covered.
    """
    ticket = _finished(session, _ticket(session, workspace))
    _tree_for(session, workspace, ticket)
    row = _active_row(session, ticket)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=reason):
        retire_worktree(session, service, row)

    recorded = _active_row(session, ticket).unresolved_reason
    assert recorded, f"{reason.name} left the row saying nothing about why the tree is still here"


def test_a_refusal_that_resolves_clears_the_reason_it_left_behind(session, workspace):
    """A stale reason is worse than none: it describes a question that has
    since been answered, and the operator reading it acts on the old one.

    The sequence is the ordinary one — a tree is refused for want of a receipt,
    the ticket then lands, and the next sweep removes it. Nothing may be left
    on the row asserting the opposite.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    _finished(session, ticket)

    refused = retire_worktree(session, _service(session, workspace), _active_row(session, ticket))
    assert refused.refusal is KeepReason.NO_RECEIPT
    assert _active_row(session, ticket).unresolved_reason

    grant_receipt(session, ticket, Path(workspace.repo_path))
    removed = retire_worktree(session, _service(session, workspace), _active_row(session, ticket))

    assert removed.refusal is None
    assert not path.exists()
    settled = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    assert settled.unresolved_reason == "", (
        "the row still carries the reason it was kept, on a tree that is gone"
    )
    assert settled.parked_at == ""


# ---------------------------------------------------------------------------
# AC7 — the claim is stamped wherever a tree is cut, not only on the ticket path
# ---------------------------------------------------------------------------


def test_a_chat_thread_s_tree_is_stamped_with_the_claim_that_cut_it(session, workspace, repo):
    """ "Wherever a worktree is created or reused" includes the chat rail.

    An unstamped chat tree reads as "no claimant" by AC7's deliberate
    fail-open, which is the right default for rows that predate the migration
    and the wrong one for a tree a live agent is writing to right now. The
    fail-open is bounded by the receipt, not by the claim, so an unstamped
    chat tree whose branch happens to have landed is removable while its
    thread is mid-turn.
    """
    chat = BaxterChatSession(workspace_id=workspace.id, title="Rename the pane editor")
    session.add(chat)
    session.commit()
    session.refresh(chat)
    service = WorktreeService(session, str(repo))

    row = service.get_or_create_for_chat_session(
        chat, make_run(session, workspace, None, "chat-1").id
    )

    assert row is not None
    mine = ClaimIdentity.current()
    assert row.claim_pid == str(mine.pid)
    assert row.claim_birth == mine.birth
    assert row.claim_boot_id == mine.boot_id

    reused = service.get_or_create_for_chat_session(
        chat, make_run(session, workspace, None, "chat-2").id
    )
    assert reused is not None and reused.id == row.id
    assert reused.claim_pid == str(mine.pid), "reuse re-stamps the claim; it does not inherit it"
    assert reused.claim_boot_id == mine.boot_id


# ---------------------------------------------------------------------------
# AC5/AC11 — the claim has THREE answers, and only one of them licenses a delete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reading", "expected_refusal"),
    [
        (IdentityReading(ProcessState.ALIVE, _FOREIGN_BIRTH), KeepReason.CLAIMANT_LIVE),
        (IdentityReading(ProcessState.UNKNOWN), KeepReason.CLAIM_UNPROVEN),
        (IdentityReading(ProcessState.GONE), None),
    ],
    ids=["alive", "unknown", "gone"],
)
def test_the_claim_s_three_states_are_read_and_only_gone_releases_the_tree(
    session, workspace, reading, expected_refusal
):
    """`ProcessState` has three members and the gate must answer all three.

    `liveness` answers UNKNOWN when `ps` could not answer at all — a timeout on
    a loaded box, a `ps` that would not run — and its own docstring says "every
    caller decides for itself what an unanswered question means; none may read
    it as GONE". A gate that tests only `is ProcessState.ALIVE` is reading it as
    GONE by omission: UNKNOWN is neither ALIVE nor `None`, so it falls through
    every premise about the claim and, with a receipt on record and a clean
    tree, reaches the rename-then-delete. That is a checkout a live process may
    still be writing to, released because the machine was too busy to say so.

    AC11 names this answer: uncertainty keeps ownership, as CLAIM_UNPROVEN, and
    is recorded on the row. The parametrization is over the whole vocabulary
    rather than over the two states somebody thought of, because the member
    that goes unread is always the one nobody wrote a case for.

    The seam stubbed is `process_identity.identify` — the one `ps` reading —
    and only for the claiming pid, so `ClaimIdentity.current()` still answers
    honestly and the self-claim arm of `_claim_state` is exercised rather than
    bypassed. `boot_identity` is stubbed per AC4; `subprocess.run` never is.
    """
    ticket, path = _retirable(session, workspace)
    row = _active_row(session, ticket)
    row.claim_pid = str(_FOREIGN_PID)
    row.claim_birth = _FOREIGN_BIRTH
    row.claim_boot_id = _BOOT_UNDER_TEST
    session.add(row)
    session.commit()

    real_identify = process_identity.identify

    def only_the_claimant(pid: int) -> IdentityReading:
        return reading if pid == _FOREIGN_PID else real_identify(pid)

    with (
        mock.patch.object(process_identity, "identify", side_effect=only_the_claimant),
        mock.patch.object(process_identity, "boot_identity", return_value=_BOOT_UNDER_TEST),
    ):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is expected_refusal
    settled = _active_row(session, ticket)
    if expected_refusal is None:
        assert not path.exists(), "a claim nothing holds is the one state that releases the tree"
        return
    assert path.is_dir(), (
        f"the checkout of a tree whose claimant read {reading.state.value!r} was DELETED"
    )
    assert outcome.deleted_nothing is True
    assert settled.unresolved_reason, "the refusal was not recorded on the row"
