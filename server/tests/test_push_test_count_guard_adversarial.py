"""Adversarial pins for the pre-push verification guard: the ways it can go green.

The test-design stage pinned the four outcomes on the diagonal — one test per
outcome, one per criterion. This module attacks the same guard from the side a
diagonal cannot reach: the precedence between two conditions that are true at
once, the mutations of the one number everything hangs on, the shell posture the
two real scripts actually run under, and the arithmetic that can turn a failed
run into exit 0.

Every test here is written so that the failure mode it guards against is a
**false green** — a push that is let through, or an outcome reported as a
different outcome — because that is the only defect class this ticket exists to
remove. A guard that wrongly blocks a push is a nuisance; a guard that wrongly
passes one is the thing it was built to prevent.

What each dimension would buy a mutation, and the test that denies it:

| dimension | the false green it buys |
|---|---|
| precedence | two conditions true at once resolve to the wrong one of four |
| shell posture | `set -e` aborts the seam before the judge runs, so the runner's |
| | own code becomes the verdict and no word is printed (R-2, mechanised) |
| discarded verdict | a branch follows the judge with an unconditional `exit 0` |
| stale state | a count from an earlier run is read as this run's evidence |
| type mutation | `{"count": true}` read as 1, `{"count": "0"}` read as 0 |
| assumption | `LOREGARDEN_ALLOW_NO_TESTS=0` read as a declaration, so the |
| | operator who meant "off" silently turned the guard off |
| exit arithmetic | a runner code of 256 forwarded as 0 |
| determinism | an ASCII-only terminal crashes the judge, and a crash at exit 1 |
| | is indistinguishable from `verified_nothing` |
| board truncation | the outcome word falls off the end of a 200-char step label |
| single writer | a second writer of the count file outside `.lefthook/scripts` |

The precedence the tables encode is the test-design stage's frozen decision, and
it is the one thing here that must not be renegotiated: **no count outranks
everything, then a recorded zero, then the runner's own non-zero code.** Measured
on pytest 9.1.1 in this tree, a zero-collection run exits 5, so the other order
reports 5 where G-3 asks for 1 and makes G-5's declared case unreachable at 0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from loregarden.services.capacity_progress import (
    _STEP_MAX,
    LeaseProgress,
    describe_progress,
)
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
JUDGE = SCRIPTS / "judge_test_run.py"
GUARD_SEAM = SCRIPTS / "test-count-guard.sh"
SERVER_TESTS = SCRIPTS / "server-tests.sh"
CLIENT_TESTS = SCRIPTS / "client-tests.sh"

COUNT_ENV = "LOREGARDEN_TEST_COUNT_FILE"
DECLARE_ENV = "LOREGARDEN_ALLOW_NO_TESTS"
PROGRESS_ENV = "LOREGARDEN_PROGRESS_FILE"

#: The literal words T-1 requires, and the one that must never appear beside a
#: let-through push.
PASSED = "passed"
FAILED = "FAILED"
NOTHING = "VERIFIED NOTHING"
UNAVAILABLE = "COULD NOT RUN"

#: `None` means "the count file is never written" — an absence, not a zero.
ABSENT: int | None = None

#: Scrubbed from every nested runner: a `-k` the OUTER run was given (a push with
#: PYTEST_ADDOPTS set, or a developer narrowing the suite) deselects the inner
#: suite, which then exits 5 and the test reads as a defect in the sink.
INHERITED_PYTEST_STATE = ("PYTEST_XDIST_WORKER", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")


# ---- helpers ---------------------------------------------------------------


def _run_judge(
    tmp_path: Path,
    *,
    runner: str = "pytest",
    exit_code: object = 0,
    count: object = ABSENT,
    count_file: Path | None = None,
    declared: str | None = None,
    listed_files: object = None,
    step: str | None = "pytest on 3 file(s) (5/5)",
    report_file: Path | None = None,
    env_extra: dict[str, str] | None = None,
    argv_extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """One judge invocation. `count` is written verbatim when it is not ABSENT."""
    path = count_file if count_file is not None else tmp_path / "count.json"
    if count is not ABSENT:
        path.write_text(json.dumps({"count": count}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in (DECLARE_ENV, PROGRESS_ENV)}
    env[COUNT_ENV] = str(path)
    if step is not None:
        env["PUSH_PROGRESS_STEP"] = step
    else:
        env.pop("PUSH_PROGRESS_STEP", None)
    if declared is not None:
        env[DECLARE_ENV] = declared
    env.update(env_extra or {})
    argv = [sys.executable, str(JUDGE), "--runner", runner, "--exit", str(exit_code)]
    if listed_files is not None:
        argv += ["--listed-files", str(listed_files)]
    if report_file is not None:
        argv += ["--report-file", str(report_file)]
    return subprocess.run(
        [*argv, *argv_extra],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _bash(script: str, tmp_path: Path, env: dict[str, str] | None = None, *, posture: str = "-e"):
    """Exercise the seam under a stated shell posture.

    `posture` defaults to `-e` because that is what `server-tests.sh:17` and
    `client-tests.sh:12` actually run under (`set -euo pipefail`). A seam
    exercised without `-e` cannot see the failure this module's headline test is
    about: `set -e` aborting the function at the failing runner, before the judge
    is ever consulted.
    """
    body = (
        f"set {posture}uo pipefail\n"
        f'source "{GUARD_SEAM}"\n'
        f'source "{SCRIPTS}/push-progress.sh"\n{script}\n'
    )
    return subprocess.run(
        ["bash", "-c", body],
        cwd=tmp_path,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


# ---- precedence: two conditions true at once ------------------------------
#
# The outcome table in the spec gives four conditions, and real runs satisfy
# more than one of them at a time. Every row below is a run that can actually
# happen; the expectation is the frozen order of precedence.


@pytest.mark.parametrize(
    ("count", "runner_exit", "declared", "code", "word", "forbidden"),
    [
        # No count at all outranks everything: a run whose collection never
        # reported cannot be judged by its exit code, declared or not.
        (ABSENT, 0, None, GATE_EX_UNAVAILABLE, UNAVAILABLE, NOTHING),
        (ABSENT, 0, "1", GATE_EX_UNAVAILABLE, UNAVAILABLE, PASSED),
        (ABSENT, 5, None, GATE_EX_UNAVAILABLE, UNAVAILABLE, FAILED),
        (ABSENT, 5, "1", GATE_EX_UNAVAILABLE, UNAVAILABLE, FAILED),
        # A recorded zero outranks the runner's own non-zero code. Measured:
        # every pytest zero-collection run in this tree exits 5, so this row is
        # the normal case and not a corner.
        (0, 0, None, 1, NOTHING, FAILED),
        (0, 5, None, 1, NOTHING, FAILED),
        (0, 4, None, 1, NOTHING, FAILED),
        # ... and the declaration speaks to a zero, which is the only thing it
        # can speak to. The line still says nothing was verified (G-5).
        (0, 0, "1", 0, NOTHING, FAILED),
        (0, 5, "1", 0, NOTHING, FAILED),
        # A real count with a real failure keeps the runner's own verdict, and
        # the declaration cannot excuse it.
        (412, 1, None, 1, FAILED, NOTHING),
        (412, 5, None, 5, FAILED, NOTHING),
        (412, 2, "1", 2, FAILED, NOTHING),
        # The good case, with and without a declaration nobody needed.
        (1830, 0, None, 0, PASSED, NOTHING),
        (1830, 0, "1", 0, PASSED, NOTHING),
    ],
)
def test_the_outcome_truth_table_for_pytest(
    tmp_path, count, runner_exit, declared, code, word, forbidden
) -> None:
    """Every combination of (count, runner exit, declaration) that can occur."""
    result = _run_judge(
        tmp_path,
        count=count,
        count_file=tmp_path / "absent.json" if count is ABSENT else None,
        exit_code=runner_exit,
        declared=declared,
    )

    assert result.returncode == code, _output(result)
    assert word in _output(result), _output(result)
    assert forbidden not in _output(result), _output(result)


@pytest.mark.parametrize(
    ("count", "listed", "declared", "code", "word"),
    [
        # G-6: on a zero suite count the probe decides, and it decides both ways.
        (0, 287, None, 0, NOTHING),
        (0, 1, None, 0, NOTHING),
        (0, 0, None, GATE_EX_UNAVAILABLE, UNAVAILABLE),
        (0, None, None, GATE_EX_UNAVAILABLE, UNAVAILABLE),
        # A declaration cannot turn "the harness found no test file at all" into
        # a pass: that is an absence, and the same rule that stops a declaration
        # excusing a missing count stops it excusing a collapsed harness.
        (0, 0, "1", GATE_EX_UNAVAILABLE, UNAVAILABLE),
        (0, None, "1", GATE_EX_UNAVAILABLE, UNAVAILABLE),
        # R-5: the probe is never consulted when suites actually ran, so a stale
        # or empty probe result cannot overturn a real run in either direction.
        (286, 0, None, 0, PASSED),
        (286, None, None, 0, PASSED),
    ],
)
def test_the_outcome_truth_table_for_the_jest_probe(
    tmp_path, count, listed, declared, code, word
) -> None:
    result = _run_judge(
        tmp_path, runner="jest", exit_code=0, count=count, listed_files=listed, declared=declared
    )

    assert result.returncode == code, _output(result)
    assert word in _output(result), _output(result)


def test_a_failing_jest_run_keeps_its_own_code_whatever_the_probe_says(tmp_path) -> None:
    """The probe is a reading of the selection, never a verdict on a red suite."""
    result = _run_judge(tmp_path, runner="jest", exit_code=1, count=286, listed_files=0)

    assert result.returncode == 1, _output(result)
    assert FAILED in _output(result)
    assert UNAVAILABLE not in _output(result)


# ---- the shell posture the two scripts actually run under ------------------
#
# `server-tests.sh:17` and `client-tests.sh:12` are both `set -euo pipefail`.
# Under `-e`, a seam whose body runs the runner as a bare command aborts at the
# failing runner and never reaches the judge — so the script exits with the
# runner's own code and prints none of the four words. That is R-2 (vacuous
# satisfaction) as a mechanism rather than an attitude, and it is invisible to a
# seam exercised without `-e`.


def test_the_seam_judges_a_zero_collection_under_the_posture_both_scripts_use(tmp_path) -> None:
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      set +e
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 0}}" > "${COUNT_ENV}"; exit 5'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "rc=1" in result.stdout, _output(result)
    assert NOTHING in result.stdout, _output(result)


def test_the_seam_judges_a_failing_runner_under_the_posture_both_scripts_use(tmp_path) -> None:
    """`set -e` must not abort the seam before the judge has spoken: the words
    are the deliverable (T-1), not just the code."""
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      set +e
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 412}}" > "${COUNT_ENV}"; exit 3'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "rc=3" in result.stdout, _output(result)
    assert FAILED in result.stdout, _output(result)


def test_the_seam_judges_a_missing_count_under_the_posture_both_scripts_use(tmp_path) -> None:
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      set +e
      run_and_judge pytest -- sh -c 'exit 0'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert f"rc={GATE_EX_UNAVAILABLE}" in result.stdout, _output(result)
    assert UNAVAILABLE in result.stdout, _output(result)


def test_the_seam_aborting_the_script_still_carries_the_judges_verdict(tmp_path) -> None:
    """The real shape: no `set +e` rescue, so `-e` aborts the script at the seam
    and the EXIT trap reports. The code that reaches the push must be the
    judge's 1, not pytest's own 5."""
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      trap 'progress_finish $?' EXIT
      progress_begin "server-tests" 5
      progress_step "pytest on 3 file(s)"
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 0}}" > "${COUNT_ENV}"; exit 5'
      echo "NEVER: the seam returned 0 for a run that collected nothing"
    """
    result = _bash(script, tmp_path)

    assert result.returncode == 1, _output(result)
    assert "NEVER" not in result.stdout, _output(result)
    assert NOTHING in result.stdout, _output(result)


