"""The gate's edges: the fallthrough, a stale reason, and the detector itself.

lg-external-harness-1026, AC11/AC12/AC15. The suites beside this one pin every
premise the spec enumerates, and `test_worktree_gate_premises.py` pins what each
refusal records. These are the cases that survive all of them.

1. **The fallthrough is the destructive step.** AC11 says the refusal is never a
   branch per member, and pins it by parametrizing over `list(KeepReason)` — so
   every member the *test* knows about is covered and the next member somebody
   adds is covered by nobody. A reason the decision site has never heard of is
   the only input that tells `if refusal is not None: keep` apart from a chain
   of `elif`s whose `else` deletes.
2. **A reason from an earlier pass is worse than none.** `unresolved_reason`
   exists to be read later, so a row still describing Monday's condition sends
   an operator after the wrong thing — "land the branch" for a tree whose actual
   problem is that nobody could stat it.
3. **The sweep test is order-dependent.** "One unreadable tree does not end the
   sweep" asserts the rest of the pass still ran with the bad row inserted
   first, which is the one arrangement an aborting loop fails. The position is
   incidental to the fixture and decisive for the assertion, so both are run —
   and the seam's own raise (`DirectoryReleaseFailed`) gets the same treatment,
   because a catch placed around the loop rather than inside it passes every
   single-row test.
4. **The chokepoint detector cannot see an alias.** The existing static test
   matches call *names* in the AST, so `import release_directory as _rd`
   followed by `_rd(...)` is invisible to it — and a static test that cannot
   fail reads as proof. So the detector here follows import aliases, and it has
   its own test that it fires (this repo has shipped a mutation control that
   silently did not).
"""

from __future__ import annotations

import ast
import enum
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    ProcessState,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import process_identity, worktree_lifecycle, worktree_service
from loregarden.services.directory_release import (
    DirectoryReleaseFailed,
    DirectoryState,
    ReleaseDisposition,
)
from loregarden.services.process_identity import IdentityReading
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import (
    KeepReason,
    RemovalFailure,
    reconcile_worktrees,
    retire_worktree,
)
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo, make_run
from tests.worktree_helpers import make_ticket_on_id_branch as _ticket

SERVER = Path(__file__).resolve().parents[1] / "loregarden"

#: A pid this suite never runs under, with a birth stamp no `ps` would print.
#: Same reasoning as `test_worktree_gate_premises.py`: the claim must name
#: ANOTHER process, because the process that cuts a tree is the one that
#: retires it and a self-claim is deliberately not a live claimant.
_FOREIGN_PID = 99999
_FOREIGN_BIRTH = "Mon Oct 20 11:02:03 2026"
_BOOT_UNDER_TEST = "boot-under-test"


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


def _row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


def _service(session, workspace):
    return worktree_lifecycle._service_for(session, workspace.id)


