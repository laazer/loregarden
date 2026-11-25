"""A test file no runner collects is not a test.

About a hundred tests sat in a root-level `tests/` from 2026-07-06 to 2026-11-24.
pytest collects `server/tests/` and jest `client/src/**/__tests__/`, so nothing
ran them. Handed the fixtures they lacked, 30 could still pass.
"""

import re

import pytest
from loregarden.services.git_subprocess import run_git
from tests.repo_paths import REPO_ROOT

pytestmark = pytest.mark.registry

TEST_NAME = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[cm]?[jt]sx?)$")

#: Where each runner looks: pytest's `testpaths`, then jest's `testMatch`.
COLLECTED = (
    re.compile(r"^server/tests/(.*/)?test_[^/]*\.py$"),
    re.compile(r"^client/src/(.*/)?__tests__/.*\.(test|spec)\.tsx?$"),
)

#: Test-shaped names that are collected elsewhere or are not tests.
EXCEPTIONS = {
    # Imports `claude-code/testing`, which only Claude Code's plugin runner provides.
    "claude-plugin/tests/garden.test.tsx",
    # A migration about selecting tests.
    "server/loregarden/db/versions/test_selection_shared.py",
}


def uncollected(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if TEST_NAME.search(path) and not any(rule.match(path) for rule in COLLECTED)
    ]


def test_every_tracked_test_file_is_one_a_runner_collects():
    listed = run_git(["ls-files"], cwd=str(REPO_ROOT), capture_output=True, text=True, check=True)
    assert set(uncollected(listed.stdout.splitlines())) == EXCEPTIONS


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_ci_api.py",
        "tests/e2e/ci-auto-fix.spec.ts",
        "client/src/lib/queue.test.ts",
        "client/src/lib/__tests__/queue.test.js",
        "server/loregarden/services/test_queue.py",
        "server/tests/queue_test.py",
    ],
)
def test_a_test_file_outside_every_runner_is_named(path):
    assert uncollected([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "server/tests/test_queue.py",
        "server/tests/gates/test_queue.py",
        "client/src/lib/__tests__/queue.test.ts",
        "client/src/lib/__tests__/deep/queue.spec.tsx",
        "server/tests/factories.py",
        "client/src/test/setup.ts",
    ],
)
def test_a_collected_test_or_a_helper_is_not(path):
    assert uncollected([path]) == []