@pytest.mark.parametrize(
    ("count_json", "runner_exit", "expected_step_word"),
    [("0", 5, NOTHING), ("412", 3, FAILED)],
)
def test_the_exit_trap_does_not_replace_the_judges_report(
    tmp_path, count_json, runner_exit, expected_step_word
) -> None:
    """B-2/B-3 under the real trap. `progress_finish` writes `FAILED: <step>`
    with no counts; if it runs after the judge for the same step, the board gets
    the bare shape measured on 13 live leases and loses the count."""
    progress = tmp_path / "progress.json"
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      trap 'progress_finish $?' EXIT
      progress_begin "server-tests" 5
      progress_step "pytest on 3 file(s)"
      run_and_judge pytest -- sh -c \
        'printf "{{\\"count\\": {count_json}}}" > "${COUNT_ENV}"; exit {runner_exit}'
    """
    result = _bash(script, tmp_path, env={PROGRESS_ENV: str(progress)})

    assert result.returncode != 0, _output(result)
    report = LeaseProgress.model_validate_json(progress.read_bytes())
    assert expected_step_word in report.step, f"the board lost the outcome: {report.step!r}"
    assert report.total == int(count_json), f"progress_finish replaced the count: {report!r}"


def test_no_branch_discards_the_judges_verdict_with_an_exit_zero() -> None:
    """`server-tests.sh` ends its full-suite branch with a literal `exit 0`
    today, which is correct only while `set -e` is the thing that fails a run.
    Once the judge decides, an unconditional `exit 0` after it turns every
    `verified_nothing` on that branch back into a green push."""
    for script in (SERVER_TESTS, CLIENT_TESTS):
        lines = script.read_text(encoding="utf-8").splitlines()
        judged = [ln for ln in lines if "run_and_judge" in ln]
        assert judged, f"{script.name} does not judge yet, so this check holds on nothing"
        for index, line in enumerate(lines):
            if "run_and_judge" not in line:
                continue
            following = lines[index + 1 : index + 6]
            offenders = [ln for ln in following if ln.strip() == "exit 0"]
            assert not offenders, (
                f"{script.name}:{index + 1} judges, then discards the verdict with "
                f"an unconditional `exit 0`"
            )


# ---- stale state: evidence from an earlier run ----------------------------


def test_a_stale_count_from_an_earlier_run_is_not_evidence_for_this_one(tmp_path) -> None:
    """A-2 gives the count file a per-run temp root, so this is a defect only a
    fixed path reintroduces — and a fixed path is the obvious shortcut. Either
    fix satisfies this: the seam clears the path before the runner starts, or it
    hands the runner a fresh path per invocation.
    """
    count = tmp_path / "count.json"
    count.write_text(json.dumps({"count": 1830}), encoding="utf-8")
    script = f"""
      export {COUNT_ENV}="{count}"
      set +e
      run_and_judge pytest -- sh -c 'exit 0'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert f"rc={GATE_EX_UNAVAILABLE}" in result.stdout, _output(result)
    assert UNAVAILABLE in result.stdout, _output(result)
    assert PASSED not in result.stdout, "a count from a previous run was read as this run's"


