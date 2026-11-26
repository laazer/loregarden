"""CI records each run's profiles and fails a pull request whose suite grew >20% over main.

The arithmetic (per-file seconds, the median, the threshold) is lore-eden's
`profile_delta`, tested there; these tests cover what CI decides: which runs form
main's baseline, what a reused main run records, and that the check reads it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path
from unittest import mock

import pytest
import yaml
from lore_eden.testing import profile_delta
from tests.repo_paths import REPO_ROOT

SCRIPTS = REPO_ROOT / ".github" / "scripts"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
REPO = "o/r"
REPO_ID = 7


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timing = _load("suite_timing")


def _files(scale: float = 1.0) -> dict[str, float]:
    return {f"tests/test_{n}.py": 10.0 * (n + 1) * scale for n in range(12)}


def _profile(files: dict[str, float], suite: str = "server", outcome: str = "passed") -> dict:
    return {
        "schema_version": 1,
        "suite": suite,
        "scope": {"kind": "full", "requested": [], "collected": len(files), "measured": len(files)},
        "tests": [
            {"nodeid": f"{path}::test_it", "outcome": outcome, "metrics": {"total_seconds": s}}
            for path, s in files.items()
        ],
    }


def _run(scale: float = 1.0, outcome: str = "passed", slow: float = 0.0) -> dict[str, dict]:
    """One run's `suite-profiles` artifact: two server shards and the client."""
    files = _files(scale)
    files["tests/test_0.py"] += sum(files.values()) * slow
    shard_1 = {k: v for n, (k, v) in enumerate(files.items()) if n % 2 == 0}
    shard_2 = {k: v for n, (k, v) in enumerate(files.items()) if n % 2 == 1}
    return {
        "server-1.json": _profile(shard_1, outcome=outcome),
        "server-2.json": _profile(shard_2, outcome=outcome),
        "client.json": _profile({"src/a.test.ts": 30.0 * scale}, suite="client"),
    }