def _landed(session, workspace, external_id="LG-1"):
    """A finished ticket whose tree has a real receipt and clears every premise:
    the input for which the gate's next step is the destructive one."""
    ticket = _ticket(session, workspace, external_id)
    path = _tree_for(session, workspace, ticket)
    # A file per ticket (lg-external-harness-1026 implement, AC18): the receipt
    # each tree earns lands its work in `main`, so a second tree committing the
    # same path with the same contents is "nothing to commit" and the FIXTURE
    # fails rather than the assertion.
    (path / f"{external_id.lower()}.txt").write_text(f"real work in {external_id}\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    grant_receipt(session, ticket, Path(workspace.repo_path))
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    return ticket, path


# ---------------------------------------------------------------------------
# AC11 — the fallthrough is the destructive step
# ---------------------------------------------------------------------------


def test_a_reason_the_decision_site_has_never_seen_still_keeps_the_tree(session, workspace):
    """The shape of the code, asserted through its behaviour.

    Two spellings satisfy every parametrized-over-`list(KeepReason)` test:

        if refusal is not None: keep          # the fallthrough is the keep
        if refusal is NO_RECEIPT: keep
        elif refusal is UNREADABLE: keep
        ...
        else: remove                          # the fallthrough is the delete

    The second is one forgotten `elif` away from deleting a tree somebody meant
    to keep, and no test written against today's members can tell them apart.
    An unknown member can: under the first spelling it is kept, under the second
    it falls through and the checkout is gone.

    A raise is an acceptable answer here (it keeps the tree, and `retire_worktree`
    is reached from a sweep that AC11 requires to survive one). Deleting is not.
    """
    ticket, path = _landed(session, workspace)
    unknown = enum.Enum("LaterKeepReason", {"SOMETHING_NOBODY_HAS_WRITTEN_YET": "a later premise"})

    with mock.patch.object(
        worktree_lifecycle,
        "_keep_reason",
        return_value=unknown.SOMETHING_NOBODY_HAS_WRITTEN_YET,
    ):
        try:
            outcome = retire_worktree(session, _service(session, workspace), _row(session, ticket))
        except Exception:
            outcome = None

    assert path.is_dir(), "an unrecognised premise fell through to the removal"
    if outcome is not None:
        assert outcome.refusal is not None
        assert outcome.deleted_nothing is True


# ---------------------------------------------------------------------------
# AC11/AC14 — the evidence columns describe this pass, not an earlier one
# ---------------------------------------------------------------------------


def test_a_second_refusal_replaces_the_first_reason_rather_than_keeping_it(session, workspace):
    """Two passes, two different reasons, one column. The column has to mean
    "why this tree is still here", and a reason from an earlier condition sends
    an operator after the wrong thing — the `land the branch` advice for a tree
    whose actual problem is that nobody can stat it."""
    ticket, path = _landed(session, workspace)

    with mock.patch.object(
        worktree_lifecycle, "directory_state", side_effect=PermissionError(13, "Permission denied")
    ):
        retire_worktree(session, _service(session, workspace), _row(session, ticket))
    first = _row(session, ticket).unresolved_reason

    ticket.landed_sha = ""
    ticket.landed_branch = ""
    session.add(ticket)
    session.commit()
    second_outcome = retire_worktree(session, _service(session, workspace), _row(session, ticket))

    assert second_outcome.refusal is KeepReason.NO_RECEIPT
    second = _row(session, ticket).unresolved_reason
    assert second and second != first, (
        f"the row still describes the earlier condition: {second!r} after {first!r}"
    )
    assert first not in second, "the reasons accumulated instead of being replaced"
    assert path.is_dir()


# ---------------------------------------------------------------------------
# AC11 — the sweep survives the raise wherever it falls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position", ["first", "last"])
def test_the_sweep_visits_every_other_tree_whatever_position_the_bad_one_is_in(
    session, workspace, position: str
):
    """Order is incidental to the fixture and decisive for the assertion.

    A loop that aborts on the raise still settles everything *before* the
    unreadable row, so a one-bad-row-first fixture is the only arrangement that
    catches it — and that arrangement is an artefact of insertion order, which
    nothing in `reconcile_worktrees` promises. Both positions, so the test
    cannot be passed by luck.
    """
    names = ["LG-A", "LG-B", "LG-C"]
    trees = {}
    for name in names:
        ticket, path = _landed(session, workspace, external_id=name)
        trees[name] = (ticket, path)

    broken = "LG-A" if position == "first" else "LG-C"
    broken_path = trees[broken][1]
    real_state = worktree_lifecycle.directory_state

    def refuse_one(path, *args, **kwargs):
        if str(path) == str(broken_path):
            raise PermissionError(13, "Permission denied")
        return real_state(path, *args, **kwargs)

    with mock.patch.object(worktree_lifecycle, "directory_state", side_effect=refuse_one):
        settled = reconcile_worktrees(session)

    assert settled == 2, f"the sweep stopped early with the bad row {position}"
    assert broken_path.is_dir()
    for name in names:
        if name == broken:
            continue
        assert not trees[name][1].exists(), f"{name} was never visited"


def test_a_release_failure_mid_sweep_does_not_end_the_sweep_either(session, workspace):
    """`DirectoryReleaseFailed` is the seam's answer (AC14) and it is a raise, so
    it has the same power to end a pass as the stat does. `retire_worktree` is
    the one place that catches it — which only helps if the catch is inside the
    loop's per-row scope rather than around the loop.
    """
    first, first_path = _landed(session, workspace, external_id="LG-FAILS")
    second, second_path = _landed(session, workspace, external_id="LG-GOES")
    real_remove = WorktreeService.remove_checkout

    def fail_the_first(self, worktree):
        if worktree.worktree_path == str(first_path):
            raise DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(first_path),
                parked_path=f"{first_path}.parked-deadbeef",
            )
        return real_remove(self, worktree)

    with mock.patch.object(WorktreeService, "remove_checkout", fail_the_first):
        reconcile_worktrees(session)

    assert not second_path.exists(), "the second tree was never visited"
    assert _row(session, first).parked_at, "the failed row recorded no park path"
    assert _row(session, second).state == WorktreeState.CLEANUP.value