def test_each_script_derives_the_count_file_from_its_own_run() -> None:
    """The source half of the same defect: a literal shared path under /tmp is a
    count one run can read from another."""
    for script in (SERVER_TESTS, CLIENT_TESTS):
        source = script.read_text(encoding="utf-8")
        assignments = [ln.strip() for ln in source.splitlines() if f"{COUNT_ENV}=" in ln]
        assert assignments, f"{script.name} must always set {COUNT_ENV}"
        for line in assignments:
            value = line.split(f"{COUNT_ENV}=", 1)[1]
            assert "$" in value, f"{script.name}: {COUNT_ENV} must be derived per run, got {line!r}"


def test_the_count_file_is_not_inside_the_directory_pytest_is_handed() -> None:
    """pytest OWNS a `--basetemp` it was given: `_pytest.tmpdir` rmtree's it at
    the FIRST `tmp_path` use, which is after collection. Measured: a count
    recorded under that directory is deleted by the first test that asks for a
    temp dir, so a suite that ran reports COULD NOT RUN. Reachable serially —
    under `-n` the workers get subdirectories of their own, which is the only
    reason the real path survived it.
    """
    source = SERVER_TESTS.read_text(encoding="utf-8")
    count_dirs = {
        ln.split(f"{COUNT_ENV}=", 1)[1].strip().strip('"').rsplit("/", 1)[0]
        for ln in source.splitlines()
        if f"{COUNT_ENV}=" in ln
    }
    basetemps = {
        part.split("--basetemp=", 1)[1].strip().strip('"\\')
        for ln in source.splitlines()
        if "--basetemp=" in ln
        for part in ln.split()
        if part.startswith("--basetemp=")
    }

    assert count_dirs and basetemps, (count_dirs, basetemps)
    assert not (count_dirs & basetemps), (
        f"the count file lives in the directory pytest is given ({count_dirs & basetemps}), "
        "which pytest deletes after collection"
    )


