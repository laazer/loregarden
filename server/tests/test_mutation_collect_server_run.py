"""The mutant index: does it describe the mutants the run actually generated?

`scripts/mutation/collect_server_run.py` merges three on-disk mutmut documents
into the one envelope `kill_matrix.py` reduces. Two of them mutmut writes
itself (`mutants/<path>.meta`, `mutants/<path>.spans`, and the mutated
`mutants/<path>.py`); the third, the `file:line  operator` column of the review
list, mutmut persists nowhere and the collector derives.

A derived column is only worth the fidelity of its derivation, and this one is
the pilot's whole output: a zero-kill verdict beside a confident wrong
`file:line  operator` is the "wrong but well-formed number" this pilot exists
to avoid, and it costs the campaign twice when it is found after the fact.
These tests are therefore written against a sandbox built by **mutmut's own
writers** — `SourceFileMutationData.save()` and `MutantLineSpans.save()` over a
real `mutate_file_contents` generation — so the thing the collector reads here
is shaped exactly like the thing a real run leaves behind. No `mutmut run` and
no Stryker run is invoked (AC-8's prohibition, carried into AC-14); mutmut's
generator functions are called directly, as library code, in milliseconds.

Criterion map (lg-build-verification-2639):

| criterion | tests here |
|---|---|
| AC-13 (i) / AC-14 (i) the index is derived from the generation the run performed | `test_the_index_describes_the_mutants_the_run_generated`, `test_no_mutant_is_indexed_against_an_uncovered_line`, `test_the_uncovered_run_is_the_one_the_meta_agrees_with` |
| AC-13 (iii) / AC-14 (ii) no operator shifts after a `# pragma: no mutate` | `test_a_pragma_no_mutate_shifts_no_operator` |
| AC-13 (ii) / AC-14 (iii) a gap is named, never guessed | `test_a_mutant_the_collector_cannot_verify_is_left_unindexed`, `test_an_unverifiable_mutant_reaches_unindexed_mutants_and_nowhere_else`, `test_a_derived_name_absent_from_the_meta_is_reported_by_name` |
| AC-6 / AC-14 (iv) a kill-matrix row with no `.meta` entry is dropped and named | `test_a_kill_matrix_row_that_is_not_a_mutant_is_dropped_and_named`, `test_a_kill_matrix_row_for_a_real_mutant_is_kept` |
| AC-8 the collector invokes no tool | `test_the_collector_shells_out_to_nothing` |

**Why a sandbox and not a call to `_derive_index`.** AC-13 leaves the mechanism
open: the collector may thread mutmut's own covered/excluded line state through,
or recover the generation from `mutants/<path>.py` and `MutantLineSpans`. Both
are legitimate, and a test that called the private helper would pin one of them
and reject the other. These tests drive `main()` over the artifacts instead, so
they state the requirement — *the index agrees with the run* — and leave the
implementer the choice.

**The defect these were written against**, reproduced here at a scale a reader
can hold: `_derive_index` re-generates with no `covered_lines`, while the run
generates under `mutate_only_covered_lines = true`. Mutants are numbered
sequentially per function, so one dropped mutation renumbers every later mutant
in that function. In `_PILOT_SOURCE` below, excluding one covered line takes
`classify` from 8 mutants to 7 and gives **all seven** of them the line and
operator of their successor.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from libcst.metadata import MetadataWrapper, PositionProvider
from mutmut.mutation import file_mutation
from mutmut.mutation.data import MutantLineSpans, SourceFileMutationData
from mutmut.mutation.file_mutation import create_mutations, mutate_file_contents
from mutmut.utils.format_utils import get_mutant_name
from tests.repo_paths import REPO_ROOT

_ROOT = REPO_ROOT
_COLLECTOR = _ROOT / "scripts" / "mutation" / "collect_server_run.py"
_REDUCER = _ROOT / "scripts" / "mutation" / "kill_matrix.py"

# A fixture path, not the module AC-3 selects. The shapes are real; the module
# is invented, so that nobody reads a number here as a measurement.
_PILOT_RELATIVE = "loregarden/services/example_pilot.py"

_PILOT_SOURCE = '''"""A stand-in for the pilot module: small, and mutable on every line."""


def classify(value, limit):
    if value > limit:
        return "over"
    if value == limit:
        return "equal"
    return "under"


def total(items):
    running = 0
    for item in items:
        running = running + item
    return running
'''

# `if value > limit:` — one real, mutable line the run's coverage did not reach.
_UNCOVERED_LINE = 5

_PRAGMA_SOURCE = '''"""A file whose first function loses a mutation to a pragma."""


def build(name, tag):
    return dict(
        name=name,
        tag=tag,  # pragma: no mutate
    )


def shout(word):
    return word.upper() + "!"
'''


# --------------------------------------------------------------------------- #
# Loading the deliverable
# --------------------------------------------------------------------------- #
#
# The collector is a script, not an installed module. Load it by path, and keep
# a missing file a per-test FAILURE rather than an ImportError at module scope:
# a collection error here would take the rest of `server/tests` down with it.


def _load(name, path):
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collector = _load("mutation_collect_server_run", _COLLECTOR)
reducer = _load("mutation_kill_matrix_for_collector", _REDUCER)


@pytest.fixture(autouse=True)
def _deliverables_exist():
    missing = [str(path.relative_to(_ROOT)) for path in (_COLLECTOR, _REDUCER) if not path.exists()]
    if missing:
        pytest.fail("not written yet: " + ", ".join(missing))


# --------------------------------------------------------------------------- #
# Ground truth: mutmut's generation, tagged by node identity
# --------------------------------------------------------------------------- #


def _generate(filename, code, covered_lines=None, excluded_lines=None):
    """mutmut's own generation pass, recording which operator produced what.

    Tagged **by node identity**, not by append order. `MutationVisitor` drops a
    mutation in `_removes_ignored_code` *after* the operator has already
    yielded it, so an order-based pairing silently shifts every later operator
    in the file by one — which is the AC-13 (iii) defect. Identity cannot shift.

    `keepalive` is load-bearing: a dropped node is unreferenced, and CPython
    reuses the id of a collected object, which would make the mapping lie.
    """
    by_node_id = {}
    keepalive = []
    original = file_mutation.mutation_operators

    def tagging(operator):
        def tagged(node):
            for mutated in operator(node):
                keepalive.append(mutated)
                by_node_id[id(mutated)] = operator.__name__
                yield mutated

        tagged.__name__ = operator.__name__
        return tagged

    file_mutation.mutation_operators = [
        (node_type, tagging(operator)) for node_type, operator in original
    ]
    try:
        module, mutations, _, _ = create_mutations(filename, code, covered_lines, excluded_lines)
    finally:
        file_mutation.mutation_operators = original

    positions = MetadataWrapper(module, unsafe_skip_copy=True).resolve(PositionProvider)

    by_function = {}
    for mutation in mutations:
        function = mutation.contained_by_top_level_function
        if function is None:
            continue
        position = positions.get(mutation.original_node)
        by_function.setdefault(function.name.value, []).append(
            {
                "line": position.start.line if position else None,
                "operator": by_node_id[id(mutation.mutated_node)],
            }
        )
    return by_function


def _expected_index(relative, code, covered_lines=None, excluded_lines=None):
    """What `mutant_index` must say, for the generation the run performed.

    Built from the same two mutmut calls the run itself makes, so the mutant
    names and the mutations are from one pass, not two.
    """
    mutated = mutate_file_contents(relative, code, covered_lines, excluded_lines)
    by_function = _generate(relative, code, covered_lines, excluded_lines)

    expected = {}
    seen = {}
    for name in mutated.mutant_names:
        base, _, _index = name.rpartition("__mutmut_")
        function = base.split("ǁ")[-1] if "ǁ" in base else base[2:]
        position = seen[function] = seen.get(function, 0) + 1
        entries = by_function[function]
        assert position <= len(entries), (
            f"{name}: mutmut named more mutants for {function} than it generated mutations "
            "— the ground-truth helper no longer matches mutmut's generation"
        )
        entry = entries[position - 1]
        expected[get_mutant_name(Path(relative), name)] = {
            "file": relative,
            "line": entry["line"],
            "operator": entry["operator"],
        }
    for function, entries in by_function.items():
        assert seen.get(function, 0) == len(entries), (
            f"{function}: {len(entries)} mutations generated but {seen.get(function, 0)} mutants "
            "named — the ground-truth helper no longer matches mutmut's generation"
        )
    return expected


# --------------------------------------------------------------------------- #
# A sandbox shaped like the one a real run leaves behind
# --------------------------------------------------------------------------- #


class _Sandbox:
    """`server/` after a `mutmut run`: the source, `mutants/`, and the rows."""

    def __init__(self, root, relative, code, expected):
        self.root = root
        self.relative = relative
        self.code = code
        self.expected = expected
        self.meta_path = root / "mutants" / (relative + ".meta")
        self.kill_matrix_dir = root / "kill-matrix"
        self.out = root / "run.json"

    def meta(self):
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def rewrite_meta(self, meta):
        self.meta_path.write_text(json.dumps(meta, indent=4), encoding="utf-8")

    def write_kill_matrix_row(
        self, mutant, failing=(), passed=(), skipped=(), timed_out=(), errored=()
    ):
        self.kill_matrix_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in mutant)
        (self.kill_matrix_dir / f"{safe}.json").write_text(
            json.dumps(
                {
                    "mutant": mutant,
                    "failing": list(failing),
                    "passed": list(passed),
                    "skipped": list(skipped),
                    "timed_out": list(timed_out),
                    "errored": list(errored),
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    def collect(self):
        """Run the collector's CLI, returning (document, stdout)."""
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = collector.main(
                [
                    "--server-root",
                    str(self.root),
                    "--source",
                    self.relative,
                    "--kill-matrix-dir",
                    str(self.kill_matrix_dir),
                    "--wall-clock-seconds",
                    "12.5",
                    "--out",
                    str(self.out),
                ]
            )
        assert exit_code == 0
        return json.loads(self.out.read_text(encoding="utf-8")), stdout.getvalue()


