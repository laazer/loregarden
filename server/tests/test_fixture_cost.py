"""What the shared fixtures cost, pinned so it cannot creep back.

`client` once ran the full production boot for every test: crash recovery (five
or more git launches and about 1.2s on a loaded machine), build discovery,
migration and seeding. About 3,500 tests use it. Each repo copy also ran
`git update-index`. A fixture that starts launching processes again fails here,
not as a slower suite nobody traces back to it.
"""

import sqlite3
import statistics
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loregarden.testing.prod_shape import build_prod_shape
from sqlmodel import Session
from tests.repo_templates import from_template

#: Process launches `client` setup may make. None today; a step that needs one
#: belongs in a session-scoped template, not in every test's setup.
CLIENT_SETUP_LAUNCH_BUDGET = 0


@dataclass
class Launches:
    argv: list[list[str]] = field(default_factory=list)


@pytest.fixture(name="launches")
def launches_fixture(seeded_template, seeded_orchestration_profiles) -> Iterator[Launches]:
    """Record every process launched from here on, until teardown.

    Depends on the session templates so building them (once per process) is not
    charged to the test that happens to run first.
    """
    seen = Launches()
    real_init = subprocess.Popen.__init__

    def recording_init(self, args, *rest, **kwargs):
        seen.argv.append([args] if isinstance(args, str) else [str(a) for a in args])
        real_init(self, args, *rest, **kwargs)

    with patch.object(subprocess.Popen, "__init__", recording_init):
        yield seen


def test_client_setup_launches_no_processes(launches: Launches, client: TestClient) -> None:
    """`launches` is set up before `client`, so this is everything `client` setup launched."""
    assert len(launches.argv) <= CLIENT_SETUP_LAUNCH_BUDGET, launches.argv


#: Setups the repeated guard enters `client` for (lg-build-verification-2802).
REPEATED_SETUPS = 20
#: Median `client` setup wall time allowed, in seconds. Measured 2026-11-25 on a
#: 10-core Mac at load average 85: median 0.074s over 20 setups (0.028s-1.2s).
#: The full boot this replaced cost about 1.5s a setup at the same load, so the
#: budget leaves ~7x headroom for a busier machine and still fails it.
CLIENT_SETUP_MEDIAN_BUDGET_SECONDS = 0.5


def test_client_setup_stays_cheap_across_repeated_setups(
    launches: Launches, enter_client, tmp_path: Path
) -> None:
    """Twenty setups of exactly what `client` runs, each on its own seeded database.

    After the first, no setup may launch a process: crash recovery's git calls
    were the bulk of the old cost and are what a regression would bring back.
    The median wall time is held under a budget measured on a loaded machine.
    """
    seconds: list[float] = []
    launched: list[list[list[str]]] = []
    for attempt in range(REPEATED_SETUPS):
        root = tmp_path / f"setup-{attempt}"
        root.mkdir()
        with pytest.MonkeyPatch.context() as monkeypatch:
            entry = enter_client(root, monkeypatch)
            before = len(launches.argv)
            started = time.perf_counter()
            client = next(entry)
            seconds.append(time.perf_counter() - started)
            launched.append(launches.argv[before:])
            assert client.get("/health").status_code == 200
            entry.close()

    assert launched[1:] == [[]] * (REPEATED_SETUPS - 1), launched
    median = statistics.median(seconds)
    print(f"client setup median {median:.3f}s over {REPEATED_SETUPS}: {sorted(seconds)}")
    assert median < CLIENT_SETUP_MEDIAN_BUDGET_SECONDS, sorted(seconds)


def _build_one_commit_repo(root: Path) -> None:
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True, capture_output=True)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)


def test_a_template_copy_is_clean_untouched_and_still_sees_edits(
    tmp_path: Path, launches: Launches
) -> None:
    """A copy runs no git, and is still a repository git reads correctly.

    Plumbing trusts the index's stat data without re-hashing, so a copy whose
    index did not match its files would read as modified to `diff-index`, and
    its first `git status` would rewrite `.git/index`.
    """
    from_template(tmp_path / "warm", "fixture_cost_one_commit", _build_one_commit_repo)
    launches.argv.clear()
    repo = from_template(tmp_path / "copy", "fixture_cost_one_commit", _build_one_commit_repo)
    assert launches.argv == []

    index = repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    assert _git(repo, "diff-index", "--quiet", "HEAD").returncode == 0
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert index.stat().st_mtime_ns == before

    # Same size, written at once: only the mtime and content differ.
    (repo / "a.txt").write_text("z\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain").stdout == " M a.txt\n"
    assert _git(repo, "diff-index", "--quiet", "HEAD").returncode == 1


def _row_counts(database: Path) -> dict[str, int]:
    conn = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def test_the_prod_shape_template_is_what_a_fresh_build_writes(
    client: TestClient, isolated_db, prod_shape_template
) -> None:
    """`prod_shape_session` copies a scenario built once per process; it must
    hold what building the scenario over this test's seeded database writes."""
    with Session(isolated_db) as session:
        build_prod_shape(session)
    isolated_db.dispose()
    fresh = _row_counts(Path(isolated_db.url.database))
    assert fresh["tickets"] > 100, fresh
    assert _row_counts(prod_shape_template.database) == fresh
