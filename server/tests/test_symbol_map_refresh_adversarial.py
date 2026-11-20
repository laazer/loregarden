"""The incremental refresh, attacked rather than demonstrated.

Ticket lg-code-knowledge-284, test-break stage. `test_symbol_map_refresh.py`
proves the writer does what the twelve criteria say. This module tries to
make it do something else: hostile paths, an ignored tree, a clock in the
wrong zone, a crash in each of the three windows a refresh has, a reader
standing in the middle of a write, and the same edits applied in a different
order.

**The one oracle everything here leans on.** An incremental writer has a
failure mode no count and no status can see: it produces a map that is
*almost* right and stamps it with a sha that says it is exactly right. The
only thing that can catch that is a comparison against what a clean full
build of the same tree produces, so `_assert_matches_a_full_build` is used
wherever a mutation matrix is applied. Counts are a weaker check and are
asserted only where the count is itself the contract.

**The three windows.** A refresh stamps `refresh_started_at`, analyses
outside any transaction, and then writes in one short transaction. A failure
in window one or two must leave no stamp behind, because a stamp nothing
clears pins every later read at BUILDING forever — a map that is
permanently, silently untrustworthy while every test about a *successful*
refresh still passes.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    Freshness,
    ParseStatus,
    SymbolMapFile,
    SymbolMapPartition,
    SymbolMapSymbol,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.symbol_map import (
    build_symbol_map,
    definitions_for,
    map_status,
    outline_for,
    refresh_at_run_entry,
    refresh_symbol_map,
)
from loregarden.services.symbol_map import sources as sources_module
from loregarden.services.symbol_map import store as store_module
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.refresh import RefreshFailure
from loregarden.services.symbol_map.sources import ChangedPaths, ChangedPathsOutcome, changed_paths
from loregarden.services.worktree_snapshot import TreeSnapshot, read_tree
from sqlalchemy import event
from sqlmodel import Session, select
from tests.symbol_map_repos import (  # noqa: F401 - fixtures resolved by name
    ALPHA_MODULE,
    BETA_REWRITTEN,
    NEWLINE_PATH,
    QUOTED_PATH,
    SPACED_PATH,
    _commit,
    _git,
    _write,
    ancestor_checkouts_fixture,
    empty_tree_repo_fixture,
    hostile_paths_repo_fixture,
    other_workspace_fixture,
    refresh_repo_fixture,
    same_commit_checkouts_fixture,
    session_fixture,
    unborn_repo_fixture,
    workspace_fixture,
)

#: How long a blocked thread may hold this suite up before it is a failure
#: rather than a slow machine. Generous: this box runs several suites at once.
_JOIN_TIMEOUT_SECONDS = 120


# --------------------------------------------------------------------------
# Readers over stored rows. Deliberate duplicates of the ones in
# `test_symbol_map_refresh.py`, for the reason given there: importing private
# helpers across suites couples their refactors, and these are four lines.
# --------------------------------------------------------------------------


def _partition(session: Session, workspace_id: str, root: Path) -> SymbolMapPartition:
    return session.exec(
        select(SymbolMapPartition).where(
            SymbolMapPartition.workspace_id == workspace_id,
            SymbolMapPartition.checkout_identity == identify(root),
        )
    ).one()


def _file_rows(session: Session, partition_id: str) -> dict[str, SymbolMapFile]:
    rows = session.exec(
        select(SymbolMapFile).where(SymbolMapFile.partition_id == partition_id)
    ).all()
    return {row.path: row for row in rows}


def _symbol_rows(session: Session, partition_id: str) -> list[SymbolMapSymbol]:
    return list(
        session.exec(
            select(SymbolMapSymbol).where(SymbolMapSymbol.partition_id == partition_id)
        ).all()
    )


def _symbol_shape(session: Session, partition_id: str) -> set[tuple[str, str, int, str]]:
    """Every symbol as a comparable value: (path, name, line, parent)."""
    files = {row.id: row.path for row in _file_rows(session, partition_id).values()}
    return {
        (files[row.file_id], row.name, row.line, row.parent)
        for row in _symbol_rows(session, partition_id)
    }


def _file_shape(session: Session, partition_id: str) -> set[tuple[str, ParseStatus]]:
    """Every file row as (path, parse_status) — the half `_symbol_shape` cannot
    see, because a file that yields no symbols contributes nothing to it."""
    return {(path, row.parse_status) for path, row in _file_rows(session, partition_id).items()}


def _assert_matches_a_full_build(
    session: Session, refreshed_workspace_id: str, bystander_id: str, root: Path
) -> None:
    """The refreshed map equals what a clean full build of the same tree holds.

    The strongest statement available about an incremental writer, and the
    only one that catches a map which is almost right. The bystander is a
    second workspace against the SAME checkout, so the comparison is between
    two maps of one tree and not between two trees.
    """
    build_symbol_map(session, bystander_id, root)
    session.expire_all()
    refreshed = _partition(session, refreshed_workspace_id, root)
    expected = _partition(session, bystander_id, root)
    assert _symbol_shape(session, refreshed.id) == _symbol_shape(session, expected.id)
    assert _file_shape(session, refreshed.id) == _file_shape(session, expected.id)
    assert refreshed.head_sha == expected.head_sha


def _assert_no_orphans(session: Session, partition_id: str) -> None:
    """No symbol row outlives the file row it belongs to.

    A per-file delete-and-reinsert has exactly one way to leave rubbish
    behind: deleting the file row and missing its symbols. Those symbols then
    answer `definitions_for` from a file that no longer exists, at line
    numbers nothing will ever correct, and no count notices.
    """
    file_ids = {row.id for row in _file_rows(session, partition_id).values()}
    orphans = [
        row.name for row in _symbol_rows(session, partition_id) if row.file_id not in file_ids
    ]
    assert orphans == [], f"symbol rows outlived their file row: {sorted(orphans)}"


# ==========================================================================
# The delta oracle under hostile input (AC1, AC6, AC9)
# ==========================================================================


def test_paths_git_has_to_quote_survive_the_delta(hostile_paths_repo):
    """A space, a double quote and a NEWLINE in three tracked paths.

    `sources` already documents this trap for `ls-files`: git's default
    output C-quotes anything awkward, so `"pkg/with\\"quote.py"` names a file
    that exists on no filesystem. A newline is worse than a quote — a parser
    splitting on newlines does not merely mangle that path, it reports TWO
    paths, neither of which exists, and records a parse failure under each.
    """
    base = _git(hostile_paths_repo, "rev-parse", "HEAD").stdout.strip()
    for path in (SPACED_PATH, QUOTED_PATH, NEWLINE_PATH):
        _write(hostile_paths_repo, path, "def moved():\n    return 2\n")

    delta = changed_paths(hostile_paths_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert delta.changed == frozenset({SPACED_PATH, QUOTED_PATH, NEWLINE_PATH})


def test_an_ignored_path_is_not_part_of_the_delta(hostile_paths_repo):
    """`.gitignore` is not advisory here.

    The untracked half of the delta is the half that can sweep in a whole
    vendored tree. `vendor/` and `*.generated.py` are this fixture's
    `node_modules` and `.venv`: an untracked scan without `--exclude-standard`
    puts tens of thousands of files into every run entry's analysis pass,
    which is the cold build this ticket exists to avoid, paid on every
    dispatch.
    """
    base = _git(hostile_paths_repo, "rev-parse", "HEAD").stdout.strip()

    delta = changed_paths(hostile_paths_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert "vendor/vendored.py" not in delta.changed
    assert "pkg/thing.generated.py" not in delta.changed


def test_a_nested_checkout_is_not_descended_into(hostile_paths_repo):
    """Git reports a nested repository as one untracked DIRECTORY and refuses
    to look inside. A scan that descends indexes another project's sources
    under this checkout's identity, and `definitions_for` then answers with
    line numbers from a repository the caller is not standing in."""
    base = _git(hostile_paths_repo, "rev-parse", "HEAD").stdout.strip()

    delta = changed_paths(hostile_paths_repo, base)

    assert "nested/inner.py" not in delta.changed
    assert not [path for path in delta.changed if path.startswith("nested/")]


@pytest.mark.parametrize(
    "base",
    ["", "   ", "not-a-sha", "HEAD~99", "refs/heads/does-not-exist"],
    ids=["empty", "blank", "garbage", "out-of-range", "missing-ref"],
)
def test_an_unusable_base_is_never_silently_ok(refresh_repo, base):
    """Every spelling of "this checkout cannot resolve your base".

    The empty string is the one that matters: a partition row whose
    `head_sha` was never stamped reads as `""`, and `git diff --name-status
    ""` is not "diff against nothing" — whatever git does with it, the answer
    may not come back as OK with an empty changed set, because that answer
    re-stamps the sha onto a map describing another tree.
    """
    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is not ChangedPathsOutcome.OK
    assert delta.changed == frozenset()
    assert delta.deleted == frozenset()


def test_a_base_that_is_an_object_but_not_a_commit_is_never_ok(refresh_repo):
    """A blob sha resolves — `cat-file -e` says yes — and is still not
    something a tree can be diffed against: git exits 129 with a usage error,
    not the 128 a bad revision gets. A guard that asks "does this object
    exist" passes it through.

    Which closed outcome it takes is the implementer's call and is not pinned
    here, because the criteria do not decide it. Worth knowing while deciding:
    BASE_UNUSABLE recovers (a full rebuild re-stamps a usable sha), while
    GIT_FAILED is specified to leave the stored sha alone — so a partition
    that somehow stored a blob sha would then fail identically forever and
    stay permanently stale.
    """
    blob = _git(refresh_repo, "hash-object", "-w", "pkg/alpha.py").stdout.strip()
    assert blob, "the fixture must actually have written a blob"

    delta = changed_paths(refresh_repo, blob)

    assert delta.outcome is not ChangedPathsOutcome.OK
    assert delta.changed == frozenset()
    assert delta.deleted == frozenset()


def test_changed_and_deleted_never_overlap_across_a_mutation_matrix(refresh_repo):
    """Edit, delete, add, rename and retype in one tree.

    A path in both sets is a refresh that deletes a file row it has just
    written, or writes one it has just deleted, and which of those happens
    depends on the order of two loops nobody chose.
    """
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    (refresh_repo / "pkg/gamma.py").unlink()
    _write(refresh_repo, "pkg/epsilon.py", "def epsilon():\n    return 5\n")
    _git(refresh_repo, "mv", "pkg/alpha.py", "pkg/alpha_renamed.py")
    (refresh_repo / "web/delta.ts").unlink()
    (refresh_repo / "web/delta.ts").symlink_to("../pkg/beta.py")

    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert not (delta.changed & delta.deleted)
    assert "pkg/gamma.py" in delta.deleted
    assert "pkg/alpha.py" in delta.deleted
    assert {"pkg/alpha_renamed.py", "pkg/beta.py", "pkg/epsilon.py"} <= delta.changed
    assert "web/delta.ts" in delta.changed, "a file that became a symlink still moved"


def test_a_path_staged_as_deleted_is_deleted_exactly_once(refresh_repo):
    """`git rm --cached` leaves the bytes on disk and the deletion in the
    index. The working-tree diff and the untracked scan then describe the same
    path from two sides, and a union that does not decide between them reports
    it as both gone and new."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _git(refresh_repo, "rm", "-q", "--cached", "pkg/gamma.py")

    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert not (delta.changed & delta.deleted)