# ---- type and structure mutations of the one number ----------------------


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("", "an empty file: the writer was killed between create and write"),
        ("   \n", "whitespace only"),
        ("{", "a truncated object: killed mid-write"),
        ('{"count": 41', "truncated after the number"),
        ("{}", "no count key at all"),
        ("[]", "a list where an object was expected"),
        ("null", "a JSON null document"),
        ("7", "a bare number, not the recorded shape"),
        ('{"count": null}', "the key is there and says nothing"),
        ('{"count": "0"}', 'a STRING zero — `int("0")` is a parse, not a record'),
        ('{"count": "7"}', "a string seven, which must not read as seven"),
        ('{"count": true}', "a bool: in Python `True == 1`, so this can fabricate a pass"),
        ('{"count": false}', "a bool: and `False == 0` can fabricate a zero"),
        ('{"count": -1}', "a negative count is not a count"),
        ('{"count": 0.5}', "a fraction of a test"),
        ('{"count": [0]}', "the number wrapped in a list"),
        ('{"count": {"n": 7}}', "the number wrapped in an object"),
        ('{"count": 1e309}', "JSON infinity"),
    ],
)
def test_a_count_that_is_not_a_non_negative_integer_is_could_not_run(
    tmp_path, payload, why
) -> None:
    """C-3 fail-closed, across the mutation matrix. Every one of these is an
    absence of a usable count: none may read as a zero (which a declaration
    could then wave through) and none may read as a pass."""
    path = tmp_path / "count.json"
    path.write_text(payload, encoding="utf-8")
    result = _run_judge(tmp_path, exit_code=0, count_file=path)

    assert result.returncode == GATE_EX_UNAVAILABLE, f"{why}: {_output(result)}"
    assert UNAVAILABLE in _output(result), f"{why}: {_output(result)}"
    assert PASSED not in _output(result), f"{why}: a malformed count was read as a pass"


@pytest.mark.parametrize("payload", ['{"count": true}', '{"count": "0"}', "{}"])
def test_a_malformed_count_is_not_rescued_by_the_declaration(tmp_path, payload) -> None:
    """The declaration says "a zero is expected here". A count that is not a
    number is not a zero, so there is nothing for it to declare."""
    path = tmp_path / "count.json"
    path.write_text(payload, encoding="utf-8")
    result = _run_judge(tmp_path, exit_code=0, count_file=path, declared="1")

    assert result.returncode == GATE_EX_UNAVAILABLE, _output(result)
    assert UNAVAILABLE in _output(result)


def test_a_count_file_that_is_a_directory_is_could_not_run(tmp_path) -> None:
    """Not a crash and not a traceback: the operator gets the path and a word."""
    path = tmp_path / "count.json"
    path.mkdir()
    result = _run_judge(tmp_path, exit_code=0, count_file=path)

    assert result.returncode == GATE_EX_UNAVAILABLE, _output(result)
    assert UNAVAILABLE in _output(result)
    assert str(path) in _output(result)
    assert "Traceback" not in _output(result)


def test_a_dangling_symlink_count_file_is_could_not_run(tmp_path) -> None:
    path = tmp_path / "count.json"
    path.symlink_to(tmp_path / "nowhere.json")
    result = _run_judge(tmp_path, exit_code=0, count_file=path)

    assert result.returncode == GATE_EX_UNAVAILABLE, _output(result)
    assert UNAVAILABLE in _output(result)


def test_an_extra_key_beside_the_count_is_still_a_count(tmp_path) -> None:
    """The one direction that must stay open: a later writer adding a field
    cannot turn every push into `could_not_run`."""
    path = tmp_path / "count.json"
    path.write_text(json.dumps({"count": 24, "recorded_by": "pytest", "mode": "-n 4"}), "utf-8")
    result = _run_judge(tmp_path, exit_code=0, count_file=path)

    assert result.returncode == 0, _output(result)
    assert PASSED in _output(result)


@pytest.mark.parametrize("count", [1, 7573, 10**6])
def test_any_recorded_count_of_one_or_more_is_a_run_that_happened(tmp_path, count) -> None:
    """The boundary between `verified_nothing` and `ran_and_passed` is at 1, and
    nothing above it is special — T-3's volumes must not be a ceiling either."""
    result = _run_judge(tmp_path, exit_code=0, count=count)

    assert result.returncode == 0, _output(result)
    assert NOTHING not in _output(result)


# ---- the declaration flag: the assumption that can disarm the guard -------


