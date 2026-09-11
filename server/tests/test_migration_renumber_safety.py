"""A renumbered migration runs twice, so every migration must survive that.

`lg-workflow-integrity-712`. Branches claim the next free number when they are
WRITTEN and main moves before they MERGE. A pre-push suite takes 13-50 minutes
here, so every branch races whatever lands during its own verification: five
collisions on 2026-09-10 alone (0116, 0117, 0118, 0122, and a near miss on 0123).

Renumbering on rebase is therefore routine, and it has a consequence nothing
guards. The old id stays in `schema_migrations` forever, the new id is not there,
so the runner executes that migration's body A SECOND TIME. The live database
carries three such orphans today, and `0120_docker_capacity_ledger` has already
run twice under two ids.

`test_migrations_are_idempotent` does NOT cover this. It asserts the RUNNER skips
ids it has already applied (`second == []`) — which is true and useful and says
nothing about whether a migration's body is safe to execute again. Under a
renumber the runner does not skip, precisely because the id is new.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.db.migration_ledger import ledger_orphans
from loregarden.db.migrations import MIGRATIONS, apply_migrations
from loregarden.models.domain import DoctorCheck, DoctorStatus, Workspace
from loregarden.services.doctor import CHECKS, check_migration_ledger
from sqlmodel import Session, SQLModel, create_engine, select, text


def _migrated_engine(tmp_path: Path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    return engine


@pytest.mark.parametrize("migration_id,migrate", MIGRATIONS, ids=[m for m, _ in MIGRATIONS])
def test_every_migration_survives_running_twice(tmp_path: Path, migration_id, migrate):
    """The assertion that makes renumbering safe rather than merely visible.

    Parametrised per migration so a failure NAMES the one that is not
    re-runnable, instead of reporting that something in a list of 120 is not.

    Invoked the way the runner invokes it — same connection handling, same
    foreign-keys pragma — so a migration that only breaks under those conditions
    is not excused by a friendlier harness.
    """
    engine = _migrated_engine(tmp_path, f"twice-{migration_id}.db")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.rollback()
        try:
            with conn.begin():
                migrate(conn)
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.rollback()


def test_a_renumbered_migration_is_reported_as_having_run_twice(tmp_path: Path):
    """The doctor check's discriminator, on the real shape.

    An applied id whose suffix matches a registered one under a different number
    is a renumber: the same migration, run again. That is a different fact from
    an id nothing registers at all, and they need different responses.
    """
    engine = _migrated_engine(tmp_path, "renumbered.db")
    registered = [mid for mid, _ in MIGRATIONS]
    real = registered[-1]
    faked_old_id = "0001_" + real.partition("_")[2]

    with Session(engine) as session:
        session.exec(
            text("INSERT INTO schema_migrations (id) VALUES (:id)").bindparams(id=faked_old_id)
        )
        session.commit()
        orphans = ledger_orphans(session, registered)

    assert [o.applied_id for o in orphans] == [faked_old_id]
    assert orphans[0].was_renumbered
    assert orphans[0].renumbered_to == real


def test_a_deleted_migration_is_not_reported_as_a_renumber(tmp_path: Path):
    """The other half. `0116_stage_timeout_budgets` is live in the real database
    and is this shape: applied, then removed from the build entirely. Nothing
    will undo what it did, which is a different problem from running twice."""
    engine = _migrated_engine(tmp_path, "deleted.db")
    registered = [mid for mid, _ in MIGRATIONS]

    with Session(engine) as session:
        session.exec(
            text("INSERT INTO schema_migrations (id) VALUES (:id)").bindparams(
                id="0001_a_migration_this_build_has_never_heard_of"
            )
        )
        session.commit()
        orphans = ledger_orphans(session, registered)

    assert len(orphans) == 1
    assert not orphans[0].was_renumbered
    assert orphans[0].renumbered_to == ""


def test_a_consistent_ledger_reports_nothing(tmp_path: Path):
    """The doctor must stay quiet on a healthy database, or it trains people to
    ignore it."""
    engine = _migrated_engine(tmp_path, "clean.db")
    with Session(engine) as session:
        assert ledger_orphans(session, [mid for mid, _ in MIGRATIONS]) == []


def test_the_doctor_check_actually_runs(db_session, tmp_path: Path):
    """Pinned because its absence already bit.

    `check_migration_ledger` was registered in CHECKS while its imports were
    missing, so it would have raised NameError the first time anything called
    it — and 200 tests passed, because nothing did. A check nobody invokes is
    indistinguishable from a check that works.
    """
    assert DoctorCheck.MIGRATION_LEDGER in CHECKS

    workspace = db_session.exec(select(Workspace)).first()
    finding = check_migration_ledger(db_session, workspace, tmp_path)

    assert finding.check == DoctorCheck.MIGRATION_LEDGER
    # A test database applies every registered migration and nothing else, so a
    # consistent ledger is the expected answer here.
    assert finding.status == DoctorStatus.PASS


def test_the_doctor_check_warns_rather_than_fails(db_session, tmp_path: Path):
    """WARN, not FAIL. Every orphan in the real database is benign — the
    migrations happen to be idempotent, which the suite now asserts — so this is
    a standing risk, and failing the doctor on it trains people to ignore the
    doctor."""
    from loregarden.models.domain import DoctorStatus, Workspace
    from loregarden.services.doctor import check_migration_ledger
    from sqlmodel import select

    db_session.exec(
        text("INSERT INTO schema_migrations (id) VALUES (:id)").bindparams(
            id="0001_long_since_renamed"
        )
    )
    db_session.commit()

    workspace = db_session.exec(select(Workspace)).first()
    finding = check_migration_ledger(db_session, workspace, tmp_path)

    assert finding.status == DoctorStatus.WARN
    assert "0001_long_since_renamed" in finding.finding