def _build_sandbox(tmp_path, monkeypatch, relative, code, covered_lines=None, excluded_lines=None):
    """Generate a run's artifacts with mutmut's own writers, under `tmp_path`.

    `SourceFileMutationData` and `MutantLineSpans` both resolve `mutants/`
    against the process CWD, exactly as they do when mutmut runs from
    `server/`. Writing them by hand instead would mean this suite asserting
    against a shape of its own invention.
    """
    root = tmp_path / "server"
    source_path = root / relative
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(code, encoding="utf-8")

    mutated = mutate_file_contents(relative, code, covered_lines, excluded_lines)

    mutants_path = root / "mutants" / relative
    mutants_path.parent.mkdir(parents=True, exist_ok=True)
    mutants_path.write_text(mutated.code, encoding="utf-8")

    monkeypatch.chdir(root)
    data = SourceFileMutationData(path=relative)
    data.exit_code_by_key = {
        get_mutant_name(Path(relative), name): 0 for name in mutated.mutant_names
    }
    data.hash_by_function_name = dict(mutated.hash_by_function_name)
    data.type_check_error_by_key = {}
    data.durations_by_key = {}
    data.estimated_time_of_tests_by_mutant = {}
    data.save()
    MutantLineSpans(path=relative, span_by_function_name=mutated.line_span_by_function_name).save()

    sandbox = _Sandbox(
        root, relative, code, _expected_index(relative, code, covered_lines, excluded_lines)
    )
    # The collector refuses an empty kill matrix (a silent `also_copy` failure
    # otherwise reads as "nothing killed anything"), so every sandbox carries
    # one real row.
    first = next(iter(data.exit_code_by_key))
    sandbox.write_kill_matrix_row(first, failing=["tests/test_example_pilot.py::test_classify"])
    return sandbox


