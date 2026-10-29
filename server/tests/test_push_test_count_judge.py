"""A pre-push test run that collected nothing is not a pass: the judge.

One standalone judge, invoked by both pre-push test scripts after their runner
exits, reads the count the runner's own collection recorded and resolves exactly
one of four outcomes — naming it in words, in one line, and in a distinct exit
code. These tests pin the judge and the seam the two scripts call it through
(`.lefthook/scripts/test-count-guard.sh`), plus the one-writer rule that keeps
the terminal's count and the queue board's count the same recorded number.

Criterion map (lg-build-verification-521):

| criterion | tests here |
|---|---|
| G-1 one judge, four outcomes | `test_the_judge_resolves_*`, `test_the_seam_passes_the_runners_own_code_through` |
| G-3 zero is a failure | `test_the_judge_resolves_verified_nothing_on_a_recorded_zero`, `test_the_seam_fails_a_run_that_collected_nothing` |
| G-4 absence is not zero | `test_the_judge_resolves_could_not_run_when_no_count_was_recorded`, `test_an_unwritable_count_is_could_not_run_not_a_zero` |
| G-5 explicit declaration | `test_a_declared_empty_selection_passes_and_still_says_nothing_was_verified` |
| G-6 client rule | `test_the_jest_rule_*` |
| T-1 three outcomes in words | `test_each_outcome_names_itself_in_words` |
| T-2 the next action | `test_the_verified_nothing_line_names_the_declaration_and_what_to_check`, `test_no_output_of_the_judge_offers_no_verify` |
| T-3 real volume | `test_the_judge_prints_one_line_at_every_measured_volume` |
| B-2 could-not-run is distinguishable on the board | `test_the_three_failing_outcomes_reach_the_lease_distinctly` |
| B-3 one recorded count, two readers | `test_only_the_runners_own_plugin_and_reporter_write_the_count_file`, `test_the_judges_report_is_the_steps_single_terminal_write` |
| N-1 no new interactive surface | `test_the_judge_is_identical_with_no_tty_and_no_stdin` |
| S-1 scope stated for verify | `test_the_unavailable_code_is_the_one_gate_runner_already_uses` |
| S-2 premise correction honoured | `test_nothing_parses_a_collected_count_out_of_stdout` |

The premise correction (section 0 of the spec) is why no test here asserts that
`server-tests.sh` exits 0 on a zero-collection run: measured on pytest 9.1.1 in
this tree, a non-matching `-k` exits 5 serially and under `-n 2`, and a bad path
exits 4. The judge must not infer the count from any of those codes.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from loregarden.services.capacity_progress import LeaseProgress, describe_progress
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
JUDGE = SCRIPTS / "judge_test_run.py"
GUARD_SEAM = SCRIPTS / "test-count-guard.sh"
SERVER_TESTS = SCRIPTS / "server-tests.sh"
CLIENT_TESTS = SCRIPTS / "client-tests.sh"

#: The env var naming the file the runner's own collection writes its count to.
COUNT_ENV = "LOREGARDEN_TEST_COUNT_FILE"
#: The env var that declares a deliberately empty selection (A-1).
DECLARE_ENV = "LOREGARDEN_ALLOW_NO_TESTS"
PROGRESS_ENV = "LOREGARDEN_PROGRESS_FILE"

#: Measured from live `docker_leases.progress_total` (T-3): server narrowed runs
#: 24 to 6,618 collected, 7,573 for the full suite; client 156 to 286 suites.
MEASURED_VOLUMES = (24, 156, 286, 6618, 7573)


# ---- helpers ---------------------------------------------------------------


def _write_count(path: Path, count: int) -> None:
    """What the runner's plugin/reporter records: the count, and nothing inferred."""
    path.write_text(json.dumps({"count": count}), encoding="utf-8")