def test_the_literal_declaration_value_declares_an_empty_selection(tmp_path) -> None:
    result = _run_judge(tmp_path, exit_code=5, count=0, declared="1")

    assert result.returncode == 0, _output(result)
    assert f"{DECLARE_ENV}=1" in _output(result)


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "",
        " ",
        "false",
        "no",
        "off",
        "2",
        "true",
        "yes",
        "01",
        "1 ",
        "\t1",
    ],
)
def test_anything_other_than_the_declared_value_does_not_disarm_the_guard(tmp_path, value) -> None:
    """A truthiness check (`if os.environ.get(DECLARE_ENV):`) reads `=0`, `=false`
    and `=no` as a declaration — so the operator who sets the variable to 0 to
    mean "off" silently turns the guard off instead. A-1 names one literal value;
    fail closed on every other spelling, including the plausible-looking ones."""
    result = _run_judge(tmp_path, exit_code=5, count=0, declared=value)

    assert result.returncode == 1, f"{DECLARE_ENV}={value!r} disarmed the guard: {_output(result)}"
    assert NOTHING in _output(result)


# ---- exit-code arithmetic: how a failed run becomes a green push ----------


@pytest.mark.parametrize("runner_exit", [256, 512, 1024, -1, -9, -15])
def test_a_runner_code_the_shell_cannot_represent_never_becomes_a_pass(
    tmp_path, runner_exit
) -> None:
    """`sys.exit(256)` exits 0: the low byte of a wait status is all a shell
    keeps. A judge that forwards the runner's number unexamined turns a failed
    run into a green push on exactly these values — and a subprocess killed by a
    signal is reported as a negative number by Python's own API."""
    result = _run_judge(tmp_path, exit_code=runner_exit, count=412)

    assert result.returncode != 0, f"exit {runner_exit} was forwarded as a pass"
    assert FAILED in _output(result), _output(result)
    assert PASSED not in _output(result)


def test_a_runner_that_itself_exited_69_is_still_ran_and_failed(tmp_path) -> None:
    """The two codes collide here — 69 is the runner's own and the judge's
    `could_not_run` — so T-1's words are the only thing that separates them."""
    result = _run_judge(tmp_path, exit_code=GATE_EX_UNAVAILABLE, count=412)

    assert result.returncode == GATE_EX_UNAVAILABLE, _output(result)
    assert FAILED in _output(result)
    assert UNAVAILABLE not in _output(result), "a red suite was reported as a collapsed one"


def test_a_runner_that_itself_exited_1_is_not_reported_as_verified_nothing(tmp_path) -> None:
    """The mirror: exit 1 is the judge's `verified_nothing` code and also the
    commonest failing-suite code there is."""
    result = _run_judge(tmp_path, exit_code=1, count=412)

    assert result.returncode == 1, _output(result)
    assert FAILED in _output(result)
    assert NOTHING not in _output(result)


@pytest.mark.parametrize("signal_code", [130, 143])
def test_a_runner_killed_by_a_signal_is_not_a_pass(tmp_path, signal_code) -> None:
    """Ctrl-C after collection: the count is real and the run is not finished."""
    with_count = _run_judge(tmp_path, exit_code=signal_code, count=412)
    interrupted_in_collection = _run_judge(
        tmp_path, exit_code=signal_code, count_file=tmp_path / "never.json"
    )

    assert with_count.returncode == signal_code, _output(with_count)
    assert FAILED in _output(with_count)
    assert interrupted_in_collection.returncode == GATE_EX_UNAVAILABLE
    assert UNAVAILABLE in _output(interrupted_in_collection)


# ---- the argument surface -------------------------------------------------


def test_an_unknown_runner_is_refused_by_name(tmp_path) -> None:
    """Two runners have a rule each (G-6 is jest-only). A third must not fall
    into whichever branch happens to be the default."""
    result = _run_judge(tmp_path, runner="mocha", exit_code=0, count=0)

    assert result.returncode != 0, _output(result)
    assert "mocha" in _output(result), _output(result)


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        (("--runner", "pytest"), "--exit"),
        (("--exit", "0"), "--runner"),
        (("--runner", "pytest", "--exit", "lots"), "lots"),
        (("--runner", "pytest", "--exit", "0", "--listed-files", "some"), "some"),
        (("--runner", "pytest", "--exit", ""), "--exit"),
    ],
)
def test_a_judge_that_cannot_read_its_own_arguments_never_reports_a_pass(
    tmp_path, argv, named
) -> None:
    """A miswired call site is a loud error, never exit 0 with the word `passed`
    — and it names the argument it could not read, because the operator reading
    the push has no other way to tell a broken guard from a broken suite."""
    path = tmp_path / "count.json"
    path.write_text(json.dumps({"count": 1830}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in (DECLARE_ENV, PROGRESS_ENV)}
    env[COUNT_ENV] = str(path)
    result = subprocess.run(
        [sys.executable, str(JUDGE), *argv],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip

    assert result.returncode != 0, _output(result)
    assert PASSED not in _output(result), _output(result)
    assert named in _output(result), _output(result)


# ---- determinism and the environment a hook actually runs in -------------


@pytest.mark.parametrize(
    ("count", "runner_exit", "code", "word"),
    [
        (1830, 0, 0, PASSED),
        (412, 3, 3, FAILED),
        (0, 5, 1, NOTHING),
        (ABSENT, 0, GATE_EX_UNAVAILABLE, UNAVAILABLE),
    ],
)
def test_the_judge_says_the_same_thing_under_an_ascii_only_terminal(
    tmp_path, count, runner_exit, code, word
) -> None:
    """A `·` or an em dash in the line crashes on an ASCII stdout, and a crashed
    judge exits 1 — which is `verified_nothing`'s own code. So a decorative
    character would make a passing suite read as a suite that verified nothing,
    in exactly the environments (a bare hook, a CI shell, an agent's pipe) where
    nobody is watching the terminal."""
    result = _run_judge(
        tmp_path,
        count=count,
        count_file=tmp_path / "absent.json" if count is ABSENT else None,
        exit_code=runner_exit,
        env_extra={"PYTHONIOENCODING": "ascii", "LC_ALL": "C", "LANG": "C"},
    )

    assert result.returncode == code, _output(result)
    assert word in _output(result), _output(result)
    assert "Traceback" not in _output(result), _output(result)


def test_the_judge_is_deterministic_over_repeated_runs(tmp_path) -> None:
    """Determinism validation: the same recorded count, five times, byte for
    byte. A guard whose wording varies cannot be asserted on by anything."""
    answers = {
        (r.returncode, r.stdout, r.stderr)
        for r in (_run_judge(tmp_path, exit_code=0, count=0) for _ in range(5))
    }

    assert len(answers) == 1, answers
    ((code, stdout, stderr),) = answers
    assert code == 1
    assert NOTHING in stdout + stderr


@pytest.mark.parametrize("name", ["count file.json", "co·unt.json"])
def test_the_count_file_path_may_hold_a_space_or_a_non_ascii_character(tmp_path, name) -> None:
    """`$TMPDIR` is whatever the developer's shell says it is. An unquoted
    expansion of the count path in the seam splits here, and the judge is then
    handed half a path — reported as `could_not_run` on a run that was fine."""
    path = tmp_path / name
    result = _run_judge(tmp_path, exit_code=0, count=24, count_file=path)

    assert result.returncode == 0, _output(result)
    assert PASSED in _output(result)


def test_the_seam_handles_a_count_path_with_a_space(tmp_path) -> None:
    room = tmp_path / "temp root"
    room.mkdir()
    count = room / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      set +e
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 24}}" > "${COUNT_ENV}"'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "rc=0" in result.stdout, _output(result)
    assert PASSED in result.stdout


