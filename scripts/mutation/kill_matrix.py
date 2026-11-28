#!/usr/bin/env python3
"""Turn a mutation run into the numbers this pilot promised, or refuse to.

Neither mutmut nor Stryker gives the answer whole. mutmut scores a pytest
INTERNALERROR as a kill, has no kill matrix at all, and reports a skipped test
as "no tests"; Stryker publishes a second, flattering score that drops exactly
the mutants nobody covered. So the rules live here, in one pure place, and
`server/tests/test_mutation_kill_matrix.py` is their specification.

Pure by construction: no subprocess, no MCP, no DB, no network, no filesystem
except the two report files `main` is handed. Everything else is a function
over a parsed document, which is what makes the control cheap enough for
anyone to re-run — and this repo's last mutation control was one nobody could.

Usage:

    python3 scripts/mutation/kill_matrix.py --side server \\
        --report mutation-out/server-run.json \\
        --control scripts/mutation/fixtures/control-predictions-server.json

Prints the reduced run as JSON on stdout. Exits 1 when the control did not
pass: the score is withheld in that case (AC-4), and an exit code of 0 beside
a withheld score would read to a caller as a clean run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------- #
# The vocabulary. `classification` is what a mutant does to the ratio;
# `category` is the named sub-category a reader sees in the report.
# --------------------------------------------------------------------------- #

CLASSIFICATION_KILLED = "killed"
CLASSIFICATION_NOT_KILLED = "not killed"
CLASSIFICATION_EXCLUDED = "excluded"

CATEGORY_KILLED = "killed"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_SURVIVED = "survived"
CATEGORY_NO_TESTS = "no tests"
CATEGORY_SKIPPED = "skipped"
CATEGORY_NO_COVERAGE = "no coverage"
CATEGORY_IMPORT_FATAL = "import fatal"

TIMEOUT_MUTMUT_SIGXCPU = "mutmut SIGXCPU"
TIMEOUT_PYTEST = "pytest-timeout SIGALRM"
TIMEOUT_STRYKER = "stryker"

# mutmut 3.8.1's own `stats.status_by_exit_code`, transcribed verbatim so the
# report shows a reader the word the tool would have shown them. What each
# status then does to the ratio is decided below, NOT here — exit 3 is "killed"
# to mutmut and excluded by us, and renaming it would hide that disagreement.
#
# mutmut's table is a defaultdict whose fallback is "suspicious", so an exit
# code it has no entry for still gets that word. `_mutmut_tool_status` mirrors
# the fallback rather than inventing one: every code that lands there is
# excluded from the ratio anyway, so the only thing a different word could
# change is whether the reader recognises what mutmut printed.
_MUTMUT_STATUS_BY_EXIT_CODE = {
    0: "survived",
    1: "killed",
    2: "check was interrupted by user",
    3: "killed",  # internal error in pytest means a kill (mutmut's comment)
    5: "no tests",
    24: "timeout",  # SIGXCPU
    -24: "timeout",  # SIGXCPU
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    152: "timeout",  # SIGXCPU
    255: "timeout",
    -9: "segfault",
    -11: "segfault",
}
_MUTMUT_NOT_CHECKED = "not checked"
_MUTMUT_UNKNOWN = "suspicious"

# The plugin's buckets, in one place for this file's two readers (`_mutmut_row`
# and `_mutmut_tests`). `mutmut_killers.py` keeps its own copy on purpose: it is
# staged into mutmut's sandbox alone, as a bare top-level filename, and cannot
# import anything from here. `errored` must stay in this tuple — it is the mark
# that makes an error-credited kill discountable, and a bucket dropped at a
# reader is dropped in silence.
_KILL_MATRIX_BUCKETS = ("failing", "passed", "skipped", "timed_out", "errored")

_MUTMUT_TIMEOUT_EXIT_CODES = frozenset({24, -24, 36, 152, 255})
_MUTMUT_NO_TESTS_EXIT_CODES = frozenset({5, 33})

# Stryker's `MutantStatus`. Killed and Timeout are kills; Survived and
# NoCoverage are not; everything else never ran as a test and is excluded.
_STRYKER_NOT_KILLED = {
    "Survived": CATEGORY_SURVIVED,
    "NoCoverage": CATEGORY_NO_COVERAGE,
}
_STRYKER_EXCLUDED = {
    "CompileError": "compile error",
    "RuntimeError": "runtime error",
    "Ignored": "ignored",
    "Pending": "pending",
}


# --------------------------------------------------------------------------- #
# mutmut
# --------------------------------------------------------------------------- #


def _mutmut_tool_status(exit_code):
    if exit_code is None:
        return _MUTMUT_NOT_CHECKED
    return _MUTMUT_STATUS_BY_EXIT_CODE.get(exit_code, _MUTMUT_UNKNOWN)


def _mutmut_row(kill_matrix, mutant_id):
    """The AC-6 plugin's row for one mutant, with every bucket present.

    An absent row is not an empty row with a different name: the plugin not
    loading at all is the silent failure this whole file exists to catch, and
    the caller checks `kill_matrix_rows` for it.
    """
    row = kill_matrix.get(mutant_id) or {}
    return {bucket: list(row.get(bucket) or []) for bucket in _KILL_MATRIX_BUCKETS}


def _classify_mutmut(exit_code, row):
    """(classification, category, timeout_instrument) for one mutmut verdict.

    Each rule names the trap it exists for.
    """
    # (iv) A timeout is a kill — the mutant made the code take unbounded time,
    # which a test did notice. Two instruments can fire and they arrive as
    # different exit codes, so the report names which one did.
    if exit_code in _MUTMUT_TIMEOUT_EXIT_CODES:
        return CLASSIFICATION_KILLED, CATEGORY_TIMEOUT, TIMEOUT_MUTMUT_SIGXCPU
    if exit_code == 1:
        # pytest-timeout's SIGALRM fails the one test, so pytest exits 1 and
        # mutmut records an ordinary kill. Only the plugin's row knows better.
        if row["timed_out"]:
            return CLASSIFICATION_KILLED, CATEGORY_TIMEOUT, TIMEOUT_PYTEST
        return CLASSIFICATION_KILLED, CATEGORY_KILLED, None

    # (iii) mutmut scores exit 3 (pytest INTERNALERROR) as a kill. But
    # server/tests/conftest.py imports 20+ loregarden modules at module scope,
    # so an import-fatal mutant kills collection rather than behaviour. Scoring
    # it as a kill errs upward, and nobody audits that direction — so it leaves
    # both the numerator and the denominator.
    if exit_code == 3:
        return CLASSIFICATION_EXCLUDED, CATEGORY_IMPORT_FATAL, None

    if exit_code == 0:
        return CLASSIFICATION_NOT_KILLED, CATEGORY_SURVIVED, None

    # (i) A skipped test executes nothing and records no coverage, so the skip
    # usually arrives as "no tests" (5/33) rather than as 34. "No tests" reads
    # to a reviewer as "write a test", when the truth is "a test covers this
    # and said nothing" — so the plugin's observation beats the exit code, and
    # the two sub-categories stay distinct.
    if exit_code == 34:
        return CLASSIFICATION_NOT_KILLED, CATEGORY_SKIPPED, None
    if exit_code in _MUTMUT_NO_TESTS_EXIT_CODES:
        if row["skipped"]:
            return CLASSIFICATION_NOT_KILLED, CATEGORY_SKIPPED, None
        return CLASSIFICATION_NOT_KILLED, CATEGORY_NO_TESTS, None

    # Killed is exit 1 and the timeout codes, and nothing else. Every other
    # verdict — suspicious, segfault, an exit code this mutmut did not have
    # when this was written, or a mutant never checked at all — is excluded
    # under the tool's own word rather than rounded up into a kill.
    return CLASSIFICATION_EXCLUDED, _mutmut_tool_status(exit_code), None


def _mutmut_tests(kill_matrix):
    """The kill matrix, inverted: what each test was observed doing.

    The universe is what the plugin observed and nothing else. A test the run
    never reached killed nothing because it was never asked to, and listing it
    would put tests on a review list on no evidence at all.
    """
    observations = (
        ("failing", "failed"),
        ("passed", "passed"),
        ("skipped", "skipped"),
        ("timed_out", "timed out"),
        # A failure outside the call phase: the mutant broke the test's import
        # or its fixture. By the operator's decision that IS a kill, so the
        # plugin writes the node to `failing` as well and the credit is read
        # from there like any other. This bucket is the MARK on that credit:
        # `killed_by_error` below, and `killers_only_by_error` on the reduced
        # payload, are what let a reviewer discount a test that looks like a
        # killer and detected nothing.
        ("errored", "errored"),
    )
    tests = {}
    for mutant_id in sorted(kill_matrix):
        row = _mutmut_row(kill_matrix, mutant_id)
        for bucket, observed in observations:
            for test_id in row[bucket]:
                entry = tests.setdefault(
                    test_id,
                    {
                        "killed": set(),
                        "killed_by_error": set(),
                        "observed_as": set(),
                        "covered": set(),
                    },
                )
                entry["observed_as"].add(observed)
                entry["covered"].add(mutant_id)
                if bucket == "failing":
                    entry["killed"].add(mutant_id)
        # The mark applies only where there is a credit to mark. A node in
        # `errored` that never reached `failing` (an older row, written before
        # the operator's override) is coverage, not a kill — calling it an
        # error-credited kill would invent a kill the score never counted.
        for test_id in _killed_by_error(row):
            tests[test_id]["killed_by_error"].add(mutant_id)
    return _freeze_tests(tests)


def _killed_by_error(row):
    """The tests whose kill of this mutant came via the `errored` mark."""
    return sorted(set(row["failing"]) & set(row["errored"]))


def _freeze_tests(tests):
    return {
        test_id: {
            "killed": sorted(entry["killed"]),
            # Rebuilt from a fixed key list, so a key added above and missed
            # here is dropped on the way out with the suite still green.
            "killed_by_error": sorted(entry["killed_by_error"]),
            "observed_as": sorted(entry["observed_as"]),
            "covered": sorted(entry["covered"]),
        }
        for test_id, entry in sorted(tests.items())
    }


def _require_operators(document):
    """AC-9: the operator list is required, so an absent one is not an empty one.

    `document.get("operators_in_play", [])` would turn a run script that failed
    to derive the list into a report stating that no operator reached anything
    — a different claim, and a false one. A zero-kill verdict is unreadable
    without it: some tests here guard concurrency, migration order and prompt
    shape that no operator can reach.
    """
    operators = document.get("operators_in_play")
    if not operators:
        raise ValueError(
            "the run document carries no operators_in_play; AC-9 requires the operator "
            "list that was in play, and an absent list must not be reported as an empty one"
        )
    return list(operators)


def parse_mutmut_run(document):
    """The three on-disk mutmut documents the run script collects -> one report."""
    operators = _require_operators(document)
    mutant_index = document.get("mutant_index") or {}
    kill_matrix = document.get("kill_matrix") or {}

    mutants = []
    seen = set()
    unindexed = []
    # `meta` is a MAPPING of source file -> its .meta block: mutmut writes one
    # per mutated source file. Reading only the first key silently drops a
    # whole file's mutants, which removes its survivors from the denominator
    # and reports a score HIGHER than the run measured.
    for file_path, block in document["meta"].items():
        for mutant_id, exit_code in (block.get("exit_code_by_key") or {}).items():
            if mutant_id in seen:
                continue
            seen.add(mutant_id)
            row = _mutmut_row(kill_matrix, mutant_id)
            classification, category, instrument = _classify_mutmut(exit_code, row)
            index = mutant_index.get(mutant_id)
            if index is None:
                unindexed.append(mutant_id)
            mutants.append(
                {
                    "mutant_id": mutant_id,
                    "file": file_path,
                    "line": (index or {}).get("line"),
                    "operator": (index or {}).get("operator"),
                    "tool_status": _mutmut_tool_status(exit_code),
                    "classification": classification,
                    "category": category,
                    "killed_by": sorted(row["failing"]),
                    # The row a reviewer reads beside `file:line  operator`, so
                    # the mark has to be on it and not only on the test entry.
                    "killed_by_error": _killed_by_error(row),
                    "timeout_instrument": instrument,
                }
            )

    return {
        "side": document.get("side", "server"),
        "tool": document.get("tool", "mutmut"),
        "tool_version": document.get("tool_version"),
        "wall_clock_seconds": document.get("wall_clock_seconds"),
        "operators_in_play": operators,
        "mutants": mutants,
        "tests": _mutmut_tests(kill_matrix),
        "unindexed_mutants": unindexed,
        "kill_matrix_rows": len(kill_matrix),
    }


# --------------------------------------------------------------------------- #
# Stryker
# --------------------------------------------------------------------------- #


def _stryker_test_names(document):
    """Test id -> the name the report is keyed by, kept DISTINCT per test.

    Stryker's `tests` map is keyed by id, and the report here is keyed by the
    human-readable name — so two tests sharing a title merge into one row.
    Jest titles repeat across files constantly ("renders", "returns null when
    empty"), and the merge is one-directional in the damaging way: the killer's
    kills are credited to the pair, so the test that killed NOTHING disappears
    off the review list entirely. That is a false "this test is fine", which is
    the direction nobody audits.

    So a name carried by more than one test id is qualified with the file it
    came from. A unique name is left exactly as the tool reported it — the
    qualifier is for the collision, not a prefix on every row.
    """
    raw = {}
    for file_path, test_file in (document.get("testFiles") or {}).items():
        for test in test_file.get("tests") or []:
            raw[test["id"]] = (file_path, test.get("name") or f"<unnamed test id {test['id']}>")

    counts = Counter(name for _, name in raw.values())
    shared = {name for name, count in counts.items() if count > 1}
    return {
        test_id: (f"{name} ({file_path})" if name in shared else name)
        for test_id, (file_path, name) in raw.items()
    }


def _stryker_name(names, test_id):
    """Resolve a Stryker test id, keeping the ones `testFiles` cannot name.

    `testFiles` is omitted or partial in more than one real configuration. A
    lookup that drops its misses loses a killer, and losing a killer is how a
    test that kills something lands on the review list as killing nothing.
    """
    return names.get(test_id, f"<unknown test id {test_id}>")


def _classify_stryker(status):
    if status == "Killed":
        return CLASSIFICATION_KILLED, CATEGORY_KILLED, None
    if status == "Timeout":
        return CLASSIFICATION_KILLED, CATEGORY_TIMEOUT, TIMEOUT_STRYKER
    if status in _STRYKER_NOT_KILLED:
        # (ii) NoCoverage is folded in with Survived, and stays in the
        # denominator. `mutationScoreBasedOnCoveredCode` is the number that
        # drops exactly these, which is the case this pilot exists to surface,
        # so that score is never computed and never reported anywhere here.
        return CLASSIFICATION_NOT_KILLED, _STRYKER_NOT_KILLED[status], None
    # Never ran as a test: a compile error, an ignored mutant, a pending one.
    # Excluded from both sides under Stryker's own word.
    return CLASSIFICATION_EXCLUDED, _STRYKER_EXCLUDED.get(status, f"unknown status {status}"), None


def parse_stryker_run(document):
    """A mutation-testing-elements report -> the same report shape."""
    names = _stryker_test_names(document)
    mutants = []
    tests = {}
    operators = set()

    for file_path, file_block in (document.get("files") or {}).items():
        for mutant in file_block.get("mutants") or []:
            mutant_id = mutant["id"]
            status = mutant.get("status")
            classification, category, instrument = _classify_stryker(status)
            killed_by_ids = list(mutant.get("killedBy") or [])
            covered_ids = list(mutant.get("coveredBy") or [])
            operators.add(mutant["mutatorName"])

            for test_id in dict.fromkeys(covered_ids + killed_by_ids):
                entry = tests.setdefault(
                    _stryker_name(names, test_id),
                    # `killed_by_error` is always empty on this side: Stryker
                    # reports a killer, not the pytest phase it failed in, so
                    # there is nothing to mark. The key is present because
                    # `_freeze_tests` is shared and a reader of the report
                    # should not have to know which tool produced it.
                    {
                        "killed": set(),
                        "killed_by_error": set(),
                        "observed_as": set(),
                        "covered": set(),
                    },
                )
                entry["covered"].add(mutant_id)
                if test_id in killed_by_ids:
                    entry["killed"].add(mutant_id)
                    entry["observed_as"].add("killed")
                else:
                    entry["observed_as"].add("covered")

            mutants.append(
                {
                    "mutant_id": mutant_id,
                    "file": file_path,
                    "line": ((mutant.get("location") or {}).get("start") or {}).get("line"),
                    "operator": mutant["mutatorName"],
                    "tool_status": status,
                    "classification": classification,
                    "category": category,
                    "killed_by": sorted(_stryker_name(names, test_id) for test_id in killed_by_ids),
                    "killed_by_error": [],
                    "timeout_instrument": instrument,
                }
            )

    # ABSENT IS NOT ZERO. Stryker's json reporter does not always carry a
    # `performance` block (a real 10.1.1 run of 548 mutants over 16m24s
    # carried none), and summing a missing block gives 0.0 — a wall clock of
    # zero seconds beside a sixteen-minute run, which a reader takes as a
    # measurement. `run-client.sh` therefore writes its own measured seconds
    # into the report as `wall_clock_seconds` and that wins; with neither
    # present this stays None, which reports as unknown rather than as none.
    measured = document.get("wall_clock_seconds")
    if measured is None:
        performance = document.get("performance") or {}
        phases = [performance.get(phase) for phase in ("setup", "initialRun", "mutation")]
        measured = sum(p for p in phases if p) / 1000 if any(phases) else None

    return {
        "side": "client",
        "tool": "stryker",
        "tool_version": (document.get("framework") or {}).get("version"),
        "wall_clock_seconds": measured,
        "operators_in_play": sorted(operators),
        "mutants": mutants,
        "tests": _freeze_tests(tests),
        "unindexed_mutants": [],
        "kill_matrix_rows": len(mutants),
    }


# --------------------------------------------------------------------------- #
# The numbers
# --------------------------------------------------------------------------- #


def score(report):
    """The mutation score with its numerator and denominator named.

    Excluded mutants leave BOTH sides of the ratio. A denominator of zero is
    reported as no score rather than as 0.0: every mutant import-fatal is what
    a mutated module that will not import looks like, and "0.0" reads as "these
    tests kill nothing", the most expensive wrong number this pilot can make.
    """
    killed = sum(1 for m in report["mutants"] if m["classification"] == CLASSIFICATION_KILLED)
    not_killed = sum(
        1 for m in report["mutants"] if m["classification"] == CLASSIFICATION_NOT_KILLED
    )
    excluded = sum(1 for m in report["mutants"] if m["classification"] == CLASSIFICATION_EXCLUDED)
    denominator = killed + not_killed
    return {
        "killed": killed,
        "not_killed": not_killed,
        "excluded": excluded,
        "denominator": denominator,
        "mutation_score": (killed / denominator) if denominator else None,
    }


def killed_without_recorded_killer(report):
    """Mutants the tool scored killed that the kill matrix has no killer for.

    A HOLE IN THE KILL MATRIX IS INVISIBLE TO THE SCORE. The collector refuses
    a kill matrix that is entirely empty, because a silent `also_copy` failure
    otherwise reads as "nothing killed anything". A PARTIAL failure has no such
    guard and is the likelier one: a pytest process that died before
    `pytest_sessionfinish`, a row that failed to write, a mutant run under a
    stale `MUTMUT_KILL_MATRIX_DIR`. The tool still scores that mutant from its
    exit code, so the number is unaffected and nothing looks wrong — while
    every test that really killed it loses its only recorded kill and lands on
    the review list as a deletion candidate. That is the output AC-6 spends
    `--maxfail=0` to avoid, arriving by another road.

    A timeout is NOT this: the mutant took the process down (SIGXCPU) before
    any row could be written, so an absent row there is expected.
    """
    return [
        mutant["mutant_id"]
        for mutant in report["mutants"]
        if mutant["classification"] == CLASSIFICATION_KILLED
        and mutant["category"] == CATEGORY_KILLED
        and not mutant["killed_by"]
    ]


def killers_only_by_error(report):
    """The discount list: tests whose EVERY recorded kill came via an error.

    These are the tests that look like killers and detected nothing. Crediting
    a failure outside the call phase (the operator's decision) takes them off
    `zero_kill_tests` for good, so without this list the discount is a manual
    cross-reference nobody performs. A test that killed one mutant by asserting
    is not here, however many of its other kills were errors — it demonstrably
    detects something.
    """
    return [
        test_id
        for test_id, entry in report["tests"].items()
        if entry["killed"] and set(entry["killed"]) == set(entry["killed_by_error"])
    ]


def zero_kill_tests(report):
    """Every test the run observed that killed no mutant — a REVIEW list.

    Each row carries the trace AC-11 requires: how the test was observed (a
    test that only ever skipped is not a test that ran and asserted nothing),
    and the mutants it did cover, so a reviewer can see what it was up against
    before writing a verdict. Nothing here is deleted on this score.
    """
    return [
        {
            "test": test_id,
            "observed_as": entry["observed_as"],
            "covered_mutants": entry["covered"],
        }
        for test_id, entry in report["tests"].items()
        if not entry["killed"]
    ]


def compare_control(report, control):
    """The control's written-before-the-run predictions against the outcomes.

    A control with no rows agrees with every run. That is the exact shape of
    this repo's last silently-failing mutation control, so a vacuous control
    (absent, empty, or written for the other side) is a FAILURE, never a pass.
    """
    by_id = {m["mutant_id"]: m for m in report["mutants"]}
    rows = []
    for prediction in (control or {}).get("predictions") or []:
        mutant = by_id.get(prediction["mutant"])
        actual = mutant["category"] if mutant else None
        rows.append(
            {
                "mutant": prediction["mutant"],
                "predicted": prediction["bucket"],
                "actual": actual,
                "match": actual == prediction["bucket"],
                "why": prediction.get("why"),
            }
        )
    return {
        "passed": bool(rows) and all(row["match"] for row in rows),
        "rows": rows,
        "predicted_before_the_run_at": (control or {}).get("predicted_before_the_run_at"),
    }


def _refusal(control_result):
    """Why no score is reported, naming what a reader must go and look at."""
    if not control_result["rows"]:
        return (
            "the control compared nothing: no predictions were supplied, so it agrees "
            "with every run. AC-4 requires predictions written before the run, and a "
            "score reported behind a vacuous control is a wrong-but-well-formed number."
        )
    missed = [row["mutant"] for row in control_result["rows"] if not row["match"]]
    return (
        "the control did not pass; predictions missed for: " + ", ".join(missed) + ". "
        "AC-4 withholds the mutation score until every prediction matches its outcome."
    )


def _report_line(mutant):
    """AC-9's `file:line  operator  status` for one not-killed mutant."""
    line = mutant["line"] if mutant["line"] is not None else "?"
    operator = mutant["operator"] or "<no operator recorded — see unindexed_mutants>"
    return f"{mutant['file']}:{line}  {operator}  {mutant['category']}"


def reduce_run(document, *, side, control):
    """The whole reduction: parse, score, gate the score on the control, print.

    The score is the only thing a failed control withholds. Refusing the score
    is not refusing the run — the not-killed list and the zero-kill list are
    what a person reads to find out WHY the control failed.
    """
    report = parse_mutmut_run(document) if side == "server" else parse_stryker_run(document)
    totals = score(report)
    control_result = compare_control(report, control)
    refused = None if control_result["passed"] else _refusal(control_result)

    not_killed = [m for m in report["mutants"] if m["classification"] == CLASSIFICATION_NOT_KILLED]
    excluded = [m for m in report["mutants"] if m["classification"] == CLASSIFICATION_EXCLUDED]

    return {
        "side": report["side"],
        "tool": report["tool"],
        "tool_version": report["tool_version"],
        "wall_clock_seconds": report["wall_clock_seconds"],
        "operators_in_play": report["operators_in_play"],
        "mutants_total": len(report["mutants"]),
        "killed": totals["killed"],
        "not_killed_total": totals["not_killed"],
        "excluded_total": totals["excluded"],
        "denominator": totals["denominator"],
        "mutation_score": None if refused else totals["mutation_score"],
        "score_refused_because": refused,
        "control": control_result,
        "not_killed": [{**m, "report_line": _report_line(m)} for m in not_killed],
        "excluded": [{**m, "report_line": _report_line(m)} for m in excluded],
        "zero_kill_tests": zero_kill_tests(report),
        "killers_only_by_error": killers_only_by_error(report),
        "unindexed_mutants": report["unindexed_mutants"],
        "killed_without_recorded_killer": killed_without_recorded_killer(report),
        "kill_matrix_rows": report["kill_matrix_rows"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--side", required=True, choices=("server", "client"))
    parser.add_argument("--report", required=True, help="the run document for that side")
    parser.add_argument("--control", help="the predictions written BEFORE the run")
    args = parser.parse_args(argv)

    document = json.loads(Path(args.report).read_text(encoding="utf-8"))
    control = json.loads(Path(args.control).read_text(encoding="utf-8")) if args.control else None
    payload = reduce_run(document, side=args.side, control=control)
    print(json.dumps(payload, indent=2, sort_keys=False))
    # Exit 1 when the score was withheld: a clean exit beside a null score
    # reads to run-*.sh, and to a person skimming, as a run that went fine.
    return 1 if payload["score_refused_because"] else 0


if __name__ == "__main__":
    sys.exit(main())
