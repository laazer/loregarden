#!/usr/bin/env python3
"""Collect the three on-disk mutmut documents into the one the reducer reads.

No single mutmut file holds a run. This script gathers them, verbatim, each
under its own key:

    meta          `mutants/<path>.meta` per mutated source file, written by
                  SourceFileMutationData.save(). `exit_code_by_key` is mutmut's
                  whole per-mutant verdict. It is a MAPPING of source file to
                  block — reading only its first key drops a whole file's
                  mutants and reports a score higher than the run measured.
    mutant_index  file / line / operator per mutant. mutmut persists NEITHER
                  the operator nor the original-source line anywhere, so this
                  is recovered here from the mutated file the run WROTE (see
                  `_derive_index`), never from a second generation pass.
    kill_matrix   the AC-6 plugin's rows, one JSON file per mutant, merged.

This lives beside `kill_matrix.py` and not inside it on purpose: the reducer
is pure by contract — no subprocess, no filesystem beyond the report it is
handed — and a collector is all filesystem. Keeping them apart is what lets
the reducer's rules be tested in milliseconds against checked-in fixtures.

WHY THE INDEX IS MATCHED AGAINST THE RUN'S OWN OUTPUT, AND NOT RE-DERIVED.
An earlier version of this file re-ran `mutate_file_contents` / `create_mutations`
with no `covered_lines`, then paired the run's `__mutmut_N` with the N-th
mutation of that unfiltered pass. Two things make that wrong, and both are
quiet (AC-13):

  * The run generates under `mutate_only_covered_lines = true`, so
    `MutationVisitor._should_mutate_node` skips every node whose start line is
    uncovered. Mutants are numbered sequentially per function, so ONE dropped
    mutation renumbers every later mutant in that function and the whole tail
    of the column points one mutation on. Measured on the pilot module: 87 ->
    86 mutants, 6 of 86 printed with a confident wrong `file:line` and operator.
  * The operator was recorded by APPEND ORDER, but `_removes_ignored_code`
    (`file_mutation.py:238`) drops a mutation AFTER the operator has already
    yielded it, so every operator after the first `# pragma: no mutate` in a
    file shifted by one.

What is used instead: `mutants/<path>.py` and its `.spans` index are artifacts
of the real run, so the exact source of mutant `N` can be read back. Each one
is matched, text for text, against the candidates of an unfiltered pass whose
operators are tagged BY NODE IDENTITY (append order is the second defect
above). A mutant that matches exactly one candidate is indexed with that
candidate's line and operator; anything else — no match, or two candidates
disagreeing about line or operator — is left out and NAMED, because a review
list with a confident wrong `file:line  operator` is the one output this pilot
exists not to produce.

A gap is reported, never blanked: an unmatched mutant is simply absent from
`mutant_index`, printed by name on stdout, and listed by the reducer under
`unindexed_mutants` rather than printed with an empty operator cell, which a
reader takes as "no operator reached this".
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider
from mutmut.mutation import file_mutation
from mutmut.mutation.data import LineSpan, MutantLineSpans, read_line_spans
from mutmut.mutation.file_mutation import create_mutations, deep_replace
from mutmut.mutation.trampoline_templates import mangle_function_name
from mutmut.utils.format_utils import get_mutant_name, is_mutated_method_name


def _tagged_operators(operator_by_node_id: dict[int, str], keepalive: list):
    """mutmut's operator registry, wrapped so each mutated node knows its operator.

    Tagged by node IDENTITY rather than by append order: `MutationVisitor`
    drops a mutation in `_removes_ignored_code` after the operator has already
    yielded it, so an order-paired list shifts every later operator in the file
    by one. `keepalive` is load-bearing — a dropped node is otherwise
    unreferenced, and CPython reuses the id of a collected object, which would
    make the mapping lie.
    """

    def tagging(operator):
        def tagged(node):
            for mutated in operator(node):
                keepalive.append(mutated)
                operator_by_node_id[id(mutated)] = operator.__name__
                yield mutated

        tagged.__name__ = operator.__name__
        return tagged

    return [
        (node_type, tagging(operator)) for node_type, operator in file_mutation.mutation_operators
    ]


def _mangled_name_by_function(module: cst.Module) -> dict[int, str]:
    """`id(FunctionDef)` -> the name mutmut mangles its mutants under.

    Keyed by node identity and carrying the class, because `Budget.spend` and
    `Ledger.spend` are `xǁBudgetǁspend__mutmut` and `xǁLedgerǁspend__mutmut`:
    grouping by the bare `spend` merges two functions whose mutants differ only
    in the part a bare name throws away.
    """
    mangled: dict[int, str] = {}
    for statement in module.body:
        if type(statement) is cst.FunctionDef:
            mangled[id(statement)] = (
                mangle_function_name(name=statement.name.value, class_name=None) + "__mutmut"
            )
        elif type(statement) is cst.ClassDef and type(statement.body) is cst.IndentedBlock:
            for member in statement.body.body:
                if type(member) is cst.FunctionDef:
                    mangled[id(member)] = (
                        mangle_function_name(
                            name=member.name.value, class_name=statement.name.value
                        )
                        + "__mutmut"
                    )
    return mangled


def _candidates_by_mangled_name(source_path: Path, code: str) -> dict[str, list[dict]]:
    """Every mutation mutmut's operators can produce for one file, by function.

    Unfiltered on purpose: this is the pool the run's real mutants are matched
    AGAINST, not a stand-in for them. Nothing here is indexed until the mutated
    file the run wrote says it is that mutation.
    """
    operator_by_node_id: dict[int, str] = {}
    keepalive: list = []
    original = file_mutation.mutation_operators
    file_mutation.mutation_operators = _tagged_operators(operator_by_node_id, keepalive)
    try:
        module, mutations, _, _ = create_mutations(str(source_path), code)
    finally:
        file_mutation.mutation_operators = original

    positions = MetadataWrapper(module, unsafe_skip_copy=True).resolve(PositionProvider)
    mangled_by_function = _mangled_name_by_function(module)

    candidates: dict[str, list[dict]] = {}
    for mutation in mutations:
        function = mutation.contained_by_top_level_function
        if function is None:
            continue
        mangled = mangled_by_function.get(id(function))
        if mangled is None:
            continue
        position = positions.get(mutation.original_node)
        candidates.setdefault(mangled, []).append(
            {
                "function": function,
                "mutation": mutation,
                "line": position.start.line if position else None,
                "operator": operator_by_node_id[id(mutation.mutated_node)],
                "module": module,
            }
        )
    return candidates


def _is_mutant_function(name: str) -> bool:
    """Is this generated function a MUTANT, rather than mutmut's own scaffolding?

    `is_mutated_method_name` accepts anything with `__mutmut` in it, which
    includes the `__mutmut_orig` copy of the unmutated function that mutmut
    writes beside every mutant. Those are not mutants, have no `.meta` verdict,
    and would otherwise be reported once per function as "exists in mutants/
    with no verdict" — four lines of noise on the pilot module, which is how a
    real divergence between the two documents stops being noticed.
    """
    if not is_mutated_method_name(name):
        return False
    return name.rpartition("__mutmut_")[2].isdigit()


def _normalise(source: str) -> str:
    """Compare a generated function by its code, not by its placement.

    A method's source arrives indented by its class and padded by the blank
    lines between definitions; neither says anything about which mutation it is.
    """
    return textwrap.dedent(source).strip()


def _run_function_sources(mutants_root: Path, relative: str) -> dict[str, str]:
    """The source of every mutant function in `mutants/<relative>.py`, by name.

    `.spans` is the index mutmut writes for exactly this, and reading it costs
    one seek per mutant instead of parsing a file many times the size of its
    source. Where a run left no `.spans` (an older mutmut), the mutated file is
    parsed instead — the same fact, read the slow way, rather than an empty
    index nobody is told about.
    """
    mutated_path = mutants_root / relative
    if not mutated_path.exists():
        return {}

    spans_path = mutants_root / (relative + ".spans")
    if spans_path.exists():
        raw = json.loads(spans_path.read_text(encoding="utf-8"))
        if raw.get("version") == MutantLineSpans.format_version:
            names = [name for name in raw["spans"] if _is_mutant_function(name)]
            spans = [LineSpan(*raw["spans"][name]) for name in names]
            return dict(zip(names, read_line_spans(mutated_path, spans), strict=True))

    mutated_module = cst.parse_module(mutated_path.read_text(encoding="utf-8"))
    sources: dict[str, str] = {}

    def collect(body):
        for statement in body:
            if type(statement) is cst.FunctionDef and _is_mutant_function(statement.name.value):
                sources[statement.name.value] = mutated_module.code_for_node(statement)
            elif type(statement) is cst.ClassDef and type(statement.body) is cst.IndentedBlock:
                collect(statement.body.body)

    collect(mutated_module.body)
    return sources


def _derive_index(
    source_path: Path, mutants_root: Path, repo_relative: str, meta_keys: set[str]
) -> tuple[dict[str, dict], list[str], list[str]]:
    """Recover `file / line / operator` for the mutants THIS RUN generated.

    Returns `(index, unverified, derived_without_meta)`:

      index                 one entry per mutant whose source in `mutants/` is
                            matched by exactly one candidate mutation.
      unverified            `.meta` keys that could not be matched — no mutant
                            function of that name in the run's output, or two
                            candidates disagreeing about line or operator.
      derived_without_meta  mutant functions the run WROTE that `.meta` carries
                            no verdict for. The two documents disagree about
                            what was tested, and that is evidence, not noise.
    """
    code = source_path.read_text(encoding="utf-8")
    run_sources = _run_function_sources(mutants_root, repo_relative)
    candidates = _candidates_by_mangled_name(source_path, code)

    index: dict[str, dict] = {}
    unverified: list[str] = []
    derived_without_meta: list[str] = []

    # Rendered ONCE per candidate, not once per (mutant, candidate) pair: a
    # function with N mutants has N candidates, and rendering inside both loops
    # is N^2 CST renders of a whole function. The pilot module would survive
    # that; a 500-mutant module in one function would not.
    rendered_by_mangled_function: dict[str, list[tuple[str, int | None, str]]] = {}
    for mangled_function, entries in candidates.items():
        rendered_by_mangled_function[mangled_function] = [
            (
                _normalise(
                    candidate["module"].code_for_node(
                        deep_replace(
                            candidate["function"],
                            candidate["mutation"].original_node,
                            candidate["mutation"].mutated_node,
                        )
                    )
                ),
                candidate["line"],
                candidate["operator"],
            )
            for candidate in entries
        ]

    for mangled_name, source in run_sources.items():
        qualified = get_mutant_name(Path(repo_relative), mangled_name)
        if qualified not in meta_keys:
            derived_without_meta.append(qualified)
            continue
        mangled_function = mangled_name.rpartition("__mutmut_")[0] + "__mutmut"
        entries = candidates.get(mangled_function)
        if not entries:
            # The run wrote a mutant for a function no current generation
            # produces — the source moved under the run's artifacts. Nothing
            # here can be verified, and a guess is the thing AC-13 forbids.
            unverified.append(qualified)
            continue
        # The run renamed the function to the mutant's name; the candidates
        # carry the original one. Rename the run's copy back, anchored on
        # `def <name>(` — a bare substring replace would also hit a recursive
        # call in the body, and a `$`-anchored one matches nothing at all,
        # because the suffix sits mid-line on the `def`.
        original_name = entries[0]["function"].name.value
        wanted = _normalise(source.replace(f"def {mangled_name}(", f"def {original_name}(", 1))
        matches = {
            (line, operator)
            for rendered, line, operator in rendered_by_mangled_function.get(mangled_function, [])
            if rendered == wanted
        }
        if len(matches) != 1:
            # Zero: no operator in play produces the code the run actually ran.
            # More than one: they disagree about the line or the operator, and
            # picking either would print a column nothing verified.
            unverified.append(qualified)
            continue
        line, operator = matches.pop()
        index[qualified] = {"file": repo_relative, "line": line, "operator": operator}

    for qualified in meta_keys:
        if qualified not in index and qualified not in unverified:
            unverified.append(qualified)

    return index, sorted(unverified), sorted(derived_without_meta)


def _operators_in_play() -> list[str]:
    """The operators that were actually registered for this run (AC-9).

    Required, not optional: without it a zero-kill verdict is unreadable,
    because some tests here guard concurrency, migration order and prompt
    shape that no operator can reach.
    """
    return sorted({operator.__name__ for _, operator in file_mutation.mutation_operators})


def _merge_kill_matrix(kill_matrix_dir: Path, mutant_names: set[str]) -> dict[str, dict]:
    """Merge the plugin's per-mutant rows, keeping only rows that ARE mutants.

    mutmut drives its own phases through MUTANT_UNDER_TEST too — "fail" for
    the baseline check and "stats" while collecting coverage, among others
    (`mutmut/__main__.py`). The plugin already declines to write those, but
    this is the half that cannot be fooled by a stale row left in the output
    directory by an older build: `mutant_names` comes from mutmut's own .meta,
    so anything outside it is not a mutant of this run.

    Dropped rows are returned, not swallowed: a row this function discards in
    silence is a test result that vanished between the run and the report.
    """
    rows = {}
    dropped = []
    for path in sorted(kill_matrix_dir.glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        name = row["mutant"]
        if name not in mutant_names:
            dropped.append(name)
            continue
        rows[name] = {
            bucket: row.get(bucket, [])
            for bucket in ("failing", "passed", "skipped", "timed_out", "errored")
        }
    return rows, dropped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--server-root", required=True, help="the `server/` directory mutmut ran in"
    )
    parser.add_argument(
        "--source", required=True, action="append", help="pilot source, relative to --server-root"
    )
    parser.add_argument("--kill-matrix-dir", required=True)
    parser.add_argument("--wall-clock-seconds", type=float, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    server_root = Path(args.server_root).resolve()
    mutants_root = server_root / "mutants"
    meta = {}
    mutant_index = {}
    unverified: list[str] = []
    derived_without_meta: list[str] = []
    for relative in args.source:
        meta_path = mutants_root / (relative + ".meta")
        if not meta_path.exists():
            # Loudly, not as an empty block: a missing .meta means mutmut never
            # recorded that file, and a run document quietly short of one file
            # is a score reported over the remainder.
            raise SystemExit(f"no mutmut .meta for {relative} at {meta_path} — did the run finish?")
        meta[relative] = json.loads(meta_path.read_text(encoding="utf-8"))
        index, unmatched, orphaned = _derive_index(
            server_root / relative,
            mutants_root,
            relative,
            set(meta[relative].get("exit_code_by_key", {})),
        )
        mutant_index.update(index)
        unverified.extend(unmatched)
        derived_without_meta.extend(orphaned)

    if unverified:
        # BY NAME, never as a count. "6 mutants unindexed" cannot be acted on;
        # the names can be read back out of `mutants/` by hand, and they are
        # the same names the reducer lists under `unindexed_mutants`.
        print(f"could not verify {len(unverified)} mutant(s) against the run's own output;")
        print("  these carry no line and no operator rather than a guessed one:")
        for name in sorted(unverified):
            print(f"  {name}")
    if derived_without_meta:
        # The other direction: the run WROTE this mutant and recorded no
        # verdict for it. The two documents disagree about what was tested,
        # which is evidence about the run, not a rounding error in the report.
        print(f"{len(derived_without_meta)} mutant(s) exist in mutants/ with no .meta verdict:")
        for name in sorted(derived_without_meta):
            print(f"  {name}")

    mutant_names = {key for block in meta.values() for key in block.get("exit_code_by_key", {})}
    kill_matrix, dropped_rows = _merge_kill_matrix(Path(args.kill_matrix_dir), mutant_names)
    if dropped_rows:
        # Named, not counted: "3 rows dropped" tells a reader nothing about
        # whether they were mutmut's own phases or real mutants the .meta is
        # missing, and those two want opposite responses.
        print(f"ignored {len(dropped_rows)} kill-matrix row(s) that are not mutants of this run:")
        for name in sorted(dropped_rows):
            print(f"  {name}")
    if not kill_matrix:
        raise SystemExit(
            "the kill matrix is empty: `-p mutmut_killers` wrote no rows. mutmut still "
            "scores every mutant from its exit code, so this fails silently — the only "
            "symptom is a review list with nothing in it. Check also_copy and "
            "MUTMUT_KILL_MATRIX_DIR before trusting any number from this run."
        )

    document = {
        "side": "server",
        "tool": "mutmut",
        "tool_version": _mutmut_version(),
        "wall_clock_seconds": args.wall_clock_seconds,
        "pilot_module": args.source[0],
        "operators_in_play": _operators_in_play(),
        "meta": meta,
        "mutant_index": mutant_index,
        "kill_matrix": kill_matrix,
    }
    Path(args.out).write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(
        f"wrote {args.out}: {sum(len(b['exit_code_by_key']) for b in meta.values())} mutants, "
        f"{len(mutant_index)} indexed, {len(kill_matrix)} kill-matrix rows"
    )
    return 0


def _mutmut_version():
    from importlib.metadata import version

    return version("mutmut")


if __name__ == "__main__":
    sys.exit(main())
