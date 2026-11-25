"""CI's server suite: split across runners, gathered into one check, not repeated on main."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
import tomllib
import yaml
from loregarden.services.merge_queue_failures import GATHER_STEP
from tests.repo_paths import REPO_ROOT

SCRIPTS = REPO_ROOT / ".github" / "scripts"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shards = _load("pytest_shard")
tested = _load("tested_tree")


@pytest.fixture(name="jobs", scope="module")
def jobs_fixture() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _step(job: dict, name: str) -> dict:
    [step] = [step for step in job["steps"] if step.get("name") == name]
    return step


# -- shards ---------------------------------------------------------------------


@pytest.mark.parametrize("of", [1, 3, 4])
def test_every_test_file_is_in_exactly_one_shard(of):
    files = shards.test_files(REPO_ROOT / "server")
    dealt = [f for n in range(1, of + 1) for f in shards.shard_of(files, n, of)]
    assert sorted(dealt) == files
    assert "tests/test_ci_server_suite.py" in files


def test_the_shards_cover_what_pytest_collects(tmp_path):
    """Asked of pytest itself, over every name shape it accepts or refuses."""
    tests = tmp_path / "tests" / "nested"
    tests.mkdir(parents=True)
    for name in ("test_a.py", "b_test.py", "helper.py", "conftest.py", "test_c.txt"):
        (tests / name).write_text("def test_it():\n    pass\n")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    collected = {line.split("::")[0] for line in out.splitlines() if "::" in line}
    assert shards.test_files(tmp_path) == sorted(collected)
    assert len(collected) == 2


def test_the_server_suite_collects_by_pytests_default_names():
    with (REPO_ROOT / "server" / "pyproject.toml").open("rb") as fh:
        options = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    assert options["testpaths"] == ["tests"]
    assert "python_files" not in options


def _shard_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "pytest_shard.py"), *args], capture_output=True, text=True
    )


def test_the_script_prints_its_shards_files():
    printed = _shard_script("--shard", "2", "--of", "4").stdout.split()
    assert printed == shards.shard_of(shards.test_files(REPO_ROOT / "server"), 2, 4)


@pytest.mark.parametrize("shard", ["0", "5", "-1"])
def test_a_shard_outside_the_range_is_refused(shard):
    assert _shard_script("--shard", shard, "--of", "4").returncode == 2


def test_an_empty_shard_is_an_error_not_the_whole_suite(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_only.py").write_text("")
    result = _shard_script("--shard", "2", "--of", "2", "--server", str(tmp_path))
    assert result.returncode == 1 and result.stdout == ""


# -- the workflow ---------------------------------------------------------------


def test_the_matrix_runs_every_shard_and_one_failing_cancels_none(jobs):
    strategy = jobs["server-tests"]["strategy"]
    matrix = strategy["matrix"]["shard"]
    assert matrix == list(range(1, len(matrix) + 1))
    assert strategy["fail-fast"] is False
    run = _step(jobs["server-tests"], "Pytest")["run"]
    assert "--shard ${{ matrix.shard }} --of ${{ strategy.job-total }}" in run


def test_each_shard_uploads_a_profile_under_its_own_name(jobs):
    upload = _step(jobs["server-tests"], "Upload the server suite's profile")
    assert "${{ matrix.shard }}" in upload["with"]["name"]


def test_only_the_tests_are_skipped_on_a_tested_tree(jobs):
    """Lint and the client still run on main: they keep its dependency caches, which
    pull requests read, and only the main branch's caches reach every pull request."""
    skipping = {name for name, job in jobs.items() if "reused" in str(job.get("if", ""))}
    assert skipping == {"server-tests"}
    assert jobs["already-tested"]["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )


def test_the_tests_run_when_nothing_says_the_tree_was_tested(jobs):
    # A skipped or failed need skips this job unless its `if` has a status function.
    condition = jobs["server-tests"]["if"]
    assert "!cancelled()" in condition
    assert "needs.already-tested.outputs.reused != 'true'" in condition


def test_the_required_check_runs_even_when_the_run_is_cancelled(jobs):
    """GitHub counts a skipped required check as passed."""
    assert jobs["server"]["if"] == "always()"
    assert jobs["server"]["needs"] == ["already-tested", "server-lint", "server-tests"]


def test_the_gathering_jobs_are_the_ones_the_queue_knows_by_their_step(jobs):
    gathering = {
        name
        for name, job in jobs.items()
        if any(step.get("name") == GATHER_STEP for step in job["steps"])
    }
    assert gathering == {"server", "ci"}
    # Nothing else in them can fail, or the queue would read it as gathered too.
    assert all(len(jobs[name]["steps"]) == 1 for name in gathering)


@pytest.mark.parametrize(
    ("lint", "tests", "reused", "passes"),
    [
        ("success", "success", "", True),
        ("success", "success", "false", True),
        ("success", "skipped", "true", True),
        ("success", "skipped", "", False),
        ("success", "skipped", "false", False),
        ("success", "failure", "", False),
        ("success", "failure", "true", False),
        ("success", "cancelled", "", False),
        ("success", "", "", False),
        ("failure", "success", "", False),
        ("failure", "skipped", "true", False),
        ("skipped", "success", "", False),
        ("cancelled", "cancelled", "", False),
    ],
)
def test_the_required_check_passes_only_on_a_passed_or_reused_suite(
    jobs, lint, tests, reused, passes
):
    step = _step(jobs["server"], GATHER_STEP)
    assert set(step["env"]) == {"LINT", "TESTS", "REUSED"}
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**os.environ, "LINT": lint, "TESTS": tests, "REUSED": reused},
        capture_output=True,
    )
    assert (result.returncode == 0) is passes