@pytest.fixture
def uncovered_sandbox(tmp_path, monkeypatch):
    """The run mutated everything except one real, mutable line."""
    covered = set(range(1, len(_PILOT_SOURCE.splitlines()) + 1)) - {_UNCOVERED_LINE}
    return _build_sandbox(
        tmp_path,
        monkeypatch,
        _PILOT_RELATIVE,
        _PILOT_SOURCE,
        covered_lines=covered,
        excluded_lines=set(),
    )


@pytest.fixture
def pragma_sandbox(tmp_path, monkeypatch):
    return _build_sandbox(
        tmp_path, monkeypatch, "loregarden/services/example_pragma.py", _PRAGMA_SOURCE
    )


# --------------------------------------------------------------------------- #
# The fixtures themselves must demonstrate the hazard
# --------------------------------------------------------------------------- #


def test_the_uncovered_run_is_the_one_the_meta_agrees_with(uncovered_sandbox):
    """The sandbox reproduces AC-13's setup: coverage filtering renumbered a function.

    Without this, a green run of the tests below would not distinguish "the
    collector is right" from "the fixture had nothing to get wrong".
    """
    unfiltered = mutate_file_contents(_PILOT_RELATIVE, _PILOT_SOURCE)
    produced = set(uncovered_sandbox.meta()["exit_code_by_key"])

    assert len(produced) == len(unfiltered.mutant_names) - 1, (
        "excluding one covered line must drop exactly one mutation, or the renumbering "
        "this suite is about does not happen in this fixture"
    )

    # And the renumbering is real: the run's mutant 1 of `classify` is the
    # unfiltered pass's mutant 2.
    shifted = [
        name
        for name, entry in uncovered_sandbox.expected.items()
        if "classify" in name and entry["line"] != _UNCOVERED_LINE
    ]
    assert len(shifted) == 7, (
        "the fixture should leave seven classify mutants whose line an unfiltered "
        f"re-derivation would get wrong, found {len(shifted)}"
    )
    assert all(entry["line"] != _UNCOVERED_LINE for entry in uncovered_sandbox.expected.values())