# ---------------------------------------------------------------------------
# AC15 — the detector, and a test that the detector fires
# ---------------------------------------------------------------------------


def _destructive_calls(source: str, names: set[str]) -> set[str]:
    """Which of `names` this source reaches, following import aliases.

    Name-matching the call site is not enough: `import ... as _release` renames
    the function at the import and the call site then says nothing about what it
    reaches. Attribute access (`service.remove_checkout(...)`) and plain calls
    are both counted, and so is a module alias
    (`from loregarden.services import directory_release as dr; dr.release_directory(...)`).
    """
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in names:
                    aliases[imported.asname or imported.name] = imported.name
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if called in names:
            found.add(called)
        elif called in aliases:
            found.add(aliases[called])
    return found


def test_the_detector_sees_the_evasions_a_name_match_misses():
    """The control for the test below it.

    A static assertion that cannot fail is indistinguishable from one that
    passes, and this repo has shipped one before (a router that bound a name at
    import, so the swap under test never took effect). So the detector is
    exercised against the three spellings it has to catch and the one it must
    not flag, before it is trusted to say a module is clean.
    """
    names = {"remove_checkout", "release_directory"}

    assert _destructive_calls("release_directory(p, inside=b, park_root=r)", names) == {
        "release_directory"
    }
    assert _destructive_calls("service.remove_checkout(row)", names) == {"remove_checkout"}
    assert _destructive_calls(
        "from loregarden.services.directory_release import release_directory as _rd\n"
        "_rd(p, inside=b, park_root=r)\n",
        names,
    ) == {"release_directory"}
    assert _destructive_calls("release_elsewhere(p)\nrow.remove_branch()\n", names) == set()


@pytest.mark.parametrize("name", ["remove_checkout", "release_directory"])
def test_no_module_reaches_the_destructive_half_under_another_name(name: str):
    """AC15, with the aliases closed.

    The gate is a chokepoint only if every route to the destructive half goes
    through it, and an alias is a route. `worktree_service` and
    `directory_release` are where the two functions live; `worktree_lifecycle`
    is the gate.
    """
    allowed = {"worktree_lifecycle.py", "worktree_service.py", "directory_release.py"}
    offenders = [
        str(module.relative_to(SERVER))
        for module in SERVER.rglob("*.py")
        if module.name not in allowed
        and name in _destructive_calls(module.read_text(encoding="utf-8"), {name})
    ]

    assert offenders == [], f"{name} is reached outside the gate from: {offenders}"


# ---------------------------------------------------------------------------
# AC11 — the SECOND stat, on the removal path, where no handler was waiting
# ---------------------------------------------------------------------------


def _refuse_only(broken_path: Path):
    """A `directory_state` that raises EACCES for one path and answers for the rest.

    The real function for every other path, so the fixture cannot pass by
    disabling the stat everywhere — the other trees in the sweep still have to
    be stat'ed, released and settled for the assertions below to mean anything.
    """
    real_state = worktree_service.directory_state

    def answer(path, *args, **kwargs):
        if str(path) == str(broken_path):
            raise PermissionError(13, "Permission denied", str(broken_path))
        return real_state(path, *args, **kwargs)

    return answer


def test_a_stat_the_kernel_refuses_on_the_REMOVAL_path_is_a_refusal_not_a_raise(session, workspace):
    """AC11 converts a `directory_state` raise at the decision site. There are TWO.

    `_premise_or_uncertainty` wraps `_keep_reason`, which stats the path once to
    ask whether there is a checkout at all. Once every premise holds, the
    removal path stats it AGAIN — inside `remove_checkout`, before the release —
    and that second reading is the one with no handler around it: `_remove`
    catches `DirectoryReleaseFailed` only, so a bare `PermissionError`, `EIO` or
    `ENAMETOOLONG` from the second stat escapes `retire_worktree` entirely.

    Both stats are the same uncertainty about the same path, so both must answer
    the same way: UNREADABLE, nothing deleted, recorded on the row. A raise out
    of `retire_worktree` is not that answer — it is the seam's safety bar
    becoming the thing that ends the pass (see the sweep test below), and it is
    what `test_a_second_refusal_replaces_the_first_reason_rather_than_keeping_it`
    above cannot catch, because it patches the decision site's stat.
    """
    ticket, path = _landed(session, workspace)

    with mock.patch.object(worktree_service, "directory_state", side_effect=_refuse_only(path)):
        outcome = retire_worktree(session, _service(session, workspace), _row(session, ticket))

    assert outcome.refusal is KeepReason.UNREADABLE
    assert outcome.deleted_nothing is True
    assert path.is_dir(), "the checkout the kernel would not describe was released anyway"
    settled = _row(session, ticket)
    assert settled.state == WorktreeState.ACTIVE.value, "ownership was released on an unknown"
    assert settled.unresolved_reason, "the refusal was not recorded on the row"


