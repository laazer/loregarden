"""Changed-lines mutation testing: which functions does a diff touch?

`task mutation` mutates only the functions a change touched, so the mapping from
changed lines to mutmut's units is the whole of its precision: a line mapped to
the wrong function mutates code nobody changed, and a missed one lets a change
through untested. These pin that mapping, then check it against the names
mutmut itself generates.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest
from tests.repo_paths import REPO_ROOT

_SCRIPT = REPO_ROOT / ".lefthook" / "scripts" / "mutation_changed_lines.py"
_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _load():
    spec = importlib.util.spec_from_file_location("mutation_changed_lines", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mcl = _load()

PATH = "server/loregarden/services/sample.py"

SOURCE = textwrap.dedent(
    """\
    import functools

    LIMIT = 3


    def outer(a):
        def inner(b):
            return b + 1

        return inner(a) * 2


    class Box:
        size = 1

        def grow(self, n):
            return self.size + n

        @staticmethod
        def make(n):
            return n - 1

        @property
        def area(self):
            return self.size * 2

        class Inner:
            def deep(self):
                return 4


    @functools.cache
    def cached(x):
        return x + 9


    async def waiter(x):
        return x * 3
    """
)


def _line(text: str) -> int:
    """1-based line of the first line of SOURCE containing ``text``."""
    for number, line in enumerate(SOURCE.splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(f"{text!r} not in SOURCE")


def _plan(added=(), removed=(), old=None, new=SOURCE, old_path=PATH, new_path=PATH):
    change = mcl.FileChange(old_path, new_path, added=set(added), removed=set(removed))
    return mcl.plan_file(change, new, old)


def _targets(plan):
    return [t.unit.qualname for t in plan.targets]


# --------------------------------------------------------------------------- #
# lines → units
# --------------------------------------------------------------------------- #


def test_a_line_in_a_nested_function_mutates_the_top_level_function():
    plan = _plan(added=[_line("return b + 1")])
    assert _targets(plan) == ["outer"]
    assert plan.targets[0].unit.mutmut_name == "x_outer"


def test_a_method_is_named_class_then_method_as_mutmut_names_it():
    plan = _plan(added=[_line("return self.size + n")])
    assert _targets(plan) == ["Box.grow"]
    assert plan.targets[0].glob == "loregarden.services.sample.xǁBoxǁgrow__mutmut_*"


def test_staticmethod_is_still_mutated_and_its_decorator_line_counts():
    plan = _plan(added=[_line("@staticmethod")])
    assert _targets(plan) == ["Box.make"]


def test_other_decorators_are_reported_as_unmutable_not_targeted():
    plan = _plan(added=[_line("return self.size * 2"), _line("return x + 9")])
    assert plan.targets == []
    assert sorted(unit.qualname for _path, unit in plan.unmutable) == ["Box.area", "cached"]
    assert all("decorated" in unit.skip_reason for _path, unit in plan.unmutable)


def test_async_function_is_a_unit():
    assert _targets(_plan(added=[_line("return x * 3")])) == ["waiter"]


@pytest.mark.parametrize(
    "text",
    ["LIMIT = 3", "size = 1", "return 4"],
    ids=["module-level", "class-level", "method-of-nested-class"],
)
def test_lines_outside_any_unit_are_reported_and_skipped(text):
    line = _line(text)
    plan = _plan(added=[line])
    assert plan.targets == []
    assert plan.module_level == [(PATH, line, line)]


def test_consecutive_module_level_lines_report_as_one_range():
    source = "A = 1\nB = 2\nC = 3\n"
    plan = _plan(added=[1, 2, 3], new=source)
    assert plan.module_level == [(PATH, 1, 3)]


def test_blank_lines_are_not_reported_as_module_level_code():
    plan = _plan(added=[1, 2, 3])  # import, blank, LIMIT = 3
    assert plan.module_level == [(PATH, 1, 1), (PATH, 3, 3)]


def test_an_untracked_file_counts_no_line_past_its_end(repo):
    (repo / "server" / "loregarden" / "fresh.py").write_text("X = 1\n", encoding="utf-8")
    _merge_base, changes = mcl.collect_changes(repo, "main")
    assert [c.added for c in changes] == [{1}]


def test_one_function_touched_many_times_is_one_target():
    plan = _plan(added=[_line("def inner"), _line("return b + 1"), _line("return inner(a)")])
    assert _targets(plan) == ["outer"]


# --------------------------------------------------------------------------- #
# removals and deletions
# --------------------------------------------------------------------------- #

OLD_WITH_EXTRA = SOURCE.replace(
    "async def waiter(x):\n    return x * 3\n",
    "async def waiter(x):\n    x = x + 0\n    return x * 3\n\n\ndef gone(y):\n    return y\n",
)


def _old_line(text: str) -> int:
    for number, line in enumerate(OLD_WITH_EXTRA.splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(text)


def test_a_line_removed_inside_a_surviving_function_mutates_it():
    plan = _plan(removed=[_old_line("x = x + 0")], old=OLD_WITH_EXTRA)
    assert _targets(plan) == ["waiter"]


def test_a_deleted_function_is_reported_not_targeted():
    removed = range(_old_line("def gone"), _old_line("return y") + 1)
    plan = _plan(removed=removed, old=OLD_WITH_EXTRA)
    assert plan.targets == []
    assert plan.deleted == [(PATH, "gone")]


def test_a_deleted_file_reports_every_function_deleted():
    plan = _plan(removed=[1], old=SOURCE, new=None, new_path=None)
    assert plan.targets == []
    assert ("server/loregarden/services/sample.py", "Box.grow") in plan.deleted


# --------------------------------------------------------------------------- #
# diff parsing
# --------------------------------------------------------------------------- #


def test_parse_diff_reads_both_sides_and_pure_inserts_and_deletes():
    diff = textwrap.dedent(
        """\
        diff --git a/server/loregarden/a.py b/server/loregarden/a.py
        index 1..2 100644
        --- a/server/loregarden/a.py
        +++ b/server/loregarden/a.py
        @@ -3 +3 @@ def f():
        @@ -10,2 +9,0 @@ def g():
        @@ -20,0 +18,3 @@ def h():
        """
    )
    (change,) = mcl.parse_diff(diff)
    assert change.added == {3, 18, 19, 20}
    assert change.removed == {3, 10, 11}


def test_parse_diff_new_file_has_no_old_side():
    diff = textwrap.dedent(
        """\
        diff --git a/server/loregarden/n.py b/server/loregarden/n.py
        new file mode 100644
        --- /dev/null
        +++ b/server/loregarden/n.py
        @@ -0,0 +1,2 @@
        """
    )
    (change,) = mcl.parse_diff(diff)
    assert change.old_path is None
    assert change.new_path == "server/loregarden/n.py"
    assert change.added == {1, 2}


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("server/loregarden/services/x.py", "loregarden.services.x"),
        ("server/loregarden/services/__init__.py", "loregarden.services"),
    ],
)
def test_module_of_matches_mutmut_module_naming(path, module):
    assert mcl.module_of(path) == module


# --------------------------------------------------------------------------- #
# against real git: renames and the working tree
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=_SCRUBBED_ENV
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    pkg = root / "server" / "loregarden"
    pkg.mkdir(parents=True)
    (pkg / "old_name.py").write_text(SOURCE, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base")
    return root


def test_a_pure_rename_changes_no_function(repo):
    _git(repo, "mv", "server/loregarden/old_name.py", "server/loregarden/new_name.py")
    merge_base, changes = mcl.collect_changes(repo, "main")
    plan = mcl.build_plan(repo, merge_base, changes)
    assert plan.targets == [] and plan.deleted == []


def test_a_renamed_and_edited_file_targets_the_new_module(repo):
    _git(repo, "mv", "server/loregarden/old_name.py", "server/loregarden/new_name.py")
    path = repo / "server" / "loregarden" / "new_name.py"
    path.write_text(SOURCE.replace("return n - 1", "return n - 2"), encoding="utf-8")
    merge_base, changes = mcl.collect_changes(repo, "main")
    plan = mcl.build_plan(repo, merge_base, changes)
    assert [(t.path, t.unit.qualname) for t in plan.targets] == [
        ("server/loregarden/new_name.py", "Box.make")
    ]
    assert plan.targets[0].glob == "loregarden.new_name.xǁBoxǁmake__mutmut_*"


def test_an_untracked_module_targets_every_function(repo):
    (repo / "server" / "loregarden" / "fresh.py").write_text(
        "def a():\n    return 1\n", encoding="utf-8"
    )
    merge_base, changes = mcl.collect_changes(repo, "main")
    plan = mcl.build_plan(repo, merge_base, changes)
    assert [t.unit.qualname for t in plan.targets] == ["a"]


def test_a_bad_base_is_an_error_not_an_empty_plan(repo, capsys):
    assert mcl.main(["--repo", str(repo), "--base", "no-such-ref"]) == mcl.EXIT_ERROR
    assert "cannot read the change" in capsys.readouterr().err


def test_no_changed_function_is_exit_zero_and_says_so(repo, capsys):
    assert mcl.main(["--repo", str(repo), "--base", "main"]) == mcl.EXIT_OK
    assert "nothing to mutate" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the verdict: failure and "nothing" never share an exit code
# --------------------------------------------------------------------------- #


def _one_target_plan():
    return _plan(added=[_line("return x * 3")])


@pytest.mark.parametrize(
    ("driver_code", "result", "expected"),
    [
        (2, None, "EXIT_ERROR"),
        (75, None, "EXIT_CAPACITY_STALLED"),
        (1, {"phase": "baseline_failed", "detail": "x"}, "EXIT_BASELINE_FAILED"),
        (1, {"phase": "no_coverage", "detail": "x"}, "EXIT_NO_TESTS"),
        (1, {"phase": "error", "detail": "x"}, "EXIT_ERROR"),
        (0, {"phase": "done", "mutants": []}, "EXIT_NO_MUTANTS"),
    ],
)
def test_each_failure_has_its_own_exit_code(driver_code, result, expected):
    assert mcl.finish(_one_target_plan(), driver_code, result, 1.0) == getattr(mcl, expected)


def test_a_survivor_fails_and_is_reported_under_its_function(capsys):
    name = "loregarden.services.sample.x_waiter__mutmut_1"
    result = {
        "phase": "done",
        "mutants": [
            {"name": name, "status": "survived", "diff": "-    return x * 3\n+    return x / 3"},
            {"name": "loregarden.services.sample.x_waiter__mutmut_2", "status": "killed"},
        ],
    }
    assert mcl.finish(_one_target_plan(), 0, result, 1.0) == mcl.EXIT_SURVIVORS
    out = capsys.readouterr().out
    assert f"{PATH}:{_line('async def waiter')} waiter (1 of 2 not killed)" in out
    assert name in out and "+    return x / 3" in out


def test_all_killed_is_exit_zero():
    result = {"phase": "done", "mutants": [{"name": "m.x_waiter__mutmut_1", "status": "killed"}]}
    assert mcl.finish(_one_target_plan(), 0, result, 1.0) == mcl.EXIT_OK


def test_a_red_baseline_names_the_failing_test(capsys):
    result = {
        "phase": "baseline_failed",
        "detail": "stats run exited 1",
        "failures": ["tests/test_x.py::test_y (call)\nAssertionError: 1 != 2\n"],
    }
    assert mcl.finish(_one_target_plan(), 1, result, 1.0) == mcl.EXIT_BASELINE_FAILED
    err = capsys.readouterr().err
    assert "tests/test_x.py::test_y (call)" in err and "AssertionError: 1 != 2" in err


def _run_pytest_with_plugin(tmp_path: Path, test_source: str) -> Path:
    root = tmp_path / "basetemp-root"
    root.mkdir()
    (tmp_path / "test_sample.py").write_text(textwrap.dedent(test_source))
    env = {
        **_SCRUBBED_ENV,
        "LOREGARDEN_MUTMUT_BASETEMP_ROOT": str(root),
        "PYTHONPATH": str(_SCRIPT.parent),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_mutation_basetemp",
            "-p",
            "no:cacheprovider",
            "test_sample.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return root


def test_the_basetemp_plugin_records_a_session_s_first_failure_only(tmp_path):
    root = _run_pytest_with_plugin(
        tmp_path,
        """\
        def test_passes():
            pass

        def test_first():
            assert 1 == 2

        def test_second():
            assert 3 == 4
        """,
    )
    driver = _load_driver()
    failures = driver._first_failures(root)
    assert len(failures) == 1
    assert failures[0].startswith("test_sample.py::test_first (call)")
    assert "assert 1 == 2" in failures[0] and "test_second" not in failures[0]


def test_the_basetemp_plugin_records_a_collection_error(tmp_path):
    root = _run_pytest_with_plugin(tmp_path, "import no_such_module_anywhere\n")
    failures = _load_driver()._first_failures(root)
    assert len(failures) == 1
    assert "(collection)" in failures[0] and "no_such_module_anywhere" in failures[0]


def _load_driver():
    # mutation_changed_lines put the scripts dir on sys.path and imported it.
    return sys.modules["mutmut_driver"]


def test_the_driver_never_asks_the_system_for_proxies(tmp_path):
    env = mcl.driver_env({"PATH": "/bin", "GIT_DIR": "/x/.git"}, tmp_path)
    assert env["no_proxy"] == "*" and "GIT_DIR" not in env
    probe = subprocess.run(
        [sys.executable, "-c", "import urllib.request as u; print(u.getproxies())"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "{'no': '*'}", "getproxies answered from the environment"


def test_a_callers_proxy_setting_is_kept(tmp_path):
    env = mcl.driver_env({"HTTPS_PROXY": "http://proxy:3128"}, tmp_path)
    assert env["HTTPS_PROXY"] == "http://proxy:3128" and "no_proxy" not in env


def test_exit_codes_are_distinct():
    codes = [
        mcl.EXIT_OK,
        mcl.EXIT_SURVIVORS,
        mcl.EXIT_ERROR,
        mcl.EXIT_BASELINE_FAILED,
        mcl.EXIT_NO_MUTANTS,
        mcl.EXIT_NO_TESTS,
        mcl.EXIT_CAPACITY_STALLED,
    ]
    assert len(set(codes)) == len(codes)


# --------------------------------------------------------------------------- #
# conformance: the units are the ones mutmut mutates, named as mutmut names them
# --------------------------------------------------------------------------- #


def test_units_match_the_functions_mutmut_generates_mutants_for(tmp_path, monkeypatch):
    from mutmut import configuration
    from mutmut.mutation.file_mutation import mutate_file_contents

    (tmp_path / "pkg").mkdir()
    (tmp_path / "setup.cfg").write_text("[mutmut]\nsource_paths=pkg\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    configuration.reset_config()
    try:
        mutated = mutate_file_contents("pkg/sample.py", SOURCE, None, None)
    finally:
        configuration.reset_config()
    generated = {name.rpartition("__mutmut_")[0] for name in mutated.mutant_names}
    ours = {u.mutmut_name for u in mcl.units_of(SOURCE) if u.skip_reason is None}
    assert generated == ours


# --------------------------------------------------------------------------- #
# two passes: near tests first, survivors only against the wider reach
# --------------------------------------------------------------------------- #

RINGS = mcl.TestRings(direct=["tests/test_near.py"], wider=["tests/test_far.py"])


@pytest.mark.parametrize(
    ("rings", "reach", "first", "second"),
    [
        (RINGS, "escalate", ["tests/test_near.py"], ["tests/test_far.py"]),
        (RINGS, "direct", ["tests/test_near.py"], []),
        (RINGS, "transitive", ["tests/test_near.py", "tests/test_far.py"], []),
        (
            mcl.TestRings(direct=[], wider=["tests/test_far.py"]),
            "escalate",
            ["tests/test_far.py"],
            [],
        ),
        (mcl.TestRings(direct=[], wider=[]), "escalate", [], []),
    ],
    ids=["escalate", "direct", "transitive", "no-direct-starts-wide", "nothing"],
)
def test_each_reach_picks_its_passes(rings, reach, first, second):
    assert mcl.first_and_second_pass(rings, reach) == (first, second)


SURVIVOR = "loregarden.services.sample.x_waiter__mutmut_1"
KILLED = "loregarden.services.sample.x_waiter__mutmut_2"
PASS1 = {
    "phase": "done",
    "mutants": [
        {"name": SURVIVOR, "status": "survived", "diff": "-a\n+b"},
        {"name": KILLED, "status": "killed"},
    ],
}


def _escalate(pass2, pass1=PASS1):
    with mock.patch.object(mcl, "run_driver", return_value=pass2) as driver:
        code, result = mcl.escalate(
            Path("/repo"), _one_target_plan(), 0, pass1, ["tests/test_far.py"], Path("/w")
        )
    return driver, code, result


def test_pass_two_runs_only_the_survivors_against_only_the_wider_tests():
    driver, _code, _result = _escalate((0, {"phase": "done", "mutants": []}))
    _repo, _targets, globs, tests, _workdir = driver.call_args.args
    assert globs == [SURVIVOR]
    assert tests == ["tests/test_far.py"]


def test_a_survivor_pass_two_kills_is_killed_and_marked():
    _driver, code, result = _escalate(
        (0, {"phase": "done", "mutants": [{"name": SURVIVOR, "status": "killed"}]})
    )
    assert mcl.finish(_one_target_plan(), code, result, 1.0) == mcl.EXIT_OK
    (entry,) = [m for m in result["mutants"] if m["name"] == SURVIVOR]
    assert entry == {"name": SURVIVOR, "status": "killed", "killed_by": "wider"}


def test_a_survivor_pass_two_misses_still_fails_with_its_diff():
    pass2 = (
        0,
        {"phase": "done", "mutants": [{"name": SURVIVOR, "status": "survived", "diff": "x"}]},
    )
    _driver, code, result = _escalate(pass2)
    assert mcl.finish(_one_target_plan(), code, result, 1.0) == mcl.EXIT_SURVIVORS
    (entry,) = [m for m in result["mutants"] if m["name"] == SURVIVOR]
    assert entry["diff"] == "-a\n+b"


def test_wider_tests_reaching_none_of_the_survivors_leave_them_standing():
    _driver, code, result = _escalate((1, {"phase": "no_coverage", "detail": "x"}))
    assert mcl.finish(_one_target_plan(), code, result, 1.0) == mcl.EXIT_SURVIVORS


def test_pass_two_red_on_the_clean_copy_fails_the_run():
    _driver, code, result = _escalate((1, {"phase": "baseline_failed", "detail": "x"}))
    assert mcl.finish(_one_target_plan(), code, result, 1.0) == mcl.EXIT_BASELINE_FAILED


def test_no_survivors_means_no_second_pass():
    clean = {"phase": "done", "mutants": [{"name": KILLED, "status": "killed"}]}
    driver, _code, result = _escalate((0, None), pass1=clean)
    driver.assert_not_called()
    assert result is clean


def test_direct_tests_covering_nothing_send_every_target_wide():
    driver, _code, _result = _escalate(
        (0, {"phase": "done", "mutants": [{"name": SURVIVOR, "status": "killed"}]}),
        pass1={"phase": "no_coverage", "detail": "x"},
    )
    assert driver.call_args.args[2] == ["loregarden.services.sample.x_waiter__mutmut_*"]