def _judge(
    tmp_path: Path,
    *,
    runner: str,
    exit_code: int,
    count: int | None = None,
    count_file: Path | None = None,
    declared: bool = False,
    listed_files: int | None = None,
    step: str = "pytest on 3 file(s) (5/5)",
    report_file: Path | None = None,
    stdin_closed: bool = False,
) -> subprocess.CompletedProcess[str]:
    path = count_file if count_file is not None else tmp_path / "count.json"
    if count is not None:
        _write_count(path, count)
    env = {**os.environ, COUNT_ENV: str(path), "PUSH_PROGRESS_STEP": step}
    env.pop(DECLARE_ENV, None)
    env.pop(PROGRESS_ENV, None)
    if declared:
        env[DECLARE_ENV] = "1"
    argv = [sys.executable, str(JUDGE), "--runner", runner, "--exit", str(exit_code)]
    if listed_files is not None:
        argv += ["--listed-files", str(listed_files)]
    if report_file is not None:
        argv += ["--report-file", str(report_file)]
    return subprocess.run(
        argv,
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL if stdin_closed else None,
    )


def _one_line(result: subprocess.CompletedProcess[str]) -> str:
    """The judge's whole output, asserted to be a single line (T-3)."""
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    assert len(lines) == 1, f"the judge printed {len(lines)} lines: {lines}"
    return lines[0]


