"""The per-test ceiling fails a slow unmarked test, and nothing else.

Exercised on a real pytest run over a fixture file, in a subprocess: the plugin
changes report outcomes, which a run can only observe from outside itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from tests import slow_test_ceiling
from tests.repo_paths import REPO_ROOT

SERVER = REPO_ROOT / "server"

FIXTURE_CONFTEST = """
from tests import slow_test_ceiling

slow_test_ceiling.CEILING_SECONDS = 0.1
slow_test_ceiling.load_factor = lambda: 1.0


def pytest_configure(config):
    slow_test_ceiling.register(config, ["test_fixture.py::test_slow_and_inherited"])
"""

FIXTURE_TESTS = """
import time
from pathlib import Path

import pytest


def _first_time(name):
    marker = Path(name)
    if marker.exists():
        return False
    marker.write_text("seen")
    return True


@pytest.fixture
def slow_function_fixture():
    time.sleep(0.3)


@pytest.fixture(scope="module")
def slow_module_fixture():
    time.sleep(0.3)


def test_fast():
    pass


def test_slow_and_unmarked():
    time.sleep(0.3)


def test_slow_in_a_function_fixture(slow_function_fixture):
    pass


def test_slow_only_in_a_module_fixture(slow_module_fixture):
    pass


@pytest.mark.slow(reason="it sleeps on purpose")
def test_slow_and_marked():
    time.sleep(0.3)


@pytest.mark.slow(reason="   ")
def test_marked_with_a_blank_reason():
    pass


@pytest.mark.slow
def test_marked_with_no_reason():
    pass


def test_slow_and_inherited():
    time.sleep(0.3)


def test_slow_and_already_failing():
    time.sleep(0.3)
    assert False, "its own failure"


def test_slow_only_the_first_time():
    if _first_time("slow-once"):
        time.sleep(0.3)


def test_failing_only_the_first_time():
    assert not _first_time("fail-once"), "a flaky assertion"
"""


def _run(
    tmp_path: Path, extra_env: dict[str, str], extra_args: tuple[str, ...] = ()
) -> tuple[dict[str, ET.Element], str]:
    (tmp_path / "conftest.py").write_text(FIXTURE_CONFTEST)
    (tmp_path / "test_fixture.py").write_text(FIXTURE_TESTS)
    junit = tmp_path / "junit.xml"
    env = {k: v for k, v in os.environ.items() if k not in {"MUTANT_UNDER_TEST", "CI"}}
    env["PYTHONPATH"] = str(SERVER)
    env.update(extra_env)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            f"--junitxml={junit}",
            *extra_args,
            "--rootdir",
            str(tmp_path),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if not junit.exists():
        return {}, run.stdout + run.stderr
    cases = ET.parse(junit).getroot().iter("testcase")
    return {case.get("name"): case for case in cases}, run.stdout


def _failure(case: ET.Element) -> str | None:
    for tag in ("failure", "error"):
        element = case.find(tag)
        if element is not None:
            return f"{element.get('message', '')}\n{element.text or ''}"
    return None


@pytest.fixture(scope="module")
def enforced(tmp_path_factory) -> dict[str, ET.Element]:
    """A CI run: GitHub Actions sets CI=true, and only there does the ceiling fail."""
    return _run(tmp_path_factory.mktemp("enforced"), {"CI": "true"})[0]


@pytest.fixture(scope="module")
def under_mutmut(tmp_path_factory) -> dict[str, ET.Element]:
    env = {"CI": "true", "MUTANT_UNDER_TEST": "stats"}
    return _run(tmp_path_factory.mktemp("mutmut"), env)[0]


@pytest.fixture(scope="module")
def local(tmp_path_factory) -> tuple[dict[str, ET.Element], str]:
    return _run(tmp_path_factory.mktemp("local"), {})


def test_an_unmarked_test_over_the_ceiling_fails_with_its_time_and_the_ceiling(enforced):
    failure = _failure(enforced["test_slow_and_unmarked"])

    assert failure is not None
    assert "test_fixture.py::test_slow_and_unmarked took 0." in failure
    assert "per-test ceiling of 0.10s" in failure


def test_a_function_fixture_counts_toward_the_test(enforced):
    assert "took" in (_failure(enforced["test_slow_in_a_function_fixture"]) or "")


@pytest.mark.parametrize(
    "name",
    [
        "test_fast",
        "test_slow_and_marked",
        "test_slow_and_inherited",
        "test_slow_only_in_a_module_fixture",
    ],
)
def test_what_the_ceiling_passes(enforced, name):
    assert _failure(enforced[name]) is None


@pytest.mark.parametrize("name", ["test_marked_with_a_blank_reason", "test_marked_with_no_reason"])
def test_the_slow_marker_needs_a_non_empty_reason(enforced, name):
    assert "needs a non-empty reason" in (_failure(enforced[name]) or "")


def test_a_failing_test_keeps_its_own_failure(enforced):
    failure = _failure(enforced["test_slow_and_already_failing"]) or ""

    assert "its own failure" in failure
    assert "per-test ceiling" not in failure


def test_mutmut_runs_are_not_timed(under_mutmut):
    assert _failure(under_mutmut["test_slow_and_unmarked"]) is None


def test_the_ceiling_scales_with_load_per_core_and_never_below_one():
    assert slow_test_ceiling.load_factor(lambda: (16.0, 0, 0), lambda: 8) == 2.0
    assert slow_test_ceiling.load_factor(lambda: (1.0, 0, 0), lambda: 8) == 1.0


def test_off_ci_an_overrun_is_reported_in_the_summary_and_fails_nothing(local):
    cases, stdout = local

    assert _failure(cases["test_slow_and_unmarked"]) is None
    assert "tests over the per-test ceiling" in stdout
    assert "test_fixture.py::test_slow_and_unmarked took" in stdout
    assert "test_slow_and_marked took" not in stdout


def test_off_ci_a_blank_reason_still_fails(local):
    cases, _ = local

    assert "needs a non-empty reason" in (_failure(cases["test_marked_with_a_blank_reason"]) or "")


def test_a_breach_that_does_not_repeat_on_the_warm_rerun_passes(enforced):
    assert _failure(enforced["test_slow_only_the_first_time"]) is None


def test_only_a_breach_is_rerun_never_a_failing_assertion(enforced):
    assert "a flaky assertion" in (_failure(enforced["test_failing_only_the_first_time"]) or "")


def test_ci_refuses_to_enforce_without_the_rerun(tmp_path):
    cases, output = _run(tmp_path, {"CI": "true"}, ("-p", "no:rerunfailures"))

    assert cases == {}
    assert "pytest-rerunfailures is not installed" in output
