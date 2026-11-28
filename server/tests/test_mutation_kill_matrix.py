"""The mutation reducer: what counts as killed, and which tests killed nothing.

`scripts/mutation/kill_matrix.py` is the only place in the pilot that decides
what a mutation score means. Neither tool gives us the answer whole — mutmut
scores a pytest internal error as a kill and has no kill matrix at all, and
Stryker publishes a second score that quietly drops its uncovered mutants — so
the rules live here, and these tests are what pins them. They run against
checked-in fixtures in both tools' real on-disk shapes, in milliseconds, with
neither tool installed. That is deliberate: this repo's last mutation control
failed silently (a router bound the name at import), and a control nobody can
cheaply re-run is a control nobody re-runs.

Criterion map (lg-build-verification-2639):

| criterion | tests here |
|---|---|
| AC-1 scoped config, both sides | `test_mutmut_config_*`, `test_stryker_config_*` |
| AC-4 control gates the score | `test_control_*`, `test_no_score_is_reported_*` |
| AC-6 a real kill matrix, keyed by the mutant | `test_every_failing_test_in_a_row_is_credited_as_a_killer`, `test_maxfail_zero_is_configured_and_says_why`, `test_the_coverage_map_is_never_used_as_the_kill_matrix` |
| AC-7 (i) not-killed sub-categories | `test_survived_is_not_killed`, `test_no_tests_is_not_killed`, `test_a_skip_is_not_killed` |
| AC-7 (ii) NoCoverage counts, covered-code score never does | `test_no_coverage_is_not_killed_and_stays_in_the_denominator`, `test_the_covered_code_score_is_never_reported` |
| AC-7 (iii) import-fatal excluded from both sides of the ratio | `test_an_import_fatal_mutant_is_excluded_from_both_numerator_and_denominator`, `test_nothing_unrecognised_is_scored_as_a_kill` |
| AC-7 (iv) timeouts are kills, and name their instrument | `test_a_sigxcpu_timeout_*`, `test_a_pytest_timeout_*`, `test_a_stryker_timeout_*` |
| AC-7 purity | `test_the_reducer_imports_nothing_impure`, `test_the_reducer_prints_json_to_stdout` |
| AC-9 file:line operator status, and the operator list | `test_every_not_killed_mutant_reports_file_line_operator_and_status`, `test_the_operator_list_ships_with_the_report`, `test_a_mutant_missing_from_the_index_is_reported_not_blanked` |
| AC-10 a skip reaches the not-killed list | `test_a_skip_is_not_killed`, `test_a_test_that_only_ever_skipped_is_a_zero_killer` |
| AC-11 the zero-kill list | `test_zero_kill_tests_*` |

The reducer's contract, which these tests are the specification of:

    parse_mutmut_run(document)  -> report      # document: the mutmut envelope, below
    parse_stryker_run(document) -> report      # document: a mutation-testing-elements report
    score(report)               -> {"killed", "not_killed", "excluded", "denominator",
                                    "mutation_score"}
    zero_kill_tests(report)     -> [{"test", "observed_as", "covered_mutants"}, ...]
    compare_control(report, predictions) -> {"passed", "rows": [{"mutant", "predicted",
                                                                 "actual", "match"}]}
    reduce_run(document, side, control)  -> the JSON payload printed to stdout
    main(argv)                  -> int, printing that payload

`report` is a plain dict (the reducer prints JSON, so nothing in it may be a
type json cannot carry):

    {"side", "tool", "wall_clock_seconds", "operators_in_play": [...],
     "mutants": [{"mutant_id", "file", "line", "operator", "tool_status",
                  "classification", "category", "killed_by": [...],
                  "timeout_instrument"}],
     "tests": {"<test id>": {"killed": [mutant ids], "observed_as": [...]}},
     "unindexed_mutants": [...]}

The mutmut envelope is three real on-disk documents collected by
`scripts/mutation/run-server.sh`, each verbatim under its own key, because no
single mutmut file holds all of it:

    meta          `mutants/<path>.meta`, written by SourceFileMutationData.save() —
                  `exit_code_by_key` is mutmut's whole per-mutant verdict
    mutant_index  file / line / operator per mutant, which mutmut persists nowhere;
                  the run script derives it when the mutants are generated
    kill_matrix   the AC-6 plugin's output, keyed by `MUTANT_UNDER_TEST`

`loregarden/services/example_pilot.py` in the fixtures is a fixture path, not
the pilot module AC-3 selects. The shapes are real; the module is invented, so
that nobody reads a number here as a measurement.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib
from tests.repo_paths import REPO_ROOT

_ROOT = REPO_ROOT
_REDUCER = _ROOT / "scripts" / "mutation" / "kill_matrix.py"
_PLUGIN = _ROOT / "scripts" / "mutation" / "mutmut_killers.py"
_FIXTURES = _ROOT / "scripts" / "mutation" / "fixtures"
_SERVER_PYPROJECT = _ROOT / "server" / "pyproject.toml"
_STRYKER_CONFIG = _ROOT / "client" / "stryker.config.json"
_RUN_SERVER = _ROOT / "scripts" / "mutation" / "run-server.sh"
_RUN_CLIENT = _ROOT / "scripts" / "mutation" / "run-client.sh"

PILOT = "loregarden.services.example_pilot"


# Until the reducer exists these tests are red, which is the point of them. They
# must be red as FAILURES and not as a collection error: an ImportError at module
# scope aborts collection for the whole `server/tests` run, which would take the
# rest of the suite down with work that has not been written yet. So the missing
# deliverables are named by an autouse guard instead, one clear line per test.

_REQUIRED = {
    "the reducer": _REDUCER,
    "the kill-matrix pytest plugin": _PLUGIN,
    "the Stryker config": _STRYKER_CONFIG,
}


def _missing_deliverables():
    missing = [
        f"{what} ({path.relative_to(_ROOT)})"
        for what, path in _REQUIRED.items()
        if not path.exists()
    ]
    if "[tool.mutmut]" not in _SERVER_PYPROJECT.read_text():
        missing.append("the [tool.mutmut] block (server/pyproject.toml)")
    return missing


def _load_plugin():
    """Import the kill-matrix plugin by path, as `-p mutmut_killers` would."""
    spec = importlib.util.spec_from_file_location("mutmut_killers_under_test", _PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_reducer():
    if not _REDUCER.exists():
        return None
    spec = importlib.util.spec_from_file_location("mutation_kill_matrix", _REDUCER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reducer = _load_reducer()


@pytest.fixture(autouse=True)
def _deliverables_exist():
    missing = _missing_deliverables()
    if missing:
        pytest.fail("mutation pilot deliverable not written yet: " + "; ".join(missing))


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture
def server_document():
    return _fixture("mutmut-pilot-run.json")


@pytest.fixture
def client_document():
    return _fixture("stryker-pilot-run.json")


@pytest.fixture
def server_report(server_document):
    return reducer.parse_mutmut_run(server_document)


@pytest.fixture
def client_report(client_document):
    return reducer.parse_stryker_run(client_document)


def _mutant(report, mutant_id):
    matches = [m for m in report["mutants"] if m["mutant_id"] == mutant_id]
    assert len(matches) == 1, f"{mutant_id} appears {len(matches)} times in the report"
    return matches[0]


# --------------------------------------------------------------------------- #
# the parsers: every mutant the tool recorded reaches the report, exactly once
# --------------------------------------------------------------------------- #


def test_the_mutmut_parser_carries_every_mutant_mutmut_recorded(server_document, server_report):
    recorded = server_document["meta"][next(iter(server_document["meta"]))]["exit_code_by_key"]
    assert sorted(m["mutant_id"] for m in server_report["mutants"]) == sorted(recorded)


def test_the_mutmut_parser_keeps_the_tools_own_status_word(server_report):
    # mutmut's status_by_exit_code is the vocabulary a reader will see in the
    # tool's own output; the report must not rename it out from under them.
    assert _mutant(server_report, f"{PILOT}.x_classify__mutmut_2")["tool_status"] == "survived"
    assert _mutant(server_report, f"{PILOT}.x_threshold__mutmut_1")["tool_status"] == "skipped"
    assert _mutant(server_report, f"{PILOT}.x_threshold__mutmut_4")["tool_status"] == "no tests"


def test_the_mutmut_parser_reports_the_run_wall_clock_and_side(server_report):
    assert server_report["side"] == "server"
    assert server_report["tool"] == "mutmut"
    assert server_report["wall_clock_seconds"] == pytest.approx(611.4)


def test_the_stryker_parser_carries_every_mutant_in_the_report(client_document, client_report):
    recorded = client_document["files"]["src/lib/examplePilot.ts"]["mutants"]
    assert sorted(m["mutant_id"] for m in client_report["mutants"]) == sorted(
        m["id"] for m in recorded
    )


def test_the_stryker_parser_keeps_the_tools_own_status_word(client_report):
    assert _mutant(client_report, "3")["tool_status"] == "NoCoverage"
    assert _mutant(client_report, "5")["tool_status"] == "CompileError"


def test_the_stryker_parser_resolves_test_ids_to_test_names(client_report):
    # Stryker's killedBy/coveredBy are opaque ids. A review list of ids is not
    # a review list, so the parser resolves them through `testFiles`.
    assert _mutant(client_report, "1")["killed_by"] == [
        "examplePilot > classifies a large budget",
        "examplePilot > classifies the boundary as small",
    ]


def test_the_stryker_parser_takes_wall_clock_from_the_performance_block(client_report):
    # setup 4012ms + initialRun 18233ms + mutation 265110ms
    assert client_report["side"] == "client"
    assert client_report["tool"] == "stryker"
    assert client_report["wall_clock_seconds"] == pytest.approx(287.355)


# --------------------------------------------------------------------------- #
# AC-6: the kill matrix is a set of killers, not the first one
# --------------------------------------------------------------------------- #


def test_every_failing_test_in_a_row_is_credited_as_a_killer(server_report):
    # This is what `--maxfail=0` buys. Under mutmut's forced `-x` only the first
    # failing test is ever recorded, so the second one here would be reported as
    # having killed nothing — a false "delete this test", the most expensive
    # output this pilot can produce.
    killed_by = _mutant(server_report, f"{PILOT}.x_classify__mutmut_1")["killed_by"]
    assert killed_by == [
        "tests/test_example_pilot.py::test_classify_rejects_a_negative_budget",
        "tests/test_example_pilot.py::test_classify_rounds_to_the_floor",
    ]


def test_a_passing_test_in_a_row_is_not_credited_as_a_killer(server_report):
    mutant = _mutant(server_report, f"{PILOT}.x_classify__mutmut_1")
    assert "tests/test_example_pilot.py::test_classify_logs_the_budget" not in mutant["killed_by"]


def test_a_surviving_mutant_has_no_killers(server_report):
    assert _mutant(server_report, f"{PILOT}.x_classify__mutmut_2")["killed_by"] == []


# --------------------------------------------------------------------------- #
# AC-7 (i): not killed = survived + no tests + skipped, as named sub-categories
# --------------------------------------------------------------------------- #


def test_survived_is_not_killed(server_report):
    mutant = _mutant(server_report, f"{PILOT}.x_classify__mutmut_2")
    assert mutant["classification"] == reducer.CLASSIFICATION_NOT_KILLED
    assert mutant["category"] == reducer.CATEGORY_SURVIVED


@pytest.mark.parametrize(
    "mutant_id", [f"{PILOT}.x_classify__mutmut_3", f"{PILOT}.x_threshold__mutmut_4"]
)
def test_no_tests_is_not_killed(server_report, mutant_id):
    # exit 5 and exit 33 are both "no tests" to mutmut.
    mutant = _mutant(server_report, mutant_id)
    assert mutant["classification"] == reducer.CLASSIFICATION_NOT_KILLED
    assert mutant["category"] == reducer.CATEGORY_NO_TESTS


def test_a_skip_is_not_killed(server_report):
    # AC-10. A skipped test executes nothing and asserts nothing, so a mutant
    # whose only covering test skips survived — it did not go untested.
    mutant = _mutant(server_report, f"{PILOT}.x_threshold__mutmut_1")
    assert mutant["classification"] == reducer.CLASSIFICATION_NOT_KILLED
    assert mutant["category"] == reducer.CATEGORY_SKIPPED


def test_the_three_not_killed_sub_categories_stay_distinct(server_report):
    categories = {
        m["category"]
        for m in server_report["mutants"]
        if m["classification"] == reducer.CLASSIFICATION_NOT_KILLED
    }
    assert categories == {
        reducer.CATEGORY_SURVIVED,
        reducer.CATEGORY_NO_TESTS,
        reducer.CATEGORY_SKIPPED,
    }


def test_a_skip_is_not_collapsed_into_no_tests(server_report):
    # The reason the sub-categories must stay apart: "no tests" reads to a
    # reviewer as "write a test", when the truth here is "a test covers it and
    # said nothing".
    assert (
        _mutant(server_report, f"{PILOT}.x_threshold__mutmut_1")["category"]
        != reducer.CATEGORY_NO_TESTS
    )


# --------------------------------------------------------------------------- #
# AC-7 (ii): NoCoverage is folded in with Survived; the covered-code score is not
# reported at all
# --------------------------------------------------------------------------- #


def test_no_coverage_is_not_killed_and_stays_in_the_denominator(client_report):
    mutant = _mutant(client_report, "3")
    assert mutant["classification"] == reducer.CLASSIFICATION_NOT_KILLED
    assert mutant["category"] == reducer.CATEGORY_NO_COVERAGE
    assert "3" in {
        m["mutant_id"]
        for m in client_report["mutants"]
        if m["classification"] in (reducer.CLASSIFICATION_KILLED, reducer.CLASSIFICATION_NOT_KILLED)
    }


def _keys(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from _keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _keys(item)


def test_the_covered_code_score_is_never_reported(client_document, client_report):
    # `mutationScoreBasedOnCoveredCode` excludes exactly the NoCoverage mutants,
    # which is the case this pilot exists to surface. Reporting it would hand a
    # reader the flattering number.
    payload = reducer.reduce_run(
        client_document, side="client", control=_fixture("control-predictions-client.json")
    )
    for blob in (client_report, payload):
        assert "mutationScoreBasedOnCoveredCode" not in set(_keys(blob))
        assert "mutation_score_based_on_covered_code" not in set(_keys(blob))


# --------------------------------------------------------------------------- #
# AC-7 (iii): import-fatal is a third category, out of both numerator and
# denominator — and nothing unrecognised is ever rounded up into a kill
# --------------------------------------------------------------------------- #


def test_an_import_fatal_mutant_is_excluded_from_both_numerator_and_denominator(server_report):
    # mutmut scores exit 3 as `killed` ("internal error in pytest means a kill").
    # server/tests/conftest.py imports 20+ loregarden modules at module scope, so
    # an import-fatal mutant kills collection rather than behaviour.
    mutant = _mutant(server_report, f"{PILOT}.x_threshold__mutmut_2")
    assert mutant["classification"] == reducer.CLASSIFICATION_EXCLUDED
    assert mutant["category"] == reducer.CATEGORY_IMPORT_FATAL

    totals = reducer.score(server_report)
    assert totals["killed"] == 3
    assert totals["not_killed"] == 4
    assert totals["denominator"] == 7
    assert totals["excluded"] == 4
    assert totals["mutation_score"] == pytest.approx(3 / 7)


def test_the_score_names_its_numerator_and_denominator(server_report):
    totals = reducer.score(server_report)
    assert totals["mutation_score"] == pytest.approx(totals["killed"] / totals["denominator"])
    assert totals["killed"] + totals["not_killed"] == totals["denominator"]


@pytest.mark.parametrize(
    ("mutant_id", "tool_status"),
    [
        (f"{PILOT}.x_threshold__mutmut_5", "suspicious"),  # exit 35
        (f"{PILOT}.x_threshold__mutmut_6", "segfault"),  # exit -11
        (f"{PILOT}.xǁBudgetǁspend__mutmut_2", "not checked"),  # exit code null
    ],
)
def test_nothing_unrecognised_is_scored_as_a_kill(server_report, mutant_id, tool_status):
    # Killed is exit 1 and the timeout codes, and nothing else. Every other
    # verdict is excluded from both sides of the ratio and keeps mutmut's own
    # word, because scoring an unclear verdict as a kill errs upward and nobody
    # audits that direction.
    mutant = _mutant(server_report, mutant_id)
    assert mutant["classification"] == reducer.CLASSIFICATION_EXCLUDED
    assert mutant["category"] == tool_status


@pytest.mark.parametrize(("mutant_id", "category"), [("5", "compile error"), ("6", "ignored")])
def test_stryker_verdicts_that_are_not_a_test_result_are_excluded(
    client_report, mutant_id, category
):
    mutant = _mutant(client_report, mutant_id)
    assert mutant["classification"] == reducer.CLASSIFICATION_EXCLUDED
    assert mutant["category"] == category


def test_the_client_score_counts_no_coverage_and_drops_the_compile_errors(client_report):
    totals = reducer.score(client_report)
    assert (totals["killed"], totals["not_killed"], totals["excluded"]) == (2, 2, 2)
    assert totals["mutation_score"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# AC-7 (iv): a timeout is a kill, and the report names the instrument that fired
# --------------------------------------------------------------------------- #


def test_a_sigxcpu_timeout_is_a_kill_and_names_mutmut_as_the_instrument(server_report):
    mutant = _mutant(server_report, f"{PILOT}.x_threshold__mutmut_3")  # exit 24
    assert mutant["classification"] == reducer.CLASSIFICATION_KILLED
    assert mutant["category"] == reducer.CATEGORY_TIMEOUT
    assert mutant["timeout_instrument"] == reducer.TIMEOUT_MUTMUT_SIGXCPU


def test_a_pytest_timeout_is_a_kill_and_names_pytest_timeout_as_the_instrument(server_report):
    # pytest-timeout's SIGALRM fails the one test, so pytest exits 1 and mutmut
    # records an ordinary kill. Only the plugin's row says a timeout fired.
    mutant = _mutant(server_report, f"{PILOT}.xǁBudgetǁspend__mutmut_1")
    assert mutant["classification"] == reducer.CLASSIFICATION_KILLED
    assert mutant["category"] == reducer.CATEGORY_TIMEOUT
    assert mutant["timeout_instrument"] == reducer.TIMEOUT_PYTEST
    assert mutant["killed_by"] == ["tests/test_example_pilot.py::test_budget_spend_stops_at_zero"]


def test_a_stryker_timeout_is_a_kill_and_names_stryker_as_the_instrument(client_report):
    mutant = _mutant(client_report, "4")
    assert mutant["classification"] == reducer.CLASSIFICATION_KILLED
    assert mutant["category"] == reducer.CATEGORY_TIMEOUT
    assert mutant["timeout_instrument"] == reducer.TIMEOUT_STRYKER


def test_an_ordinary_kill_names_no_timeout_instrument(server_report):
    mutant = _mutant(server_report, f"{PILOT}.x_classify__mutmut_1")
    assert mutant["category"] == reducer.CATEGORY_KILLED
    assert mutant["timeout_instrument"] is None


# --------------------------------------------------------------------------- #
# AC-9: file:line, operator and status for every not-killed mutant
# --------------------------------------------------------------------------- #


def test_every_not_killed_mutant_reports_file_line_operator_and_status(
    server_report, client_report
):
    for report in (server_report, client_report):
        not_killed = [
            m for m in report["mutants"] if m["classification"] == reducer.CLASSIFICATION_NOT_KILLED
        ]
        assert not_killed
        for mutant in not_killed:
            if mutant["mutant_id"] in report["unindexed_mutants"]:
                continue
            assert mutant["file"]
            assert isinstance(mutant["line"], int)
            assert mutant["operator"]
            assert mutant["category"]


def test_a_mutant_missing_from_the_index_is_reported_not_blanked(server_report):
    # mutmut persists no operator and no original-source line for a mutant; the
    # run script derives both. A gap in that derivation must be visible, not a
    # blank cell in a review list that reads as "no operator reached this".
    missing = f"{PILOT}.x_threshold__mutmut_4"
    assert server_report["unindexed_mutants"] == [missing]
    mutant = _mutant(server_report, missing)
    assert mutant["line"] is None
    assert mutant["operator"] is None


def test_the_operator_list_ships_with_the_report(server_report, client_report):
    # Without it a zero-kill verdict is unreadable: some tests here guard
    # concurrency, migration order and prompt shape that no operator can reach.
    assert "operator_swap_op" in server_report["operators_in_play"]
    assert len(server_report["operators_in_play"]) == 15
    assert client_report["operators_in_play"] == [
        "ArithmeticOperator",
        "BlockStatement",
        "ConditionalExpression",
        "EqualityOperator",
        "OptionalChaining",
        "StringLiteral",
    ]


# --------------------------------------------------------------------------- #
# AC-11: the zero-kill list
# --------------------------------------------------------------------------- #


def test_zero_kill_tests_lists_a_test_that_ran_and_killed_nothing(server_report):
    zero = {row["test"]: row for row in reducer.zero_kill_tests(server_report)}
    assert "tests/test_example_pilot.py::test_classify_logs_the_budget" in zero
    assert zero["tests/test_example_pilot.py::test_classify_logs_the_budget"]["observed_as"] == [
        "passed"
    ]


def test_a_test_that_only_ever_skipped_is_a_zero_killer(server_report):
    # AC-10 on the reducer side: the skipped test is a zero-killer, and the
    # report says it only ever skipped, so a reviewer does not read it as a
    # test that ran and asserted nothing.
    zero = {row["test"]: row for row in reducer.zero_kill_tests(server_report)}
    row = zero["tests/test_example_pilot.py::test_threshold_reads_the_database"]
    assert row["observed_as"] == ["skipped"]


def test_zero_kill_tests_excludes_every_test_that_killed_something(server_report):
    zero = {row["test"] for row in reducer.zero_kill_tests(server_report)}
    assert zero == {
        "tests/test_example_pilot.py::test_classify_logs_the_budget",
        "tests/test_example_pilot.py::test_threshold_reads_the_database",
    }


def test_zero_kill_tests_names_the_mutants_the_test_did_cover(server_report):
    zero = {row["test"]: row for row in reducer.zero_kill_tests(server_report)}
    row = zero["tests/test_example_pilot.py::test_classify_logs_the_budget"]
    assert row["covered_mutants"] == [
        f"{PILOT}.x_classify__mutmut_1",
        f"{PILOT}.x_classify__mutmut_2",
    ]


def test_zero_kill_tests_works_the_same_way_on_the_stryker_side(client_report):
    zero = {row["test"] for row in reducer.zero_kill_tests(client_report)}
    assert zero == {"examplePilot > does not throw on a small budget"}


def test_a_test_that_never_ran_is_not_a_zero_killer(server_report):
    # A test the run never reached killed nothing because it was never asked to.
    # Listing it would put tests on a review list on no evidence at all.
    observed = set(server_report["tests"])
    assert "tests/test_example_pilot.py::test_never_collected" not in observed


# --------------------------------------------------------------------------- #
# AC-4: the control run gates every score
# --------------------------------------------------------------------------- #


def test_control_passes_when_every_prediction_matches(server_report):
    result = reducer.compare_control(server_report, _fixture("control-predictions-server.json"))
    assert result["passed"] is True
    assert [row["match"] for row in result["rows"]] == [True, True, True, True]


def test_control_rows_show_the_prediction_beside_the_outcome(server_report):
    result = reducer.compare_control(server_report, _fixture("control-predictions-server.json"))
    rows = {row["mutant"]: row for row in result["rows"]}
    skip_row = rows[f"{PILOT}.x_threshold__mutmut_1"]
    assert skip_row["predicted"] == reducer.CATEGORY_SKIPPED
    assert skip_row["actual"] == reducer.CATEGORY_SKIPPED


def test_control_fails_when_any_prediction_misses(server_report):
    result = reducer.compare_control(
        server_report, _fixture("control-predictions-server-mismatch.json")
    )
    assert result["passed"] is False
    missed = [row for row in result["rows"] if not row["match"]]
    assert [row["mutant"] for row in missed] == [f"{PILOT}.x_threshold__mutmut_1"]
    assert missed[0]["predicted"] == reducer.CATEGORY_KILLED
    assert missed[0]["actual"] == reducer.CATEGORY_SKIPPED


def test_control_fails_when_a_predicted_mutant_is_absent_from_the_run(server_report):
    # A control whose mutant never ran proves nothing, and must not read as a
    # pass: that is this repo's silently-failing mutation control, again.
    result = reducer.compare_control(
        server_report,
        {
            "side": "server",
            "predictions": [{"mutant": f"{PILOT}.x_absent__mutmut_1", "bucket": "killed"}],
        },
    )
    assert result["passed"] is False
    assert result["rows"][0]["actual"] is None


def test_no_score_is_reported_when_the_control_failed(server_document):
    payload = reducer.reduce_run(
        server_document,
        side="server",
        control=_fixture("control-predictions-server-mismatch.json"),
    )
    assert payload["mutation_score"] is None
    assert payload["control"]["passed"] is False
    assert f"{PILOT}.x_threshold__mutmut_1" in payload["score_refused_because"]


def test_no_score_is_reported_when_there_was_no_control_at_all(server_document):
    payload = reducer.reduce_run(server_document, side="server", control=None)
    assert payload["mutation_score"] is None
    assert payload["score_refused_because"]


def test_a_score_is_reported_once_the_control_passed(server_document):
    payload = reducer.reduce_run(
        server_document, side="server", control=_fixture("control-predictions-server.json")
    )
    assert payload["control"]["passed"] is True
    assert payload["mutation_score"] == pytest.approx(3 / 7)
    assert payload.get("score_refused_because") in (None, "")


def test_the_not_killed_list_survives_a_refused_score(server_document):
    # Refusing the score is not refusing the run: the surviving mutants and the
    # zero-kill list are what a person reads to find out why the control failed.
    payload = reducer.reduce_run(
        server_document,
        side="server",
        control=_fixture("control-predictions-server-mismatch.json"),
    )
    assert payload["not_killed"]
    assert payload["zero_kill_tests"]


# --------------------------------------------------------------------------- #
# AC-7: the reducer is pure, and prints JSON
# --------------------------------------------------------------------------- #


_FORBIDDEN_IMPORTS = {
    "subprocess",
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "sqlite3",
    "sqlmodel",
    "sqlalchemy",
    "loregarden",
    "os.popen",
}


def test_the_reducer_imports_nothing_impure():
    tree = ast.parse(_REDUCER.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & _FORBIDDEN_IMPORTS, sorted(imported & _FORBIDDEN_IMPORTS)


def test_the_reducer_prints_json_to_stdout(tmp_path):
    out = subprocess.run(
        [
            sys.executable,
            str(_REDUCER),
            "--side",
            "server",
            "--report",
            str(_FIXTURES / "mutmut-pilot-run.json"),
            "--control",
            str(_FIXTURES / "control-predictions-server.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["mutation_score"] == pytest.approx(3 / 7)


def test_the_reducer_output_is_json_serialisable(server_document, client_document):
    for document, side, control in (
        (server_document, "server", "control-predictions-server.json"),
        (client_document, "client", "control-predictions-client.json"),
    ):
        payload = reducer.reduce_run(document, side=side, control=_fixture(control))
        json.dumps(payload)


# --------------------------------------------------------------------------- #
# AC-1 / AC-6: the configuration the run depends on, pinned where a regression
# in it is cheap to see rather than a wrong number six hundred seconds later
# --------------------------------------------------------------------------- #


def _mutmut_block():
    text = _SERVER_PYPROJECT.read_text()
    marker = "\n[tool.mutmut]\n"
    start = text.index(marker)
    rest = text[start + len(marker) :]
    end = rest.find("\n[")
    return rest if end == -1 else rest[:end]


def test_mutmut_config_defeats_the_forced_dash_x():
    block = _mutmut_block()
    assert '"--maxfail=0"' in block
    assert '"-p"' in block and '"mutmut_killers"' in block


def test_maxfail_zero_is_configured_and_says_why():
    # mutmut forces `-x` in harness._pytest_args_regular_run. `-x` is
    # store_const const=1 dest=maxfail, and the later-appended `--maxfail=0`
    # writes the same dest, so it wins. Without the comment the next reader
    # deletes it as a slow default.
    comments = "\n".join(
        line for line in _mutmut_block().splitlines() if line.lstrip().startswith("#")
    ).lower()
    assert "-x" in comments
    assert "first killer" in comments or "only the first" in comments


@pytest.mark.parametrize(
    "setting",
    ["max_stack_depth", "mutate_only_covered_lines", "also_copy"],
)
def test_mutmut_config_names_the_sandbox_settings(setting):
    assert setting in _mutmut_block()


def _executable_lines(script_path):
    """A shell script with its comment lines removed.

    An assertion over the whole file cannot tell a flag that RUNS from a
    comment explaining why it must not be used.
    """
    return "\n".join(
        line for line in script_path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def test_run_client_refuses_to_reduce_a_report_this_run_did_not_write():
    """A stale report must not be read as this run's.

    mutation-out/ is gitignored and persists between runs, and the json
    reporter's path is the same every time. Without removing it first: Stryker
    dies before writing anything, the previous run's report is still there, the
    existence check passes, record_wall_clock.py stamps this run's seconds onto
    it — erasing the last sign it is stale — and the reducer prints a complete
    mutation score, exit 0, for a run that never happened. Reproduced with a
    failing npx shim: EXIT=0, mutants_total 6, killed 2, denominator 4.

    The condition is "no FRESH report", not "status != 0": Stryker exits
    non-zero whenever a mutant survived, which is the pilot's normal outcome,
    so failing on the status would reject every healthy run.
    """
    script = _executable_lines(_RUN_CLIENT)
    assert 'rm -f "$REPORT"' in script
    # Ordering is the whole guarantee: removing it AFTER the run would delete
    # the report the reducer needs, and removing it before is what makes "the
    # file exists" mean "this run wrote it".
    assert script.index('rm -f "$REPORT"') < script.index("npx stryker run")
    # And the status must not be the failure condition on its own.
    assert "stryker_status" in script


def test_run_client_checks_the_path_stryker_actually_writes():
    # $MUTATION_OUT_DIR cannot move Stryker's output: the json reporter's path
    # has no CLI form in Stryker 10, so client/stryker.config.json is the only
    # place it is set. Rebuilding the path from $MUTATION_OUT_DIR pointed the
    # existence check at a file Stryker never writes.
    script = _executable_lines(_RUN_CLIENT)
    assert "jsonReporter" in script, (
        "REPORT must be derived from the config that sets the reporter path, "
        "not rebuilt from $MUTATION_OUT_DIR"
    )
    assert 'REPORT="$OUT_DIR/client-run.json"' not in script


def test_an_absent_stryker_performance_block_reports_unknown_not_zero():
    """A zero that is not a measurement is worse than a gap that says so.

    Stryker's json reporter does not reliably carry a `performance` block: a
    real 10.1.1 run of 548 mutants over 16 minutes 24 seconds carried none,
    and the reducer summed the absent block to 0.0 — a wall clock of zero
    seconds printed beside it, which a reader takes as measured.
    """
    reducer = _load_reducer()
    document = copy.deepcopy(json.loads((_FIXTURES / "stryker-pilot-run.json").read_text()))
    document.pop("performance", None)
    assert reducer.parse_stryker_run(document)["wall_clock_seconds"] is None


def test_the_run_script_records_the_wall_clock_it_measured():
    # The script is the thing that watched the clock. The server side already
    # says this through collect_server_run.py --wall-clock-seconds.
    reducer = _load_reducer()
    document = copy.deepcopy(json.loads((_FIXTURES / "stryker-pilot-run.json").read_text()))
    document.pop("performance", None)
    document["wall_clock_seconds"] = 984.0
    assert reducer.parse_stryker_run(document)["wall_clock_seconds"] == 984.0
    assert "record_wall_clock.py" in _executable_lines(_RUN_CLIENT)


def _mutmut_config():
    """The [tool.mutmut] table as mutmut will read it."""
    return tomllib.loads(_SERVER_PYPROJECT.read_text())["tool"]["mutmut"]


def test_source_paths_is_the_whole_package_not_the_pilot_file():
    """mutmut copies source_paths; it does not copy only what it mutates.

    `copy_src_dir` copies exactly what `source_paths` names into
    server/mutants/, and `setup_source_paths` then puts mutants/ on sys.path
    and REMOVES the real source directory so the tests cannot reach unmutated
    code. Naming the single pilot FILE here produced a sandbox holding 1 of
    loregarden's 934 modules, and the run died in mutmut's baseline at
    `ModuleNotFoundError: No module named 'loregarden.config'` while importing
    server/tests/conftest.py — before any mutant ran, so no .meta and no
    score.
    """
    source_paths = _mutmut_config()["source_paths"]
    assert source_paths == ["loregarden"], (
        "source_paths must be the importable package, because mutmut copies it "
        "wholesale and then hides the original from sys.path"
    )


def test_the_pilot_is_scoped_by_only_mutate_to_exactly_one_module():
    # This is where "one pilot module" lives now. source_paths cannot carry it
    # (see above), and `walk_mutatable_files` filters on only_mutate, so this
    # is the key mutmut actually provides for the job.
    only_mutate = _mutmut_config()["only_mutate"]
    assert len(only_mutate) == 1, "a second entry here is a second pilot"
    assert only_mutate[0].endswith(".py")


@pytest.mark.parametrize(
    "selected",
    tomllib.loads(_SERVER_PYPROJECT.read_text())["tool"]["mutmut"][
        "pytest_add_cli_args_test_selection"
    ],
)
def test_no_selected_test_file_resolves_paths_from_its_own_location(selected):
    """Every selected file must be runnable from server/mutants/.

    mutmut runs pytest with cwd=mutants, so a test that resolves a path from
    its own __file__ resolves it one directory deeper and opens something that
    is not there. That failure lands in the BASELINE run, before any mutant is
    tested, so the campaign writes no .meta, no kill matrix and no score —
    hours spent to produce nothing.

    This is a cheap static proxy for `run-server.sh --check-selection`, which
    is the real check because it runs the files from the sandbox. It exists
    because the real check costs a sandbox generation and this one costs a
    read: tests/test_queue_slot_claims.py was in this list with
    a REPO_ROOT computed two levels up from its own file at line 32, and nothing
    in a suite of 91 passing config tests could see it.
    """
    source = (_ROOT / "server" / selected).read_text()
    for hazard in ("Path(__file__).resolve().parents", "__file__).parent.parent"):
        assert hazard not in source, (
            f"{selected} resolves a path from its own location, which moves when "
            "mutmut copies the tree to server/mutants/. Drop it from "
            "pytest_add_cli_args_test_selection (saying why, since its tests then "
            "appear nowhere in the review list) or pick another pilot module."
        )


def test_run_server_offers_the_sandbox_check_that_config_tests_cannot_do():
    # No number of passing config tests can see the sandbox; only a run from
    # the copied tree can. The flag exists so that check costs one command.
    script = _executable_lines(_RUN_SERVER)
    # The exact option arm, not the bare string: the failure message further
    # down names the flag too, and renaming the arm to --check-selectionXX
    # still contains "--check-selection" as a prefix. A substring standing in
    # for a behaviour is the defect class this file keeps meeting.
    assert "--check-selection) CHECK_SELECTION=1" in script
    # And the check must prove it is testing the sandbox rather than the real
    # package: the venv's editable install made a bare pytest run from
    # mutants/ import the REAL loregarden and report 81 passed for a selection
    # that then failed mutmut's own baseline.
    assert "PYTHONPATH" in script
    assert "*/mutants/loregarden/*" in script


def test_the_stryker_run_excludes_tests_that_assert_on_source_text():
    # usabilityCheckRepeatedText.test.ts readFileSync's usabilityCheck.ts and
    # asserts the source CONTAINS four literals. Stryker rewrites that file to
    # instrument it, so the assertion cannot hold and the DRY RUN fails before
    # any mutant is tested — exit 1, no json report. It is the TypeScript twin
    # of the Python file dropped from the mutmut selection.
    jest_config = json.loads(_STRYKER_CONFIG.read_text())["jest"]["config"]
    ignored = jest_config["testPathIgnorePatterns"]
    assert any("usabilityCheckRepeatedText" in pattern for pattern in ignored)


def _also_copy_entries():
    """The `also_copy` list as mutmut will read it."""
    return tomllib.loads(_SERVER_PYPROJECT.read_text())["tool"]["mutmut"]["also_copy"]


def test_mutmut_config_copies_the_kill_matrix_plugin_into_the_sandbox():
    # mutmut runs pytest with cwd=mutants/. A plugin that is not in `also_copy`
    # is not there, and `-p mutmut_killers` fails the whole run.
    #
    # This assertion used to be `"scripts/mutation/mutmut_killers.py" in
    # _mutmut_block()`, and it PASSED against a config that copied nothing:
    # the string was in the file, and the path it named did not exist from
    # server/. Asserting a substring of a config is not asserting that the
    # config works. What it has to pin is the shape mutmut can actually act on.
    assert "mutmut_killers.py" in _also_copy_entries()


@pytest.mark.parametrize("entry", _also_copy_entries())
def test_every_also_copy_entry_is_a_bare_top_level_filename(entry):
    """Three constraints in mutmut 3.8.1 (utils/file_utils.py), all load-bearing.

    `copy_also_copy_files` resolves each entry against mutmut's CWD — server/,
    where pyproject.toml is, NOT the repo root. It skips a missing path with a
    bare `continue`, so a wrong entry is a SILENT no-op. It copies with
    `shutil.copy2` and never mkdirs the destination's parent, so a nested path
    that does exist raises FileNotFoundError instead. And `setup_source_paths`
    puts only `mutants/` on sys.path, so `-p <name>` can import nothing but a
    top-level module there.

    A nested entry therefore fails three different ways and works in none.
    """
    assert "/" not in entry, (
        f"{entry!r} is nested: mutmut resolves also_copy against server/, "
        "copies without mkdir, and only puts mutants/ on sys.path"
    )


def test_run_server_stages_the_plugin_where_mutmut_can_copy_it():
    # `also_copy = ["mutmut_killers.py"]` is only true at run time if something
    # puts that file in server/. The repository keeps one copy, under
    # scripts/mutation/, so run-server.sh stages it and removes it after.
    #
    # Over executable lines, not the whole file: commenting the trap out left
    # its text in place, and an earlier version of this test passed on that.
    script = _executable_lines(_RUN_SERVER)
    assert "scripts/mutation/mutmut_killers.py" in script
    assert "$SERVER/mutmut_killers.py" in script
    assert "trap cleanup EXIT" in script


def test_run_server_refuses_a_run_whose_plugin_never_reached_the_sandbox():
    # copy_also_copy_files skips a missing path in silence. The only symptom
    # downstream is a review list with nothing in it, which reads as good news.
    #
    # The path has to be in the `[ -f ... ]` TEST, not merely in the error
    # message that test prints — weakening the condition and leaving the
    # message intact is exactly how this check would rot.
    script = _executable_lines(_RUN_SERVER)
    assert '[ -f "$SERVER/mutants/mutmut_killers.py" ]' in script
    assert "exit 1" in script


def test_the_plugin_ignores_mutmuts_own_phases():
    # mutmut sets MUTANT_UNDER_TEST to "fail", "stats", "list_all_tests" and
    # "mutant_generation" for its own pytest runs (mutmut/__main__.py). Each
    # would otherwise write a row and reach the report looking like a mutant
    # with no line, no operator and no killer.
    #
    # Asserted against the imported frozenset, not the source text: the names
    # also appear in the comment above it, so a source-substring check passed
    # even after the set itself was emptied.
    assert _load_plugin()._MUTMUT_PHASE_SENTINELS == frozenset(
        {"fail", "stats", "list_all_tests", "mutant_generation"}
    )


def test_the_plugin_registers_nothing_for_one_of_those_phases(monkeypatch, tmp_path):
    """The guard, exercised rather than read.

    A `fail.json` and a `stats.json` in the kill-matrix directory is what this
    looked like before: two rows that no .meta knows about, each reaching the
    report as a mutant nothing killed.
    """
    plugin = _load_plugin()
    monkeypatch.setenv("MUTMUT_KILL_MATRIX_DIR", str(tmp_path))

    class _Recorder:
        def __init__(self):
            self.registered = []

        def register(self, plugin_obj, name):
            self.registered.append(name)

        def get_plugin(self, name):
            return None

    for phase in ("fail", "stats", "list_all_tests", "mutant_generation"):
        monkeypatch.setenv("MUTANT_UNDER_TEST", phase)
        config = type("Config", (), {"pluginmanager": _Recorder()})()
        plugin.pytest_configure(config)
        assert config.pluginmanager.registered == [], phase

    monkeypatch.setenv("MUTANT_UNDER_TEST", f"{PILOT}.x_classify__mutmut_1")
    config = type("Config", (), {"pluginmanager": _Recorder()})()
    plugin.pytest_configure(config)
    assert config.pluginmanager.registered == ["mutmut-kill-matrix-recorder"]


def test_the_kill_matrix_plugin_keys_its_output_by_the_mutant_under_test():
    source = _PLUGIN.read_text()
    assert "MUTANT_UNDER_TEST" in source


def test_the_coverage_map_is_never_used_as_the_kill_matrix():
    # mutmut's `tests_by_mangled_function_name` is COVERAGE. Substituting it for
    # kills omits exactly the tests that touch the code and assert nothing,
    # which are the tests the zero-kill list exists to find (ticket 434).
    for path in sorted((_ROOT / "scripts" / "mutation").rglob("*")):
        if path.is_file() and path.suffix in (".py", ".sh", ".json", ".toml"):
            assert "tests_by_mangled_function_name" not in path.read_text(), path
    assert "tests_by_mangled_function_name" not in _mutmut_block()


def test_jest_is_held_to_one_worker_inside_each_stryker_worker():
    # jest.config.cjs reads $LOREGARDEN_CAPACITY_WORKERS, so N Stryker workers
    # each running jest spend the grant N times over. That does not fail
    # honestly; it presents as flake under load.
    config = json.loads(_STRYKER_CONFIG.read_text())
    assert config["jest"]["config"]["maxWorkers"] == 1
    # The config file is the ONLY place this can live: the jest runner's
    # options have no CLI form, and `stryker run --jest.config` is rejected
    # with "unknown option" in Stryker 10 — which run-client.sh then reported
    # as a missing json report, pointing the operator at the wrong cause.
    #
    # Comments are excluded on purpose: run-client.sh explains at that line
    # why the flag is not there, and the explanation is the thing that stops
    # the next reader from adding it back.
    assert "--jest." not in _executable_lines(_RUN_CLIENT)


def test_run_client_reports_strykers_own_exit_code_when_the_report_is_missing():
    # A bare `|| true` turned "stryker refused the command line" into
    # "stryker wrote no json report".
    script = _RUN_CLIENT.read_text()
    assert "stryker_status" in script
    assert "|| stryker_status=$?" in script


def test_stryker_config_keeps_the_projects_own_jest_config():
    # jest.config.maxWorkers is layered OVER jest.config.cjs by the runner's
    # mergeConfigSettings — it must not replace it, or the swc transform, the
    # jsdom environment and the setup files all go with it.
    config = json.loads(_STRYKER_CONFIG.read_text())
    assert config["jest"]["configFile"] == "jest.config.cjs"
    assert config["jest"]["projectType"] == "custom"


def test_stryker_config_scopes_the_pilot_and_asks_for_per_test_coverage():
    config = json.loads(_STRYKER_CONFIG.read_text())
    assert config["coverageAnalysis"] == "perTest"
    assert "json" in config["reporters"]
    assert len(config["mutate"]) == 1


@pytest.mark.parametrize(
    "pattern", [".stryker-tmp", "data/", ".profile/", "server/.venv", "server/mutants"]
)
def test_stryker_config_ignores_the_trees_that_would_be_copied_into_the_sandbox(pattern):
    config = json.loads(_STRYKER_CONFIG.read_text())
    ignored = " ".join(config["ignorePatterns"])
    assert pattern in ignored


# --------------------------------------------------------------------------- #
# test-break: the cases that reach a wrong-but-well-formed number
#
# Everything above runs against one fixture document per side, each holding one
# source file, one complete kill matrix, and one control that is present and
# well formed. Those are the happy shapes. A reducer can satisfy every one of
# them and still produce the output this pilot exists to prevent — a score that
# reads fine and is wrong, or a review list that says "delete these tests" about
# tests that kill things. The cases below are the ones that do that, derived
# from the same checked-in fixtures so the shapes stay real.
# --------------------------------------------------------------------------- #


def _rename(key):
    return key.replace("example_pilot.", "example_pilot_two.")


def _mutmut_with_a_second_file(document):
    """The same envelope, with a second mutated source file in it.

    mutmut writes one `<path>.meta` per mutated source file, so `meta` is a
    mapping and `run-server.sh` collects all of them. A reducer that reads
    `meta[next(iter(meta))]` — the shape every test above would accept, because
    every fixture above has exactly one key — silently drops a whole file's
    mutants and reports a score over the remainder.
    """
    twin = copy.deepcopy(document)
    second = "loregarden/services/example_pilot_two.py"
    block = copy.deepcopy(twin["meta"][next(iter(twin["meta"]))])
    for field in (
        "exit_code_by_key",
        "durations_by_key",
        "estimated_durations_by_key",
        "hash_by_function_name",
    ):
        block[field] = {_rename(key): value for key, value in block[field].items()}
    twin["meta"][second] = block
    twin["mutant_index"].update(
        {_rename(key): {**value, "file": second} for key, value in document["mutant_index"].items()}
    )
    twin["kill_matrix"].update(
        {_rename(key): copy.deepcopy(value) for key, value in document["kill_matrix"].items()}
    )
    return twin, second


def _stryker_with_a_second_file(document):
    twin = copy.deepcopy(document)
    first = twin["files"]["src/lib/examplePilot.ts"]
    second = copy.deepcopy(first)
    for mutant in second["mutants"]:
        mutant["id"] = "b" + mutant["id"]
    twin["files"]["src/lib/examplePilotTwo.ts"] = second
    return twin, "src/lib/examplePilotTwo.ts"


def test_the_mutmut_parser_reads_every_file_in_the_meta_mapping(server_document):
    document, second = _mutmut_with_a_second_file(server_document)
    report = reducer.parse_mutmut_run(document)

    expected = set()
    for block in document["meta"].values():
        expected |= set(block["exit_code_by_key"])
    assert {m["mutant_id"] for m in report["mutants"]} == expected
    assert len(report["mutants"]) == 22, "a mutant must reach the report exactly once"
    assert _mutant(report, f"{PILOT}_two.x_classify__mutmut_1")["file"] == second


def test_a_second_source_file_moves_the_score_rather_than_being_dropped(server_document):
    # The failure this catches is silent in the worst direction: dropping a file
    # removes its surviving mutants from the denominator, so the score reads
    # higher than the run measured.
    document, _ = _mutmut_with_a_second_file(server_document)
    totals = reducer.score(reducer.parse_mutmut_run(document))
    assert (totals["killed"], totals["not_killed"], totals["denominator"]) == (6, 8, 14)
    assert totals["excluded"] == 8


def test_the_stryker_parser_reads_every_file_in_the_report(client_document):
    document, _ = _stryker_with_a_second_file(client_document)
    report = reducer.parse_stryker_run(document)
    assert {m["mutant_id"] for m in report["mutants"]} == {
        "1", "2", "3", "4", "5", "6", "b1", "b2", "b3", "b4", "b5", "b6",
    }  # fmt: skip
    totals = reducer.score(report)
    assert (totals["killed"], totals["not_killed"], totals["excluded"]) == (4, 4, 4)


@pytest.mark.parametrize(
    ("mutant_id", "exit_code"),
    [(f"{PILOT}.x_classify__mutmut_3", 5), (f"{PILOT}.x_threshold__mutmut_4", 33)],
)
def test_a_skip_recorded_by_the_plugin_beats_a_no_tests_exit_code(
    server_document, mutant_id, exit_code
):
    # This is the ticket's own trap, in the shape it actually occurs: "a skipped
    # test executes nothing, records no coverage, and so lands in `no tests`".
    # Exit 34 is not how the skip usually arrives — the fixture above is the
    # easy case. When the skip arrives as exit 5 or 33, the exit code cannot
    # tell "nobody wrote a test" from "a test covers this and said nothing",
    # and only the plugin's row can. A reducer that classifies from the exit
    # code alone tells a reviewer to write a test that already exists, and the
    # mutant it covers goes on surviving.
    document = copy.deepcopy(server_document)
    assert (
        document["meta"][next(iter(document["meta"]))]["exit_code_by_key"][mutant_id] == exit_code
    )
    skipped = "tests/test_example_pilot.py::test_threshold_reads_the_database"
    document["kill_matrix"][mutant_id]["skipped"] = [skipped]

    mutant = _mutant(reducer.parse_mutmut_run(document), mutant_id)
    assert mutant["classification"] == reducer.CLASSIFICATION_NOT_KILLED
    assert mutant["category"] == reducer.CATEGORY_SKIPPED
    assert mutant["category"] != reducer.CATEGORY_NO_TESTS


@pytest.mark.parametrize(
    "control",
    [
        pytest.param({"side": "server", "predictions": []}, id="empty-predictions"),
        pytest.param({"side": "server"}, id="no-predictions-key"),
        pytest.param({}, id="empty-document"),
    ],
)
def test_a_control_that_compares_nothing_is_not_a_pass(server_report, control):
    # A control with no rows agrees with every run, which is the exact shape of
    # this repo's last silently-failing mutation control. `control is None` is
    # already refused above; a control that is present and vacuous reads as a
    # pass to every check in this file, and is the one a stale or half-written
    # predictions file actually produces.
    result = reducer.compare_control(server_report, control)
    assert result["passed"] is False
    assert result["rows"] == []


@pytest.mark.parametrize(
    "control",
    [
        pytest.param({"side": "server", "predictions": []}, id="empty-predictions"),
        pytest.param({"side": "server"}, id="no-predictions-key"),
    ],
)
def test_no_score_is_reported_for_a_control_that_compares_nothing(server_document, control):
    payload = reducer.reduce_run(server_document, side="server", control=control)
    assert payload["mutation_score"] is None
    assert payload["score_refused_because"]


def test_a_control_written_for_the_other_side_does_not_gate_this_one(server_document):
    # Stryker mutant ids are small integers and mutmut's are dotted names, so a
    # cross-side control can never match a row. It must fail for that reason
    # rather than pass for want of anything to disagree with.
    control = _fixture("control-predictions-client.json")
    assert (
        reducer.compare_control(reducer.parse_mutmut_run(server_document), control)["passed"]
        is False
    )
    payload = reducer.reduce_run(server_document, side="server", control=control)
    assert payload["mutation_score"] is None


def test_a_run_with_nothing_measurable_reports_no_score_rather_than_zero(server_document):
    # Every mutant import-fatal is what a mutated module that will not import
    # looks like, and it is reachable from one bad `also_copy`. The denominator
    # is then zero: a reducer that divides raises, and one that guards with
    # `or 1` prints 0.0 — which reads as "these tests kill nothing", the most
    # expensive wrong number this pilot can produce.
    document = copy.deepcopy(server_document)
    block = document["meta"][next(iter(document["meta"]))]
    block["exit_code_by_key"] = dict.fromkeys(block["exit_code_by_key"], 3)

    totals = reducer.score(reducer.parse_mutmut_run(document))
    assert (totals["killed"], totals["not_killed"], totals["denominator"]) == (0, 0, 0)
    assert totals["mutation_score"] is None

    payload = reducer.reduce_run(
        document, side="server", control=_fixture("control-predictions-server.json")
    )
    assert payload["mutation_score"] is None


def test_an_empty_kill_matrix_lists_no_zero_killers_at_all(server_document):
    # `-p mutmut_killers` not loading is a silent failure: mutmut still scores
    # every mutant from its exit code, and the only symptom is that no row was
    # written. If the zero-kill universe came from anywhere other than what the
    # plugin observed, the review list would name every test in the covering set
    # as a killer of nothing — a delete list for the whole module's tests, on no
    # evidence. An empty observation must produce an empty list.
    document = copy.deepcopy(server_document)
    document["kill_matrix"] = {}
    report = reducer.parse_mutmut_run(document)

    assert report["tests"] == {}
    assert reducer.zero_kill_tests(report) == []
    assert reducer.score(report)["denominator"] == 7


@pytest.mark.parametrize("side", ["server", "client"])
def test_the_parsers_do_not_rewrite_the_document_they_were_given(
    server_document, client_document, side
):
    # "Pure" is checked above by its import list, which a reducer that pops keys
    # off its input passes. run-*.sh reduces one document and then reports on
    # it; a parser that consumes its input gives the second reader a different
    # answer from the first.
    document = server_document if side == "server" else client_document
    parse = reducer.parse_mutmut_run if side == "server" else reducer.parse_stryker_run

    before = json.dumps(document, sort_keys=True)
    first = parse(document)
    second = parse(document)
    assert json.dumps(document, sort_keys=True) == before
    assert first == second


def test_reduce_run_is_repeatable_on_one_document(server_document):
    control = _fixture("control-predictions-server.json")
    before = json.dumps(server_document, sort_keys=True)
    first = reducer.reduce_run(server_document, side="server", control=control)
    second = reducer.reduce_run(server_document, side="server", control=control)
    assert json.dumps(server_document, sort_keys=True) == before
    assert first == second


def test_a_killer_stryker_cannot_name_is_kept_rather_than_dropped(client_document):
    # Stryker's `testFiles` is omitted or partial in more than one real
    # configuration. Resolving ids through it with a lookup that drops misses
    # loses a killer, and losing a killer is how a test that kills something
    # lands on the review list as killing nothing.
    document = copy.deepcopy(client_document)
    document["files"]["src/lib/examplePilot.ts"]["mutants"][0]["killedBy"] = ["0", "99"]

    killers = _mutant(reducer.parse_stryker_run(document), "1")["killed_by"]
    assert len(killers) == 2, killers
    assert any("99" in killer for killer in killers)


def test_a_test_whose_only_mutant_died_of_a_timeout_carries_that_trace(client_document):
    # Stryker attributes no `killedBy` to a Timeout, so a test that covered only
    # a timed-out mutant killed nothing it can be credited for and reaches the
    # review list. That is honest — and it is also exactly the row a reviewer
    # would wrongly mark `delete`, so the row has to carry the mutant it did
    # cover. AC-11 calls a row with no trace a defect in the criterion.
    document = copy.deepcopy(client_document)
    first = document["files"]["src/lib/examplePilot.ts"]["mutants"][0]
    first["killedBy"] = ["2"]
    first["coveredBy"] = ["1", "2"]

    report = reducer.parse_stryker_run(document)
    rows = {row["test"]: row for row in reducer.zero_kill_tests(report)}
    row = rows["examplePilot > classifies a large budget"]
    assert "4" in row["covered_mutants"]
    assert _mutant(report, "4")["category"] == reducer.CATEGORY_TIMEOUT


def test_a_missing_operator_list_is_not_silently_an_empty_one(server_document):
    # AC-9 makes the operator list required because a zero-kill verdict is
    # unreadable without it: some tests here guard concurrency, migration order
    # and prompt shape that no operator can reach. `document.get(..., [])` turns
    # a run script that failed to derive the list into a report that states no
    # operator reached anything, which is a different claim and a false one.
    document = copy.deepcopy(server_document)
    del document["operators_in_play"]
    try:
        payload = reducer.reduce_run(
            document, side="server", control=_fixture("control-predictions-server.json")
        )
    except (KeyError, ValueError):
        return  # refusing to reduce a run with no operator list is a fine answer
    assert payload.get("operators_in_play") != [], (
        "an absent operator list must not be reported as an empty one"
    )


# --------------------------------------------------------------------------- #
# Adversarial (test-break): the three ways this pilot loses a test off the
# review list, which is the direction nobody audits
# --------------------------------------------------------------------------- #
#
# The expensive output of this pilot is a test wrongly listed as killing
# nothing — AC-6 says so about `-x`, and the whole `--maxfail=0` argument is
# built on it. But every rule above checks the list's CONTENTS. Nothing yet
# checks what silently leaves it. A test disappears from the review list in
# exactly three ways, and each is a false "this test is fine" rather than a
# false "delete this test": its killer row never arrives, its name is shared
# with a test that did kill something, or its import error is credited to it
# as a kill. All three fail closed in the flattering direction.


def test_a_killed_mutant_with_a_recorded_killer_names_no_gap(server_document):
    """The control for the test below: the whole fixture reports no gap.

    Without this, a reducer that always reported every killed mutant as a gap
    would pass the next test and tell a reader nothing.
    """
    payload = reducer.reduce_run(server_document, side="server", control=None)
    assert payload["killed_without_recorded_killer"] == []


def test_a_killed_mutant_whose_kill_matrix_row_never_arrived_is_named(server_document):
    """A plugin that loaded for some mutants and not others must not pass as whole.

    The collector refuses a kill matrix that is entirely empty, because a
    silent `also_copy` failure otherwise reads as "nothing killed anything".
    A PARTIAL failure has no such guard and is the more likely one: one pytest
    process crashed before `pytest_sessionfinish`, one row failed to write, one
    mutant ran under a stale `MUTMUT_KILL_MATRIX_DIR`. mutmut still scores that
    mutant `killed` from its exit code, so the score is unaffected and nothing
    looks wrong — while every test that really killed it loses its only
    recorded kill and lands on the review list as a candidate for deletion.
    That is precisely the output AC-6 spends `--maxfail=0` to avoid, arriving
    by a different road.

    A timeout is NOT this: a mutant killed by SIGXCPU took the process down
    before any row could be written, so an absent row there is expected and
    must not be reported as a gap.
    """
    document = copy.deepcopy(server_document)
    killed = f"{PILOT}.x_classify__mutmut_1"
    assert document["meta"]["loregarden/services/example_pilot.py"]["exit_code_by_key"][killed] == 1
    lost_killers = document["kill_matrix"].pop(killed)["failing"]
    assert lost_killers, "the fixture's killed mutant must have had recorded killers"

    payload = reducer.reduce_run(document, side="server", control=None)

    assert killed in payload["killed_without_recorded_killer"], (
        "a mutant mutmut scored killed with no recorded killer is a hole in the kill "
        "matrix, and the report must name it: the score cannot see it and the review "
        f"list is what it corrupts. Report: {payload['killed_without_recorded_killer']}"
    )
    # And the damage is real, which is why it has to be named: the test that
    # killed it is now on the review list.
    assert any(row["test"] in lost_killers for row in payload["zero_kill_tests"]), (
        "the point of naming the gap is that a real killer has just become a zero-killer"
    )
    # The timeout is not dragged in with it.
    assert f"{PILOT}.x_threshold__mutmut_3" not in payload["killed_without_recorded_killer"]


def test_two_tests_sharing_a_name_are_not_one_test(client_document):
    """Stryker's `tests` map is keyed by id; two files may hold the same title.

    `_stryker_test_names` resolves id -> name and the report is then keyed by
    NAME, so a test called "examplePilot > classifies a large budget" in a
    second file is merged with the one in the first. Jest titles repeat across
    files constantly ("renders", "returns null when empty"), and the merge is
    one-directional in the damaging way: the killer's kills are credited to the
    pair, so the test that killed nothing vanishes from the review list.

    The merged pair here is a killer (id 0) and a newcomer (id 3) that covers
    one mutant and kills none — the exact row AC-11 exists to produce.
    """
    document = copy.deepcopy(client_document)
    shared = document["testFiles"]["src/lib/__tests__/examplePilot.test.ts"]["tests"][0]["name"]
    document["testFiles"]["src/lib/__tests__/examplePilotMore.test.ts"] = {
        "tests": [{"id": "3", "name": shared, "location": {"start": {"line": 4, "column": 3}}}]
    }
    survived = next(
        mutant
        for block in document["files"].values()
        for mutant in block["mutants"]
        if mutant["status"] == "Survived"
    )
    survived["coveredBy"] = [*survived.get("coveredBy", []), "3"]

    report = reducer.parse_stryker_run(document)
    zero_killers = {row["test"] for row in reducer.zero_kill_tests(report)}

    assert any(shared in test for test in zero_killers), (
        "a test that covered a surviving mutant and killed nothing was absorbed into a "
        "same-named test that did kill something, and left the review list entirely; "
        f"zero-kill list: {sorted(zero_killers)}"
    )
    killers = [
        test for test, entry in report["tests"].items() if shared in test and entry["killed"]
    ]
    assert killers, "and the test that really killed something must still be credited"


def test_a_failure_outside_the_call_phase_is_credited_but_marked():
    """A mutant that breaks an import fails every test in the module at SETUP.

    Which is a kill — the operator decided this one, overriding the assumption
    this stage first proceeded on ("a setup error credits no killer"). The
    binding answer is: **credit it, but mark those killers so the review list
    can discount them.** The suite's earlier reading — that a test which never
    reached its call phase detected nothing, so crediting it takes every test
    in the file off the review list for good — is the argument the mark now
    carries instead of the exclusion.

    So both facts have to survive into the row: the test killed this mutant,
    AND the way it killed it was by erroring. `errored` is where the mark
    already lives (the plugin writes that bucket today and the collector
    carries it through the merge), so the credit is additive: the node is in
    `failing` as well.

    The bucket names are the suite's choice. The requirement is that the
    reducer can tell an error-credited kill from an assertion, because that is
    the only thing a reviewer can discount.
    """
    plugin = _load_plugin()
    recorder = plugin.KillMatrixRecorder(f"{PILOT}.x_classify__mutmut_1", Path("/unused"))

    node = "tests/test_example_pilot.py::test_asserts_nothing_about_classify"
    recorder.pytest_runtest_logreport(
        _Report(nodeid=node, when="setup", failed=True, longrepr="ImportError: no module named x")
    )

    assert node in recorder.row["failing"], (
        "a failure outside the call phase is credited as a kill (operator decision); "
        "the review list discounts it from the mark, it is not withheld here"
    )
    assert node in recorder.row["errored"], (
        "and it must be MARKED: a credit with no mark is indistinguishable from an "
        "assertion that caught the mutant, which is what the reviewer has to discount"
    )


def test_a_call_phase_failure_is_a_kill_and_is_not_marked():
    """The control: an ordinary assertion failure must carry no error mark.

    Without this, a plugin that marked every failure would satisfy the test
    above and the mark would mean nothing.
    """
    plugin = _load_plugin()
    recorder = plugin.KillMatrixRecorder(f"{PILOT}.x_classify__mutmut_1", Path("/unused"))

    node = "tests/test_example_pilot.py::test_classify_rounds_to_the_floor"
    recorder.pytest_runtest_logreport(
        _Report(nodeid=node, when="call", failed=True, longrepr="assert 'over' == 'under'")
    )

    assert node in recorder.row["failing"]
    assert node not in recorder.row["errored"], (
        "an assertion that caught the mutant is not an error-credited kill"
    )


def test_an_error_credited_kill_reaches_the_report_marked(server_document):
    """The mark has to arrive where the review list is read, not stop at the row.

    A test credited by an import error is not on the zero-kill list any more —
    that is what crediting means — so a reviewer meets it as an ordinary
    killer. The discount is only possible if the report says which of its kills
    were errors, per mutant, and names the tests whose kills were ALL errors:
    those are the ones that look like killers and detected nothing, and they
    are what the first reading of this rule was trying to protect.
    """
    document = copy.deepcopy(server_document)
    mutant = f"{PILOT}.x_classify__mutmut_2"  # exit 0 in the fixture: a survivor
    block = document["meta"]["loregarden/services/example_pilot.py"]["exit_code_by_key"]
    block[mutant] = 1  # the import-fatal mutant mutmut scored as a kill
    only_errored = "tests/test_example_pilot.py::test_classify_logs_the_budget"
    document["kill_matrix"][mutant] = {
        "failing": [only_errored],
        "passed": [],
        "skipped": [],
        "timed_out": [],
        "errored": [only_errored],
    }

    report = reducer.parse_mutmut_run(document)
    payload = reducer.reduce_run(document, side="server", control=None)
    entry = report["tests"][only_errored]

    assert mutant in entry["killed"], "the kill is credited (operator decision)"
    assert mutant in entry["killed_by_error"], (
        "and the report must mark WHICH kills were errors; a reviewer cannot discount "
        f"what the report does not distinguish. Entry: {entry}"
    )
    assert only_errored in _mutant(report, mutant)["killed_by_error"], (
        "the mutant's own row must name its error-credited killers, because that is the "
        "row a reviewer reads beside its file:line operator"
    )
    assert only_errored in payload["killers_only_by_error"], (
        "a test whose every recorded kill is an error looks like a killer and detected "
        "nothing; the review list must be able to find it in one read"
    )
    assert only_errored not in {row["test"] for row in payload["zero_kill_tests"]}, (
        "it is credited, so it is not a zero-killer — the discount list is the mechanism, "
        "not the zero-kill list"
    )


def test_a_test_that_killed_by_asserting_is_not_on_the_discount_list(server_document):
    """The control for the discount list: a real killer must stay off it."""
    payload = reducer.reduce_run(server_document, side="server", control=None)
    report = reducer.parse_mutmut_run(server_document)

    real_killer = "tests/test_example_pilot.py::test_classify_rounds_to_the_floor"
    assert report["tests"][real_killer]["killed"], "fixture guard: this test kills something"
    assert report["tests"][real_killer]["killed_by_error"] == []
    assert real_killer not in payload["killers_only_by_error"]
    assert payload["killers_only_by_error"] == [], (
        "nothing in the untouched fixture was credited by an error, so the discount list "
        "is empty — a list that is never empty is a list nobody reads"
    )


class _Report:
    """The handful of `TestReport` attributes the plugin reads."""

    def __init__(self, *, nodeid, when, failed=False, skipped=False, passed=False, longrepr=None):
        self.nodeid = nodeid
        self.when = when
        self.failed = failed
        self.skipped = skipped
        self.passed = passed
        self.longrepr = longrepr