# ==========================================================================
# What the answer says about itself (AC11)
# ==========================================================================


def test_the_age_of_a_map_built_a_moment_ago_is_measured_in_milliseconds(
    session, workspace, refresh_repo
):
    """`age_ms` is `now - built_at`, and `built_at` is NAIVE UTC from
    `models.domain.enums.utcnow`.

    Subtracting it from `datetime.now()` — local time, which this repository's
    ruff `DTZ` rules exist to keep out — yields the machine's UTC offset as
    the age of a map built a moment ago. On this machine that is 18,000,000 ms
    of lag reported for a map that is seconds old, and the operator-facing
    line reads "index stale, 0 behind" on a map that is fresh.

    This test cannot fail on a machine running in UTC. It fails on every other
    one, which is where this control plane is actually used.

    The ceiling is the window this test itself took, not a literal: the age of a
    map built inside that window cannot exceed it, however slow the machine is,
    while a UTC offset exceeds it by orders of magnitude.
    """
    before = utcnow()
    build_symbol_map(session, workspace.id, refresh_repo)

    status = map_status(session, workspace.id, refresh_repo)
    window_ms = (utcnow() - before).total_seconds() * 1000

    assert status.freshness is Freshness.MATCHES
    assert 0 <= status.age_ms <= window_ms, (
        f"age_ms={status.age_ms} exceeds the {window_ms:.0f}ms this build and read took, "
        "so it is an offset rather than an age"
    )


