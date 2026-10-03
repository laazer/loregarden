"""`loregarden capacity run`: hold capacity for exactly as long as a command runs.

The properties a pre-push hook depends on:

- the command's exit status is the caller's, and the lease is released after it
  whatever that status was — including when the process is told to stop;
- a full machine means waiting in line, and a wait that runs out gives the place
  back rather than leaving a waiter wedged at the head of the queue;
- capacity that cannot be had raises *before* the command starts, so a wrapper
  can tell "the ledger failed" from "the tests failed";
- two of these at once on a machine with room for one run one after the other.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest
from loregarden.cli import capacity as capacity_cli
from loregarden.cli.errors import UsageError
from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases, host_capacity
from loregarden.services.capacity_run import (
    WORKERS_ENV,
    CapacityNotGranted,
    CapacityRequest,
    acquire,
    run_holding,
)
from loregarden.services.docker_ledger import load_pool
from sqlalchemy import create_engine
from sqlmodel import Session, select

SERVER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    """A 4-cpu / 8 GB host, written to the pool row."""
    row = load_pool(session, CapacityPool.HOST)
    row.ceiling_cpus = 4.0
    row.ceiling_memory_mb = 8192
    row.ceiling_leases = 4
    row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(row)
    session.commit()
    return row


def _request(**overrides) -> CapacityRequest:
    fields = {"label": "pytest", "cpus": 2.0, "memory_mb": 2048, "max_wait_seconds": 60.0}
    fields.update(overrides)
    return CapacityRequest(**fields)


def _leases(session) -> list[DockerLease]:
    session.expire_all()
    return list(session.exec(select(DockerLease)))


def test_the_command_runs_under_a_lease_and_its_status_is_passed_through(
    isolated_db, session, host, tmp_path
) -> None:
    seen = tmp_path / "workers"
    started = tmp_path / "started"

    code = run_holding(
        lambda: Session(isolated_db),
        _request(),
        ["sh", "-c", f'printf %s "${WORKERS_ENV}" > {seen}; exit 7'],
        report=lambda _line: None,
        started_file=started,
    )

    assert code == 7
    assert started.exists()
    assert seen.read_text() == "2"
    (lease,) = _leases(session)
    assert lease.status is DockerLeaseStatus.RELEASED
    assert lease.end_reason is DockerLeaseEndReason.RELEASED
    assert lease.pool is CapacityPool.HOST
    assert (load_pool(session, CapacityPool.HOST).held_cpus, lease.holder_pid) == (0, os.getpid())


def test_the_command_runs_where_the_caller_stood(isolated_db, host, tmp_path) -> None:
    """A hook's `bash .lefthook/scripts/x.sh` is relative to the repo, not the server dir."""
    caller = tmp_path / "repo"
    (caller / "scripts").mkdir(parents=True)
    (caller / "scripts" / "check.sh").write_text("exit 5\n")

    code = run_holding(
        lambda: Session(isolated_db),
        _request(),
        ["sh", "scripts/check.sh"],
        report=lambda _line: None,
        cwd=caller,
    )

    assert code == 5


def test_the_cli_runs_the_command_in_the_callers_directory(tmp_path) -> None:
    """`scripts/loregarden-cli.sh` cds into server/ for uv; the held command must not follow it."""
    caller = tmp_path / "repo"
    caller.mkdir()
    args = argparse.Namespace(
        held_command=["--", "true"],
        footprint="light",
        cpus=0.0,
        memory_mb=0,
        label="pytest",
        pool=CapacityPool.HOST.value,
        max_wait=1.0,
        ttl=None,
        workspace=None,
        parent_lease="",
        started_file="started",
    )

    with (
        mock.patch.dict(os.environ, {capacity_cli.CALLER_CWD_ENV: str(caller)}),
        mock.patch.object(capacity_cli.db_session, "init_db"),
        mock.patch.object(capacity_cli, "run_holding", return_value=0) as held,
        pytest.raises(SystemExit),
    ):
        capacity_cli._run(args)

    assert held.call_args.kwargs["cwd"] == caller
    assert held.call_args.kwargs["started_file"] == caller / "started"


