"""The hooks run in the order their comments promise.

Both orders were claims nobody had run. pre-commit's checks were one parallel
`commands` map with format-staged at `priority: 100`, meant to run first;
lefthook orders by priority only when a hook is not parallel, so every check
raced the formatter and an import order `ruff check --fix` would have sorted
failed the commit. pre-push said signed-commits ran "first and cheap", but
lefthook runs a `commands` map in name order, so it ran after both suites.
Both are `jobs` lists now, which run as written. A list alone was still not
enough for pre-push: without `piped`, lefthook runs every job after a failed one.
"""

from __future__ import annotations

import pytest
import yaml
from loregarden.testing.lefthook_config import hook_jobs
from tests.repo_paths import REPO_ROOT

# A registry check: runs on pre-commit (.lefthook/scripts/registry-checks.sh).
pytestmark = pytest.mark.registry

LEFTHOOK = REPO_ROOT / "lefthook.yml"


def _hook(name: str) -> dict:
    return yaml.safe_load(LEFTHOOK.read_text(encoding="utf-8"))[name]


def _precommit() -> dict:
    return _hook("pre-commit")


def test_the_formatter_runs_alone_before_any_check():
    section = _precommit()
    first, *rest = section["jobs"]

    # Sequential at the top: the formatter finishes and restages first.
    assert not section.get("parallel")
    assert first["name"] == "format-staged"
    assert first["stage_fixed"] is True
    # Everything after it is one parallel group, so the checks still overlap.
    assert [job["group"]["parallel"] for job in rest] == [True]


def test_no_job_relies_on_priority_for_ordering():
    assert all("priority" not in job for job in hook_jobs(LEFTHOOK, "pre-commit").values())


def test_every_check_sees_formatted_files():
    jobs = list(hook_jobs(LEFTHOOK, "pre-commit"))

    assert jobs[0] == "format-staged"
    assert {"py-review", "py-organization", "client-lint", "registry-checks"} <= set(jobs[1:])


def test_pre_push_refuses_an_unsigned_commit_before_the_suites_run():
    section = _hook("pre-push")

    # Sequential, and a list: a `commands` map would run in name order.
    assert section["parallel"] is False
    assert "commands" not in section
    # A failed job ends the push. Measured on lefthook 2.1.10: without it the
    # jobs after a failed one all run, queueing for capacity on the way.
    assert section["piped"] is True
    assert [job["name"] for job in section["jobs"]] == [
        "signed-commits",
        "server-prerequisites",
        "client-prerequisites",
        "server-tests",
        "client-tests",
    ]


def test_nothing_reserves_capacity_before_the_prerequisites_have_passed():
    jobs = list(hook_jobs(LEFTHOOK, "pre-push").values())
    reserving = [i for i, job in enumerate(jobs) if "capacity-run.sh" in job["run"]]
    checks = [i for i, job in enumerate(jobs) if "push-prerequisites.sh" in job["run"]]

    assert len(reserving) == len(checks) == 2
    assert max(checks) < min(reserving)


@pytest.mark.parametrize("suite", ["server", "client"])
def test_a_prerequisite_check_runs_exactly_when_its_suite_does(suite):
    jobs = hook_jobs(LEFTHOOK, "pre-push")
    check, tests = jobs[f"{suite}-prerequisites"], jobs[f"{suite}-tests"]

    assert check["run"].endswith(f"push-prerequisites.sh {suite}-tests")
    assert check["glob"] == tests["glob"]
    # The same condition: a staging push skips the suite, so it needs no check.
    assert [skip["run"].split()[:2] for skip in check["skip"]] == [
        skip["run"].split()[:2] for skip in tests["skip"]
    ]
