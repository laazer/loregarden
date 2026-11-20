"""Incremental symbol-map freshness: the writer, and the lag it reports.

Ticket lg-code-knowledge-284, specification artifact
`cf03f621-a6d8-4b5a-8ba3-111a4627f11a`, requirements R1-R6 and R8. The call
site itself (R7 / AC1) is exercised through a real dispatch in
`test_symbol_map_run_entry.py`, and the measurement (AC10) sits beside the
existing MEASURED block in `test_symbol_map_on_this_repo.py`.

Deliberately NOT added to `test_symbol_map.py`: that module is 2,360 lines
against a 2,500-line cap, and a change that pushes it over inherits the split.

Every repository is built by a fixture in `tests/symbol_map_repos.py`, never
in a test body. A broken setup inside a test body fails in the call phase and
is indistinguishable from a healthy failure.

**What this suite is adversarial about.** A refresh has one failure mode that
passes every naive test: it advances `head_sha` without having read the tree
that sha describes. The map then answers `MATCHES` with every line number
wrong, which is strictly worse than no map at all, because the designed grep
fallback never fires. Three tests exist only for that shape —
`test_a_git_failure_is_never_an_empty_changed_set`,
`test_a_git_failure_leaves_the_stored_sha_alone`, and
`test_an_unchanged_files_row_id_survives_a_refresh` (the last proves the
converse: that a refresh which *did* advance the sha actually re-read only
what moved).
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest
from loregarden.db.migrations import MIGRATIONS
from loregarden.db.versions import symbol_map as symbol_map_migration
from loregarden.db.versions import symbol_map_refresh as refresh_migration
from loregarden.models.domain import (
    Freshness,
    SymbolMapFile,
    SymbolMapPartition,
    SymbolMapSymbol,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.symbol_map import (
    build_symbol_map,
    definitions_for,
    map_status,
    outline_for,
    refresh_at_run_entry,
    refresh_symbol_map,
    suggest_names,
)
from loregarden.services.symbol_map import refresh as refresh_module
from loregarden.services.symbol_map import sources as sources_module
from loregarden.services.symbol_map import store as store_module
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.freshness import MapStatus
from loregarden.services.symbol_map.refresh import RefreshFailure, RefreshReport
from loregarden.services.symbol_map.sources import ChangedPaths, ChangedPathsOutcome, changed_paths
from loregarden.services.worktree_snapshot import TreeSnapshot, read_tree
from sqlmodel import Session, select
from tests.symbol_map_repos import (  # noqa: F401 - fixtures resolved by name
    ALPHA_MODULE,
    BETA_REWRITTEN,
    _commit,
    _git,
    _init,
    _write,
    ancestor_checkouts_fixture,
    migrated_engine_fixture,
    other_workspace_fixture,
    refresh_repo_fixture,
    same_commit_checkouts_fixture,
    session_fixture,
    unborn_repo_fixture,
    workspace_fixture,
    worktree_pair_fixture,
)

PACKAGE_ROOT = Path(sources_module.__file__).parent
SERVER_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Small readers over the stored rows. Deliberate duplicates of the ones in
# `test_symbol_map.py`: importing private helpers across two suites couples
# their refactors, and these are four lines each.
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
    """Every symbol as a comparable value: (path, name, line, parent).

    Row ids are deliberately excluded — two builds mint different uuid4s for
    the same source, so a shape is the only thing two builds can be equal on.
    """
    files = {row.id: row.path for row in _file_rows(session, partition_id).values()}
    return {
        (files[row.file_id], row.name, row.line, row.parent)
        for row in _symbol_rows(session, partition_id)
    }


def _package_modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.glob("*.py"))


def _server_sources() -> list[Path]:
    return [
        path
        for path in SERVER_ROOT.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    ]


# ==========================================================================
# R1 / AC1 — the delta oracle, `sources.changed_paths`
# ==========================================================================


def test_an_edited_tracked_file_and_an_untracked_file_are_both_changed(refresh_repo):
    """R1.3. The uncommitted half is not an optimisation: every mid-stage edit
    this control plane makes is uncommitted, and an untracked file is an
    implementer's brand-new module. A map reporting MATCHES while missing it
    is the exact inversion of this milestone's purpose.
    """
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _write(refresh_repo, "pkg/new.py", "def brand_new():\n    return 9\n")

    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert delta.changed == frozenset({"pkg/beta.py", "pkg/new.py"})
    assert delta.deleted == frozenset()


def test_a_path_deleted_in_the_working_tree_is_deleted_and_not_changed(refresh_repo):
    """R1.4. The two sets must not overlap: a path in both would be deleted
    and re-inserted, and whichever ran last would decide whether the symbol
    still exists."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    (refresh_repo / "pkg/gamma.py").unlink()

    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert delta.deleted == frozenset({"pkg/gamma.py"})
    assert "pkg/gamma.py" not in delta.changed


def test_a_rename_arrives_as_a_delete_and_an_add(refresh_repo):
    """`--no-renames`, so the status parser stays three letters wide. Without
    it git emits `R100\told\tnew`, a two-path record the three-letter parser
    reads as one path named `old\tnew`."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _git(refresh_repo, "mv", "pkg/gamma.py", "pkg/renamed.py")

    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert "pkg/renamed.py" in delta.changed
    assert delta.deleted == frozenset({"pkg/gamma.py"})


def test_a_non_ascii_path_round_trips(refresh_repo):
    """`-z` on every git read. Default output C-quotes any byte above 0x7f,
    so `pkg_Ω.py` prints as `"pkg_\\316\\251.py"` — a path that exists on no
    filesystem, and one no caller will ever ask for."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _write(refresh_repo, "pkg/omega_Ω.py", "def omega():\n    return 1\n")

    delta = changed_paths(refresh_repo, base)

    assert "pkg/omega_Ω.py" in delta.changed