def test_the_judge_does_not_write_an_inherited_progress_file(tmp_path) -> None:
    """C-4 executably, not by grep: handed a live progress file, the judge must
    not touch it. Its one write is the `--report-file` the shell then renders."""
    progress = tmp_path / "progress.json"
    before = json.dumps({"step": "pytest on 3 file(s) (5/5)", "done": 7, "total": 1830})
    progress.write_text(before, encoding="utf-8")
    result = _run_judge(tmp_path, exit_code=0, count=0, env_extra={PROGRESS_ENV: str(progress)})

    assert result.returncode == 1, _output(result)
    assert progress.read_text(encoding="utf-8") == before, "the judge is a second writer"
    # `*.json`, not every entry: the suite's own `isolated_db` fixture puts
    # pytest.db and its WAL sidecars in every tmp_path, so an exhaustive listing
    # here asserts something about the harness rather than about the judge.
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["count.json", "progress.json"]


# ---- the report that reaches the board -----------------------------------


def _report(tmp_path: Path, **kwargs) -> LeaseProgress:
    report_file = tmp_path / "report.json"
    result = _run_judge(tmp_path, report_file=report_file, **kwargs)
    assert report_file.exists(), f"no report was composed: {_output(result)}"
    return LeaseProgress.model_validate_json(report_file.read_bytes())


def test_the_outcome_survives_the_leases_step_truncation(tmp_path) -> None:
    """`record_progress` keeps the first 200 characters of the step. A judge that
    appends its outcome to the step label loses the word for exactly the longest
    labels — the 469-file selections, which are the runs most worth reading."""
    long_step = "pytest on 469 file(s), " + "tests/test_capacity_progress.py " * 12
    assert len(long_step) > _STEP_MAX, "the fixture must exceed what the lease keeps"
    report = _report(tmp_path, exit_code=0, count=0, step=f"{long_step}(5/5)")

    assert NOTHING in report.step[:_STEP_MAX], f"truncated to: {report.step[:_STEP_MAX]!r}"


def test_a_report_composed_with_no_step_named_is_still_a_valid_report(tmp_path) -> None:
    """`PUSH_PROGRESS_STEP` is exported by `progress_step`, so it is absent when
    the judge is called from anywhere else. `LeaseProgress.step` is
    `min_length=1`, so an empty label is a report the board rejects outright."""
    report = _report(tmp_path, exit_code=0, count=0, step=None)

    assert report.step.strip(), "an empty step is a report the board throws away"
    assert NOTHING in report.step


def test_an_unwritable_report_file_does_not_change_the_verdict(tmp_path) -> None:
    """R-7's limit: the count failing to arrive is a verdict, but the board
    failing to hear about the verdict is not. The push's own answer stands."""
    report_file = tmp_path / "no-such-dir" / "report.json"
    result = _run_judge(tmp_path, exit_code=0, count=0, report_file=report_file)

    assert result.returncode == 1, _output(result)
    assert NOTHING in _output(result)


def test_the_four_outcomes_reach_the_board_as_four_distinct_labels(tmp_path) -> None:
    """B-2 widened by one: `ran_and_passed` must be distinguishable from the
    three failures too, so a waiter reading the board can tell a finished green
    run from a collapsed one."""
    steps = [
        _report(tmp_path, exit_code=0, count=1830).step,
        _report(tmp_path, exit_code=3, count=412).step,
        _report(tmp_path, exit_code=0, count=0).step,
        _report(tmp_path, exit_code=0, count=ABSENT, count_file=tmp_path / "absent.json").step,
    ]

    assert len(set(steps)) == 4, steps


# ---- B-1: the board's rendering of a recorded zero -----------------------


def test_a_recorded_zero_is_not_rendered_as_progress() -> None:
    rendered = describe_progress("jest, changed since push base (3/3)", 0, 0)

    assert "0 done" not in rendered
    assert "collected nothing" in rendered


