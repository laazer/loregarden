"""Machine → Disk's Docker cleanup: what it would free, what it keeps, what it runs.

Docker is never spawned: `run_docker_reclaim` is replaced by a fake that
answers `system df` from a fixture and records every command, so each test
proves which argv would have reached the daemon.
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    DiskCleanupEvent,
    DiskCleanupMethod,
    DiskCleanupOutcome,
    DiskKind,
)
from loregarden.services import docker_subprocess
from loregarden.services.disk_usage import docker_reclaim
from loregarden.services.disk_usage.docker_reclaim import (
    DockerOutputUnreadable,
    DockerReclaimRequest,
    DockerUnavailable,
    parse_size,
)
from loregarden.services.docker_subprocess import DockerVerbRefused, assert_disk_reclaim
from sqlmodel import Session, select

ANON_A = "a" * 64
ANON_B = "b" * 64
ANON_ATTACHED = "c" * 64

DF = {
    "Images": [
        {"Containers": "0", "UniqueSize": "1.5GB", "Repository": "old"},
        {"Containers": "2", "UniqueSize": "459MB", "Repository": "pgvector/pgvector"},
        {"Containers": "N/A", "UniqueSize": "10MB", "Repository": "unknown"},
    ],
    "Containers": [],
    "Volumes": [
        {"Name": ANON_A, "Links": "0", "Size": "2GB"},
        {"Name": ANON_B, "Links": "0", "Size": "500MB"},
        {"Name": ANON_ATTACHED, "Links": "1", "Size": "9GB"},
        {"Name": "server_kairos_postgres_data", "Links": "1", "Size": "64.85MB"},
        {"Name": "old_stack_db", "Links": "0", "Size": "300MB"},
        {"Name": "d" * 64, "Links": "N/A", "Size": "1GB"},
    ],
    "BuildCache": [
        {"InUse": "false", "Size": "3.2GB"},
        {"InUse": "true", "Size": "1GB"},
    ],
}


def _done(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class FakeDocker:
    def __init__(self, df: dict | None = None) -> None:
        self.df = df or DF
        self.calls: list[tuple[str, ...]] = []
        self.volume_rm = lambda names: _done("\n".join(names) + "\n")
        self.prune = _done("Deleted Images:\nuntagged: x\n\nTotal reclaimed space: 1.2GB\n")

    def __call__(self, args, *, timeout):
        assert_disk_reclaim(args)  # the fake refuses what the real one refuses
        tokens = tuple(args)
        self.calls.append(tokens)
        if tokens[:2] == ("system", "df"):
            return _done(json.dumps(self.df))
        if tokens[:2] == ("volume", "rm"):
            return self.volume_rm(list(tokens[2:]))
        return self.prune


@pytest.fixture(name="docker")
def docker_fixture():
    fake = FakeDocker()
    with mock.patch.object(docker_reclaim, "run_docker_reclaim", fake):
        yield fake


# --- the chokepoint -------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["system", "df", "-v", "--format", "{{json .}}"],
        ["image", "prune", "-a", "-f"],
        ["builder", "prune", "-a", "-f"],
        ["volume", "rm", ANON_A, ANON_B],
    ],
)
def test_the_cleanup_commands_are_allowed(args) -> None:
    assert_disk_reclaim(args)


@pytest.mark.parametrize(
    "args",
    [
        ["system", "prune", "-a", "-f", "--volumes"],
        ["image", "prune", "-a", "-f", "--filter", "until=1h"],
        ["volume", "prune", "-f"],
        ["volume", "rm", "server_kairos_postgres_data"],
        ["volume", "rm", "-f", ANON_A],
        ["volume", "rm"],
        ["container", "rm", "kairos-postgres"],
        ["rm", "-f", "x"],
        [],
    ],
)
def test_anything_else_is_refused(args) -> None:
    with pytest.raises(DockerVerbRefused):
        assert_disk_reclaim(args)


def test_run_docker_reclaim_refuses_before_spawning_anything() -> None:
    with mock.patch("subprocess.run") as spawn, pytest.raises(DockerVerbRefused):
        docker_subprocess.run_docker_reclaim(["system", "prune", "-f"], timeout=5)
    spawn.assert_not_called()


def test_the_ledger_entry_point_stays_read_only() -> None:
    with pytest.raises(DockerVerbRefused):
        docker_subprocess.assert_read_only(["image", "prune", "-a", "-f"])


# --- sizes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0B", 0),
        ("63B", 63),
        ("1.5kB", 1500),
        ("292.6MB", 292_600_000),
        ("18.95GB", 18_950_000_000),
    ],
)
def test_docker_sizes_are_decimal(text, expected) -> None:
    assert parse_size(text) == expected


def test_an_unreadable_size_raises_rather_than_counting_as_zero() -> None:
    with pytest.raises(DockerOutputUnreadable):
        parse_size("lots")


# --- preview ----------------------------------------------------------------------


def test_preview_counts_only_what_nothing_uses(docker) -> None:
    found = docker_reclaim.preview()
    assert (found.unused_images, found.unused_images_bytes) == (1, 1_500_000_000)
    assert (found.build_cache_entries, found.build_cache_bytes) == (1, 3_200_000_000)
    assert [v.name for v in found.volumes] == [ANON_A, ANON_B]
    assert found.volumes_bytes == 2_500_000_000
    assert found.total_bytes == 1_500_000_000 + 3_200_000_000 + 2_500_000_000


def test_a_named_unattached_volume_is_kept_and_listed(docker) -> None:
    found = docker_reclaim.preview()
    assert [v.name for v in found.kept_named_volumes] == ["old_stack_db"]
    assert "old_stack_db" not in {v.name for v in found.volumes}


def test_a_volume_whose_links_are_unknown_counts_as_attached(docker) -> None:
    names = {v.name for v in docker_reclaim.preview().volumes}
    assert "d" * 64 not in names
    assert ANON_ATTACHED not in names


def test_no_docker_binary_is_unavailable_not_empty() -> None:
    with (
        mock.patch.object(docker_reclaim, "run_docker_reclaim", side_effect=FileNotFoundError),
        pytest.raises(DockerUnavailable, match="not installed"),
    ):
        docker_reclaim.preview()


def test_a_dead_daemon_is_unavailable_with_its_reason() -> None:
    dead = _done("", 1, "Cannot connect to the Docker daemon")
    with (
        mock.patch.object(docker_reclaim, "run_docker_reclaim", return_value=dead),
        pytest.raises(DockerUnavailable, match="Cannot connect"),
    ):
        docker_reclaim.preview()


# --- reclaim ----------------------------------------------------------------------


def test_reclaim_runs_only_what_was_confirmed_and_records_it(docker, isolated_db) -> None:
    with Session(isolated_db) as session:
        result = docker_reclaim.reclaim(
            session, DockerReclaimRequest(images=True, volumes=[ANON_A])
        )
        events = session.exec(select(DiskCleanupEvent)).all()

    writes = [c for c in docker.calls if c[:2] != ("system", "df")]
    assert writes == [("volume", "rm", ANON_A), ("image", "prune", "-a", "-f")]
    assert result.reclaimed_bytes == 2_000_000_000 + 1_200_000_000
    assert result.failed_count == 0
    assert {e.kind for e in events} == {DiskKind.DOCKER}
    assert {e.method for e in events} == {DiskCleanupMethod.DOCKER_PRUNE}
    assert {e.outcome for e in events} == {DiskCleanupOutcome.DELETED}


def test_a_volume_attached_since_the_preview_is_not_asked_for(docker, isolated_db) -> None:
    with Session(isolated_db) as session:
        result = docker_reclaim.reclaim(
            session, DockerReclaimRequest(volumes=[ANON_ATTACHED, ANON_B])
        )
    assert ("volume", "rm", ANON_B) in docker.calls
    assert not any(ANON_ATTACHED in call for call in docker.calls)
    assert [s.outcome for s in result.steps] == [
        DiskCleanupOutcome.REFUSED,
        DiskCleanupOutcome.DELETED,
    ]


def test_a_partly_failed_volume_rm_reports_what_stayed(docker, isolated_db) -> None:
    docker.volume_rm = lambda names: _done(names[0] + "\n", 1, "volume is in use")
    with Session(isolated_db) as session:
        result = docker_reclaim.reclaim(session, DockerReclaimRequest(volumes=[ANON_A, ANON_B]))
    (step,) = result.steps
    assert step.reclaimed_bytes == 2_000_000_000
    assert "1 not removed" in step.detail
    assert "volume is in use" in step.detail


def test_a_failed_prune_is_a_failure_not_zero_bytes(docker, isolated_db) -> None:
    docker.prune = _done("", 1, "daemon gone")
    with Session(isolated_db) as session:
        result = docker_reclaim.reclaim(session, DockerReclaimRequest(build_cache=True))
    assert result.failed_count == 1
    assert result.steps[0].outcome is DiskCleanupOutcome.FAILED
    assert result.steps[0].detail == "daemon gone"


def test_reclaim_with_nothing_chosen_is_rejected(docker, isolated_db) -> None:
    with Session(isolated_db) as session, pytest.raises(ValueError):
        docker_reclaim.reclaim(session, DockerReclaimRequest())
    assert docker.calls == []


def test_the_api_says_docker_is_unavailable_with_503(client) -> None:
    with mock.patch.object(docker_reclaim, "run_docker_reclaim", side_effect=FileNotFoundError):
        response = client.get("/api/machine/disk/docker")
    assert response.status_code == 503
    assert "not installed" in response.json()["detail"]
