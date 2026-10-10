"""Following a push: what a held command reports reaches the lease, the line and the board.

- the held command is told where to write progress, and what it writes is on
  the lease — including the last report, written just before it exited;
- a malformed report is said, not swallowed, and does not stop the run;
- a waiter repeats its place in line while nothing changes, naming what is
  ahead and how far it has got, instead of falling silent behind a long suite;
- the board carries each holder's progress and how long it has held;
- the pre-push pytest plugin counts tests into the file, with and without xdist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.db.versions.capacity_lease_progress import m_capacity_lease_progress
from loregarden.models.domain import (
    CapacityPool,
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import docker_leases
from loregarden.services.capacity_progress import (
    PROGRESS_ENV,
    LeaseProgress,
    ProgressSync,
    describe_progress,
    format_seconds,
)
from loregarden.services.capacity_run import CapacityRequest, acquire, run_holding
from loregarden.services.docker_board import capacity_status
from loregarden.services.docker_ledger import load_pool
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    """A 4-cpu / 8 GB host. Pinned in settings too: a waiter's own reap pass
    re-derives the ceiling, and would otherwise measure this machine."""
    with (
        mock.patch.object(settings, "host_capacity_cpus", 4.0),
        mock.patch.object(settings, "host_capacity_memory_mb", 8192),
        mock.patch.object(settings, "host_capacity_max_leases", 4),
    ):
        yield _pin_host(session)


def _pin_host(session):
    row = load_pool(session, CapacityPool.HOST)
    row.ceiling_cpus = 4.0
    row.ceiling_memory_mb = 8192
    row.ceiling_leases = 4
    row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(row)
    session.commit()
    return row


def _request(**overrides) -> CapacityRequest:
    fields = {"label": "pytest", "cpus": 2.0, "memory_mb": 2048, "stall_seconds": 600.0}
    fields.update(overrides)
    return CapacityRequest(**fields)


def _write_report(step: str, done: int, total: int) -> str:
    """A held command that reports once and exits — the last report before exit."""
    body = json.dumps({"step": step, "done": done, "total": total})
    return (
        f"import os, pathlib; p = pathlib.Path(os.environ[{PROGRESS_ENV!r}]); "
        f"t = p.with_name('x.tmp'); t.write_text({body!r}); os.replace(t, p)"
    )


def test_the_held_commands_last_report_is_on_the_lease(isolated_db, session, host) -> None:
    code = run_holding(
        lambda: Session(isolated_db),
        _request(),
        [sys.executable, "-c", _write_report("pytest (5/5)", 412, 1830)],
        report=lambda _line: None,
    )

    assert code == 0
    (lease,) = session.exec(select(DockerLease)).all()
    assert (lease.progress_step, lease.progress_done, lease.progress_total) == (
        "pytest (5/5)",
        412,
        1830,
    )
    assert lease.progress_at is not None


def test_the_progress_file_is_removed_after_the_run(isolated_db, session, host, tmp_path) -> None:
    seen = tmp_path / "path"
    run_holding(
        lambda: Session(isolated_db),
        _request(),
        ["sh", "-c", f'printf %s "${PROGRESS_ENV}" > {seen}'],
        report=lambda _line: None,
    )

    named = Path(seen.read_text())
    assert named.name == "progress.json"
    assert not named.parent.exists()


def test_a_malformed_report_is_said_and_the_lease_keeps_the_last_good_one(
    isolated_db, session, host, tmp_path
) -> None:
    claim = docker_leases.reserve(
        session,
        holder_label="p",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=512,
        pool=CapacityPool.HOST,
    )
    progress = tmp_path / "progress.json"
    lines: list[str] = []
    sync = ProgressSync(lambda: Session(isolated_db), claim.lease_id, progress, lines.append)

    progress.write_text(json.dumps({"step": "ruff check (1/5)"}))
    sync.sync()
    progress.write_text("{not json")
    os.utime(progress, ns=(1, 1))  # a new mtime even on a coarse clock
    sync.sync()
    sync.sync()  # the same bad write is reported once, not every tick

    session.expire_all()
    lease = session.get(DockerLease, claim.lease_id)
    assert lease.progress_step == "ruff check (1/5)"
    assert len([line for line in lines if "malformed progress" in line]) == 1


def test_no_report_yet_leaves_the_lease_without_progress(isolated_db, session, host, tmp_path):
    claim = docker_leases.reserve(
        session,
        holder_label="p",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=512,
        pool=CapacityPool.HOST,
    )
    lines: list[str] = []
    ProgressSync(
        lambda: Session(isolated_db), claim.lease_id, tmp_path / "progress.json", lines.append
    ).sync()

    session.expire_all()
    assert session.get(DockerLease, claim.lease_id).progress_step == ""
    assert lines == []


def test_a_report_with_no_step_is_rejected() -> None:
    with pytest.raises(ValueError):
        LeaseProgress.model_validate_json('{"step": "", "done": 3}')


@pytest.mark.parametrize(
    ("step", "done", "total", "expected"),
    [
        ("", None, None, ""),
        ("tsc -b (2/3)", None, None, "tsc -b (2/3)"),
        ("pytest", 412, 1830, "pytest · 412/1830 (22%)"),
        ("pytest", 7, None, "pytest · 7 done"),
        ("pytest", 9, 0, "pytest · 9 done"),
        ("pytest", 5, 4, "pytest · 5/4 (100%)"),  # a late collection never reads over 100
    ],
)
def test_describe_progress(step, done, total, expected) -> None:
    assert describe_progress(step, done, total) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0, "0s"), (59.9, "59s"), (245, "4m05s"), (4320, "1h12m")]
)
def test_format_seconds(seconds, expected) -> None:
    assert format_seconds(seconds) == expected


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_waiter_repeats_its_place_and_names_what_is_ahead(session, host) -> None:
    holder = docker_leases.reserve(
        session,
        holder_label="pre-push server-tests · other-branch",
        footprint=DockerFootprint.CUSTOM,
        cpus=4.0,
        memory_mb=4096,
        pool=CapacityPool.HOST,
    )
    row = session.get(DockerLease, holder.lease_id)
    row.progress_step, row.progress_done, row.progress_total = "pytest (5/5)", 412, 1830
    session.add(row)
    session.commit()
    clock = _Clock()
    sleeps = 0
    lines: list[str] = []

    def sleep(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        clock.now += seconds
        if clock.now >= 400:  # the suite ahead finishes after a few quiet minutes
            docker_leases.release_lease(session, holder.lease_id)

    acquire(session, _request(), report=lines.append, sleep=sleep, clock=clock)

    places = [line for line in lines if "waiting for host capacity" in line]
    # Nothing about the line changed for minutes, and it was still said every minute.
    assert len(places) >= 5
    assert sleeps >= len(places)
    ahead = [line for line in lines if "other-branch" in line]
    assert ahead and "412/1830" in ahead[0]


def test_the_board_shows_how_far_a_holder_has_got(session, host) -> None:
    holder = docker_leases.reserve(
        session,
        holder_label="pre-push client-tests · b",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=512,
        pool=CapacityPool.HOST,
    )
    row = session.get(DockerLease, holder.lease_id)
    row.progress_step, row.progress_done, row.progress_total = "jest (3/3)", 38, 412
    row.progress_at = utcnow()
    row.granted_at = utcnow() - timedelta(seconds=125)
    session.add(row)
    session.commit()

    (payload,) = capacity_status(session, measure_if_unknown=False)["holders"]

    assert payload["progress"]["summary"] == "jest (3/3) · 38/412 (9%)"
    assert (payload["progress"]["done"], payload["progress"]["total"]) == (38, 412)
    assert 124 <= payload["held_seconds"] <= 130


def test_a_holder_that_never_reported_has_no_progress(session, host) -> None:
    docker_leases.reserve(
        session,
        holder_label="x",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=512,
        pool=CapacityPool.HOST,
    )
    (payload,) = capacity_status(session, measure_if_unknown=False)["holders"]
    assert payload["progress"] is None


def test_the_migration_adds_the_columns_and_reruns_cleanly(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE docker_leases (id TEXT PRIMARY KEY)"))
        conn.execute(text("INSERT INTO docker_leases (id) VALUES ('a')"))
        m_capacity_lease_progress(conn)
        m_capacity_lease_progress(conn)
        row = conn.execute(text("SELECT * FROM docker_leases")).mappings().one()
    assert row["progress_step"] == ""
    assert (row["progress_done"], row["progress_total"], row["progress_at"]) == (None, None, None)


# ---- the pre-push pytest plugin -------------------------------------------


@pytest.fixture(name="tiny_suite")
def tiny_suite_fixture(tmp_path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('i', range(6))\n"
        "def test_ok(i):\n    assert i >= 0\n\n"
        "def test_fails():\n    assert False\n"
    )
    return suite


@pytest.mark.parametrize("workers", ["0", "2"])
def test_the_pytest_plugin_counts_every_test(tiny_suite, tmp_path, workers) -> None:
    progress = tmp_path / "progress.json"
    env = {
        **os.environ,
        PROGRESS_ENV: str(progress),
        "PUSH_PROGRESS_STEP": "pytest on 1 file(s) (5/5)",
        "PYTHONPATH": str(SCRIPTS),
    }
    env.pop("PYTEST_XDIST_WORKER", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_push_progress", "-n", workers,
         "-p", "no:cacheprovider", str(tiny_suite)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip

    assert result.returncode == 1, result.stdout + result.stderr  # the plugin keeps the verdict
    assert json.loads(progress.read_text()) == {
        "step": "pytest on 1 file(s) (5/5)",
        "done": 7,
        "total": 7,
    }


def test_the_pytest_plugin_is_inert_without_a_progress_file(tiny_suite, tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if k != PROGRESS_ENV}
    env["PYTHONPATH"] = str(SCRIPTS)
    with mock.patch.dict(os.environ, env, clear=True):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "pytest_push_progress",
             "-p", "no:cacheprovider", "-k", "ok", str(tiny_suite)],
            cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
        )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.glob("*.json")) == []