def _bash(script: str, tmp_path: Path, env: dict[str, str] | None = None):
    """Exercise the seam the two scripts call the judge through."""
    body = (
        f'set -uo pipefail\nsource "{GUARD_SEAM}"\nsource "{SCRIPTS}/push-progress.sh"\n{script}\n'
    )
    return subprocess.run(
        ["bash", "-c", body],
        cwd=tmp_path,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# ---- G-1: one judge, four outcomes -----------------------------------------


def test_the_judge_resolves_ran_and_passed(tmp_path) -> None:
    result = _judge(tmp_path, runner="pytest", exit_code=0, count=1830)

    assert result.returncode == 0
    line = _one_line(result)
    assert "passed" in line
    assert "1830" in line


def test_the_judge_resolves_ran_and_failed_with_the_runners_own_code(tmp_path) -> None:
    """A runner that failed WITH a recorded count keeps its own verdict and code."""
    result = _judge(tmp_path, runner="pytest", exit_code=3, count=412)

    assert result.returncode == 3
    assert "FAILED" in _one_line(result)


def test_the_judge_resolves_verified_nothing_on_a_recorded_zero(tmp_path) -> None:
    result = _judge(tmp_path, runner="pytest", exit_code=0, count=0)

    assert result.returncode == 1
    assert "VERIFIED NOTHING" in _one_line(result)


def test_the_judge_resolves_could_not_run_when_no_count_was_recorded(tmp_path) -> None:
    """No count file at all: an absence, not a zero (G-4)."""
    missing = tmp_path / "never-written.json"
    result = _judge(tmp_path, runner="pytest", exit_code=0, count_file=missing)

    assert result.returncode == GATE_EX_UNAVAILABLE
    line = _one_line(result)
    assert "COULD NOT RUN" in line
    assert str(missing) in line


def test_a_recorded_zero_outranks_the_runners_own_non_zero_code(tmp_path) -> None:
    """Measured: pytest exits 5 on a zero-collection run. The judge must call that
    `verified_nothing` (exit 1) from the recorded count, not `ran_and_failed` from
    the code — R-2, and the whole point of judging a count."""
    result = _judge(tmp_path, runner="pytest", exit_code=5, count=0)

    assert result.returncode == 1
    assert "VERIFIED NOTHING" in _one_line(result)


def test_an_unparseable_count_is_could_not_run_not_a_zero(tmp_path) -> None:
    path = tmp_path / "count.json"
    path.write_text("{not json", encoding="utf-8")
    result = _judge(tmp_path, runner="pytest", exit_code=0, count_file=path)

    assert result.returncode == GATE_EX_UNAVAILABLE
    assert "COULD NOT RUN" in _one_line(result)


def test_an_unwritable_count_is_could_not_run_not_a_zero(tmp_path) -> None:
    """R-7: a sink that could not write names the file and the reason, so the
    operator can act on it instead of reading a blocked push as a mystery."""
    unwritable = tmp_path / "no-such-dir" / "count.json"
    result = _judge(tmp_path, runner="pytest", exit_code=0, count_file=unwritable)

    assert result.returncode == GATE_EX_UNAVAILABLE
    line = _one_line(result)
    assert str(unwritable) in line
    assert "COULD NOT RUN" in line


def test_the_count_file_env_being_unset_is_also_could_not_run(tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in (COUNT_ENV, DECLARE_ENV)}
    result = subprocess.run(
        [sys.executable, str(JUDGE), "--runner", "pytest", "--exit", "0"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip

    assert result.returncode == GATE_EX_UNAVAILABLE
    assert "COULD NOT RUN" in _one_line(result)


# ---- G-5: an explicitly declared empty selection ---------------------------


def test_a_declared_empty_selection_passes_and_still_says_nothing_was_verified(tmp_path) -> None:
    result = _judge(tmp_path, runner="pytest", exit_code=5, count=0, declared=True)

    assert result.returncode == 0
    line = _one_line(result)
    assert "VERIFIED NOTHING" in line
    assert f"{DECLARE_ENV}=1" in line


def test_the_declaration_does_not_excuse_a_missing_count(tmp_path) -> None:
    """Declaring an empty selection says "zero is fine here"; it cannot say
    anything about a run whose collection never reported at all."""
    result = _judge(
        tmp_path, runner="pytest", exit_code=0, count_file=tmp_path / "gone.json", declared=True
    )

    assert result.returncode == GATE_EX_UNAVAILABLE
    assert "COULD NOT RUN" in _one_line(result)


def test_the_declaration_does_not_excuse_a_real_failure(tmp_path) -> None:
    result = _judge(tmp_path, runner="pytest", exit_code=2, count=412, declared=True)

    assert result.returncode == 2
    assert "FAILED" in _one_line(result)


# ---- G-6: the client rule, judged against a second independent probe -------


def test_the_jest_rule_passes_a_legitimately_empty_changed_since_selection(tmp_path) -> None:
    """Zero recorded suites, but the harness can still see test files: that is
    "no test imports what you changed", which is a real answer here."""
    result = _judge(tmp_path, runner="jest", exit_code=0, count=0, listed_files=287)

    assert result.returncode == 0
    assert "VERIFIED NOTHING" in _one_line(result)


def test_the_jest_rule_calls_a_harness_that_lists_nothing_could_not_run(tmp_path) -> None:
    """Measured: `npx jest --listTests` prints one empty line and exits 0 when the
    selection resolves to nothing. Zero listed files means drifted `testMatch`, a
    renamed `client/src`, or a bogus pattern — not an empty selection."""
    result = _judge(tmp_path, runner="jest", exit_code=0, count=0, listed_files=0)

    assert result.returncode == GATE_EX_UNAVAILABLE
    assert "COULD NOT RUN" in _one_line(result)


def test_the_jest_probe_is_not_consulted_when_something_ran(tmp_path) -> None:
    """R-5: the probe is paid only on a zero count, and never overrides a real run."""
    result = _judge(tmp_path, runner="jest", exit_code=0, count=286, listed_files=0)

    assert result.returncode == 0
    assert "passed" in _one_line(result)


def test_a_zero_jest_count_with_no_probe_result_is_could_not_run(tmp_path) -> None:
    """The probe is how a zero suite count is established on the client; without
    it the judge has no second reading, and an absence is never a pass."""
    result = _judge(tmp_path, runner="jest", exit_code=0, count=0)

    assert result.returncode == GATE_EX_UNAVAILABLE
    assert "COULD NOT RUN" in _one_line(result)


# ---- T-1 / T-2: the terminal says which of four things happened ------------


@pytest.mark.parametrize(
    ("kwargs", "word", "forbidden"),
    [
        ({"exit_code": 0, "count": 1830}, "passed", "VERIFIED NOTHING"),
        ({"exit_code": 3, "count": 412}, "FAILED", "VERIFIED NOTHING"),
        ({"exit_code": 0, "count": 0}, "VERIFIED NOTHING", "FAILED"),
        ({"exit_code": 0, "count": None}, "COULD NOT RUN", "FAILED"),
    ],
)
def test_each_outcome_names_itself_in_words(tmp_path, kwargs, word, forbidden) -> None:
    """From the output alone, with no exit code, which of the four it was."""
    result = _judge(tmp_path, runner="pytest", **kwargs)
    line = _one_line(result)

    assert word in line
    assert forbidden not in line


@pytest.mark.parametrize(
    ("runner", "hints"),
    [
        ("pytest", ("-k", "-m")),
        ("jest", ("--changedSince", "testMatch")),
    ],
)
def test_the_verified_nothing_line_names_the_declaration_and_what_to_check(
    tmp_path, runner, hints
) -> None:
    listed = 0 if runner == "jest" else None
    # The jest zero-suite failure a person must act on is the listed-nothing one.
    result = _judge(tmp_path, runner=runner, exit_code=0, count=0, listed_files=listed)
    line = _one_line(result)

    assert f"{DECLARE_ENV}=1" in line
    for hint in hints:
        assert hint in line, f"{runner}: the line must name {hint}"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"exit_code": 0, "count": 1830}, 0),
        ({"exit_code": 3, "count": 412}, 3),
        ({"exit_code": 0, "count": 0}, 1),
        ({"exit_code": 0, "count": None}, GATE_EX_UNAVAILABLE),
    ],
)
def test_no_output_of_the_judge_offers_no_verify(tmp_path, kwargs, code) -> None:
    """A guard whose own advice is to bypass the hook is not a guard."""
    result = _judge(tmp_path, runner="pytest", **kwargs)

    assert result.returncode == code, result.stdout + result.stderr
    assert "--no-verify" not in result.stdout + result.stderr