# --------------------------------------------------------------------------- #
# AC-13 (i) / AC-14 (i): the index describes the run, not a second generation
# --------------------------------------------------------------------------- #


def test_the_index_describes_the_mutants_the_run_generated(uncovered_sandbox):
    """Line for line and operator for operator, against the run's own generation."""
    document, _ = uncovered_sandbox.collect()
    index = document["mutant_index"]

    wrong = {
        name: {"reported": index[name], "actual": expected}
        for name, expected in uncovered_sandbox.expected.items()
        if name in index and index[name] != expected
    }
    assert not wrong, (
        "the mutant index disagrees with the generation the run performed:\n"
        + json.dumps(wrong, indent=2)
    )
    assert set(index) == set(uncovered_sandbox.expected), (
        "every mutant the run generated is indexable from the run's own artifacts; "
        f"missing {sorted(set(uncovered_sandbox.expected) - set(index))}, "
        f"invented {sorted(set(index) - set(uncovered_sandbox.expected))}"
    )


def test_no_mutant_is_indexed_against_an_uncovered_line(uncovered_sandbox):
    """`mutate_only_covered_lines = true` means no mutant exists on line 5.

    A row pointing a reviewer at a line the run never mutated sends them to read
    code that has nothing to do with the verdict beside it.
    """
    document, _ = uncovered_sandbox.collect()
    offenders = {
        name: entry
        for name, entry in document["mutant_index"].items()
        if entry["line"] == _UNCOVERED_LINE
    }
    assert not offenders, (
        f"line {_UNCOVERED_LINE} was excluded from coverage and generated no mutant, but "
        f"the index puts {len(offenders)} there: {sorted(offenders)}"
    )


def test_every_indexed_operator_is_one_that_was_in_play(uncovered_sandbox):
    document, _ = uncovered_sandbox.collect()
    in_play = set(document["operators_in_play"])
    assert in_play
    for name, entry in document["mutant_index"].items():
        assert entry["operator"] in in_play, f"{name}: unknown operator {entry['operator']!r}"


# --------------------------------------------------------------------------- #
# AC-13 (iii) / AC-14 (ii): a pragma must not shift the operator column
# --------------------------------------------------------------------------- #


def test_a_pragma_no_mutate_shifts_no_operator(pragma_sandbox):
    """`_removes_ignored_code` drops a mutation the operator already yielded.

    Pairing by append order therefore shifts every later operator in the file by
    one. The symptom is quiet and total: in this fixture `shout`'s three mutants
    are a string swap, a method swap and an operator swap, and an order-paired
    derivation labels all three `operator_arg_removal`.
    """
    document, _ = pragma_sandbox.collect()
    index = document["mutant_index"]

    wrong = {
        name: {"reported": index[name]["operator"], "actual": expected["operator"]}
        for name, expected in pragma_sandbox.expected.items()
        if name in index and index[name]["operator"] != expected["operator"]
    }
    assert not wrong, (
        "a mutation dropped at a `# pragma: no mutate` shifted the operator column:\n"
        + json.dumps(wrong, indent=2)
    )
    assert set(index) == set(pragma_sandbox.expected)
    assert {entry["operator"] for entry in index.values()} != {"operator_arg_removal"}, (
        "every operator collapsing to one name is the shift, not a file with one operator"
    )


# --------------------------------------------------------------------------- #
# AC-13 (ii) / AC-14 (iii): a gap is named, never guessed
# --------------------------------------------------------------------------- #


def test_a_mutant_the_collector_cannot_verify_is_left_unindexed(uncovered_sandbox):
    """A `.meta` key the run's artifacts cannot account for gets no line, no operator.

    This is the reconciliation AC-13 (ii) asks for: the derived names are
    checked against mutmut's own `.meta`, not against a second copy of the same
    unfiltered pass (which agrees with itself by construction and checks
    nothing).
    """
    meta = uncovered_sandbox.meta()
    stale = "loregarden.services.example_pilot.x_classify__mutmut_99"
    meta["exit_code_by_key"][stale] = 0
    uncovered_sandbox.rewrite_meta(meta)

    document, stdout = uncovered_sandbox.collect()

    assert stale not in document["mutant_index"], (
        "the collector invented a line and operator for a mutant no generation produced"
    )
    assert stale in stdout, (
        "a mutant the collector could not verify must be named on stdout, not left to a "
        "reader to notice as a blank cell"
    )