@pytest.mark.parametrize(
    ("done", "total", "expected"),
    [
        # A run that has only just begun, with one test collected: still progress.
        (0, 1, "step · 0/1 (0%)"),
        (0, 2, "step · 0/2 (0%)"),
        # The measured volumes, so the fix for a zero cannot change the arithmetic.
        (6618, 7573, "step · 6618/7573 (87%)"),
        (7573, 7573, "step · 7573/7573 (100%)"),
        (156, 286, "step · 156/286 (54%)"),
        # A count not yet reported is an absence, and reads as it always did.
        (0, None, "step · 0 done"),
        (9, None, "step · 9 done"),
        (None, None, "step"),
        (None, 0, "step"),
    ],
)
def test_the_neighbours_of_a_recorded_zero_are_unchanged(done, total, expected) -> None:
    """R-6, widened past the one case the criterion names. The fix for `(0, 0)`
    touches the `total` falsiness test, which every row here also passes
    through."""
    assert describe_progress("step", done, total) == expected


def test_a_recorded_zero_and_an_unreported_count_read_differently() -> None:
    """The whole of B-1 in one assertion: those are the two live shapes, and
    today they render the same, so a waiter cannot tell them apart."""
    recorded_zero = describe_progress("jest (3/3)", 0, 0)
    not_yet_reported = describe_progress("jest (3/3)", 0, None)

    assert recorded_zero != not_yet_reported


# ---- B-3 structurally, across the whole tree -----------------------------

#: Everything else may read the count file; only these two record a number in it.
COUNT_WRITERS = {"pytest_push_progress.py", "jest_push_progress.cjs"}
#: The modules whose job is to READ the count. The judge writes too — the report
#: the shell puts on the lease — so a "names the count and writes something"
#: heuristic cannot tell it from a second writer, and these are named instead.
COUNT_READERS = {
    "judge_test_run.py",
    "test-count-guard.sh",
    "server-tests.sh",
    "client-tests.sh",
}
#: Where a second writer would plausibly be added, and must not be.
_SEARCHED = ("server/loregarden", "client/src", "scripts", ".lefthook", "services", "src-tauri")
_SKIP_PARTS = {".venv", "node_modules", "__pycache__", "dist", "target", ".git"}


def test_no_module_outside_the_two_runner_plugins_writes_the_count_file() -> None:
    """B-3 is a single-writer rule, and the test-design stage checks it inside
    `.lefthook/scripts` only. The count is reachable from anywhere that can read
    an environment variable, so the search has to be as wide as the tree: a
    helper in `server/loregarden/services` that "also records the count" is
    exactly how one recorded number becomes two that disagree."""
    named: dict[str, Path] = {}
    for root in _SEARCHED:
        for path in sorted((REPO_ROOT / root).rglob("*")):
            if not path.is_file() or _SKIP_PARTS & set(path.parts):
                continue
            if path.suffix in {".png", ".jpg", ".ico", ".woff", ".woff2", ".lock"}:
                continue
            if COUNT_ENV in path.read_text(encoding="utf-8", errors="ignore"):
                named[path.name] = path

    assert COUNT_WRITERS <= set(named), f"the two runner plugins must record it; saw {set(named)}"
    unexpected = set(named) - COUNT_WRITERS - COUNT_READERS
    assert not unexpected, f"an unexpected module names {COUNT_ENV}: {sorted(unexpected)}"
    for name, path in sorted(named.items()):
        if name not in COUNT_READERS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # A reader may name the count path; it may never be the thing a write
        # redirects into. `> "$COUNT_ENV"` is how a shell helper would "also
        # record the count", and that is the second writer this forbids.
        assert '> "$' + COUNT_ENV not in text, f"{path.relative_to(REPO_ROOT)} writes {COUNT_ENV}"


def test_each_recorder_claims_the_count_file_rather_than_replacing_it() -> None:
    """B-3 is a rule about PROCESSES, and the test above is a rule about module
    names: the second writer that made a 25-test run report 6 was
    `pytest_push_progress.py` itself, in a pytest the suite had spawned, so the
    grep was satisfied while the count was wrong. The executable proof is
    `test_a_nested_runner_cannot_overwrite_the_outer_runs_count` in
    test_push_test_count_sink.py; this pins the two mechanisms it rests on,
    either of which a refactor to a plain write would remove in silence.
    """
    plugin = (SCRIPTS / "pytest_push_progress.py").read_text(encoding="utf-8")
    reporter = (SCRIPTS / "jest_push_progress.cjs").read_text(encoding="utf-8")

    # One: the path is taken out of the environment, so no child inherits it.
    assert "os.environ.pop" in plugin, "the plugin must take the count path out of os.environ"
    assert "delete process.env" in reporter, "the reporter must take the path out of process.env"
    # Two: the file is claimed, so the first recorder owns the number even when
    # some child was handed the path by hand.
    assert "O_EXCL" in plugin, "the plugin must claim the count file, not replace it"
    assert '"wx"' in reporter, "the reporter must claim the count file, not replace it"


# ---- the sink under an adversarial runner --------------------------------
#
# The count is only as good as the runner's willingness to record it. These
# exercise the real runners against the three shapes the diagonal skips: a run
# that never collected, a run whose collection raised, and a reporter handed a
# result object that does not carry the field it reads.