@pytest.mark.parametrize("count", MEASURED_VOLUMES)
def test_the_judge_prints_one_line_at_every_measured_volume(tmp_path, count) -> None:
    """T-3: one line per suite, no per-test or per-file detail of its own."""
    passed = _judge(tmp_path, runner="pytest", exit_code=0, count=count)
    failed = _judge(tmp_path, runner="pytest", exit_code=1, count=count)

    assert (passed.returncode, failed.returncode) == (0, 1)
    assert str(count) in _one_line(passed)
    assert _one_line(failed)


# ---- N-1: no new interactive surface ---------------------------------------


def test_the_judge_is_identical_with_no_tty_and_no_stdin(tmp_path) -> None:
    attached = _judge(tmp_path, runner="pytest", exit_code=0, count=0)
    closed = _judge(tmp_path, runner="pytest", exit_code=0, count=0, stdin_closed=True)

    assert closed.returncode == 1, closed.stdout + closed.stderr
    assert "VERIFIED NOTHING" in _one_line(closed)
    assert (closed.returncode, closed.stdout, closed.stderr) == (
        attached.returncode,
        attached.stdout,
        attached.stderr,
    )


def test_the_judge_reads_no_stdin(tmp_path) -> None:
    """Fed a terminal's worth of input, it consumes none of it and still answers."""
    _write_count(tmp_path / "count.json", 0)
    env = {**os.environ, COUNT_ENV: str(tmp_path / "count.json"), "PUSH_PROGRESS_STEP": "pytest"}
    env.pop(DECLARE_ENV, None)
    result = subprocess.run(
        [sys.executable, str(JUDGE), "--runner", "pytest", "--exit", "0"],
        env=env, cwd=tmp_path, input="y\ny\ny\n", capture_output=True,
        text=True, timeout=60, check=False,
    )  # fmt: skip

    assert result.returncode == 1
    assert "VERIFIED NOTHING" in _one_line(result)


# ---- B-2 / B-3: one recorded count, two readers ----------------------------


def _report(tmp_path: Path, **kwargs) -> LeaseProgress:
    # A count file of its own per call: `count=None` means "this run recorded
    # nothing", and a shared path would hand it the count an earlier call in the
    # same tmp_path left behind — which the judge is right to read as a zero.
    name = f"{kwargs['exit_code']}-{kwargs.get('count')}"
    kwargs.setdefault("count_file", tmp_path / f"count-{name}.json")
    report_file = tmp_path / f"report-{name}.json"
    _judge(tmp_path, report_file=report_file, **kwargs)
    return LeaseProgress.model_validate_json(report_file.read_bytes())