def test_an_unverifiable_mutant_reaches_unindexed_mutants_and_nowhere_else(uncovered_sandbox):
    """Composed with the reducer, because that is where the gap becomes visible.

    The name chosen here is the dangerous one: `x_classify__mutmut_8` is a
    mutant the *unfiltered* generation produces and this run did not. A
    collector that re-derives without the run's coverage filter will happily
    hand it a confident line and operator, which is the whole AC-13 defect in
    one row.
    """
    meta = uncovered_sandbox.meta()
    stale = "loregarden.services.example_pilot.x_classify__mutmut_8"
    meta["exit_code_by_key"][stale] = 0
    uncovered_sandbox.rewrite_meta(meta)

    document, _ = uncovered_sandbox.collect()
    report = reducer.parse_mutmut_run(document)

    assert stale in report["unindexed_mutants"]
    row = next(mutant for mutant in report["mutants"] if mutant["mutant_id"] == stale)
    assert row["line"] is None
    assert row["operator"] is None

    # And nothing else was dragged into the gap: every mutant the run really
    # generated still carries its line and operator.
    unexpected = set(report["unindexed_mutants"]) - {stale}
    assert not unexpected, f"indexable mutants reported as gaps: {sorted(unexpected)}"


def test_a_derived_name_absent_from_the_meta_is_reported_by_name(uncovered_sandbox):
    """The other direction: the collector derived a mutant the run did not record.

    A `.meta` short of a mutant the generation produces means the run and the
    report disagree about what was tested. Absorbing it writes a row for a
    mutant with no verdict; dropping it in silence loses the only evidence that
    the two documents diverged.
    """
    meta = uncovered_sandbox.meta()
    missing = sorted(meta["exit_code_by_key"])[-1]
    del meta["exit_code_by_key"][missing]
    uncovered_sandbox.rewrite_meta(meta)

    document, stdout = uncovered_sandbox.collect()

    assert missing not in document["mutant_index"], (
        "a mutant mutmut never recorded a verdict for must not reach the index"
    )
    assert missing in stdout, (
        "a derived name absent from the .meta must be reported BY NAME: a count tells a "
        "reader nothing about whether the run or the derivation is the one that is wrong"
    )


# --------------------------------------------------------------------------- #
# AC-6 / AC-14 (iv): the kill-matrix merge drops and names
# --------------------------------------------------------------------------- #


def test_a_kill_matrix_row_that_is_not_a_mutant_is_dropped_and_named(uncovered_sandbox):
    """A stale row from an older build, or one of mutmut's own phases.

    Kept, it arrives at the review list as a mutant nothing killed, with no line
    and no operator. Dropped in silence, it is a test result that vanished
    between the run and the report.
    """
    uncovered_sandbox.write_kill_matrix_row(
        "loregarden.services.example_pilot.x_classify__mutmut_404",
        failing=["tests/test_example_pilot.py::test_stale"],
    )
    uncovered_sandbox.write_kill_matrix_row("stats", passed=["tests/test_example_pilot.py::test_x"])

    document, stdout = uncovered_sandbox.collect()

    assert "loregarden.services.example_pilot.x_classify__mutmut_404" not in document["kill_matrix"]
    assert "stats" not in document["kill_matrix"]
    assert "x_classify__mutmut_404" in stdout
    assert "stats" in stdout


def test_a_kill_matrix_row_for_a_real_mutant_is_kept(uncovered_sandbox):
    """The control for the test above: the drop rule must not drop everything."""
    real = sorted(uncovered_sandbox.meta()["exit_code_by_key"])[0]
    uncovered_sandbox.write_kill_matrix_row(
        real,
        failing=[
            "tests/test_example_pilot.py::test_over",
            "tests/test_example_pilot.py::test_under",
        ],
        skipped=["tests/test_example_pilot.py::test_equal"],
    )

    document, _ = uncovered_sandbox.collect()

    assert document["kill_matrix"][real]["failing"] == [
        "tests/test_example_pilot.py::test_over",
        "tests/test_example_pilot.py::test_under",
    ]
    assert document["kill_matrix"][real]["skipped"] == ["tests/test_example_pilot.py::test_equal"]


# --------------------------------------------------------------------------- #
# AC-8 / AC-14: the collector runs no tool
# --------------------------------------------------------------------------- #