def test_the_cli_refuses_a_caller_directory_that_is_gone(tmp_path) -> None:
    with mock.patch.dict(os.environ, {capacity_cli.CALLER_CWD_ENV: str(tmp_path / "gone")}):
        with pytest.raises(UsageError):
            capacity_cli._caller_cwd()


def test_a_full_machine_means_waiting_then_running(session, host) -> None:
    holder = docker_leases.reserve(
        session,
        holder_label="other push",
        footprint=DockerFootprint.CUSTOM,
        cpus=4.0,
        memory_mb=4096,
        pool=CapacityPool.HOST,
    )
    lines: list[str] = []

    def sleep(_seconds: float) -> None:
        # The other push finishes while this one waits.
        docker_leases.release_lease(session, holder.lease_id)

    reservation = acquire(session, _request(), report=lines.append, sleep=sleep)

    assert reservation.granted
    assert any("waiting for host capacity" in line for line in lines)
    lease = session.get(DockerLease, reservation.lease_id)
    assert lease.status is DockerLeaseStatus.HELD
    assert lease.last_polled_at is not None  # what keeps it out of the abandonment sweep


def test_a_wait_that_runs_out_gives_its_place_back(session, host) -> None:
    docker_leases.reserve(
        session,
        holder_label="other push",
        footprint=DockerFootprint.CUSTOM,
        cpus=4.0,
        memory_mb=4096,
        pool=CapacityPool.HOST,
    )
    ticks = iter(range(0, 1000, 30))

    with pytest.raises(CapacityNotGranted, match="still queued"):
        acquire(
            session,
            _request(max_wait_seconds=60),
            report=lambda _line: None,
            sleep=lambda _seconds: None,
            clock=lambda: float(next(ticks)),
        )

    waiter = next(lease for lease in _leases(session) if lease.holder_label.startswith("pytest"))
    assert waiter.status is DockerLeaseStatus.RELEASED
    assert waiter.end_reason is DockerLeaseEndReason.ABANDONED


@pytest.mark.parametrize("command", [["sh", "-c", "exit 127"], ["sh", "-c", "exit 126"]])
def test_a_command_the_shell_could_not_run_is_not_recorded_as_a_hold(
    isolated_db, session, host, tmp_path, command
) -> None:
    """Every capacity-gated push once died at once with "No such file", and
    those instant clean releases dragged the wait estimate down to seconds."""
    code = run_holding(
        lambda: Session(isolated_db), _request(), command, report=lambda _line: None, cwd=tmp_path
    )

    assert code in (126, 127)
    (lease,) = _leases(session)
    assert lease.end_reason is DockerLeaseEndReason.COMMAND_NOT_RUN


def test_a_command_that_cannot_be_started_is_not_recorded_as_a_hold(
    isolated_db, session, host, tmp_path
) -> None:
    with pytest.raises(FileNotFoundError):
        run_holding(
            lambda: Session(isolated_db),
            _request(),
            [str(tmp_path / "missing")],
            report=lambda _line: None,
            cwd=tmp_path,
        )

    (lease,) = _leases(session)
    assert lease.status is DockerLeaseStatus.RELEASED
    assert lease.end_reason is DockerLeaseEndReason.COMMAND_NOT_RUN


def test_an_unmeasurable_host_refuses_before_the_command_starts(
    isolated_db, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "host_capacity_cpus", 0.0)
    started = tmp_path / "started"
    ran = tmp_path / "ran"
    failed_probe = host_capacity.HostProbe(error="sysctl hw.memsize exited 1")

    with (
        mock.patch.object(host_capacity, "probe_host", return_value=failed_probe),
        pytest.raises(CapacityNotGranted, match="host_unavailable"),
    ):
        run_holding(
            lambda: Session(isolated_db),
            _request(),
            ["touch", str(ran)],
            report=lambda _line: None,
            started_file=started,
        )

    assert not started.exists()
    assert not ran.exists()