# -- already tested -------------------------------------------------------------

SHA = "a" * 40
HEAD = "b" * 40


class GitHub:
    def __init__(self) -> None:
        self.pulls: list[dict] = [self.pull()]
        self.trees = {SHA: "t1", HEAD: "t1"}
        self.checks: list[dict] = [self.check()]
        self.asked: list[str] = []
        #: How many reads of the commit's pulls come back empty first.
        self.late = 0

    @staticmethod
    def pull(**fields) -> dict:
        return {
            "number": 7,
            "merged_at": "2026-11-25T09:31:02Z",
            "merge_commit_sha": SHA,
            "head": {"sha": HEAD},
            **fields,
        }

    @staticmethod
    def check(conclusion: str | None = "success", app: str = "github-actions") -> dict:
        return {"conclusion": conclusion, "app": {"slug": app}}

    def read(self, path: str):
        self.asked.append(path)
        if path.endswith("/pulls"):
            self.late -= 1
            return [] if self.late >= 0 else self.pulls
        if "/git/commits/" in path:
            return {"tree": {"sha": self.trees[path.rsplit("/", 1)[-1]]}}
        assert path.startswith(f"repos/o/r/commits/{HEAD}/check-runs?")
        return {"check_runs": self.checks}

    def answer(self) -> tuple[bool, str]:
        return tested.already_tested("o/r", SHA, self.read, sleep=lambda _s: None)


def test_a_landed_tree_that_passed_is_not_tested_again():
    github = GitHub()
    reused, why = github.answer()
    assert reused and "#7" in why
    assert "check_name=Server%20%28Python%29" in github.asked[-1]


def test_a_landing_github_links_late_is_asked_about_again():
    github = GitHub()
    github.late = tested.ATTEMPTS - 1
    assert github.answer()[0]
    github.late = tested.ATTEMPTS
    assert not github.answer()[0]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"pulls": []}, id="pushed-with-no-pull-request"),
        pytest.param({"pulls": [GitHub.pull(), GitHub.pull(number=8)]}, id="two-pull-requests"),
        pytest.param({"pulls": [GitHub.pull(merged_at=None)]}, id="not-merged"),
        pytest.param({"pulls": [GitHub.pull(merge_commit_sha=HEAD)]}, id="merged-as-another"),
        pytest.param({"trees": {SHA: "t2", HEAD: "t1"}}, id="another-tree"),
        pytest.param({"checks": []}, id="suite-never-ran"),
        pytest.param({"checks": [GitHub.check("failure")]}, id="suite-failed"),
        pytest.param({"checks": [GitHub.check("skipped")]}, id="suite-skipped"),
        pytest.param({"checks": [GitHub.check(None)]}, id="suite-running"),
        pytest.param({"checks": [GitHub.check(app="other")]}, id="another-apps-check"),
        pytest.param({"checks": [GitHub.check(), GitHub.check("failure")]}, id="one-of-two"),
        pytest.param({"checks": [{"app": {}}]}, id="unexpected-shape"),
        pytest.param({"pulls": [GitHub.pull(head=None)]}, id="deleted-head"),
    ],
)
def test_anything_else_runs_the_suite(change):
    github = GitHub()
    vars(github).update(change)
    reused, why = github.answer()
    assert not reused and why


@pytest.mark.parametrize(
    "gh",
    [
        pytest.param(
            {"return_value": subprocess.CompletedProcess([], 1, "", "HTTP 403")}, id="refused"
        ),
        # gh prints the body of a refusal too, and it is JSON.
        pytest.param(
            {"return_value": subprocess.CompletedProcess([], 1, "[]", "HTTP 404")}, id="with-a-body"
        ),
        pytest.param(
            {"return_value": subprocess.CompletedProcess([], 0, "<html>", "")}, id="not-json"
        ),
        pytest.param({"side_effect": OSError("no gh")}, id="no-gh"),
        pytest.param({"side_effect": subprocess.TimeoutExpired("gh", 60)}, id="no-answer"),
    ],
)
def test_github_not_answering_runs_the_suite_and_says_so(gh):
    with mock.patch.object(tested.subprocess, "run", **gh):
        reused, why = tested.already_tested("o/r", SHA, tested.read_api)
    assert not reused and "gh api" in why


@pytest.mark.parametrize("reused", [True, False])
def test_the_answer_reaches_the_workflow(tmp_path, capsys, reused):
    output: Path = tmp_path / "out"
    argv = ["tested_tree.py", "--repo", "o/r", "--sha", SHA, "--output", str(output)]
    with (
        mock.patch.object(tested, "already_tested", return_value=(reused, "because")),
        mock.patch.object(sys, "argv", argv),
    ):
        assert tested.main() == 0
    assert output.read_text() == f"reused={str(reused).lower()}\n"
    assert "because" in capsys.readouterr().out