def test_the_collector_shells_out_to_nothing():
    """No `mutmut run`, no Stryker, no subprocess at all.

    The collector reads artifacts a run already left behind. A subprocess here
    would make this suite a campaign, and AC-8's millisecond re-runnability is
    the reason the control is one anybody re-runs.
    """
    tree = ast.parse(_COLLECTOR.read_text(encoding="utf-8"))
    forbidden = {"subprocess", "os.system", "popen", "pty", "asyncio.subprocess"}
    imported = set()
    for node in ast.walk(tree):
        if type(node) is ast.Import:
            imported.update(alias.name for alias in node.names)
        elif type(node) is ast.ImportFrom and node.module:
            imported.add(node.module)
    assert not (imported & forbidden), f"the collector imports {sorted(imported & forbidden)}"

    source = _COLLECTOR.read_text(encoding="utf-8")
    for call in ("subprocess.", "os.system(", "os.popen(", "mutmut run", "stryker run"):
        assert call not in source, f"the collector invokes {call!r}"


# --------------------------------------------------------------------------- #
# Adversarial (test-break): shapes the pilot module really has, which the
# fixtures above do not
# --------------------------------------------------------------------------- #
#
# Everything above is one module-level function per mutant name. The module
# AC-3 selects is ordinary server code, which means classes — and mutmut mangles
# a method as `xǁClassǁmethod__mutmut_N`, so two classes with a method of the
# same name produce two runs of mutants whose names differ only in a part the
# collector's `_function_of` throws away. That is not a corner: `spend`, `run`,
# `save`, `to_dict` on two classes of one module is the ordinary case.


_TWO_CLASSES_SOURCE = '''"""Two classes, one method name — mutmut mangles the class into the name."""


class Budget:
    def spend(self, amount):
        self.left = self.left - amount
        return self.left


class Ledger:
    def spend(self, amount):
        return amount * 2


def free(value):
    return value + 1
'''

# `return amount * 2` in `Ledger.spend` — nowhere near `Budget.spend`, so an
# index that confuses the two is visible as a line, not only as an operator.
_LEDGER_BODY_LINE = 12

_PRAGMA_AND_COVERAGE_SOURCE = '''"""Both hazards in one file: a pragma in one function, a dead line in another."""


def build(name, tag):
    return dict(
        name=name,
        tag=tag,  # pragma: no mutate
    )


def classify(value, limit):
    if value > limit:
        return "over"
    return "under"
'''

# `if value > limit:` in `classify`, below the pragma that drops a mutation in
# `build`. A fix for either hazard alone leaves this file wrong.
_PRAGMA_AND_COVERAGE_DEAD_LINE = 12

_SECOND_SOURCE = '''"""A second pilot source in the same run."""


def tally(rows):
    return len(rows) + 1
'''


def _qualified_name_by_def_line(code):
    """`{line of the `def`: mutmut's mangled function name}` for one module.

    mutmut mangles a method as `xǁClassǁmethod` and a function as `x_name`
    (`utils/format_utils.py`), and the `def` line is what tells two methods of
    the same name apart. libcst and ast agree on that line for an undecorated
    `def`, and these fixtures carry no decorators.
    """
    names = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if type(child) is ast.ClassDef:
                walk(child, [*prefix, child.name])
            elif type(child) in (ast.FunctionDef, ast.AsyncFunctionDef):
                names[child.lineno] = (
                    "ǁ".join(["x", *prefix, child.name]) if prefix else "x_" + child.name
                )

    walk(ast.parse(code), [])
    return names


def _expected_index_qualified(relative, code, covered_lines=None, excluded_lines=None):
    """`_expected_index`, but keyed by the mangled name rather than the bare one.

    The helper above groups mutations under `function.name.value`, which is
    `spend` for both `Budget.spend` and `Ledger.spend`. That grouping is the
    very confusion under test here, so the ground truth must not share it:
    group by the identity of the enclosing `def` instead, which is unique.
    """
    operator_by_node = {}
    keepalive = []
    original = file_mutation.mutation_operators

    def tagging(operator):
        def tagged(node):
            for mutated in operator(node):
                keepalive.append(mutated)
                operator_by_node[id(mutated)] = operator.__name__
                yield mutated

        tagged.__name__ = operator.__name__
        return tagged

    file_mutation.mutation_operators = [
        (node_type, tagging(operator)) for node_type, operator in original
    ]
    try:
        module, mutations, _, _ = create_mutations(relative, code, covered_lines, excluded_lines)
    finally:
        file_mutation.mutation_operators = original

    positions = MetadataWrapper(module, unsafe_skip_copy=True).resolve(PositionProvider)
    mangled_by_line = _qualified_name_by_def_line(code)

    entries = {}
    for mutation in mutations:
        function = mutation.contained_by_top_level_function
        if function is None:
            continue
        mangled = mangled_by_line.get(positions.get(function).start.line)
        assert mangled, "the ground-truth helper no longer matches mutmut's naming"
        position = positions.get(mutation.original_node)
        entries.setdefault(mangled, []).append(
            {
                "line": position.start.line if position else None,
                "operator": operator_by_node[id(mutation.mutated_node)],
            }
        )

    mutated = mutate_file_contents(relative, code, covered_lines, excluded_lines)
    expected = {}
    for name in mutated.mutant_names:
        mangled, _, index = name.rpartition("__mutmut_")
        produced = entries[mangled]
        assert int(index) <= len(produced), (
            f"{name}: mutmut named more mutants for {mangled} than it generated mutations"
        )
        entry = produced[int(index) - 1]
        expected[get_mutant_name(Path(relative), name)] = {
            "file": relative,
            "line": entry["line"],
            "operator": entry["operator"],
        }
    return expected