def test_an_unchanged_tree_is_ok_with_an_empty_changed_set(refresh_repo):
    """R1.1. The empty set is a legitimate answer — but only under OK, which
    is what the next test is about."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()

    delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.OK
    assert delta.changed == frozenset()
    assert delta.deleted == frozenset()


def test_a_base_sha_this_checkout_does_not_have_is_base_unusable(refresh_repo):
    """R1.5 / AC6. Routine, not exotic: a force-push, a squash-merge, a gc or
    a copied database all leave a stored sha this checkout cannot resolve."""
    delta = changed_paths(refresh_repo, "0" * 40)

    assert delta.outcome is ChangedPathsOutcome.BASE_UNUSABLE
    assert delta.changed == frozenset()


def test_a_git_failure_is_never_an_empty_changed_set(refresh_repo):
    """R1.2 — the highest-risk criterion in the ticket after the call site.

    A failed diff that reads as "nothing changed" re-stamps the sha onto a map
    describing another tree. The reader then answers MATCHES with every line
    number wrong, and the designed grep fallback never fires. An empty
    `changed` is only ever legal under OK; a failure must be its own outcome.
    """
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()

    class _Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    with mock.patch.object(sources_module, "run_git", return_value=_Failed()):
        delta = changed_paths(refresh_repo, base)

    assert delta.outcome is ChangedPathsOutcome.GIT_FAILED
    assert delta.changed == frozenset()
    assert delta.deleted == frozenset()


def test_an_untracked_path_is_found_from_the_snapshot_the_caller_already_read(refresh_repo):
    """The executor has already run `git status --untracked-files=all` at
    `cli.py:283`; asking git the same question again is a second process on
    every dispatch for an answer already in the caller's hand."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _write(refresh_repo, "pkg/new.py", "def brand_new():\n    return 9\n")
    tree = read_tree(refresh_repo)
    assert "pkg/new.py" in (tree.dirty_paths or frozenset())

    with mock.patch.object(sources_module, "run_git", wraps=sources_module.run_git) as git_calls:
        delta = changed_paths(refresh_repo, base, tree=tree)

    assert "pkg/new.py" in delta.changed
    assert len(git_calls.call_args_list) == 1, "one diff, and no second ls-files"


def test_an_unreadable_snapshot_asks_git_rather_than_assuming_a_clean_tree(refresh_repo):
    """`TreeSnapshot.dirty_paths` is None when git could not answer. That is
    "unreadable", not "clean"; treating it as an empty set silently drops
    every untracked file from the delta."""
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _write(refresh_repo, "pkg/new.py", "def brand_new():\n    return 9\n")
    unreadable = TreeSnapshot(repo_root=refresh_repo, head_sha="", branch="", dirty_paths=None)

    delta = changed_paths(refresh_repo, base, tree=unreadable)

    assert "pkg/new.py" in delta.changed


def test_the_delta_is_against_the_working_tree_and_not_a_commit_range(refresh_repo):
    """The decision in `f541f308`, made falsifiable. A commit range
    (`base..HEAD`) cannot see uncommitted work — and uncommitted work is every
    mid-stage edit this control plane makes. Here the committed change and the
    uncommitted one must BOTH appear in one answer.
    """
    base = _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    _write(refresh_repo, "pkg/alpha.py", "def alpha():\n    return 111\n")
    _commit(refresh_repo, "feat: committed")
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)

    delta = changed_paths(refresh_repo, base)

    assert delta.changed == frozenset({"pkg/alpha.py", "pkg/beta.py"})


def test_changed_paths_is_a_frozen_value_carrying_a_closed_outcome():
    """R1.1. A string vocabulary trips `py-organization`, and a bare list
    cannot say "I failed" at all."""
    delta = ChangedPaths(
        outcome=ChangedPathsOutcome.OK,
        changed=frozenset({"a.py"}),
        deleted=frozenset(),
    )

    assert set(ChangedPathsOutcome) == {
        ChangedPathsOutcome.OK,
        ChangedPathsOutcome.BASE_UNUSABLE,
        ChangedPathsOutcome.GIT_FAILED,
    }
    with pytest.raises((AttributeError, TypeError, ValueError)):
        delta.changed = frozenset()


# ==========================================================================
# R2 / AC11 — the answer's own freshness state
# ==========================================================================


def test_building_is_part_of_the_freshness_vocabulary():
    """AC11. Four-valued now: "a peer is writing this index right now" is a
    different fact from "it describes another commit"."""
    assert Freshness.BUILDING in set(Freshness)
    assert {member.value for member in Freshness} == {
        "matches",
        "sha_mismatch",
        "no_partition",
        "building",
    }


def test_a_fresh_read_reports_matches_with_no_lag(session, workspace, refresh_repo):
    """AC11. `files_behind` is the honest zero here, not an unknown: the sha
    matches and nothing in the tree moved."""
    build_symbol_map(session, workspace.id, refresh_repo)

    status = map_status(session, workspace.id, refresh_repo)

    assert status.freshness is Freshness.MATCHES
    assert status.files_behind == 0
    assert status.build_progress == 1.0
    assert status.age_ms >= 0