def _write(directory: Path, run: dict[str, dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in run.items():
        (directory / name).write_text(json.dumps(doc))
    return directory


# -- AC2 + AC3 + AC4: main's baseline, and the check that reads it ------------------


@pytest.mark.parametrize(("slow", "exit_code"), [(0.25, 1), (0.10, 0)])
def test_a_pull_request_25_percent_slower_than_main_fails_and_10_percent_passes(
    tmp_path, capsys, slow, exit_code
):
    github = FakeGitHub()
    for n in range(12):
        github.add(timing.PROFILES_ARTIFACT, "main", _run(), run_id=100 + n)
    main_now = _write(tmp_path / "run", _run())
    baseline = tmp_path / "baseline" / timing.BASELINE_FILE
    argv = ["baseline", "--repo", REPO, "--run-id", "1", "--current", str(main_now)]
    with github.patched():
        assert timing.main([*argv, "--work", str(tmp_path / "w1"), "--out", str(baseline)]) == 0
    stored = json.loads(baseline.read_text())
    assert len(stored["suites"]["server"]["runs"]) == profile_delta.DEFAULT_RUNS
    assert stored["suites"]["server"]["runs"][0] == "run"

    github.add(timing.BASELINE_ARTIFACT, "main", {timing.BASELINE_FILE: stored})
    pull = _write(tmp_path / "pull", _run(slow=slow))
    summary = tmp_path / "summary.md"
    argv = ["check", "--repo", REPO, "--current", str(pull), "--work", str(tmp_path / "w2")]
    with github.patched():
        assert timing.main([*argv, "--summary", str(summary)]) == exit_code
    text = summary.read_text()
    assert ("❌ server:" in text) is (exit_code == 1)
    assert "| `tests/test_0.py` |" in text
    assert ("::error title=Suite time::" in capsys.readouterr().out) is (exit_code == 1)


def test_the_baseline_reads_only_main_runs_of_this_repository(tmp_path):
    """A pull request, or a fork's branch called main, cannot raise the bar."""
    github = FakeGitHub()
    github.add(timing.PROFILES_ARTIFACT, "main", _run(), run_id=1)
    github.add(timing.PROFILES_ARTIFACT, "feature", _run(9), run_id=2)
    github.add(timing.PROFILES_ARTIFACT, "main", _run(9), run_id=3, fork=True)
    github.add(timing.PROFILES_ARTIFACT, "main", _run(9), run_id=4, expired=True)
    github.add(timing.PROFILES_ARTIFACT, "main", _run(9), run_id=42)
    runs = timing.main_runs(REPO, github.read, github.fetch, tmp_path, skip_run=42, want=10)
    assert [run.name for run in runs] == ["main-1"]


def test_main_is_read_newest_first_until_every_suite_has_its_runs(tmp_path):
    github = FakeGitHub()
    for n in range(5):
        github.add(timing.PROFILES_ARTIFACT, "main", _run(), run_id=10 + n)
    runs = timing.main_runs(REPO, github.read, github.fetch, tmp_path, skip_run=0, want=2)
    assert [run.name for run in runs] == ["main-14", "main-13"]


def test_a_failed_main_run_is_read_but_not_counted(tmp_path):
    github = FakeGitHub()
    github.add(timing.PROFILES_ARTIFACT, "main", _run(), run_id=10)
    github.add(timing.PROFILES_ARTIFACT, "main", _run(outcome="failed"), run_id=11)
    runs = timing.main_runs(REPO, github.read, github.fetch, tmp_path, skip_run=0, want=1)
    assert [run.name for run in runs] == ["main-11", "main-10"]


def test_main_with_no_baseline_yet_warns_and_passes(tmp_path, capsys):
    pull = _write(tmp_path / "pull", _run(slow=0.5))
    argv = ["check", "--repo", REPO, "--current", str(pull), "--work", str(tmp_path / "w")]
    with FakeGitHub().patched():
        assert timing.main(argv) == 0
    assert "::warning title=Suite time::main has stored no baseline yet" in capsys.readouterr().out


def test_github_not_answering_fails_the_check_and_says_why(tmp_path, capsys):
    pull = _write(tmp_path / "pull", _run())

    def refuse(path: str):
        raise timing.CannotTell(f"gh api {path}: HTTP 502")

    argv = ["check", "--repo", REPO, "--current", str(pull), "--work", str(tmp_path / "w")]
    with mock.patch.object(timing, "read_api", refuse):
        assert timing.main(argv) == 1
    assert "could not read main's baseline: gh api" in capsys.readouterr().out


# -- AC1: what a run records --------------------------------------------------------


def _downloaded(root: Path, shards: int = 2) -> Path:
    """The layout download-artifact gives `pattern: profile-*`."""
    for n in range(1, shards + 1):
        shard = root / f"profile-server-{n}"
        shard.mkdir(parents=True)
        (shard / "server.json").write_text(json.dumps(_profile({f"tests/test_{n}.py": 2.0})))
    (root / "profile-client").mkdir(parents=True)
    jest = {
        "numTotalTestSuites": 1,
        "testResults": [
            {"name": "/w/client/src/a.test.ts", "status": "passed", "startTime": 0, "endTime": 3000}
        ],
    }
    (root / "profile-client" / "client-jest.json").write_text(json.dumps(jest))
    return root


def test_a_run_records_every_server_shard_and_the_client_in_one_schema(tmp_path):
    out = tmp_path / "run"
    argv = ["collect", "--profiles", str(_downloaded(tmp_path / "dl"))]
    assert timing.main([*argv, "--client-root", "/w/client", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "client.json",
        "server-1.json",
        "server-2.json",
    ]
    measured = profile_delta.measure(profile_delta.load_run(out))
    assert measured["server"].files == {"tests/test_1.py": 2.0, "tests/test_2.py": 2.0}
    assert measured["client"].files == {"src/a.test.ts": 3.0}


def test_a_run_with_no_profiles_at_all_is_an_error(tmp_path, capsys):
    (tmp_path / "dl").mkdir()
    argv = ["collect", "--profiles", str(tmp_path / "dl"), "--client-root", "/w/client"]
    assert timing.main([*argv, "--out", str(tmp_path / "run")]) == 1
    assert "recorded no profiles at all" in capsys.readouterr().out


def _landed(github: FakeGitHub) -> None:
    github.pulls = [
        {"number": 5, "head": {"sha": "head5"}, "merged_at": "x", "merge_commit_sha": "landed"}
    ]


def test_a_reused_main_run_carries_the_server_profiles_of_the_run_that_tested_its_tree(
    tmp_path, capsys
):
    github = FakeGitHub()
    _landed(github)
    github.add(timing.PROFILES_ARTIFACT, "feature", _run(2), run_id=77, head_sha="head5")
    dl = tmp_path / "dl"
    _downloaded(dl, shards=0)
    out = tmp_path / "timing" / "run"
    argv = ["collect", "--profiles", str(dl), "--client-root", "/w/client", "--out", str(out)]
    argv += ["--repo", REPO, "--sha", "landed", "--event", "push", "--ref", timing.MAIN_REF]
    with github.patched(), mock.patch.object(timing.time, "sleep"):
        assert timing.main(argv) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "client.json",
        "server-1.json",
        "server-2.json",
    ]
    measured = profile_delta.measure(profile_delta.load_run(out))["server"]
    assert measured.total_seconds == pytest.approx(sum(_files(2).values()))
    assert "carried from run 77 on #5" in capsys.readouterr().out


def test_a_reused_main_run_never_carries_a_failed_run(tmp_path):
    github = FakeGitHub()
    _landed(github)
    github.add(timing.PROFILES_ARTIFACT, "feature", _run(outcome="failed"), head_sha="head5")
    shards, why = timing.tested_server(
        REPO, "landed", github.read, github.fetch, tmp_path, sleep=lambda _s: None
    )
    assert shards == []
    assert "no complete server profiles recorded for #5" in why


# -- the 30-minute wall-time ceiling ------------------------------------------------


def _walls(root: Path, *minutes: float) -> Path:
    for n, m in enumerate(minutes, start=1):
        shard = root / f"profile-server-{n}"
        shard.mkdir(parents=True)
        (shard / timing.SERVER_WALL).write_text(json.dumps({"wall_seconds": m * 60}))
    return root


def test_shards_within_30_minutes_in_all_pass(tmp_path):
    assert timing.main(["ceiling", "--profiles", str(_walls(tmp_path, 7, 7, 7, 8.5))]) == 0


def test_one_shard_over_30_minutes_fails(tmp_path, capsys):
    assert timing.main(["ceiling", "--profiles", str(_walls(tmp_path, 31))]) == 1
    assert "profile-server-1 took 31.0 min" in capsys.readouterr().out


def test_shards_each_under_but_summed_over_30_minutes_fail(tmp_path, capsys):
    assert timing.main(["ceiling", "--profiles", str(_walls(tmp_path, 8, 8, 8, 8))]) == 1
    out = capsys.readouterr().out
    assert "the whole suite took 32.0 min over 4 shards" in out
    assert "profile-server-1 took" not in out


def test_a_shard_that_wrote_no_wall_time_fails_rather_than_passing(tmp_path, capsys):
    _walls(tmp_path, 5)
    (tmp_path / "profile-server-2").mkdir()
    assert timing.main(["ceiling", "--profiles", str(tmp_path)]) == 1
    assert f"profile-server-2 has no readable {timing.SERVER_WALL}" in capsys.readouterr().out


def test_a_run_with_no_server_shards_has_nothing_to_hold(tmp_path):
    (tmp_path / "profile-client").mkdir()
    assert timing.main(["ceiling", "--profiles", str(tmp_path)]) == 0


# -- the workflow -------------------------------------------------------------------


@pytest.fixture(name="jobs", scope="module")
def jobs_fixture() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _step(job: dict, name: str) -> dict:
    [step] = [step for step in job["steps"] if step.get("name") == name]
    return step


def test_both_suites_upload_their_timings_on_every_run(jobs):
    test = _step(jobs["client"], "Test")["run"]
    assert f"--json --outputFile=../.profile/{timing.JEST_RESULTS}" in test
    # jest writes the file but not its directory; a green suite failed CI without it.
    assert test.splitlines()[0] == "mkdir -p ../.profile"
    upload = _step(jobs["client"], "Upload the client suite's timings")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "profile-client"
    server = _step(jobs["server-tests"], "Upload the server suite's profile")
    assert server["if"] == "always()"
    assert server["with"]["path"].split() == [
        f".profile/{timing.SERVER_PROFILE}",
        f".profile/{timing.SERVER_WALL}",
    ]
    pytest_step = _step(jobs["server-tests"], "Pytest")["run"]
    assert f"> ../.profile/{timing.SERVER_WALL}" in pytest_step
    assert pytest_step.rstrip().endswith("exit $code")


def test_the_named_check_records_every_run_and_compares_only_pull_requests(jobs):
    job = jobs["suite-timing"]
    assert job["name"] == "Suite time (vs main)"
    assert job["needs"] == ["server-tests", "client"]
    assert job["if"] == "${{ !cancelled() }}"
    assert "if" not in _step(job, "Collect this run's profiles")
    assert _step(job, "Upload this run's profiles")["with"]["name"] == timing.PROFILES_ARTIFACT
    main_push = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert _step(job, "Store main's baseline")["if"] == main_push
    stored = _step(job, "Upload main's baseline")
    assert stored["if"] == main_push
    assert stored["with"]["name"] == timing.BASELINE_ARTIFACT
    assert stored["with"]["path"].endswith(timing.BASELINE_FILE)
    assert _step(job, "Compare with main's baseline")["if"] == "github.event_name == 'pull_request'"
    assert job["permissions"]["actions"] == "read"
    ceiling = _step(job, "Server wall time (30-minute ceiling)")
    assert ceiling["if"] == "${{ !cancelled() }}"
    assert "suite_timing.py ceiling" in ceiling["run"]


# -- a fake GitHub ------------------------------------------------------------------


def _zipped(members: dict[str, dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, doc in members.items():
            archive.writestr(name, json.dumps(doc))
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self) -> None:
        self.listed: dict[str, list[dict]] = {}
        self.blobs: dict[int, bytes] = {}
        self.pulls: list[dict] = []
        self.next_id = 1000

    def add(  # noqa: PLR0913 - one fake artifact's every distinguishing field
        self,
        name: str,
        branch: str,
        members: dict[str, dict],
        run_id: int | None = None,
        head_sha: str = "",
        fork: bool = False,
        expired: bool = False,
    ) -> None:
        """Added oldest first; GitHub lists them back newest first."""
        self.next_id += 1
        self.blobs[self.next_id] = _zipped(members)
        self.listed.setdefault(name, []).append(
            {
                "id": self.next_id,
                "expired": expired,
                "created_at": f"2026-11-01T00:{self.next_id - 1000:02d}:00Z",
                "workflow_run": {
                    "id": run_id or self.next_id,
                    "repository_id": REPO_ID,
                    "head_repository_id": REPO_ID + 1 if fork else REPO_ID,
                    "head_branch": branch,
                    "head_sha": head_sha,
                },
            }
        )

    def read(self, path: str):
        if path.startswith(f"repos/{REPO}/commits/"):
            return self.pulls
        name = path.split("name=", 1)[1].split("&", 1)[0]
        page = int(path.rsplit("page=", 1)[1])
        listed = list(reversed(self.listed.get(name, [])))
        return {"artifacts": listed[(page - 1) * 100 : page * 100]}

    def fetch(self, repo: str, artifact_id: int) -> bytes:
        assert repo == REPO
        return self.blobs[artifact_id]

    def patched(self):
        return mock.patch.multiple(timing, read_api=self.read, fetch_zip=self.fetch)