@pytest.fixture
def two_classes_sandbox(tmp_path, monkeypatch):
    relative = "loregarden/services/example_two_classes.py"
    sandbox = _build_sandbox(tmp_path, monkeypatch, relative, _TWO_CLASSES_SOURCE)
    sandbox.expected = _expected_index_qualified(relative, _TWO_CLASSES_SOURCE)
    return sandbox


@pytest.fixture
def pragma_and_coverage_sandbox(tmp_path, monkeypatch):
    code = _PRAGMA_AND_COVERAGE_SOURCE
    covered = set(range(1, len(code.splitlines()) + 1)) - {_PRAGMA_AND_COVERAGE_DEAD_LINE}
    return _build_sandbox(
        tmp_path,
        monkeypatch,
        "loregarden/services/example_both_hazards.py",
        code,
        covered_lines=covered,
        excluded_lines=set(),
    )


def test_the_two_class_fixture_really_shares_a_method_name(two_classes_sandbox):
    """The hazard is in the fixture, not only in the test's intent.

    Both classes must contribute mutants under the same bare method name, and
    their lines must differ — otherwise a confused index would be right by
    accident and the test below would prove nothing.
    """
    names = set(two_classes_sandbox.meta()["exit_code_by_key"])
    budget = {name for name in names if "ǁBudgetǁspend" in name}
    ledger = {name for name in names if "ǁLedgerǁspend" in name}
    assert budget and ledger, f"the fixture generated no paired method mutants: {sorted(names)}"

    lines = {entry["line"] for entry in two_classes_sandbox.expected.values()}
    assert len(lines) > 1, "every mutant on one line cannot distinguish a confused index"
    assert _LEDGER_BODY_LINE in lines, (
        f"line {_LEDGER_BODY_LINE} is Ledger.spend's only mutable line and must carry mutants"
    )


def test_a_method_name_shared_by_two_classes_is_never_indexed_against_the_wrong_one(
    two_classes_sandbox,
):
    """AC-13 (ii) over mutmut's `ǁ` mangling, which is how a method is named.

    `_function_of` keeps only the last `ǁ` segment, so `Budget.spend` and
    `Ledger.spend` collapse into one bucket of mutations. The ambiguity guard
    does not fire, because it compares a count against a count: two classes
    with two mutations each produce four mutants and four mutations, which
    agree. Every `Ledger.spend` mutant is then handed the line and the operator
    of the `Budget.spend` mutant with the same ordinal — a confident wrong
    `file:line  operator` on a review list, which is the one output this pilot
    exists not to produce.

    Either answer satisfies the criterion: index the mutant correctly, or name
    it as a gap. What is forbidden is the third thing the collector does today
    — printing a line and an operator it never verified.
    """
    document, stdout = two_classes_sandbox.collect()
    index = document["mutant_index"]

    wrong = {
        name: {"reported": index[name], "actual": expected}
        for name, expected in two_classes_sandbox.expected.items()
        if name in index and index[name] != expected
    }
    assert not wrong, (
        "a mutant was indexed against another class's method of the same name:\n"
        + json.dumps(wrong, indent=2, ensure_ascii=False)
    )

    for name in sorted(set(two_classes_sandbox.expected) - set(index)):
        assert name in stdout, (
            f"{name} could not be indexed and must be named on stdout rather than "
            "disappearing from the report"
        )