@pytest.fixture(name="tiny_suite")
def tiny_suite_fixture(tmp_path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return suite


def _pytest_then_judge(
    tmp_path: Path, *, args: tuple[str, ...], workers: str
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    """Run the real plugin, then judge whatever it recorded — the push's own path."""
    count = tmp_path / "count.json"
    env = {k: v for k, v in os.environ.items() if k not in (DECLARE_ENV, PROGRESS_ENV)}
    env.update({"PYTHONPATH": str(SCRIPTS), COUNT_ENV: str(count)})
    for inherited in INHERITED_PYTEST_STATE:
        env.pop(inherited, None)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_push_progress",
         "-n", workers, "-p", "no:cacheprovider", *args],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=110, check=False,
    )  # fmt: skip
    verdict = subprocess.run(
        [sys.executable, str(JUDGE), "--runner", "pytest", "--exit", str(run.returncode)],
        env={**env, "PUSH_PROGRESS_STEP": "pytest on 1 file(s) (5/5)"},
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    return run, verdict


@pytest.mark.parametrize("workers", ["0", "1", "2"])
def test_the_sink_records_the_count_under_a_single_worker_too(
    tiny_suite, tmp_path, workers
) -> None:
    """R-1 at its boundary. `-n 1` is still xdist — the controller path, one
    worker — and `test-workers.sh` computes that number from the capacity grant,
    so a one-cpu grant is the shape a developer on a busy machine actually gets."""
    run, verdict = _pytest_then_judge(tmp_path, args=(str(tiny_suite),), workers=workers)

    assert run.returncode == 0, _output(run)
    assert json.loads((tmp_path / "count.json").read_text(encoding="utf-8"))["count"] == 1
    assert verdict.returncode == 0, _output(verdict)
    assert PASSED in _output(verdict)


def test_a_path_the_runner_cannot_find_is_never_reported_as_a_pass(tiny_suite, tmp_path) -> None:
    """G-4 end to end through the real runner, and a premise re-measured here.

    The original of this test asserted `could_not_run` on the reasoning that
    collection never finishes for a path pytest cannot find. Measured on pytest
    9.1.1 in this tree: it exits 4, and `pytest_collection_finish` DOES fire,
    with zero items — so the sink records a real zero, and the judge calls it
    `verified_nothing`. Getting 69 here instead would need the sink to re-derive
    pytest's own path check, or the judge to read exit 4, which C-2 forbids.

    Either way the push is blocked and the terminal names the selector paths,
    which is the right next action for a selection that named a missing file.
    `server-tests.sh` also rejects that before pytest runs (A-4), so the real
    pre-push path does not reach here at all.
    """
    del tiny_suite
    run, verdict = _pytest_then_judge(tmp_path, args=("tests/no_such_file.py",), workers="0")

    assert run.returncode == 4, _output(run)
    assert verdict.returncode != 0, _output(verdict)
    assert PASSED not in _output(verdict), _output(verdict)
    assert NOTHING in _output(verdict) or UNAVAILABLE in _output(verdict), _output(verdict)
    assert "-k" in _output(verdict), "the line must name what to check"


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_collection_error_is_never_reported_as_a_pass(tmp_path, workers) -> None:
    """A test file whose import raises. Measured in this tree: pytest exits 2
    serially and 1 under `-n 2` for the same broken import, which is itself a
    reason not to read a verdict off the code. Whatever the sink recorded, the
    push must not be let through: a file that could not be imported collected no
    tests, and both readings of that are failures."""
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_broken.py").write_text("import no_such_module_at_all\n", encoding="utf-8")
    run, verdict = _pytest_then_judge(tmp_path, args=(str(suite),), workers=workers)

    assert run.returncode != 0, _output(run)
    assert verdict.returncode != 0, _output(verdict)
    assert NOTHING in _output(verdict) or UNAVAILABLE in _output(verdict), _output(verdict)
    assert PASSED not in _output(verdict), _output(verdict)


@pytest.mark.parametrize(
    ("results", "why"),
    [
        ("{}", "jest handed the reporter no suite count at all"),
        ("{ numTotalTestSuites: undefined }", "the field is there and says nothing"),
        ("{ numTotalTestSuites: null }", "an explicit null"),
        ('{ numTotalTestSuites: "286" }', "a string, which must not read as 286"),
    ],
)
def test_the_jest_reporter_never_records_a_count_jest_did_not_give_it(
    tmp_path, results, why
) -> None:
    """A-3's number is `results.numTotalTestSuites`, and a reporter that writes
    whatever that attribute holds turns a jest API drift into a recorded count.
    `JSON.stringify({count: undefined})` is `{}` and `NaN` becomes `null`, so the
    fail-closed outcome is `could_not_run`. What it must never be is a pass."""
    node = shutil.which("node")
    assert node, "the client suite needs node; so does its reporter"
    count = tmp_path / "count.json"
    script = (
        f"const R = require({str(SCRIPTS / 'jest_push_progress.cjs')!r});\n"
        f"const r = new R({{}}, {{}});\nr.onRunStart({results});\nr.onRunComplete({{}}, {results});\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in (DECLARE_ENV, PROGRESS_ENV)}
    env[COUNT_ENV] = str(count)
    subprocess.run(
        [node, "-e", script],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    verdict = subprocess.run(
        [sys.executable, str(JUDGE), "--runner", "jest", "--exit", "0", "--listed-files", "287"],
        env={**env, "PUSH_PROGRESS_STEP": "jest (3/3)"},
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip

    assert verdict.returncode == GATE_EX_UNAVAILABLE, f"{why}: {_output(verdict)}"
    assert UNAVAILABLE in _output(verdict), f"{why}: {_output(verdict)}"