@pytest.mark.parametrize("position", ["first", "last"])
def test_a_removal_path_stat_raise_mid_sweep_does_not_end_the_sweep_either(
    session, workspace, position: str
):
    """The same arrangement as the decision-site sweep test, one stat later.

    `reconcile_worktrees` guards its own `_on_disk` stat and converts the
    decision site's, and its docstring claims "every refusal is handled INSIDE
    the loop, per row". The removal path's stat is the one that is not: it
    escapes `_settle_one`, escapes the loop, and ends the pass with every
    remaining ACTIVE row unvisited — silently, because an aborted pass and a
    clean pass both finish with no error. Both positions, because a loop that
    aborts on the raise still settles everything inserted before the bad row.
    """
    names = ["LG-A", "LG-B", "LG-C"]
    trees = {name: _landed(session, workspace, external_id=name) for name in names}
    broken = "LG-A" if position == "first" else "LG-C"
    broken_path = trees[broken][1]

    with mock.patch.object(
        worktree_service, "directory_state", side_effect=_refuse_only(broken_path)
    ):
        settled = reconcile_worktrees(session)

    assert settled == 2, f"the sweep stopped early with the bad row {position}"
    assert broken_path.is_dir()
    assert _row(session, trees[broken][0]).unresolved_reason, "the kept row recorded no reason"
    for name in names:
        if name == broken:
            continue
        assert not trees[name][1].exists(), f"{name} was never visited"


# ---------------------------------------------------------------------------
# AC9/AC1 — the absence that licenses a removal is spent on whatever is there
# ---------------------------------------------------------------------------