def test_a_module_function_beside_two_classes_keeps_its_own_line(two_classes_sandbox):
    """The collapse must not be answered by dropping the whole file.

    `free` is an ordinary module-level function in the same module as the two
    colliding methods. A fix that reaches for "index nothing in a file that has
    an ambiguity" passes the test above and empties the review list's operator
    column, which AC-9 requires to be there.
    """
    document, _ = two_classes_sandbox.collect()
    index = document["mutant_index"]
    free = {name: entry for name, entry in index.items() if "x_free__" in name}
    assert free, "no module-level function mutant survived indexing at all"
    for name, entry in free.items():
        assert entry == two_classes_sandbox.expected[name], f"{name} is indexed wrongly"


def test_a_pragma_and_an_uncovered_line_in_one_file_shift_nothing(pragma_and_coverage_sandbox):
    """Both AC-13 hazards at once, which is the state of any real module.

    The two defects have different causes — an append-ordered operator list for
    the pragma, an unfiltered re-generation for the coverage — and a fix for
    one leaves the other. A file carrying both is the only fixture that can say
    so, because each single-hazard fixture passes under the other's fix alone.
    """
    document, stdout = pragma_and_coverage_sandbox.collect()
    index = document["mutant_index"]

    wrong = {
        name: {"reported": index[name], "actual": expected}
        for name, expected in pragma_and_coverage_sandbox.expected.items()
        if name in index and index[name] != expected
    }
    assert not wrong, (
        "a pragma and an uncovered line in one file moved the index off the run:\n"
        + json.dumps(wrong, indent=2)
    )
    assert not [
        name for name, entry in index.items() if entry["line"] == _PRAGMA_AND_COVERAGE_DEAD_LINE
    ], f"line {_PRAGMA_AND_COVERAGE_DEAD_LINE} was uncovered and generated no mutant"
    for name in sorted(set(pragma_and_coverage_sandbox.expected) - set(index)):
        assert name in stdout, f"{name} is unindexed and must be named"


def test_every_source_of_a_multi_file_run_is_indexed(tmp_path, monkeypatch):
    """`--source` is `action="append"`, and the index is one dict for all of them.

    The reducer's own tests already pin that a second file moves the score
    rather than being dropped; nothing yet pins that the collector's index
    survives a second file. A derivation that keys anything by the bare
    function name, or that reads one file's artifacts for every file, is
    visible here and nowhere else.
    """
    first = _build_sandbox(tmp_path, monkeypatch, _PILOT_RELATIVE, _PILOT_SOURCE)

    second_relative = "loregarden/services/example_second.py"
    second = _build_sandbox(tmp_path, monkeypatch, second_relative, _SECOND_SOURCE)
    assert second.root == first.root

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = collector.main(
            [
                "--server-root",
                str(first.root),
                "--source",
                _PILOT_RELATIVE,
                "--source",
                second_relative,
                "--kill-matrix-dir",
                str(first.kill_matrix_dir),
                "--wall-clock-seconds",
                "12.5",
                "--out",
                str(first.out),
            ]
        )
    assert exit_code == 0
    document = json.loads(first.out.read_text(encoding="utf-8"))

    assert set(document["meta"]) == {_PILOT_RELATIVE, second_relative}
    index = document["mutant_index"]
    expected = {**first.expected, **second.expected}
    wrong = {
        name: {"reported": index[name], "actual": entry}
        for name, entry in expected.items()
        if name in index and index[name] != entry
    }
    assert not wrong, "a second source moved the first file's index:\n" + json.dumps(
        wrong, indent=2
    )
    assert {entry["file"] for entry in index.values()} == {
        _PILOT_RELATIVE,
        second_relative,
    }, "one of the two sources contributed no indexed mutant at all"


def test_the_error_mark_survives_the_kill_matrix_merge(uncovered_sandbox):
    """`errored` is where an error-credited kill is marked, so it has to arrive.

    The operator's decision for this ticket is that a failure outside the call
    phase IS credited as a kill, discounted on the review list from a mark
    rather than withheld. `_merge_kill_matrix` rebuilds each row bucket by
    bucket from a fixed list of names, so a bucket missing from that list is
    dropped in silence — and a mark that does not survive the merge leaves the
    credit behind with nothing to discount it by, which is the one outcome the
    decision rules out.
    """
    real = sorted(uncovered_sandbox.meta()["exit_code_by_key"])[0]
    errored = "tests/test_example_pilot.py::test_imports_the_module_and_asserts_nothing"
    uncovered_sandbox.write_kill_matrix_row(real, failing=[errored], errored=[errored])

    document, _ = uncovered_sandbox.collect()
    row = document["kill_matrix"][real]

    assert row["failing"] == [errored], "the credit must reach the report"
    assert row.get("errored") == [errored], (
        "and so must the mark: a credited kill the report cannot tell from an assertion "
        f"is one a reviewer cannot discount. Row: {row}"
    )