def test_the_three_failing_outcomes_reach_the_lease_distinctly(tmp_path) -> None:
    """B-2: a push waiting in line can tell a collapsed run from a red one. Today
    13 live leases carry a bare `FAILED: …` with both counts NULL."""
    failed = _report(tmp_path, runner="pytest", exit_code=3, count=412)
    nothing = _report(tmp_path, runner="pytest", exit_code=0, count=0)
    unavailable = _report(tmp_path, runner="pytest", exit_code=0, count=None)

    steps = [failed.step, nothing.step, unavailable.step]
    assert len(set(steps)) == 3, steps
    assert "FAILED" in failed.step
    assert "VERIFIED NOTHING" in nothing.step
    assert "COULD NOT RUN" in unavailable.step


def test_the_report_for_a_test_step_carries_the_recorded_count(tmp_path) -> None:
    """B-3: the number the judge judged is the number the board displays."""
    failed = _report(tmp_path, runner="pytest", exit_code=3, count=412)
    passed = _report(tmp_path, runner="pytest", exit_code=0, count=1830)

    assert failed.total == 412
    assert passed.total == 1830
    assert describe_progress(passed.step, passed.done, passed.total).startswith(passed.step)


def test_a_could_not_run_report_claims_no_count(tmp_path) -> None:
    """There is no count to show, and inventing a zero is the defect B-1 fixes."""
    unavailable = _report(tmp_path, runner="pytest", exit_code=0, count=None)

    assert (unavailable.done, unavailable.total) == (None, None)


def test_only_the_runners_own_plugin_and_reporter_write_the_count_file() -> None:
    """B-3, structurally: single writer per file. Everything else only reads it.

    A module-name rule, and so blind to the same module recording twice from two
    processes — see `test_a_nested_runner_cannot_overwrite_the_outer_runs_count`
    (test_push_test_count_sink.py) for the process half, which is where the real
    defect lived.
    """
    writers = {"pytest_push_progress.py", "jest_push_progress.cjs"}
    readers = {"judge_test_run.py", "test-count-guard.sh", "server-tests.sh", "client-tests.sh"}
    seen = set()
    for path in sorted(SCRIPTS.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if COUNT_ENV in text:
            seen.add(path.name)
    assert writers <= seen
    assert seen <= writers | readers, f"an unexpected module names {COUNT_ENV}: {seen}"
    judge = JUDGE.read_text(encoding="utf-8")
    assert PROGRESS_ENV not in judge, "the judge must not be a second writer of the progress file"


def test_the_judges_report_is_the_steps_single_terminal_write(tmp_path) -> None:
    """C-4: the final report is written once, after the runner exited, when exactly
    one writer is live. `progress_finish` must not then replace it with a bare
    `FAILED: …` and two NULL counts — the measured mechanism behind those 13 rows."""
    progress = tmp_path / "progress.json"
    count = tmp_path / "count.json"
    script = f"""
      progress_begin "server-tests" 5
      progress_step "pytest on 3 file(s)"
      export {COUNT_ENV}="{count}"
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 412}}" > "${COUNT_ENV}"; exit 3'
      rc=$?
      progress_finish "$rc"
      exit "$rc"
    """
    result = _bash(script, tmp_path, env={PROGRESS_ENV: str(progress)})

    assert result.returncode == 3, result.stdout + result.stderr
    report = LeaseProgress.model_validate_json(progress.read_bytes())
    assert "FAILED" in report.step
    assert report.total == 412, "progress_finish replaced the judge's count"


# ---- G-1 at the seam: the two scripts call it, and pass its exit through ----


def test_the_seam_passes_the_runners_own_code_through(tmp_path) -> None:
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 24}}" > "${COUNT_ENV}"; exit 7'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "rc=7" in result.stdout, result.stdout + result.stderr
    assert "FAILED" in result.stdout


def test_the_seam_passes_a_green_run_through(tmp_path) -> None:
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 24}}" > "${COUNT_ENV}"'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "rc=0" in result.stdout, result.stdout + result.stderr
    assert "passed" in result.stdout


