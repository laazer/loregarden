#!/usr/bin/env python3
"""Did this push verify anything? One judge, four outcomes, one line.

Both pre-push test scripts call this after their runner exits, through
`test-count-guard.sh:run_and_judge`. It reads the count the runner's OWN
collection recorded in `$LOREGARDEN_TEST_COUNT_FILE` and resolves exactly one
of four outcomes, naming it in words:

    ran_and_passed    count >= 1, runner exited 0        exit 0   "passed"
    ran_and_failed    runner exited non-zero, count kept the runner's own code
    verified_nothing  a count was recorded and it is 0   exit 1   "VERIFIED NOTHING"
    could_not_run     no usable count was recorded       exit 69  "COULD NOT RUN"

Exit 69 is `sysexits.h` EX_UNAVAILABLE, the code `services/gate_runner.py`
already uses for `GateOutcome.UNAVAILABLE`; this reuses that vocabulary.

Two rules do the work, and both are the point rather than detail:

* **The count is never inferred.** Not from the runner's output (measured: `-q`
  prints no count line at all on a run) and not from an exit code (measured: a
  zero-collection pytest exits 5, and a broken import exits 2 serially but 1
  under `-n 2`). The judge never starts the runner and never reads it.
* **A recorded zero outranks the runner's own non-zero code**, and an absent
  count outranks everything. Any other order reports pytest's 5 where a run
  that verified nothing needs its own answer, and makes the declared empty
  selection unreachable.

Nothing here writes the progress file: `--report-file` composes a report the
shell then puts in place, so the step has exactly one terminal writer.

Standard library only, and it parses under 3.9 — a hook's bare `python3` is 3.9
on macOS, and a 3.10 construct here would fail every push for the wrong reason.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

#: Written only by the runner's own plugin/reporter; read by this judge.
COUNT_ENV = "LOREGARDEN_TEST_COUNT_FILE"
#: Declares a deliberately empty selection. An environment variable because
#: lefthook invokes both scripts with no arguments, so a flag is unreachable.
DECLARE_ENV = "LOREGARDEN_ALLOW_NO_TESTS"
#: The one literal value that declares. A truthiness test would read `=0`,
#: `=false` and `=no` as a declaration, so an operator who meant "off" would
#: silently turn the guard off — the wrong direction for a fail-closed guard.
DECLARED = "1"
#: The step label `progress_step` exported, used for the board's report.
STEP_ENV = "PUSH_PROGRESS_STEP"

#: `sysexits.h` EX_UNAVAILABLE, == gate_runner.GATE_EX_UNAVAILABLE.
EX_UNAVAILABLE = 69
#: A run that verified nothing: its own code, distinct from any runner's.
EX_NOTHING = 1

PASSED = "passed"
FAILED = "FAILED"
NOTHING = "VERIFIED NOTHING"
UNAVAILABLE = "COULD NOT RUN"

#: What to look at, per runner. The operator reading a blocked push has nothing
#: else to go on, and `--no-verify` is never one of the options.
HINTS = {
    "pytest": "check the paths the selector chose and the -k / -m filters",
    "jest": "check the --changedSince base and the testMatch selection",
}
#: pytest counts tests; jest only knows files up front, so it counts suites.
UNITS = {"pytest": "test", "jest": "test suite"}


def _read_count(raw):
    """`(count, problem)` — exactly one is None.

    Fail closed, and widely: an empty file, a truncated object, a string zero,
    a bool (which `==` 1 in Python), a negative or a fraction are each an
    absence of a usable count, never a zero a declaration could wave through.
    """
    if not raw:
        return None, f"${COUNT_ENV} names no file, so nothing recorded a count"
    path = Path(raw)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"{path} could not be read ({exc.strerror or exc})"
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return None, f"{path} holds no readable report ({exc})"
    if type(payload) is not dict or "count" not in payload:
        return None, f"{path} holds no count"
    value = payload["count"]
    if type(value) is not int or value < 0:  # a bool is not a count, nor is "0"
        return None, f"{path} holds {value!r}, which is not a number of tests"
    return value, None


def _failure_code(runner_exit):
    """The runner's own code, as a shell can carry it.

    `sys.exit(256)` exits 0 — a shell keeps only the low byte of a wait status —
    and Python reports a signalled child as a negative number. Forwarding either
    unexamined turns a failed run into a green push, so a code that cannot be
    represented falls back to a non-zero one.
    """
    return runner_exit % 256 or EX_NOTHING


def _report(word, step, done, total):
    """The board's view of the same verdict.

    The outcome word goes FIRST: `record_progress` keeps 200 characters of the
    step, and a word appended to a 469-file label falls off for exactly the runs
    most worth reading.
    """
    label = (step or "").strip() or "the test step"
    return {"step": f"{word}: {label}", "done": done, "total": total}


def _judge_zero(runner, listed_files, step):
    """A recorded zero: the case this guard exists for."""
    hint = HINTS[runner]
    unit = UNITS[runner]
    declared = os.environ.get(DECLARE_ENV) == DECLARED
    if runner == "jest" and not listed_files:
        # The second, independent probe: `npx jest --listTests` over the same
        # selection. Zero suites with files on disk is "no test imports what you
        # changed", a real answer. Zero files is a harness that found nothing —
        # a drifted testMatch, a renamed client/src, a bogus pattern — and that
        # is an absence, which no declaration can speak to.
        recorded_in = os.environ.get(COUNT_ENV, "")
        line = (
            f"pre-push verification: {UNAVAILABLE} - the recorded suite count in "
            f"{recorded_in} is 0 and jest --listTests found no test file at all, so the "
            f"harness had nothing to run; {hint}; {DECLARE_ENV}={DECLARED} declares an "
            f"empty selection, never a harness that found nothing"
        )
        return EX_UNAVAILABLE, line, _report(UNAVAILABLE, step, None, None)
    if declared:
        why = f"{DECLARE_ENV}={DECLARED} declares this selection deliberately empty"
        code = 0
    elif runner == "jest":
        why = (
            f"{listed_files} test file(s) exist, so no test imports what you changed; "
            f"set {DECLARE_ENV}={DECLARED} to declare that deliberate"
        )
        code = 0
    else:
        why = f"set {DECLARE_ENV}={DECLARED} to declare a deliberately empty selection"
        code = EX_NOTHING
    line = (
        f"pre-push verification: {NOTHING} - 0 {unit}(s) ran, so this push proves "
        f"nothing; {why}; {hint}"
    )
    return code, line, _report(NOTHING, step, 0, 0)


def _judge(runner, runner_exit, listed_files, step):
    """`(exit code, the one line, the board's report)`, in the frozen order."""
    unit = UNITS[runner]
    count, problem = _read_count(os.environ.get(COUNT_ENV, ""))
    if problem is not None:
        line = (
            f"pre-push verification: {UNAVAILABLE} - no {unit} count was recorded: "
            f"{problem}; the run's own collection never reported, so there is nothing "
            f"to judge; {HINTS[runner]}"
        )
        return EX_UNAVAILABLE, line, _report(UNAVAILABLE, step, None, None)
    if count == 0:
        return _judge_zero(runner, listed_files, step)
    if runner_exit != 0:
        line = (
            f"pre-push verification: {FAILED} - the suite ran {count} {unit}(s) and "
            f"exited {runner_exit}"
        )
        return _failure_code(runner_exit), line, _report(FAILED, step, None, count)
    line = f"pre-push verification: {PASSED} - {count} {unit}(s) ran green"
    return 0, line, _report(PASSED, step, count, count)


def _write_report(raw, report):
    """Compose the board's report. A note on failure, never a changed verdict:
    a count that did not arrive is a verdict, but the board not hearing about
    one is not."""
    try:
        Path(raw).write_text(json.dumps(report), encoding="utf-8")
    except OSError as exc:
        return f"the queue board was not told: {exc}"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Judge one pre-push test run against the count its collection recorded."
    )
    parser.add_argument("--runner", required=True, choices=sorted(UNITS))
    parser.add_argument(
        "--exit", dest="runner_exit", required=True, type=int, help="the runner's own exit code"
    )
    parser.add_argument(
        "--listed-files",
        dest="listed_files",
        type=int,
        default=None,
        help="test files the harness could see, from the runner's own listing probe",
    )
    parser.add_argument(
        "--report-file",
        dest="report_file",
        default=None,
        help="where to compose the report the shell then puts on the lease",
    )
    args = parser.parse_args(argv)

    code, line, report = _judge(
        args.runner, args.runner_exit, args.listed_files, os.environ.get(STEP_ENV, "")
    )
    note = _write_report(args.report_file, report) if args.report_file else None
    # One line per suite, at every volume, with no per-test detail of its own.
    print(line if note is None else f"{line} ({note})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