def _unlanded(session, workspace, external_id="LG-U"):
    """A finished ticket whose tree holds uncommitted work and has NO receipt.

    The input every premise refuses: no landing on record, nothing committed,
    and a file that exists in this checkout and nowhere else. If the gate
    deletes this, it deleted the thing the parent ticket exists to protect.
    """
    ticket = _ticket(session, workspace, external_id)
    path = _tree_for(session, workspace, ticket)
    (path / "unsaved.txt").write_text("1164 lines that are in no commit\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    return ticket, path


def _as_absent():
    """A decision-site `directory_state` that answers ABSENT for every path.

    Not an invention: it is what the real call answers for a checkout that is
    missing *at the moment it is asked*, which is the whole input to the
    short-circuit under test. The removal path reads the filesystem for itself,
    through `worktree_service`'s own import, and that read is left real — so
    the arrangement is exactly "absent when the gate looked, there when the
    release ran", and nothing else is faked.
    """
    return mock.patch.object(
        worktree_lifecycle, "directory_state", return_value=DirectoryState.ABSENT
    )


@pytest.mark.parametrize("entry", ["retire_worktree", "reconcile_worktrees"])
def test_an_absence_the_gate_observed_does_not_license_releasing_what_is_there(
    session, workspace, entry: str
):
    """`_keep_reason` answers "no refusal" for an absent directory, and that
    answer is then spent on a rename-then-delete of whatever the path holds when
    the removal runs.

    This is the check-then-act window AC9 is about, one level up from the one it
    closed. `directory_release`'s own module docstring states the rule it is
    judged by: "It asked `worktree_path.exists()` and then removed, so the
    answer to the question is what licensed the act — and between the question
    and the act anything can happen." The `exists()` moved into
    `directory_state` and up into `_keep_reason`; the window did not close. The
    gate stats, concludes there is nothing to gate, returns `None`, and
    `remove_checkout` then stats for itself, finds a checkout and releases it —
    having consulted no receipt, no claim and no premise, because the absence
    returned ahead of all of them.

    It is reachable without a contrived race. A `worktrees` row stays ACTIVE
    while its checkout is missing (a crash between the row and the `worktree
    add`), `resolve_execution_root` REUSES that row and recreates the checkout
    for the next run — pinned by
    `test_reusing_a_ticket_s_worktree_restamps_the_claim` — and
    `reconcile_worktrees` runs on a timer, not only at boot. So the sweep reads
    absent, a new run cuts the checkout into the same recorded path, and the
    sweep deletes it.

    Both entry points, because the short-circuit is written twice: `_keep_reason`
    returns `None` on absence for `retire_worktree`, and `reconcile_worktrees`
    has its own `_on_disk` arm that skips the premises altogether and goes
    straight to the destructive half.

    The tree here is the worst case on purpose — no receipt, nothing committed,
    an untracked file — so that the assertion is about data loss and not about
    bookkeeping. Every premise would have refused it. None was asked.
    """
    ticket, path = _unlanded(session, workspace)

    with _as_absent():
        if entry == "retire_worktree":
            retire_worktree(session, _service(session, workspace), _row(session, ticket))
        else:
            reconcile_worktrees(session)

    assert path.is_dir(), (
        "the gate released a live checkout holding uncommitted work on the strength of a "
        "stat that said the directory was not there"
    )
    assert (path / "unsaved.txt").read_text().startswith("1164"), (
        "the checkout survived but the work in it did not"
    )


def test_a_live_claimant_s_tree_is_not_released_by_an_absence_the_gate_misread(session, workspace):
    """The same window, read for which premise it skips.

    `CLAIMANT_LIVE` is the one premise whose entire job is "an agent is still
    working in it", and the absence short-circuit returns ahead of it — so the
    guard that exists to protect a live agent's checkout is bypassed by the one
    input it was never asked about. The tree has a receipt and a clean history
    here, so the claim is the ONLY thing standing between it and the rename:
    if the delete still happens, it happened past a live claimant.

    `process_identity.identify` is stubbed for the claiming pid alone, per AC4
    and AC5 — never `subprocess.run` — so `ClaimIdentity.current()` still
    answers honestly and the self-claim arm is exercised rather than bypassed.
    """
    ticket, path = _landed(session, workspace)
    row = _row(session, ticket)
    row.claim_pid = str(_FOREIGN_PID)
    row.claim_birth = _FOREIGN_BIRTH
    row.claim_boot_id = _BOOT_UNDER_TEST
    session.add(row)
    session.commit()

    real_identify = process_identity.identify

    def only_the_claimant(pid: int) -> IdentityReading:
        if pid == _FOREIGN_PID:
            return IdentityReading(ProcessState.ALIVE, _FOREIGN_BIRTH)
        return real_identify(pid)

    with (
        _as_absent(),
        mock.patch.object(process_identity, "identify", side_effect=only_the_claimant),
        mock.patch.object(process_identity, "boot_identity", return_value=_BOOT_UNDER_TEST),
    ):
        retire_worktree(session, _service(session, workspace), _row(session, ticket))

    assert path.is_dir(), "a live claimant's checkout was released; the claim was never read"
    assert (path / "lg-1.txt").is_file(), "the live claimant's work went with the checkout"


# ---------------------------------------------------------------------------
# AC11 — a failure with no clause written for it must not end the pass
# ---------------------------------------------------------------------------

#: A cause the pairing table has no sentence for. `CleanupRefusal` has two
#: members today and `_FAILURE_CLAUSE` has two entries; this stands in for the
#: third somebody adds, without editing the production enum to find out.
_LATER_CAUSE = enum.Enum("LaterCleanupRefusal", {"SOMETHING_ELSE_WENT_WRONG": "a later cause"})


def _release_failure(cause):
    """A seam that fails the way `DirectoryReleaseFailed` permits by construction.

    `refusal` defaults to `None` on that exception — "left for the seam to
    stamp", per its own docstring — so an unstamped raise is representable
    without anybody writing a bug, and a cause nobody wrote a clause for is one
    enum member away.
    """
    failed = DirectoryReleaseFailed(
        "the release failed",
        disposition=ReleaseDisposition.STILL_PARKED,
        uncleaned_path="/w/x",
        parked_path="/p/y",
    )
    failed.refusal = cause
    return failed


@pytest.mark.parametrize(
    "cause",
    [None, _LATER_CAUSE.SOMETHING_ELSE_WENT_WRONG],
    ids=["unstamped", "a-cause-with-no-clause"],
)
def test_a_release_failure_with_no_clause_is_a_refusal_and_never_a_raise(session, workspace, cause):
    """`_remove` reads the failure's cause with `_FAILURE_CLAUSE[failed.refusal]`
    — an unguarded index — so a cause the table has no sentence for raises
    `KeyError` out of `retire_worktree`.

    The table's own comment chooses that deliberately: "a mapping rather than a
    branch per member so a cause added later has no clause and fails loudly
    instead of inheriting a sentence written for the other one." The first half
    is right and the second half is the part that is not survivable here —
    *loudly* on this path means ending the sweep, which AC11 forbids in the
    same breath as it forbids the inherited sentence.

    `_clause_for` already shows the resolution and is three lines away: it reads
    `_REASON_CLAUSE.get(refusal, refusal.value)` and documents exactly this
    reasoning — "the fallthrough in this module is the destructive step, so a
    member nobody wrote a clause for must still answer a refusal rather than
    fall through — it reports its own value, which is thinner than a written
    clause and is not a neighbour's sentence." The removal path needs the same
    fallback and does not have it.

    Both shapes, because they are one defect with two doors: `refusal=None` is
    reachable today (the default on the exception), and a third `CleanupRefusal`
    member is reachable the day somebody adds one.
    """
    ticket, path = _landed(session, workspace)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    service.remove_checkout.side_effect = _release_failure(cause)

    outcome = retire_worktree(session, service, _row(session, ticket))

    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.deleted_nothing is True
    assert outcome.parked_path == "/p/y", "the recovery path the operator needs was dropped"
    assert path.is_dir()


@pytest.mark.parametrize("position", ["first", "last"])
def test_a_clauseless_release_failure_mid_sweep_leaves_no_row_unvisited(
    session, workspace, position: str
):
    """The same unguarded index, read for its real consequence.

    `reconcile_worktrees`'s docstring claims "every refusal is handled INSIDE
    the loop, per row ... any of them escaping would end the pass with every
    remaining worktree unvisited — silently, because an aborted sweep and a
    clean sweep both finish with no error." A `KeyError` from the clause table
    is one of those, and it is raised from `_remove`, which is inside the loop
    and outside every handler in it.

    Both positions: a pass that aborts on the bad row still settles everything
    inserted before it, so the bad row going last proves nothing on its own.
    """
    names = ["LG-A", "LG-B", "LG-C"]
    trees = {name: _landed(session, workspace, external_id=name) for name in names}
    broken = "LG-A" if position == "first" else "LG-C"
    broken_id = _row(session, trees[broken][0]).id

    real_remove = worktree_service.WorktreeService.remove_checkout

    def refuse_one(self, worktree):
        if worktree.id == broken_id:
            raise _release_failure(None)
        return real_remove(self, worktree)

    with mock.patch.object(worktree_service.WorktreeService, "remove_checkout", refuse_one):
        settled = reconcile_worktrees(session)

    assert settled == 2, f"the sweep stopped early with the bad row {position}"
    assert trees[broken][1].is_dir()
    for name in names:
        if name == broken:
            continue
        assert not trees[name][1].exists(), f"{name} was never visited"


# ---------------------------------------------------------------------------
# AC13 — the two vocabularies must stay tellable apart, and `str` is why
# ---------------------------------------------------------------------------


def test_the_two_refusal_vocabularies_never_share_a_value(session, workspace):
    """`worktree_premises` asserts in prose that the two enums are "disjoint,
    which is what lets `Retirement.refusal` carry either and still be read for
    which kind it is". Nothing checks it, and both are `StrEnum`.

    Two `StrEnum` members with the same value compare equal AND hash equal
    ACROSS their enums — measured under `server/.venv` (3.11.15). So a value
    collision does not merely look confusing, it silently rewires three
    lookups: `_clause_for`'s `_REASON_CLAUSE.get(refusal, ...)` hands back the
    other vocabulary's sentence (AC13: "each member's detail is its own,
    repeating no neighbour's"), `_premise_clause`'s `refusal not in
    _RECEIPT_PREMISES` sends a removal failure to `resolve_receipt` for a
    landing detail, and any caller telling a premise from a failure by value
    gets the wrong kind — which decides whether it tells the operator to retry.

    An invariant held by nothing but a docstring is one a value somebody picks
    next quarter breaks with no test going red. `session` and `workspace` are
    unused and present so this reads in the module it guards; the check is pure.
    """
    del session, workspace
    premises = {member.value: member.name for member in KeepReason}
    failures = {member.value: member.name for member in RemovalFailure}
    shared = set(premises) & set(failures)
    assert not shared, (
        "KeepReason and RemovalFailure share a value, so they compare and hash equal: "
        + ", ".join(f"{premises[v]} / {failures[v]} = {v!r}" for v in sorted(shared))
    )
    assert all(member not in KeepReason.__members__.values() for member in RemovalFailure), (
        "a removal failure answers equal to a premise"
    )
