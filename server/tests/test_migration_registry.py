"""Versioned migrations: ids parallel branches cannot collide on, ordered by `after`.

The numbered list made the second of two parallel branches renumber, and a
renumbered migration runs twice. These cover the chain that replaces it, and the
two places it touches the real build: the registry `migrations.py` loads, and
what `origin/main` ships.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.db import migrations as M
from loregarden.db import versions
from loregarden.db.migration_registry import (
    ChainFork,
    MigrationChainError,
    MigrationRegistry,
    VersionedMigration,
    chain_order,
    ids_declared_in_source,
    import_submodules,
)
from loregarden.db.shared_database_guard import SHIPPED_REF, shipped_migration_ids
from loregarden.models.domain import DoctorCheck, DoctorStatus, Workspace
from loregarden.services import doctor
from loregarden.services.git_subprocess import run_git
from sqlmodel import select

ROOT = "0151_ticket_criteria_checked"
REPO_ROOT = Path(os.environ.get("LOREGARDEN_REPO_ROOT") or Path(__file__).resolve().parents[2])


def _noop(conn) -> None:
    pass


def _step(migration_id: str, after: str) -> VersionedMigration:
    return VersionedMigration(migration_id, after, _noop)


def _ids(order) -> list[str]:
    return [item.id for item in order.ordered]


def test_the_chain_orders_by_after_not_by_id_or_declaration():
    """Merge order is the order: a branch written earlier but merged later runs later."""
    merged_first = _step("20261005_merged_first", ROOT)
    written_first = _step("20261001_written_first", "20261005_merged_first")
    tip = _step("20261007_tip", "20261001_written_first")

    order = chain_order(ROOT, [tip, written_first, merged_first])

    assert _ids(order) == ["20261005_merged_first", "20261001_written_first", "20261007_tip"]
    assert order.forks == ()


def test_two_branches_following_one_tip_are_a_fork_not_a_crash():
    """The merge that produces this has no textual conflict; it must still boot."""
    a = _step("20261002_branch_a", ROOT)
    b = _step("20261002_branch_b", ROOT)
    after_b = _step("20261003_after_b", "20261002_branch_b")

    order = chain_order(ROOT, [after_b, b, a])

    assert order.forks == (ChainFork(ROOT, ("20261002_branch_a", "20261002_branch_b")),)
    assert _ids(order) == ["20261002_branch_a", "20261002_branch_b", "20261003_after_b"]


def test_an_after_naming_nothing_is_rejected():
    """What a rename of a merged migration does to the one that followed it."""
    with pytest.raises(MigrationChainError, match="20261002_orphan -> 20261001_renamed_away"):
        chain_order(ROOT, [_step("20261002_orphan", "20261001_renamed_away")])


def test_an_after_into_the_middle_of_the_frozen_list_is_rejected():
    with pytest.raises(MigrationChainError, match="unknown id"):
        chain_order(ROOT, [_step("20261002_mine", "0150_workspace_archived_at")])


def test_a_cycle_is_rejected():
    with pytest.raises(MigrationChainError, match="cycle"):
        chain_order(
            ROOT,
            [
                _step("20261002_a", "20261003_b"),
                _step("20261003_b", "20261002_a"),
            ],
        )


@pytest.mark.parametrize(
    "bad_id", ["0152_numbered", "workspace_flag", "2026-10-02_flag", "20261002_Camel"]
)
def test_an_id_that_is_not_date_and_name_is_rejected(bad_id):
    with pytest.raises(MigrationChainError, match="YYYYMMDD_snake_case_name"):
        MigrationRegistry().migration(bad_id, after=ROOT)


def test_a_duplicate_id_is_rejected():
    registry = MigrationRegistry()
    registry.migration("20261002_same", after=ROOT)(_noop)

    with pytest.raises(MigrationChainError, match="Duplicate migration id '20261002_same'"):
        registry.migration("20261002_same", after="20261002_same")(_noop)


def test_the_source_reader_sees_a_decorated_id():
    source = (
        "from loregarden.db import versions\n"
        "from loregarden.db.versions import migration\n"
        '@migration("20261002_by_name", after="x")\n'
        "def a(conn): pass\n"
        '@versions.migration("20261003_by_attribute", after="20261002_by_name")\n'
        "def b(conn): pass\n"
        "MIGRATION_ID = '20261004_not_a_decorator'\n"
    )
    assert ids_declared_in_source(source) == ["20261002_by_name", "20261003_by_attribute"]


def test_the_source_reader_cannot_see_a_computed_id():
    """Why the suite checks the real package below: this one would hide from the guard."""
    source = 'ID = "20261002_x"\n@migration(ID, after="y")\ndef a(conn): pass\n'
    assert ids_declared_in_source(source) == []


def test_import_submodules_imports_every_module(tmp_path, monkeypatch):
    package = tmp_path / "fake_versions_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "one.py").write_text("LOADED = True\n")
    (package / "two.py").write_text("LOADED = True\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    import fake_versions_pkg

    assert import_submodules(fake_versions_pkg) == ["one", "two"]
    assert sys.modules["fake_versions_pkg.one"].LOADED


# --- The real build ---------------------------------------------------------------


def test_migrations_is_the_frozen_list_then_the_chain():
    tail = [(item.id, item.migrate) for item in M.VERSION_CHAIN.ordered]
    assert M.MIGRATIONS == [*M.FROZEN_MIGRATIONS, *tail]
    assert M.FROZEN_MIGRATIONS[-1][0] == ROOT


def test_the_real_chain_does_not_fork():
    """A fork means two branches merged against one tip. Point the later `after` at the earlier."""
    assert M.VERSION_CHAIN.forks == ()


def test_the_guard_reads_every_id_the_registry_holds():
    """The guard parses source; a migration it cannot see would be refused nowhere."""
    package_dir = Path(versions.__file__).parent
    declared = [
        migration_id
        for path in sorted(package_dir.glob("*.py"))
        for migration_id in ids_declared_in_source(path.read_text())
    ]

    assert sorted(declared) == sorted(item.id for item in versions.REGISTRY.registered())


def test_no_merged_id_has_been_renamed_or_removed():
    """A merged id is permanent: the live database records it, and a new name runs it again.

    Compares against where this branch left main, not main's tip, so a branch
    that is merely behind is not accused of removing what it never had. Needs
    `origin/main` and its history, so CI checks out with full depth. Failing
    when they are missing is deliberate — skipping would make the one check
    that sees a rename optional.
    """
    fork_point = run_git(
        ["merge-base", "HEAD", SHIPPED_REF],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    registered = {migration_id for migration_id, _ in M.MIGRATIONS}

    missing = sorted(shipped_migration_ids(REPO_ROOT, fork_point) - registered)

    assert not missing, (
        f"{SHIPPED_REF} shipped {missing} before this branch left it, and this build no "
        "longer registers them. Restore the ids; a merged migration is never renamed or "
        "removed."
    )


# --- The doctor ------------------------------------------------------------------


def test_the_doctor_chain_check_runs_against_the_real_chain(db_session, tmp_path: Path):
    assert doctor.CHECKS[DoctorCheck.MIGRATION_CHAIN] is doctor.check_migration_chain
    workspace = db_session.exec(select(Workspace)).first()

    finding = doctor.check_migration_chain(db_session, workspace, tmp_path)

    assert finding.check == DoctorCheck.MIGRATION_CHAIN
    assert finding.status == DoctorStatus.PASS


def test_the_doctor_warns_on_a_fork_and_names_both_sides(db_session, tmp_path: Path):
    forked = chain_order(ROOT, [_step("20261002_branch_a", ROOT), _step("20261002_branch_b", ROOT)])
    assert forked.forks  # the injected chain really is forked
    workspace = db_session.exec(select(Workspace)).first()

    with mock.patch.object(doctor, "VERSION_CHAIN", forked):
        finding = doctor.check_migration_chain(db_session, workspace, tmp_path)

    assert finding.status == DoctorStatus.WARN
    assert "20261002_branch_a, 20261002_branch_b" in finding.finding
    assert ROOT in finding.finding