def test_a_built_at_in_the_future_does_not_produce_a_negative_age(session, workspace, refresh_repo):
    """Clock skew is ordinary: this database is copied between checkouts and
    travels across a filesystem whose clock nobody owns. A negative age is a
    number no renderer has a format for and no consumer can compare."""
    build_symbol_map(session, workspace.id, refresh_repo)
    partition = _partition(session, workspace.id, refresh_repo)
    partition.built_at = utcnow() + timedelta(hours=1)
    session.add(partition)
    session.commit()

    status = map_status(session, workspace.id, refresh_repo)

    assert status.age_ms >= 0


def test_a_tree_with_no_tracked_files_reports_progress_rather_than_dividing_by_zero(
    session, workspace, empty_tree_repo
):
    """`build_progress` is indexed over indexed-plus-behind. On a repository
    whose only commit is empty both are zero, and `0 / 0` is a ZeroDivisionError
    out of a function AC11 says never refuses — raised, note, on the READ path,
    so it reaches every consumer rather than the writer."""
    build_symbol_map(session, workspace.id, empty_tree_repo)

    status = map_status(session, workspace.id, empty_tree_repo)

    assert status.freshness is Freshness.MATCHES
    assert 0.0 <= status.build_progress <= 1.0
    assert status.files_behind == 0


def test_a_building_partition_still_names_its_lag(session, workspace, refresh_repo):
    """AC11 computes `files_behind` "once the verdict is already a mismatch or
    BUILDING". A BUILDING read that reports zero behind tells the operator the
    index is being rebuilt for no reason, which is the one state in which the
    number is most worth having."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: a real lag to report")
    partition = _partition(session, workspace.id, refresh_repo)
    partition.refresh_started_at = utcnow()
    session.add(partition)
    session.commit()

    status = map_status(session, workspace.id, refresh_repo)

    assert status.freshness is Freshness.BUILDING
    assert status.files_behind >= 1


def test_a_checkout_that_has_been_removed_reports_rather_than_raises(
    session, workspace, same_commit_checkouts
):
    """Worktrees are cut per ticket and removed when it lands; the partition
    outlives the directory until the retirement sweep runs. A read against a
    path that is gone must answer, because the consumer asking is a prompt
    being assembled and its fallback is live grep."""
    first, second = same_commit_checkouts
    build_symbol_map(session, workspace.id, second)
    _git(first, "worktree", "remove", "--force", str(second))
    assert not second.exists()

    status = map_status(session, workspace.id, second)

    assert status.freshness is not Freshness.MATCHES
    assert status.age_ms >= 0


def test_a_git_failure_on_the_read_path_never_answers_matches(session, workspace, refresh_repo):
    """`map_status` cannot compare shas when git itself fails. The one answer
    it may not give is the confident one: MATCHES from a comparison that never
    happened is the designed grep fallback never firing."""
    build_symbol_map(session, workspace.id, refresh_repo)

    with mock.patch.object(sources_module, "head_sha", side_effect=OSError("git is unavailable")):
        status = map_status(session, workspace.id, refresh_repo)

    assert status.freshness is not Freshness.MATCHES


def test_every_reader_agrees_with_map_status_on_one_tree(session, workspace, refresh_repo):
    """Three readers each carrying a status is three chances to compute it
    differently. They must all be the same verdict on the same tree, or a
    consumer that reads two of them has to decide which one to believe."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: make them all stale at once")

    standalone = map_status(session, workspace.id, refresh_repo)
    reads = [
        definitions_for(session, workspace.id, refresh_repo, "beta"),
        outline_for(session, workspace.id, refresh_repo, "pkg/beta.py"),
    ]

    assert {read.status.freshness for read in reads} == {standalone.freshness}
    assert {read.status.files_behind for read in reads} == {standalone.files_behind}


# ==========================================================================
# The three windows a refresh has (AC2, AC4, AC9, AC11)
# ==========================================================================