def test_the_seam_fails_a_run_that_collected_nothing(tmp_path) -> None:
    """G-3 through the seam both scripts use: a runner that recorded zero, with no
    declaration, fails the push and says so in words."""
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      run_and_judge pytest -- sh -c 'printf "{{\\"count\\": 0}}" > "${COUNT_ENV}"; exit 5'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert "VERIFIED NOTHING" in result.stdout


def test_the_seam_fails_a_runner_that_recorded_no_count_at_all(tmp_path) -> None:
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      run_and_judge pytest -- sh -c 'exit 0'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert f"rc={GATE_EX_UNAVAILABLE}" in result.stdout, result.stdout + result.stderr
    assert "COULD NOT RUN" in result.stdout


def test_the_seam_runs_the_jest_probe_only_on_a_zero_count(tmp_path) -> None:
    """G-6 through the seam: the listed-files probe is paid only when the recorded
    suite count was zero, and its result decides empty-selection vs found-nothing."""
    count = tmp_path / "count.json"
    probed = tmp_path / "probed"
    script = f"""
      export {COUNT_ENV}="{count}"
      LIST_PROBE=(sh -c 'echo ran >> "{probed}"; printf "%s\\n" /a.test.ts /b.test.ts')
      run_and_judge jest -- sh -c 'printf "{{\\"count\\": 0}}" > "${COUNT_ENV}"'
      echo "empty=$?"
      run_and_judge jest -- sh -c 'printf "{{\\"count\\": 286}}" > "${COUNT_ENV}"'
      echo "full=$?"
    """
    result = _bash(script, tmp_path)

    assert "empty=0" in result.stdout, result.stdout + result.stderr
    assert "full=0" in result.stdout
    assert probed.read_text().count("ran") == 1, "the probe must be paid only on a zero count"


def test_the_seam_calls_a_harness_that_lists_nothing_could_not_run(tmp_path) -> None:
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      LIST_PROBE=(sh -c 'echo ""')
      run_and_judge jest -- sh -c 'printf "{{\\"count\\": 0}}" > "${COUNT_ENV}"'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert f"rc={GATE_EX_UNAVAILABLE}" in result.stdout, result.stdout + result.stderr


# ---- the wiring in the two named scripts (C-1) -----------------------------


@pytest.mark.parametrize("script", [SERVER_TESTS, CLIENT_TESTS])
def test_each_pre_push_script_sets_the_count_file_and_judges_through_the_seam(script) -> None:
    source = script.read_text(encoding="utf-8")

    assert GUARD_SEAM.name in source, "the judge is reached through the one seam"
    assert COUNT_ENV in source, f"{script.name} must always set {COUNT_ENV}"
    assert "run_and_judge" in source


def test_the_server_script_judges_every_pytest_invocation() -> None:
    """Both branches — the full-suite fallback and the narrowed run."""
    source = SERVER_TESTS.read_text(encoding="utf-8")
    pytest_runs = source.count(" pytest -x -q")

    assert pytest_runs == 2, f"expected the two known pytest invocations, found {pytest_runs}"
    assert source.count("run_and_judge pytest") == pytest_runs


def test_the_client_script_judges_every_jest_invocation() -> None:
    """All four branches: forced-full, no-base, wide-change, and --changedSince."""
    source = CLIENT_TESTS.read_text(encoding="utf-8")

    assert source.count("npm test") == 4, "the four known jest invocations"
    assert source.count("run_and_judge jest") == 4
    assert "--listTests" in source, "G-6: the second probe over the same selection"
    assert "--passWithNoTests" in source, "stays on the narrowed branch; the guard judges"


def test_the_count_file_lives_in_the_scripts_own_temp_root_and_is_not_the_progress_file() -> None:
    """A-2 and C-4: a private path, removed on exit, never the progress file."""
    for script in (SERVER_TESTS, CLIENT_TESTS):
        source = script.read_text(encoding="utf-8")
        assignment = [ln for ln in source.splitlines() if re.search(rf"{COUNT_ENV}=", ln)]
        assert assignment, f"{script.name} must always set {COUNT_ENV}"
        for line in assignment:
            assert PROGRESS_ENV not in line, "the count file is never the progress file (C-4)"
        assert "mktemp" in source, f"{script.name}: a private temp root for the count file"
        assert re.search(r"trap .*rm -rf", source), f"{script.name} removes it on exit"