def test_a_stale_read_names_its_lag_rather_than_only_saying_stale(session, workspace, refresh_repo):
    """AC11 / R2.1, and the surveyed renderer's own note: "naming the lag, not
    just stale, since matches are read live from disk regardless". Two commits
    behind, each touching one file, is two files behind."""
    build_symbol_map(session, workspace.id, refresh_repo)
    indexed = len(_file_rows(session, _partition(session, workspace.id, refresh_repo).id))
    _write(refresh_repo, "pkg/alpha.py", "def alpha():\n    return 11\n")
    _commit(refresh_repo, "feat: one")
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: two")

    status = map_status(session, workspace.id, refresh_repo)

    assert status.freshness is Freshness.SHA_MISMATCH
    assert status.files_behind == 2
    assert status.age_ms > 0
    assert status.build_progress == pytest.approx(indexed / (indexed + 2))
    assert 0.0 < status.build_progress < 1.0


def test_a_partition_mid_refresh_reads_as_building(session, workspace, refresh_repo):
    """AC11 / R2.2. BUILDING is reachable, not vocabulary: `refresh_started_at`
    is stamped before any row is written and cleared on commit, so a reader
    entering while a peer refreshes the same partition sees it. Measured peak
    on live data: six run starts in one minute."""
    build_symbol_map(session, workspace.id, refresh_repo)
    partition = _partition(session, workspace.id, refresh_repo)
    partition.refresh_started_at = utcnow()
    session.add(partition)
    session.commit()

    status = map_status(session, workspace.id, refresh_repo)

    assert status.freshness is Freshness.BUILDING
    assert definitions_for(session, workspace.id, refresh_repo, "alpha").status.freshness is (
        Freshness.BUILDING
    )