def _fail_in(window: str):
    """A failure injected into one of the refresh's three windows."""
    if window == "analysis":
        return mock.patch.object(
            store_module, "extraction_for", side_effect=RuntimeError("injected in analysis")
        )
    if window == "write":
        return mock.patch.object(
            store_module, "SymbolMapSymbol", side_effect=RuntimeError("injected in the write")
        )
    return mock.patch.object(
        sources_module, "changed_paths", side_effect=OSError("injected in the delta")
    )


@pytest.mark.parametrize("window", ["delta", "analysis", "write"], ids=lambda w: f"fails-in-{w}")
def test_a_failed_refresh_never_leaves_the_partition_pinned_at_building(
    session, workspace, refresh_repo, window
):
    """The stamp is written before any row and cleared on commit — and a
    refresh that never reaches its commit has to clear it anyway.

    A stamp nothing clears is the worst outcome in this ticket that no other
    test can see: every later read of that checkout answers BUILDING forever,
    the operator-facing line says "building" on an index nobody is building,
    and every criterion about a SUCCESSFUL refresh still passes. The three
    windows are injected separately because clearing the stamp in the write's
    own `except` covers only the third.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: work this refresh will not finish")

    with _fail_in(window):
        report = refresh_at_run_entry(session, workspace.id, refresh_repo)

    session.expire_all()
    assert report.ok is False
    assert _partition(session, workspace.id, refresh_repo).refresh_started_at is None
    assert map_status(session, workspace.id, refresh_repo).freshness is Freshness.SHA_MISMATCH


@pytest.mark.parametrize("window", ["delta", "analysis", "write"], ids=lambda w: f"fails-in-{w}")
def test_the_session_the_dispatch_still_needs_survives_a_failed_refresh(
    session, workspace, refresh_repo, window
):
    """`refresh_at_run_entry` shares the executor's Session, and the dispatch
    keeps writing run rows through it afterwards. A Session left in
    pending-rollback by a refresh raises `PendingRollbackError` on the next
    write — so a bookkeeping failure becomes the outcome of the work it was
    only supposed to describe, and the run fails for a reason no log names.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: work this refresh will not finish")

    with _fail_in(window):
        refresh_at_run_entry(session, workspace.id, refresh_repo)

    # The write the executor is about to do, in the form it does it.
    session.add(Workspace(slug="after-the-failure", name="after", repo_path=""))
    session.commit()
    assert session.exec(
        select(Workspace).where(Workspace.slug == "after-the-failure")
    ).one_or_none()