def test_a_footprint_bigger_than_the_machine_claims_the_whole_pool(session, host) -> None:
    lines: list[str] = []

    reservation = acquire(
        session,
        _request(footprint=DockerFootprint.HEAVY, cpus=0.0, memory_mb=0),
        report=lines.append,
    )

    # heavy is 4 cpus / 8192 MB; this host books 4 cpus / 8192 MB in all.
    assert reservation.granted
    assert (reservation.cpus, reservation.memory_mb) == (4.0, 8192)

    row = load_pool(session, CapacityPool.HOST)
    row.ceiling_cpus = 2.0
    session.add(row)
    session.commit()
    docker_leases.release_lease(session, reservation.lease_id)

    smaller = acquire(
        session,
        _request(label="again", footprint=DockerFootprint.HEAVY, cpus=0.0, memory_mb=0),
        report=lines.append,
    )
    assert smaller.granted
    assert smaller.cpus == 2.0
    assert any("more than this machine's whole host pool" in line for line in lines)


def test_the_lease_is_renewed_while_the_command_runs(isolated_db, session, host) -> None:
    run_holding(
        lambda: Session(isolated_db),
        _request(),
        [sys.executable, "-c", "import time; time.sleep(1.0)"],
        report=lambda _line: None,
        heartbeat_seconds=0.2,
    )

    (lease,) = _leases(session)
    assert lease.last_renewed_at is not None and lease.granted_at is not None
    assert lease.last_renewed_at > lease.granted_at


# ---- the real CLI, as a hook runs it ------------------------------------


def _cli_env(database: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("LOREGARDEN_")}
    env.update(
        {
            "LOREGARDEN_DATABASE_URL": f"sqlite:///{database}",
            "LOREGARDEN_HOST_CAPACITY_CPUS": "2",
            "LOREGARDEN_HOST_CAPACITY_MEMORY_MB": "4096",
            "LOREGARDEN_DOCKER_POLL_MIN_INTERVAL_SECONDS": "1",
        }
    )
    return env


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "loregarden.cli.main", "capacity", "run", *args]


def _wait_for_status(database: Path, status: DockerLeaseStatus, *, timeout: float = 60.0) -> None:
    engine = create_engine(f"sqlite:///{database}")
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if database.exists():
                with Session(engine) as session:
                    try:
                        statuses = [lease.status for lease in session.exec(select(DockerLease))]
                    except Exception:  # noqa: BLE001 — table not created yet; poll again
                        statuses = []
                if status in statuses:
                    return
            time.sleep(0.2)
    finally:
        engine.dispose()
    raise AssertionError(f"no lease reached {status} within {timeout}s")


def test_two_runs_with_room_for_one_do_not_overlap(tmp_path) -> None:
    database = tmp_path / "ledger.db"
    env = _cli_env(database)
    stamp = (
        "import sys, time; open(sys.argv[1], 'w').write(str(time.time())); "
        "time.sleep(1.5); open(sys.argv[2], 'w').write(str(time.time()))"
    )

    def run(name: str) -> subprocess.Popen:
        return subprocess.Popen(
            _cli(
                "--label",
                name,
                "--cpus",
                "2",
                "--memory-mb",
                "1024",
                "--",
                sys.executable,
                "-c",
                stamp,
                str(tmp_path / f"{name}.start"),
                str(tmp_path / f"{name}.end"),
            ),
            cwd=SERVER_ROOT,
            env=env,
        )

    first = run("first")
    _wait_for_status(database, DockerLeaseStatus.HELD)
    second = run("second")
    assert first.wait(timeout=120) == 0
    assert second.wait(timeout=120) == 0

    first_end = float((tmp_path / "first.end").read_text())
    second_start = float((tmp_path / "second.start").read_text())
    assert second_start >= first_end


def test_sigterm_stops_the_command_and_releases_the_lease(tmp_path) -> None:
    database = tmp_path / "ledger.db"
    started = tmp_path / "started"
    process = subprocess.Popen(
        _cli(
            "--label",
            "stopped",
            "--cpus",
            "1",
            "--memory-mb",
            "512",
            "--started-file",
            str(started),
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
        ),
        cwd=SERVER_ROOT,
        env=_cli_env(database),
    )
    _wait_for_status(database, DockerLeaseStatus.HELD)
    deadline = time.monotonic() + 30
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.1)

    process.send_signal(signal.SIGTERM)

    assert process.wait(timeout=60) == 128 + signal.SIGTERM
    engine = create_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            (lease,) = session.exec(select(DockerLease)).all()
            assert lease.status is DockerLeaseStatus.RELEASED
    finally:
        engine.dispose()