def test_enforcement_lives_in_the_two_scripts_only() -> None:
    """C-1: not in conftest.py, not in addopts. A conftest hook would make every
    ad-hoc `pytest -k one_thing` hostile, which is how a guard earns a bypass."""
    conftests = sorted((REPO_ROOT / "server").rglob("conftest.py"))
    conftests = [path for path in conftests if ".venv" not in path.parts]
    pyproject = (REPO_ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")

    assert conftests, "the server's conftest is where a hostile guard would live"
    for conftest in conftests:
        source = conftest.read_text(encoding="utf-8")
        assert COUNT_ENV not in source, f"{conftest}: enforcement lives in the scripts (C-1)"
        assert "judge_test_run" not in source
    assert COUNT_ENV not in pyproject
    assert "judge_test_run" not in pyproject
    assert "addopts" not in pyproject, "no addopts, so an ad-hoc `pytest -k one_thing` is unarmed"


# ---- S-1 / S-2: the stated scope, and the corrected premise ----------------


def test_the_unavailable_code_is_the_one_gate_runner_already_uses() -> None:
    """S-1: `could_not_run` reuses gate_runner's existing vocabulary. The pointer
    is to the constant, not to a call site, and that module gets no edit."""
    assert GATE_EX_UNAVAILABLE == 69
    assert JUDGE.exists(), "the judge is what reuses the vocabulary"
    gate_runner = (REPO_ROOT / "server" / "loregarden" / "services" / "gate_runner.py").read_text(
        encoding="utf-8"
    )
    assert "judge_test_run" not in gate_runner
    assert COUNT_ENV not in gate_runner


def test_nothing_parses_a_count_out_of_the_runners_output() -> None:
    """S-2 and C-2: measured, `-q` prints no `collected N items` line on a run, so
    a stdout parser matches nothing in exactly the cases this guard is about. The
    judge reads a recorded number; it never reads the runner."""
    pattern = re.compile(r"collected\s*(\\d|\(|N |%|\$)")
    for path in sorted(SCRIPTS.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not pattern.search(text), f"{path.name} parses a count out of the output"
    judge = JUDGE.read_text(encoding="utf-8")
    assert "subprocess" not in judge, "the judge does not run the runner, so it cannot read it"
    assert "sys.stdin" not in judge


def test_the_seam_leaves_the_runners_own_output_alone(tmp_path) -> None:
    """The corollary: the runner's output is not captured and scanned, it goes to
    the terminal as it always did (and the per-file list stays unchanged, C-5)."""
    count = tmp_path / "count.json"
    script = f"""
      export {COUNT_ENV}="{count}"
      run_and_judge pytest -- sh -c 'echo "7 passed in 1.23s"; printf "{{\\"count\\": 7}}" > "${COUNT_ENV}"'
      echo "rc=$?"
    """
    result = _bash(script, tmp_path)

    assert "7 passed in 1.23s" in result.stdout, result.stdout + result.stderr
    assert "rc=0" in result.stdout


def test_the_judge_needs_only_the_standard_library() -> None:
    """It runs from a git hook, before any project environment is guaranteed."""
    tree = ast.parse(JUDGE.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):  # py-org: allow-isinstance - walking an AST
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance - walking an AST
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names) | {""}, roots


def test_the_judge_parses_under_the_oldest_python_a_hook_may_find() -> None:
    """A bare `python3` in a hook is 3.9 on macOS; a 3.10+ construct in the judge
    would make every push on such a box `could_not_run` for the wrong reason."""
    source = JUDGE.read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 9))


def test_the_judge_is_executable_and_shebanged() -> None:
    assert JUDGE.exists(), "the standalone judge module"
    assert JUDGE.read_text(encoding="utf-8").startswith("#!")
    assert shutil.which("bash"), "the seam is bash; the suite needs it"