def test_a_changed_path_that_vanishes_before_the_analysis_does_not_crash(
    session, workspace, refresh_repo
):
    """The analysis pass runs OUTSIDE any transaction (AC4), which is correct
    and also opens a real window: the stage whose dispatch triggered this
    refresh is editing the same tree, and a path listed by the diff can be
    gone by the time it is read.

    `store.extraction_for` already answers that with a PARSE_FAILED row rather
    than an exception. What is pinned here is that the refresh keeps that
    property: the map stays usable and the untouched rows are untouched.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    partition_id = _partition(session, workspace.id, refresh_repo).id
    before = {path: row.id for path, row in _file_rows(session, partition_id).items()}
    ghost = ChangedPaths(
        outcome=ChangedPathsOutcome.OK,
        changed=frozenset({"pkg/ghost.py"}),
        deleted=frozenset(),
    )

    with mock.patch.object(sources_module, "changed_paths", return_value=ghost):
        report = refresh_at_run_entry(session, workspace.id, refresh_repo)

    session.expire_all()
    assert report.ok is True, f"a path that vanished mid-pass is not a failed refresh: {report}"
    after = _file_rows(session, partition_id)
    assert {path: row.id for path, row in after.items() if path in before} == before
    assert not [
        row
        for row in _symbol_rows(session, partition_id)
        if row.file_id not in {r.id for r in after.values()}
    ]


def test_a_file_that_became_unparseable_loses_its_stale_symbols(
    session, workspace, other_workspace, refresh_repo
):
    """An edit that breaks the syntax is the commonest mid-stage state there
    is, and it is the one where keeping the old rows is most tempting and most
    wrong: those line numbers describe a file nobody can parse any more, and
    the reader has no way to know. AC2 says never a file row with zero
    symbols for a file that HAS symbols; it does not say keep the old ones.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/alpha.py", "def alpha(:\n")
    _commit(refresh_repo, "feat: a half-written edit")

    refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition_id = _partition(session, workspace.id, refresh_repo).id
    assert _file_rows(session, partition_id)["pkg/alpha.py"].parse_status is (
        ParseStatus.PARSE_FAILED
    )
    assert not [row for row in _symbol_shape(session, partition_id) if row[0] == "pkg/alpha.py"]
    _assert_no_orphans(session, partition_id)
    _assert_matches_a_full_build(session, workspace.id, other_workspace.id, refresh_repo)


def test_an_edited_unsupported_file_is_replaced_and_never_opened(
    session, workspace, other_workspace, refresh_repo
):
    """`README.md` changed, so it is in the delta — and an OTHER-language path
    is never read, which is what makes 448 oversized and binary files free.
    A refresh that opens what the build refused to open is a decode error on
    a path the build was careful about."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "README.md", "# rewritten\n")
    _commit(refresh_repo, "docs: a changed path with no symbols in it")
    real = store_module.extraction_for
    opened: list[str] = []

    def recording(repo_root, path):
        opened.append(path)
        return real(repo_root, path)

    with mock.patch.object(store_module, "extraction_for", side_effect=recording):
        report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition_id = _partition(session, workspace.id, refresh_repo).id
    assert report.ok is True
    assert _file_rows(session, partition_id)["README.md"].parse_status is (
        ParseStatus.UNSUPPORTED_LANGUAGE
    )
    _assert_matches_a_full_build(session, workspace.id, other_workspace.id, refresh_repo)


# ==========================================================================
# Determinism, order and the full-build oracle (AC1, AC2, AC3)
# ==========================================================================


def _apply_the_matrix(root: Path) -> None:
    """Every kind of change a refresh has to decide about, in one commit."""
    _write(root, "pkg/beta.py", BETA_REWRITTEN)
    (root / "pkg/gamma.py").unlink()
    _write(root, "pkg/epsilon.py", "def epsilon():\n    return 5\n")
    _git(root, "mv", "pkg/alpha.py", "pkg/alpha_renamed.py")
    _write(root, "web/delta.ts", "export const DELTA = 44;\nexport class Zeta {}\n")
    _write(root, "README.md", "# changed\n")
    _commit(root, "feat: edit, delete, add, rename, retype and an unsupported path")


def test_a_refreshed_map_equals_a_full_build_of_the_same_tree(
    session, workspace, other_workspace, refresh_repo
):
    """The oracle this module leans on, stated once as its own test.

    Every count in the report can be right while the map is wrong. Only a
    comparison against a clean full build of the same tree can say the rows,
    the line numbers, the parse statuses and the sha all agree — which is the
    whole claim an incremental writer makes.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _apply_the_matrix(refresh_repo)

    refresh_symbol_map(session, workspace.id, refresh_repo)

    _assert_no_orphans(session, _partition(session, workspace.id, refresh_repo).id)
    _assert_matches_a_full_build(session, workspace.id, other_workspace.id, refresh_repo)


def test_the_same_edits_in_two_commits_land_where_one_commit_lands(
    session, workspace, other_workspace, refresh_repo
):
    """Order dependency, which is the characteristic bug of a writer that
    carries state between calls. Refreshing after each of two commits must
    reach the same map as refreshing once after both — otherwise the map a
    checkout holds depends on how often a stage happened to run.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    build_symbol_map(session, other_workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: one")

    refresh_symbol_map(session, workspace.id, refresh_repo)  # the stepwise lane

    (refresh_repo / "pkg/gamma.py").unlink()
    _write(refresh_repo, "pkg/epsilon.py", "def epsilon():\n    return 5\n")
    _commit(refresh_repo, "feat: two")
    refresh_symbol_map(session, workspace.id, refresh_repo)
    refresh_symbol_map(session, other_workspace.id, refresh_repo)  # the one-shot lane

    session.expire_all()
    stepwise = _partition(session, workspace.id, refresh_repo)
    one_shot = _partition(session, other_workspace.id, refresh_repo)
    assert _symbol_shape(session, stepwise.id) == _symbol_shape(session, one_shot.id)
    assert _file_shape(session, stepwise.id) == _file_shape(session, one_shot.id)


def test_refreshing_twice_in_a_row_is_a_no_op_down_to_the_row_ids(session, workspace, refresh_repo):
    """Determinism, and the cheapest proof that the second pass really is
    incremental: a rebuild wearing the right return value re-mints every id.
    Run entries are frequent — six in one minute at the measured peak — and
    most of them stand on a tree nothing has touched."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _apply_the_matrix(refresh_repo)
    refresh_symbol_map(session, workspace.id, refresh_repo)
    session.expire_all()
    partition_id = _partition(session, workspace.id, refresh_repo).id
    ids = {path: row.id for path, row in _file_rows(session, partition_id).items()}
    shape = _symbol_shape(session, partition_id)

    second = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    assert second.files_reanalyzed == 0
    assert second.files_deleted == 0
    assert {path: row.id for path, row in _file_rows(session, partition_id).items()} == ids
    assert _symbol_shape(session, partition_id) == shape


def test_a_refresh_writes_only_its_own_workspaces_partition(
    session, workspace, other_workspace, refresh_repo
):
    """Two workspaces can share one checkout — the primary one here does. A
    delete scoped by `checkout_identity` alone rather than by workspace takes
    the bystander's rows with it, and the bystander's next read answers from
    a map that was never refreshed and no longer matches its own sha."""
    build_symbol_map(session, workspace.id, refresh_repo)
    build_symbol_map(session, other_workspace.id, refresh_repo)
    bystander = _partition(session, other_workspace.id, refresh_repo)
    before = (bystander.head_sha, _symbol_shape(session, bystander.id))
    _apply_the_matrix(refresh_repo)

    refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    bystander = _partition(session, other_workspace.id, refresh_repo)
    assert (bystander.head_sha, _symbol_shape(session, bystander.id)) == before


# ==========================================================================
# A reader standing in the middle of a write (AC5, AC11)
# ==========================================================================


def test_a_reader_entering_mid_refresh_is_told_building_and_never_sees_a_half_index(
    isolated_db, session, workspace, refresh_repo
):
    """AC5 pins two WRITERS against each other. This pins a READER against a
    writer, which is the commoner race: six run starts in one minute, each
    assembling a prompt from this map.

    The reader is on a second connection, so what it sees is what was
    COMMITTED. It must be told BUILDING — the state `refresh_started_at`
    exists to make reachable — and the file being replaced must still answer
    with its old rows rather than with nothing, because "the index has no
    symbols for this file" is the answer a consumer acts on by believing it.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    workspace_id = workspace.id
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: the write the reader will interrupt")
    session.commit()
    reached_analysis = threading.Event()
    may_continue = threading.Event()
    failures: list[BaseException] = []
    real = store_module.extraction_for

    def blocking(repo_root, path):
        reached_analysis.set()
        may_continue.wait(timeout=_JOIN_TIMEOUT_SECONDS)
        return real(repo_root, path)

    def refresh() -> None:
        try:
            with Session(isolated_db) as own:
                refresh_symbol_map(own, workspace_id, refresh_repo)
        except BaseException as exc:  # noqa: BLE001 - re-raised below as the assertion
            failures.append(exc)
        finally:
            reached_analysis.set()

    with mock.patch.object(store_module, "extraction_for", side_effect=blocking):
        writer = threading.Thread(target=refresh, name="mid-refresh-writer")
        writer.start()
        try:
            assert reached_analysis.wait(timeout=_JOIN_TIMEOUT_SECONDS), "analysis never started"
            with Session(isolated_db) as reader:
                mid = map_status(reader, workspace_id, refresh_repo)
                outline = outline_for(reader, workspace_id, refresh_repo, "pkg/beta.py")
        finally:
            may_continue.set()
            writer.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert failures == [], [repr(exc) for exc in failures]
    assert writer.is_alive() is False
    assert mid.freshness is Freshness.BUILDING
    assert [row.line for row in outline.rows] == [1], "the old rows were already gone"
    session.expire_all()
    assert map_status(session, workspace_id, refresh_repo).freshness is Freshness.MATCHES
    assert _partition(session, workspace_id, refresh_repo).refresh_started_at is None


# ==========================================================================
# Seeding a cold checkout, attacked (R6)
# ==========================================================================


def test_a_seeded_partition_shares_no_row_id_with_the_ancestor_it_copied(
    session, workspace, ancestor_checkouts
):
    """A copy is rows, not references. Reusing the ancestor's `id` values puts
    two partitions' files under one primary key: the insert fails outright, or
    — worse, where the ids merely travel into `symbol_map_symbols.file_id` —
    retiring the ancestor silently empties a live checkout's map."""
    primary, cold, _moved = ancestor_checkouts
    build_symbol_map(session, workspace.id, primary)
    ancestor_ids = {
        row.id
        for row in _file_rows(session, _partition(session, workspace.id, primary).id).values()
    }

    refresh_symbol_map(session, workspace.id, cold)

    session.expire_all()
    seeded = _partition(session, workspace.id, cold)
    seeded_ids = {row.id for row in _file_rows(session, seeded.id).values()}
    assert seeded_ids
    assert not (seeded_ids & ancestor_ids)
    _assert_no_orphans(session, seeded.id)


def test_a_cold_checkout_never_seeds_from_another_workspaces_map(
    session, workspace, other_workspace, ancestor_checkouts
):
    """`symbol_map_partitions` is keyed by workspace AND checkout. A seed that
    searches on the checkout alone hands one workspace another's map — and
    both are maps of the same files, so nothing about the result looks wrong
    until a workspace that was never built answers a query."""
    primary, cold, _moved = ancestor_checkouts
    build_symbol_map(session, other_workspace.id, primary)

    report = refresh_symbol_map(session, workspace.id, cold)

    session.expire_all()
    assert report.seeded is False, "the only mapped checkout belongs to another workspace"
    assert report.ok is True
    assert map_status(session, workspace.id, cold).freshness is Freshness.MATCHES


def test_seeding_from_an_ancestor_mid_refresh_still_lands_a_correct_map(
    session, workspace, other_workspace, ancestor_checkouts
):
    """An ancestor carrying `refresh_started_at` is a partition a peer is
    writing right now. Whatever the seed decides to do about that — copy it,
    or fall back to a full build — the map this cold checkout ends up with has
    to equal a clean build of its own tree. That is the claim; the mechanism
    is the implementer's.
    """
    primary, cold, _moved = ancestor_checkouts
    build_symbol_map(session, workspace.id, primary)
    ancestor = _partition(session, workspace.id, primary)
    ancestor.refresh_started_at = utcnow()
    session.add(ancestor)
    session.commit()

    report = refresh_symbol_map(session, workspace.id, cold)

    assert report.ok is True
    _assert_no_orphans(session, _partition(session, workspace.id, cold).id)
    _assert_matches_a_full_build(session, workspace.id, other_workspace.id, cold)


def test_the_testable_core_raises_on_an_unborn_head_and_the_wrapper_reports_it(
    session, workspace, unborn_repo
):
    """The split, exercised against a REAL repository with no commit rather
    than against a patched `head_sha`. A checkout mid-`git init` is what a
    freshly cut worktree looks like for a moment, and the two halves of the
    contract have to hold on the same tree: the core raises so it can be
    tested, the wrapper never does so a stage is never failed by it.
    """
    with pytest.raises(sources_module.UnbornHeadError):
        refresh_symbol_map(session, workspace.id, unborn_repo)

    report = refresh_at_run_entry(session, workspace.id, unborn_repo)

    assert report.ok is False
    assert report.reason is RefreshFailure.UNBORN_HEAD
    assert map_status(session, workspace.id, unborn_repo).freshness is Freshness.NO_PARTITION


# ==========================================================================
# test-break, second round: the five mutants the suite could not see
#
# The first round of this module was written before the implementation
# existed, so every assertion in it was aimed at a design rather than at
# code. With the writer in the tree a stronger question is available, and it
# is the one verify's AC2 finding says to ask: mutate the shipped code and
# watch whether anything goes red. Twelve mutations were run against
# `tests/test_symbol_map_refresh.py`, this module, `test_symbol_map_run_entry.py`
# and `test_symbol_map.py`; seven died and FIVE survived a green 242-test run:
#
#   M2  `_untracked` returns an empty set instead of None when git fails
#   M4  `head_of` drops the root comparison and trusts any snapshot
#   M6  `apply_extractions` stops advancing `built_at`
#   M10 `_verdict` lets `UnbornHeadError` escape the reader
#   M12 `_drop_paths` resolves its file ids OUTSIDE the write transaction
#
# Each one is a criterion with no detector, so each gets one below. The
# production code is correct in all five cases: these tests are green on the
# shipped tree and red on their mutant, and the pairing was measured rather
# than reasoned about.
# ==========================================================================


def test_an_unreadable_untracked_list_is_never_read_as_a_clean_tree(
    session, workspace, refresh_repo
):
    """AC1's union half, which a failed `ls-files --others` can forge.

    The diff covers tracked paths. Brand-new modules — the commonest thing an
    implementer stage produces — are reached only by the untracked scan, and
    `_untracked` answers None for "I could not look" precisely so it cannot be
    confused with "there are none". An empty set in its place is the whole
    silent failure in one line: the delta looks OK, the refresh succeeds, the
    sha advances onto a tree whose newest file was never read, and every later
    read answers MATCHES. Worse than no map, because the grep fallback the
    design relies on never fires.

    Mutant M2: `return None` -> `return frozenset()` in `sources._untracked`.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    base = _partition(session, workspace.id, refresh_repo).head_sha
    _write(refresh_repo, "pkg/brand_new.py", "def brand_new():\n    return 1\n")

    real = sources_module.run_git

    def blind_to_untracked(args, **kwargs):
        if list(args[:2]) == ["ls-files", "--others"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: could not open index")
        return real(args, **kwargs)

    with mock.patch.object(sources_module, "run_git", side_effect=blind_to_untracked):
        delta = changed_paths(refresh_repo, base)
        report = refresh_symbol_map(session, workspace.id, refresh_repo)

    assert delta.outcome is ChangedPathsOutcome.GIT_FAILED
    assert (delta.changed, delta.deleted) == (frozenset(), frozenset())
    assert report.ok is False
    assert report.reason is RefreshFailure.GIT_FAILED

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    assert partition.head_sha == base, "a delta nobody could read advanced the sha"
    assert partition.refresh_started_at is None
    assert "pkg/brand_new.py" not in _file_rows(session, partition.id)


def test_a_snapshot_that_could_not_see_the_tree_is_not_an_empty_tree(
    session, workspace, refresh_repo
):
    """The same confusion one layer up, where the caller supplies it.

    `TreeSnapshot.dirty_paths` is None on exactly the same contract, and the
    executor hands the refresh a snapshot on every dispatch. A `None` read as
    "clean" would drop the untracked half without git ever failing, so the
    fall-through to git is the behaviour and not an implementation detail.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    base = _partition(session, workspace.id, refresh_repo).head_sha
    _write(refresh_repo, "pkg/brand_new.py", "def brand_new():\n    return 1\n")
    unreadable = TreeSnapshot(
        repo_root=refresh_repo, head_sha=base, branch="main", dirty_paths=None
    )

    delta = changed_paths(refresh_repo, base, tree=unreadable)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert "pkg/brand_new.py" in delta.changed, "an unreadable snapshot was read as a clean tree"


def test_a_snapshot_of_another_checkout_never_stamps_this_map(
    session, workspace, ancestor_checkouts
):
    """AC1 keys the map to a checkout; the sha has to come from the same one.

    `head_of` takes HEAD from the caller's snapshot to save a process, and
    that shortcut is only sound while the snapshot is a reading of THIS root.
    Two checkouts of one repository at two shas is the ordinary state of this
    machine — ~102 live worktrees off one checkout — so handing the refresh
    the wrong snapshot is a mix-up a dispatch can actually make. The map would
    then carry a sha describing a tree nothing read: `map_status` answers
    SHA_MISMATCH forever on a map that is in fact current, and the next
    refresh diffs against a commit this working tree never stood on.

    Mutant M4: delete the `os.path.realpath(...) == os.path.realpath(...)`
    conjunct from `refresh.head_of`.
    """
    primary, cold, _ = ancestor_checkouts
    build_symbol_map(session, workspace.id, primary)
    _write(primary, "pkg/alpha.py", "def alpha():\n    return 99\n")
    true_head = _commit(primary, "feat: the primary moves on its own")
    foreign = read_tree(cold)
    assert foreign.head_sha and foreign.head_sha != true_head, "the fixture lost its two shas"

    report = refresh_symbol_map(session, workspace.id, primary, tree=foreign)

    session.expire_all()
    partition = _partition(session, workspace.id, primary)
    assert report.ok is True
    assert report.head_sha == true_head
    assert partition.head_sha == true_head, "this map was stamped with another checkout's HEAD"
    assert map_status(session, workspace.id, primary).freshness is Freshness.MATCHES


def test_built_at_advances_on_every_successful_refresh_including_the_no_op(
    session, workspace, refresh_repo
):
    """AC3 names `built_at` twice, and nothing was watching it.

    It is the only thing `age_ms` is derived from, so a refresh that advances
    the sha and leaves `built_at` behind produces the one answer AC11 exists
    to prevent: a map that reports MATCHES and, in the same breath, an age
    that keeps growing. An operator reading "fresh, 4 hours behind" cannot
    tell which half to believe.

    The no-op pass is asserted in the same test because AC3 pins it
    separately — the sha and `built_at` advance, and no file row is touched —
    and because "nothing changed" is the path where skipping the stamp is the
    tempting optimisation.

    Mutant M6: delete `partition.built_at = stamped_at` from
    `store.apply_extractions`.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    partition = _partition(session, workspace.id, refresh_repo)
    ids_before = {path: row.id for path, row in _file_rows(session, partition.id).items()}

    backdated = utcnow().replace(tzinfo=None) - timedelta(hours=1)
    partition.built_at = backdated
    session.add(partition)
    session.commit()

    quiet = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    assert quiet.ok is True
    assert quiet.files_reanalyzed == 0
    assert partition.built_at > backdated, "a no-op refresh left the map's age climbing"
    assert {
        path: row.id for path, row in _file_rows(session, partition.id).items()
    } == ids_before, "a no-op refresh re-minted file rows"

    backdated_again = utcnow().replace(tzinfo=None) - timedelta(hours=1)
    partition.built_at = backdated_again
    session.add(partition)
    session.commit()
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: one real change")

    busy = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    assert busy.ok is True
    assert busy.files_reanalyzed == 1
    assert partition.built_at > backdated_again


def test_a_reader_on_a_checkout_with_no_commit_reports_rather_than_raises(
    session, workspace, unborn_repo
):
    """AC11's "never refuses", at the reader rather than at the writer.

    `test_the_testable_core_raises_on_an_unborn_head_and_the_wrapper_reports_it`
    covers the WRITE side. The read side is the one AC11 actually names — "the
    live `UnbornHeadError` escape from `head_sha` is caught and reported as
    `NO_PARTITION`" — and it needs a partition row standing over a checkout
    with no commit, which is reachable for the reason the schema docstring
    gives: this database is copied into other checkouts, and a copy can land
    beside a tree that has no commit yet.

    An exception out of `map_status` is strictly worse than a stale answer. It
    is raised on the READ path, so it reaches 282, 283 and 300 rather than the
    writer, and it fails the very query the grep fallback exists to keep
    answerable.

    Mutant M10: delete the `except sources.UnbornHeadError` arm from
    `freshness._verdict`.
    """
    session.add(
        SymbolMapPartition(
            workspace_id=workspace.id,
            checkout_identity=identify(unborn_repo),
            checkout_root=str(unborn_repo),
            head_sha="0" * 40,
            built_at=utcnow(),
        )
    )
    session.commit()

    status = map_status(session, workspace.id, unborn_repo)

    assert status.freshness is Freshness.NO_PARTITION
    assert (status.age_ms, status.files_behind, status.build_progress) == (0, 0, 0.0)
    assert (
        definitions_for(session, workspace.id, unborn_repo, "thing").status.freshness
        is Freshness.NO_PARTITION
    )
    assert (
        outline_for(session, workspace.id, unborn_repo, "pkg/thing.py").status.freshness
        is Freshness.NO_PARTITION
    )


def test_the_losing_writer_resolves_its_file_ids_inside_its_own_transaction(
    isolated_db, session, workspace, other_workspace, refresh_repo
):
    """AC5, with the interleaving forced instead of hoped for.

    The existing AC5 test starts two threads and asserts they both finish. The
    race it is aimed at only opens when the loser resolves its file ids before
    the winner commits, and whether that happens depends on machine load: the
    implementer saw it at `-n 4` and the same test passes here against the
    pre-fix shape. A detector that needs a busy box is not a detector.

    So the order is imposed through a `before_cursor_execute` listener — a
    public SQLAlchemy seam, not a patched module global — holding the winner
    inside its write transaction until the loser has reached the statement
    that resolves its ids. Both waits are bounded, so a wrong ordering fails
    the test rather than hanging the suite.

    Shipped, the loser's first statement is the DELETE carrying its id
    subquery: SQLite blocks it on the winner's write lock and it resolves the
    ids the winner committed. Resolved beforehand in Python, it deletes the
    file rows by path while holding ids that no longer exist, which strands
    the winner's symbol rows under a foreign key that is enforced on this
    engine.

    Mutant M12: resolve `doomed` with `session.exec(...).all()` before the
    DELETE in `store._drop_paths`.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    workspace_id = workspace.id
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _write(refresh_repo, "pkg/alpha.py", "def alpha():\n    return 11\n")
    _commit(refresh_repo, "feat: work for both writers")
    build_symbol_map(session, other_workspace.id, refresh_repo)
    bystander = _partition(session, other_workspace.id, refresh_repo).id
    expected_symbols = _symbol_shape(session, bystander)
    expected_files = _file_shape(session, bystander)
    session.commit()

    #: The winner is inside its write transaction, past its deletes.
    holding = threading.Event()
    #: The loser has reached the statement that resolves its file ids.
    resolved = threading.Event()
    failures: list[BaseException] = []

    def order_the_two_writers(conn, cursor, statement, parameters, context, executemany):
        if "symbol_map_files" not in statement:
            return
        name = threading.current_thread().name
        if name == "writer-a" and statement.lstrip().lower().startswith(
            "insert into symbol_map_files"
        ):
            holding.set()
            resolved.wait(timeout=_JOIN_TIMEOUT_SECONDS)
        elif name == "writer-b" and not resolved.is_set():
            holding.wait(timeout=_JOIN_TIMEOUT_SECONDS)
            resolved.set()

    def refresh() -> None:
        try:
            with Session(isolated_db) as own:
                refresh_symbol_map(own, workspace_id, refresh_repo)
        except BaseException as exc:  # noqa: BLE001 - re-raised below as the assertion
            failures.append(exc)

    event.listen(isolated_db, "before_cursor_execute", order_the_two_writers)
    try:
        threads = [threading.Thread(target=refresh, name=f"writer-{n}") for n in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    finally:
        event.remove(isolated_db, "before_cursor_execute", order_the_two_writers)

    assert [thread.is_alive() for thread in threads] == [False, False]
    assert holding.is_set() and resolved.is_set(), "the interleaving this test needs never happened"
    assert failures == [], [repr(exc) for exc in failures]

    session.expire_all()
    partition = _partition(session, workspace_id, refresh_repo)
    _assert_no_orphans(session, partition.id)
    assert _symbol_shape(session, partition.id) == expected_symbols
    assert _file_shape(session, partition.id) == expected_files
    assert partition.refresh_started_at is None