def test_a_completed_refresh_clears_the_building_stamp(session, workspace, refresh_repo):
    """The other half of R2.2: a stamp that is never cleared pins every later
    read at BUILDING, which is a map that reads as permanently untrustworthy
    and a `files_behind` nobody can act on."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: something to do")

    refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    assert _partition(session, workspace.id, refresh_repo).refresh_started_at is None
    assert map_status(session, workspace.id, refresh_repo).freshness is Freshness.MATCHES


def test_a_checkout_with_no_commit_reports_no_partition_and_does_not_raise(
    session, workspace, unborn_repo
):
    """AC11 / R2.3. A live 281 defect: `head_sha` raises `UnbornHeadError`,
    and `map_freshness` reached it from every reader. The answer must be a
    verdict, never an exception out of a read."""
    status = map_status(session, workspace.id, unborn_repo)

    assert status.freshness is Freshness.NO_PARTITION
    assert status.age_ms == 0
    assert status.files_behind == 0
    assert status.build_progress == 0.0
    for read in (
        definitions_for(session, workspace.id, unborn_repo, "thing"),
        outline_for(session, workspace.id, unborn_repo, "pkg/thing.py"),
        suggest_names(session, workspace.id, unborn_repo, "thng"),
    ):
        assert read.status.freshness is Freshness.NO_PARTITION
        assert list(read.rows) == []


def test_the_matches_path_costs_exactly_one_git_process(session, workspace, refresh_repo):
    """AC11 / R2.4. `definitions_for`, `outline_for` and `suggest_names` are
    the surfaces this milestone exists to make cheap. `files_behind` is
    computed only once the verdict is already a mismatch, so the fresh path
    stays the single `rev-parse` it was before this ticket.

    Counted at `sources.run_git` because `sources` is the package's only
    process seam, pinned by `test_only_sources_shells_git`.
    """
    build_symbol_map(session, workspace.id, refresh_repo)

    with mock.patch.object(sources_module, "run_git", wraps=sources_module.run_git) as git_calls:
        status = map_status(session, workspace.id, refresh_repo)

    assert status.freshness is Freshness.MATCHES
    argvs = [call.args[0] for call in git_calls.call_args_list]
    assert argvs == [["rev-parse", "HEAD"]], argvs


def test_build_progress_is_derived_from_rows_and_not_written_by_a_progress_loop(
    session, workspace, refresh_repo
):
    """AC11. A counter written during the pass needs a second transaction and
    a writer on the read path. Derived from rows, the number also degrades
    correctly on its own: 0.0 on a cold checkout, 1.0 when nothing is behind.
    """
    cold = map_status(session, workspace.id, refresh_repo)
    assert cold.build_progress == 0.0

    build_symbol_map(session, workspace.id, refresh_repo)

    assert map_status(session, workspace.id, refresh_repo).build_progress == 1.0
    columns = set(SymbolMapPartition.model_fields)
    assert "build_progress" not in columns
    assert "files_behind" not in columns


def test_map_status_is_a_frozen_value_with_exactly_four_fields():
    """AC11, as a shape. A fifth field added by a later ticket without a
    decision is how a value object becomes a bag."""
    status = MapStatus(freshness=Freshness.MATCHES, age_ms=0, files_behind=0, build_progress=1.0)

    assert (status.freshness, status.age_ms, status.files_behind, status.build_progress) == (
        Freshness.MATCHES,
        0,
        0,
        1.0,
    )
    with pytest.raises((AttributeError, TypeError, ValueError)):
        status.age_ms = 5


def test_no_map_freshness_spelling_survives_anywhere_in_server():
    """AC11 / R2.5. Internal posture: the type is broken and every caller and
    every test is migrated in the same change. A shim left behind is the dual
    path the posture forbids, and it is also how the old name reappears in the
    next ticket's diff."""
    offenders = [
        str(path.relative_to(SERVER_ROOT))
        for path in _server_sources()
        # This module itself names the dead spelling, in the docstring above
        # and in this test's own name. Scanning for the string would make the
        # test permanently red for the prose that records the removal.
        if path != Path(__file__).resolve() and "map_freshness" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_no_reader_hands_back_a_bare_freshness_attribute(session, workspace, refresh_repo):
    """The other half of R2.5: the rename has to reach the value object, not
    only the function. `MapRead.freshness` surviving beside `MapRead.status`
    is two ways to ask one question, and they will disagree."""
    build_symbol_map(session, workspace.id, refresh_repo)

    read = definitions_for(session, workspace.id, refresh_repo, "alpha")

    assert not hasattr(read, "freshness")
    assert read.status.freshness is Freshness.MATCHES


# ==========================================================================
# R3 — the migration
# ==========================================================================

_PARTITION_TABLE = "symbol_map_partitions"


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def test_the_migration_adds_refresh_started_at_to_the_partition(migrated_engine):
    """R3.1. The one new column this ticket adds. `indexed_sha` is already a
    column on `SymbolMapFile` — checked in the tree, which is what closed the
    open question in the ui-design artifact."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        assert "refresh_started_at" not in _columns(migrated_engine, _PARTITION_TABLE)

    with migrated_engine.begin() as conn:
        refresh_migration.m_symbol_map_refresh(conn)

    assert "refresh_started_at" in _columns(migrated_engine, _PARTITION_TABLE)


def test_running_the_refresh_migration_twice_is_a_no_op(migrated_engine):
    """R3.1. Append-only and self-guarding: a second run on a database that
    already has the column must not raise."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        refresh_migration.m_symbol_map_refresh(conn)
    before = _columns(migrated_engine, _PARTITION_TABLE)

    with migrated_engine.begin() as conn:
        refresh_migration.m_symbol_map_refresh(conn)

    assert _columns(migrated_engine, _PARTITION_TABLE) == before


def test_the_model_and_the_migration_agree_on_the_column(migrated_engine):
    """R3.2. A model column the migration never builds exists in every test
    database and in no live one — and the code reading it is then broken in
    production only."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        refresh_migration.m_symbol_map_refresh(conn)

    assert "refresh_started_at" in SymbolMapPartition.model_fields
    assert SymbolMapPartition.model_fields["refresh_started_at"].default is None
    assert "refresh_started_at" in _columns(migrated_engine, _PARTITION_TABLE)


def test_the_refresh_migration_is_registered_after_the_symbol_map_one():
    """An id that runs before the tables exist fails on a fresh database."""
    ids = [mid for mid, _ in MIGRATIONS]

    assert "20261118_symbol_map_refresh" in ids
    assert ids.index("20261118_symbol_map_refresh") > ids.index("20261117_symbol_map")


# ==========================================================================
# R4 — one row-minting loop
# ==========================================================================


def test_exactly_one_place_in_the_package_mints_each_kind_of_row():
    """R4.2. Two loops is the shape where a later extractor change reaches the
    build and misses the refresh — and nothing fails, because both still
    produce rows."""
    sources = {module.name: module.read_text(encoding="utf-8") for module in _package_modules()}

    assert sum(text.count("SymbolMapFile(") for text in sources.values()) == 1
    assert sum(text.count("SymbolMapSymbol(") for text in sources.values()) == 1


def test_the_extraction_helper_is_public_so_the_refresh_reuses_it():
    """R4. `refresh.py` copying `_extraction_for` is the same two-loops bug
    one level down: the copy is what stops reading the file the same way."""
    assert callable(store_module.extraction_for)
    assert not hasattr(store_module, "_extraction_for")


# ==========================================================================
# R5 / AC1-AC4, AC9 — the refresh itself
# ==========================================================================


def test_a_refresh_replaces_only_the_changed_files(session, workspace, refresh_repo):
    """R5.1 / AC1, AC3. One edit, one delete, one brand-new untracked module,
    and one file nobody touched. Every clause of the contract in one pass."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    (refresh_repo / "pkg/gamma.py").unlink()
    _write(refresh_repo, "pkg/epsilon.py", "def epsilon():\n    return 5\n")
    _commit(refresh_repo, "feat: edit, delete, add")

    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    files = _file_rows(session, partition.id)
    shape = _symbol_shape(session, partition.id)

    assert report.ok is True
    assert report.reason is None
    # The edited file: new rows only, at their new lines.
    assert ("pkg/beta.py", "beta", 4, "") in shape
    assert ("pkg/beta.py", "beta_helper", 8, "") in shape
    assert ("pkg/beta.py", "beta", 1, "") not in shape
    # The deleted file: no file row, no symbol row.
    assert "pkg/gamma.py" not in files
    assert not [row for row in shape if row[0] == "pkg/gamma.py"]
    # The new file: present.
    assert ("pkg/epsilon.py", "epsilon", 1, "") in shape
    # The sha advanced to the tree that was actually read.
    assert partition.head_sha == _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    assert report.files_deleted == 1


def test_an_unchanged_files_row_id_survives_a_refresh(session, workspace, refresh_repo):
    """AC1's pin, and the only assertion that proves the refresh was
    INCREMENTAL rather than a full rebuild wearing the right return value.

    A full rebuild passes every other assertion in this module: the rows are
    right, the sha is right, the counts are right. The id is the one thing a
    rebuild cannot preserve, because `build_symbol_map` mints a fresh uuid4
    per written file row.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    partition_id = _partition(session, workspace.id, refresh_repo).id
    before = {path: row.id for path, row in _file_rows(session, partition_id).items()}
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: touch exactly one file")

    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    after = {path: row.id for path, row in _file_rows(session, partition_id).items()}
    assert after["pkg/alpha.py"] == before["pkg/alpha.py"]
    assert after["pkg/gamma.py"] == before["pkg/gamma.py"]
    assert after["web/delta.ts"] == before["web/delta.ts"]
    assert after["pkg/beta.py"] != before["pkg/beta.py"], "the changed file IS replaced"
    assert report.files_reanalyzed == 1


def test_nothing_changed_advances_the_sha_and_touches_no_file_row(session, workspace, refresh_repo):
    """AC3's second half, and AC12's generation rule in its shipped spelling.
    A commit that touches no source still moves HEAD; re-minting every row for
    it is the per-worktree whole-repository rebuild this ticket must not ship.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    partition_id = _partition(session, workspace.id, refresh_repo).id
    before_ids = {path: row.id for path, row in _file_rows(session, partition_id).items()}
    before_built_at = _partition(session, workspace.id, refresh_repo).built_at
    _git(refresh_repo, "commit", "-q", "--allow-empty", "-m", "chore: an empty commit")

    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    assert report.ok is True
    assert report.files_reanalyzed == 0
    assert report.files_deleted == 0
    assert partition.head_sha == _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    assert partition.built_at >= before_built_at
    assert {p: r.id for p, r in _file_rows(session, partition_id).items()} == before_ids
    assert map_status(session, workspace.id, refresh_repo).freshness is Freshness.MATCHES


def test_a_failure_between_the_delete_and_the_insert_leaves_the_old_rows(
    isolated_db, session, workspace, refresh_repo
):
    """AC2 / R5.2. The per-file replace is one transaction.

    Read back on a SECOND connection, so the assertion is about what was
    COMMITTED and not about what this session happens to be holding
    uncommitted.

    **Where the failure is injected, and why it has to be there.** The target
    is `store.uuid4`, which `apply_extractions` calls once, as the FIRST
    statement of the insert loop: after `_drop_paths` has issued both deletes
    and after the `session.flush()` that follows them, and before any new row
    exists. That is the only window AC2 names, and a target outside it proves
    nothing — an earlier one leaves "the old rows survived" trivially true
    because nothing was ever deleted.

    This test previously patched `store.SymbolMapSymbol`, which looks like the
    row-minting site and is not: `_drop_paths` reads the same module global in
    `delete(SymbolMapSymbol)`, so the patch raised `ArgumentError` out of
    SQLAlchemy BEFORE a single DML statement reached SQLite. Measured: with a
    `session.commit()` inserted after the `_drop_paths` call — the exact
    non-atomicity this criterion forbids — the old test stayed green. With
    `uuid4` as the target it goes red on "the OLD file row must survive".

    The caller's session must also survive: a bookkeeping write that shares a
    Session has to leave that Session usable on failure, or the recorder's
    error becomes the outcome of the work it was only supposed to describe.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    partition_id = _partition(session, workspace.id, refresh_repo).id
    before = _symbol_shape(session, partition_id)
    before_file_ids = {path: row.id for path, row in _file_rows(session, partition_id).items()}
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: the write that will fail")

    def exploding(*args, **kwargs):
        raise RuntimeError("injected between the delete and the insert")

    with mock.patch.object(store_module, "uuid4", side_effect=exploding):
        report = refresh_at_run_entry(session, workspace.id, refresh_repo)

    assert report.ok is False
    assert report.reason is RefreshFailure.UNEXPECTED
    with Session(isolated_db) as committed:
        rows = _file_rows(committed, partition_id)
        # The file row the refresh was replacing, and its id, both survived:
        # the delete was rolled back rather than committed on its own.
        assert "pkg/beta.py" in rows, "the OLD file row must survive"
        assert {path: row.id for path, row in rows.items()} == before_file_ids
        assert _symbol_shape(committed, partition_id) == before
        assert [
            row
            for row in _symbol_rows(committed, partition_id)
            if row.file_id == rows["pkg/beta.py"].id
        ], "never a file row with zero symbols"
    # The session the dispatch is still going to use is usable.
    assert map_status(session, workspace.id, refresh_repo).freshness is not None


def test_extraction_runs_with_no_open_transaction(session, workspace, refresh_repo):
    """AC4 / R5.3. SQLite serializes writers: a write transaction held across
    the analysis pass blocks every unrelated orchestration write for as long
    as parsing takes, which on this repository is seconds.

    The probe reaches `store.extraction_for` through the module attribute, so
    `refresh.py` must call it the same way — a `from store import
    extraction_for` would bind the function at import and this probe would
    observe nothing, which is why the assertion below also demands the probe
    actually fired.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _write(refresh_repo, "pkg/alpha.py", "def alpha():\n    return 11\n")
    _commit(refresh_repo, "feat: two files to parse")
    real = store_module.extraction_for
    observed: list[bool] = []

    def probing(repo_root, path):
        observed.append(session.in_transaction())
        return real(repo_root, path)

    with mock.patch.object(store_module, "extraction_for", side_effect=probing):
        refresh_symbol_map(session, workspace.id, refresh_repo)

    assert len(observed) >= 2, "the probe never fired — the seam was bypassed"
    assert observed == [False] * len(observed)


def test_no_partition_at_all_builds_rather_than_erroring(session, workspace, refresh_repo):
    """AC6 / R5.4. `symbol_map_partitions` holds zero rows in production, so
    this is the first thing that happens on every checkout, not an edge."""
    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    assert report.ok is True
    assert report.cold is True
    assert _file_rows(session, _partition(session, workspace.id, refresh_repo).id)
    assert map_status(session, workspace.id, refresh_repo).freshness is Freshness.MATCHES


def test_a_stored_sha_this_checkout_cannot_resolve_rebuilds(session, workspace, refresh_repo):
    """AC6 / R5.4. BASE_UNUSABLE is a full rebuild, never an exception and
    never an empty changed-set — the latter would leave the map describing a
    tree nobody can name while reporting MATCHES."""
    build_symbol_map(session, workspace.id, refresh_repo)
    partition = _partition(session, workspace.id, refresh_repo)
    partition.head_sha = "0" * 40
    session.add(partition)
    session.commit()
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: a tree the garbage sha cannot describe")

    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    assert report.ok is True
    assert partition.head_sha == _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    assert ("pkg/beta.py", "beta_helper", 8, "") in _symbol_shape(session, partition.id)


def test_a_git_failure_leaves_the_stored_sha_alone(session, workspace, refresh_repo):
    """AC9 / R5.6. The whole point of the three-valued delta. Re-stamping the
    sha here converts a recoverable staleness into a confident lie, and the
    grep fallback the consumers are designed around never fires again."""
    build_symbol_map(session, workspace.id, refresh_repo)
    stored = _partition(session, workspace.id, refresh_repo).head_sha
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: a change the refresh will not see")
    failed = ChangedPaths(
        outcome=ChangedPathsOutcome.GIT_FAILED, changed=frozenset(), deleted=frozenset()
    )

    with mock.patch.object(sources_module, "changed_paths", return_value=failed):
        report = refresh_at_run_entry(session, workspace.id, refresh_repo)

    session.expire_all()
    partition = _partition(session, workspace.id, refresh_repo)
    assert report.ok is False
    assert report.reason is RefreshFailure.GIT_FAILED
    assert partition.head_sha == stored
    assert partition.refresh_started_at is None, "a failed refresh must not pin BUILDING"
    assert map_status(session, workspace.id, refresh_repo).freshness is Freshness.SHA_MISMATCH


@pytest.mark.parametrize(
    "expected",
    [
        RefreshFailure.GIT_FAILED,
        RefreshFailure.UNBORN_HEAD,
        RefreshFailure.INTERPRETER_TOO_OLD,
        RefreshFailure.UNEXPECTED,
    ],
    ids=lambda member: member.value,
)
def test_every_failure_shape_returns_a_report_and_raises_nothing(
    session, workspace, refresh_repo, unborn_repo, expected
):
    """AC9 / R5.5. A refresh must never fail a run: a stale or missing map
    degrades consumers to live grep, which is the designed fallback and
    strictly better than blocking a stage.

    `refresh_at_run_entry` is the only place in this package a broad catch is
    allowed, and it is not silent — the failure is a value on the report, and
    the reason is a closed enum rather than an exception class name or a
    traceback string that no consumer can branch on.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: work for the refresh to fail at")
    root = refresh_repo
    failed = ChangedPaths(
        outcome=ChangedPathsOutcome.GIT_FAILED, changed=frozenset(), deleted=frozenset()
    )
    injectors = {
        RefreshFailure.GIT_FAILED: lambda: mock.patch.object(
            sources_module, "changed_paths", return_value=failed
        ),
        RefreshFailure.INTERPRETER_TOO_OLD: lambda: mock.patch.object(
            sys, "version_info", (3, 9, 18, "final", 0)
        ),
        RefreshFailure.UNEXPECTED: lambda: mock.patch.object(
            store_module, "extraction_for", side_effect=RuntimeError("boom")
        ),
        RefreshFailure.UNBORN_HEAD: lambda: mock.patch.object(
            sources_module, "head_sha", side_effect=sources_module.UnbornHeadError("no commit")
        ),
    }
    if expected is RefreshFailure.UNBORN_HEAD:
        root = unborn_repo

    with injectors[expected]():
        report = refresh_at_run_entry(session, workspace.id, root)

    assert report.ok is False
    assert report.reason is expected
    assert report.reason in set(RefreshFailure)


def test_a_refresh_failure_is_logged_once_at_warning_and_never_below_it(
    session, workspace, refresh_repo, caplog
):
    """AC9. `debug` and `info` are below the default handler level, so a
    failure reported there is reported nowhere. Once, because a per-file
    warning on a cold repository is 3,479 lines in a run log.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: work")

    with (
        caplog.at_level(logging.DEBUG, logger=refresh_module.__name__),
        mock.patch.object(store_module, "extraction_for", side_effect=RuntimeError("boom")),
    ):
        report = refresh_at_run_entry(session, workspace.id, refresh_repo)

    records = [r for r in caplog.records if r.name == refresh_module.__name__]
    assert report.ok is False
    assert [r.levelno for r in records] == [logging.WARNING]


def test_refresh_symbol_map_raises_so_the_testable_core_stays_testable(
    session, workspace, refresh_repo
):
    """R5. The split is the point: a single function that both does the work
    and swallows every failure cannot be tested for the failures it swallows.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: work")

    with mock.patch.object(store_module, "extraction_for", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            refresh_symbol_map(session, workspace.id, refresh_repo)


def test_the_report_carries_the_numbers_the_run_log_line_needs(session, workspace, refresh_repo):
    """The `RefreshReport` is 284's deliverable; `lg-code-knowledge-2731` owns
    what is printed from it. Pinned here so 2731 is not blocked on a field
    that was never added."""
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: one file")

    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    assert report.files_reanalyzed == 1
    assert report.files_deleted == 0
    assert report.elapsed_ms >= 0
    assert report.head_sha == _git(refresh_repo, "rev-parse", "HEAD").stdout.strip()
    assert Path(report.checkout_root) == Path(refresh_repo).resolve()
    assert report.cold is False
    assert report.seeded is False
    assert isinstance(refresh_module.COLD_BUILD_BUDGET_MS, int)  # py-org: allow-isinstance


def test_the_report_is_frozen():
    """Following `GateRunResult`: a result a consumer can edit is a result two
    consumers will disagree about."""
    report = RefreshReport(
        ok=True,
        reason=None,
        files_reanalyzed=0,
        files_deleted=0,
        files_behind=0,
        elapsed_ms=0,
        head_sha="abc",
        checkout_root="/tmp",
        cold=False,
        seeded=False,
    )

    with pytest.raises((AttributeError, TypeError, ValueError)):
        report.ok = False


def test_a_refresh_creates_no_tracked_file_changes(session, workspace, refresh_repo):
    """AC8. Asserted on the tmp_path repository this suite built, never on
    `settings.repo_root` — which is legitimately dirty during an
    orchestration, and where this assertion is a test that gets deleted rather
    than satisfied (`lg-code-knowledge-2730` is open on exactly that mistake).
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _commit(refresh_repo, "feat: a committed change, so the tree starts clean")
    assert read_tree(refresh_repo).dirty_paths == frozenset()

    refresh_symbol_map(session, workspace.id, refresh_repo)

    assert read_tree(refresh_repo).dirty_paths == frozenset()


# ==========================================================================
# AC5 — two concurrent refreshes of the SAME partition
# ==========================================================================


def test_two_concurrent_refreshes_of_the_same_partition_both_complete(
    isolated_db, session, workspace, other_workspace, refresh_repo
):
    """AC5, in its hard form.

    Two Sessions on two CONNECTIONS against the file-backed engine
    (`tests/conftest.py` builds a real WAL pool, explicitly not StaticPool:
    StaticPool hands every thread the same DBAPI connection, so the two would
    share one transaction and the test would prove nothing). Two threads, both
    refreshing the SAME partition — two different partitions is the easy case
    and must not be the one the test uses.

    The expected row set is produced by one clean full build in a bystander
    workspace against the same tree, so "both committed" is checked against
    what the map SHOULD hold rather than against whatever the pair produced.
    """
    build_symbol_map(session, workspace.id, refresh_repo)
    workspace_id = workspace.id
    _write(refresh_repo, "pkg/beta.py", BETA_REWRITTEN)
    _write(refresh_repo, "pkg/alpha.py", "def alpha():\n    return 11\n")
    _commit(refresh_repo, "feat: work for both threads")
    build_symbol_map(session, other_workspace.id, refresh_repo)
    expected = _symbol_shape(session, _partition(session, other_workspace.id, refresh_repo).id)
    session.commit()
    failures: list[BaseException] = []

    def refresh() -> None:
        try:
            with Session(isolated_db) as own:
                refresh_symbol_map(own, workspace_id, refresh_repo)
        except BaseException as exc:  # noqa: BLE001 - re-raised below as the assertion
            failures.append(exc)

    threads = [threading.Thread(target=refresh, name=f"refresh-{n}") for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert [thread.is_alive() for thread in threads] == [False, False]
    assert failures == [], [repr(exc) for exc in failures]
    session.expire_all()
    partition = _partition(session, workspace_id, refresh_repo)
    assert _symbol_shape(session, partition.id) == expected
    assert partition.refresh_started_at is None


# ==========================================================================
# R6 — cold checkouts seed from an ancestor
# ==========================================================================


def test_a_cold_checkout_seeds_from_an_ancestor_and_reanalyzes_only_the_delta(
    session, workspace, ancestor_checkouts
):
    """R6.1. The cold path is the DOMINANT one, not the exception:
    `symbol_map_partitions` holds zero rows in production against ~94 live
    worktrees, worktrees are cut per ticket, and a cold full build measured
    7.9 s on this repository. Without seeding, the common path is the slow
    path and this ticket's purpose is unmet where it matters most.
    """
    primary, cold, moved = ancestor_checkouts
    build_symbol_map(session, workspace.id, primary)
    parsed: list[str] = []
    real = store_module.extraction_for

    def recording(repo_root, path):
        parsed.append(path)
        return real(repo_root, path)

    with mock.patch.object(store_module, "extraction_for", side_effect=recording):
        report = refresh_symbol_map(session, workspace.id, cold)

    session.expire_all()
    assert report.ok is True
    assert report.seeded is True
    assert set(parsed) == set(moved), "an unchanged file was re-parsed"
    assert map_status(session, workspace.id, cold).freshness is Freshness.MATCHES
    shape = _symbol_shape(session, _partition(session, workspace.id, cold).id)
    assert ("pkg/beta.py", "beta_helper", 8, "") in shape
    assert ("pkg/unchanged.py", "unchanged", 1, "") in shape


def test_no_ancestor_partition_falls_back_to_a_full_build(session, workspace, refresh_repo):
    """R6.2. Seeding is an optimisation on top of R5.4, never a replacement
    for it: the very first checkout of a workspace has no ancestor by
    definition."""
    report = refresh_symbol_map(session, workspace.id, refresh_repo)

    session.expire_all()
    assert report.ok is True
    assert report.seeded is False
    assert report.cold is True
    assert _symbol_shape(session, _partition(session, workspace.id, refresh_repo).id)


def test_a_seeded_partition_is_this_checkouts_and_leaves_the_ancestor_untouched(
    session, workspace, ancestor_checkouts
):
    """R6.3. A copy that inherited the ancestor's identity or root would be
    the same partition under two names — and the retirement sweep would then
    delete a live checkout's map when the ancestor's directory went away."""
    primary, cold, _moved = ancestor_checkouts
    build_symbol_map(session, workspace.id, primary)
    ancestor = _partition(session, workspace.id, primary)
    ancestor_before = (ancestor.id, ancestor.head_sha, ancestor.checkout_root)
    ancestor_shape = _symbol_shape(session, ancestor.id)

    refresh_symbol_map(session, workspace.id, cold)

    session.expire_all()
    seeded = _partition(session, workspace.id, cold)
    ancestor = _partition(session, workspace.id, primary)
    assert seeded.id != ancestor.id
    assert seeded.checkout_identity == identify(cold) != identify(primary)
    assert Path(seeded.checkout_root) == Path(cold).resolve()
    assert (ancestor.id, ancestor.head_sha, ancestor.checkout_root) == ancestor_before
    assert _symbol_shape(session, ancestor.id) == ancestor_shape


# ==========================================================================
# AC7 — the per-checkout keying, proved
# ==========================================================================


def test_two_checkouts_at_different_shas_never_share_a_map(session, workspace, worktree_pair):
    """AC7. The keying was decided and implemented by 281 and priced by 481 v4
    section 4 (~98 live worktrees x 18.78 MB measured, about 1.8 GB on a
    5.56 GB database); what remains here is the proof.

    The fixture's two checkouts hold the same module two lines apart, so the
    failure this pins is not "B is empty" but "B answers with A's line
    numbers" — a map that is confidently wrong about a line, which is strictly
    worse than no map.
    """
    primary, linked = worktree_pair
    refresh_symbol_map(session, workspace.id, primary)

    status = map_status(session, workspace.id, linked)
    read = definitions_for(session, workspace.id, linked, "Widget")
    outline = outline_for(session, workspace.id, linked, "pkg/widget.py")

    assert status.freshness is Freshness.NO_PARTITION
    assert list(read.rows) == []
    assert list(outline.rows) == []
    primary_lines = {
        row.line for row in definitions_for(session, workspace.id, primary, "Widget").rows
    }
    assert primary_lines, "the primary really was mapped"
    assert not primary_lines & {row.line for row in read.rows}


# ==========================================================================
# R8 / AC12 — the mtime absence, proved rather than asserted
# ==========================================================================


def test_the_package_reads_no_mtime_anywhere():
    """AC12 / R8.1. The surveyed engine's indexer tests mtime plus size while
    its reader tests size only, because a fresh worktree checkout restamps
    every mtime and would otherwise read as fully unindexed. We run many
    worktrees off one checkout, so an mtime-based freshness check at run entry
    would report a whole-repository rebuild every time a worktree is created.

    Our oracle is git shas plus `checkout_identity`, and we read mtime nowhere
    on either side — so the trap is structurally unreachable rather than
    merely avoided. This is the test that keeps it that way, because the
    natural optimisation a later ticket reaches for is `stat().st_mtime`.
    """
    offenders = [
        module.name
        for module in _package_modules()
        if "st_mtime" in module.read_text(encoding="utf-8")
        or "getmtime" in module.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_a_second_checkout_at_the_same_commit_refreshes_zero_files(
    session, workspace, same_commit_checkouts
):
    """AC12 / R8.2. The mtime trap, proved rather than claimed in a comment.

    `git worktree add` writes every file fresh, so every mtime in the second
    checkout is newer than every mtime in the first. An mtime oracle reports
    the whole tree as changed here. The honest answer is zero.
    """
    first, second = same_commit_checkouts
    build_symbol_map(session, workspace.id, first)
    parsed: list[str] = []
    real = store_module.extraction_for

    def recording(repo_root, path):
        parsed.append(path)
        return real(repo_root, path)

    with mock.patch.object(store_module, "extraction_for", side_effect=recording):
        report = refresh_symbol_map(session, workspace.id, second)

    assert report.ok is True
    assert report.files_reanalyzed == 0
    assert parsed == []
    assert map_status(session, workspace.id, second).freshness is Freshness.MATCHES


def test_the_refresh_module_records_why_mtime_is_absent():
    """AC12. The paragraph is a deliverable, not a note: it goes where the
    later ticket's author is standing when they reach for `stat().st_mtime`.
    A documented absence with no home decays into no absence at all.
    """
    docstring = refresh_module.__doc__ or ""

    assert "mtime" in docstring
    assert "worktree" in docstring


def test_no_content_hash_column_is_added_here():
    """AC12's last clause. Git is already the content oracle, and
    `content_hash` is reserved for `lg-code-knowledge-368`; adding it here
    would be a second oracle nobody chose between."""
    assert "content_hash" not in SymbolMapFile.model_fields
    assert "content_hash" not in SymbolMapPartition.model_fields
