#!/usr/bin/env python3
"""Mutation-test only the server functions a change touched.

    mutation_changed_lines.py [--base REF] [--repo PATH] [--dry-run]

Whole-repo mutation testing is not affordable here (one 644-line module yields
~920 mutants; the package is ~216k lines). mutmut cannot mutate only some
lines, but it names every mutant after the function it lives in
(`loregarden.services.x.x_func__mutmut_3`, methods `…xǁClassǁmethod__mutmut_3`)
and `mutmut run` takes those names as globs. So this:

1. diffs the merge-base of REF and HEAD against the working tree (uncommitted
   edits count: a stage gate fires before anything is committed) and keeps
   the changed lines in ``server/loregarden/**/*.py``;
2. maps each line to the top-level function or method enclosing it, the same
   units mutmut puts trampolines on (`ast`, below);
3. picks pass 1's tests with the pre-push selector (``select_pytest_targets``,
   imported rather than re-implemented): the files importing a changed module
   themselves (``select_direct``);
4. runs ``mutmut run`` on just those functions' mutants against them, under
   the capacity ledger (``mutmut_driver.py``), then re-runs only the survivors
   against the tests that *call* a surviving function, a few calls out
   (``mutation_callers``). Not the import graph's transitive reach: mutmut's
   stats pass runs every selected file serially, and for a widely imported
   module that reach is most of the suite (566 of 808 files, 2h36m, no kills on
   #939). Over ``--pass2-cap`` files, pass 2 is skipped and says so
   (``--reach direct`` stops after pass 1; ``--reach transitive`` runs one pass
   over the whole import reach);
5. reports survivors grouped by function, with ``mutmut show``'s diff.

Exit codes — "it failed" and "there was nothing to test" never share one:

  0  every mutant was killed, or the diff changes no mutable function (said so)
  1  at least one mutant survived (or its function is run by no selected test)
  2  could not run: bad ref, git failed, mutmut crashed, no result written
  3  the clean baseline failed: the selected tests are red on the unmutated copy
  4  functions changed but mutmut produced zero mutants for them
  5  functions changed but no test reaches them (selector or mutmut coverage)
  75 capacity-run.sh gave up waiting (passed through; see that script)
"""

from __future__ import annotations

import argparse
import ast
import bisect
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mutation_callers  # noqa: E402
import select_pytest_targets  # noqa: E402
from mutmut_driver import Phase  # noqa: E402

EXIT_OK = 0
EXIT_SURVIVORS = 1
EXIT_ERROR = 2
EXIT_BASELINE_FAILED = 3
EXIT_NO_MUTANTS = 4
EXIT_NO_TESTS = 5
EXIT_CAPACITY_STALLED = 75

#: Most test files pass 2 runs. mutmut's stats pass took them serially at about
#: 17s a file on #939 (566 files, 2h36m), so 100 keeps pass 2 near half an hour.
DEFAULT_PASS2_CAP = 100

PACKAGE_PREFIX = "server/loregarden/"
SERVER_DIR = "server"
#: mutmut's separator between class and method in a mutant name
#: (mutmut.mutation.trampoline_templates.CLASS_NAME_SEPARATOR).
CLASS_SEPARATOR = "ǁ"
#: The only decorators mutmut still mutates through
#: (mutmut.mutation.file_mutation, `_skip_node_and_children`).
MUTABLE_DECORATORS = frozenset({"staticmethod", "classmethod"})

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# --------------------------------------------------------------------------- #
# diff → changed lines
# --------------------------------------------------------------------------- #


@dataclass
class FileChange:
    """One file's changed lines: added/modified on the new side, removed on the old."""

    old_path: str | None  # None when the file is new
    new_path: str | None  # None when the file was deleted
    added: set[int] = field(default_factory=set)
    removed: set[int] = field(default_factory=set)


def parse_diff(text: str) -> list[FileChange]:
    """Parse `git diff -U0 --find-renames` output into per-file line sets."""
    changes: list[FileChange] = []
    current: FileChange | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = FileChange(old_path=None, new_path=None)
            changes.append(current)
        elif current is None:
            continue
        elif line.startswith("--- "):
            current.old_path = _strip_side(line[4:], "a/")
        elif line.startswith("+++ "):
            current.new_path = _strip_side(line[4:], "b/")
        elif line.startswith("rename from "):
            current.old_path = line[len("rename from ") :]
        elif line.startswith("rename to "):
            current.new_path = line[len("rename to ") :]
        else:
            match = _HUNK.match(line)
            if match:
                old_start, old_count, new_start, new_count = match.groups()
                current.removed.update(_span(int(old_start), old_count))
                current.added.update(_span(int(new_start), new_count))
    return changes


def _strip_side(path: str, prefix: str) -> str | None:
    path = path.split("\t", 1)[0]
    if path == "/dev/null":
        return None
    return path[len(prefix) :] if path.startswith(prefix) else path


def _span(start: int, count: str | None) -> range:
    """Lines a hunk side covers. `-U0` writes `,0` for a pure insert/delete,
    which covers no line on that side."""
    n = 1 if count is None else int(count)
    return range(start, start + n)


# --------------------------------------------------------------------------- #
# lines → functions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Unit:
    """A function mutmut can put a trampoline on: a module-level def, or a
    method directly in a module-level class. Nested defs belong to theirs."""

    qualname: str  # `func` or `Class.method`
    mutmut_name: str  # `x_func` or `xǁClassǁmethod`
    start: int  # first decorator line, else the `def` line
    def_line: int
    end: int
    skip_reason: str | None  # why mutmut generates no mutants for it, if it does not


class _UnitCollector(ast.NodeVisitor):
    """Visits a module's top level, and one level into its top-level classes —
    the only places mutmut puts trampolines. Nothing below is a unit of its own:
    a nested def is part of its function, a nested class's methods are left alone."""

    def __init__(self) -> None:
        self.units: list[Unit] = []
        self._class: str | None = None

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.units.append(_unit(node, self._class))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._class is not None:
            return  # a class inside a class: mutmut mutates none of its methods
        self._class = node.name
        for statement in node.body:
            self.visit(statement)
        self._class = None

    def generic_visit(self, node: ast.AST) -> None:
        """Any other statement holds no unit (mutmut skips defs under `if`, too)."""


def units_of(source: str) -> list[Unit]:
    """Every trampoline-able unit in ``source``, in line order."""
    collector = _UnitCollector()
    collector.visit(ast.parse(source))
    return sorted(collector.units, key=lambda u: u.start)


def _unit(node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str | None) -> Unit:
    if class_name is None:
        qualname, mutmut_name = node.name, f"x_{node.name}"
    else:
        qualname = f"{class_name}.{node.name}"
        mutmut_name = f"x{CLASS_SEPARATOR}{class_name}{CLASS_SEPARATOR}{node.name}"
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    return Unit(
        qualname=qualname,
        mutmut_name=mutmut_name,
        start=start,
        def_line=node.lineno,
        end=node.end_lineno or node.lineno,
        skip_reason=_skip_reason(node),
    )


def _skip_reason(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    decorators = node.decorator_list
    if not decorators:
        return None
    if len(decorators) == 1 and ast.unparse(decorators[0]) in MUTABLE_DECORATORS:
        return None
    return "decorated (mutmut mutates no decorated function but @staticmethod/@classmethod)"


class UnitIndex:
    """Line → enclosing unit, by bisection over the (non-overlapping) units."""

    def __init__(self, units: list[Unit]) -> None:
        self.units = units
        self._starts = [u.start for u in units]

    def at(self, line: int) -> Unit | None:
        i = bisect.bisect_right(self._starts, line) - 1
        if i >= 0 and self.units[i].start <= line <= self.units[i].end:
            return self.units[i]
        return None

    def names(self) -> set[str]:
        return {u.qualname for u in self.units}


def module_of(path: str) -> str:
    """Dotted module for a repo-relative ``server/…`` path, as mutmut names it."""
    rel = Path(path).relative_to(SERVER_DIR).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass(frozen=True)
class Target:
    path: str
    module: str
    unit: Unit

    @property
    def glob(self) -> str:
        return f"{self.module}.{self.unit.mutmut_name}__mutmut_*"


@dataclass
class Plan:
    targets: list[Target] = field(default_factory=list)
    #: (path, unit) changed, but mutmut will not mutate it.
    unmutable: list[tuple[str, Unit]] = field(default_factory=list)
    #: (path, first, last) changed lines outside any unit.
    module_level: list[tuple[str, int, int]] = field(default_factory=list)
    #: (old path, qualname) of functions the change removed.
    deleted: list[tuple[str, str]] = field(default_factory=list)


def plan_file(change: FileChange, new_source: str | None, old_source: str | None) -> Plan:
    """Which units of one file the change touched.

    New-side lines map to the new file's units. Old-side (removed) lines map to
    the old file's units: a removal inside a function that still exists changes
    that function, and one whose function is gone records it as deleted."""
    plan = Plan()
    if change.new_path is None or new_source is None:
        if old_source is not None and change.old_path is not None:
            plan.deleted.extend((change.old_path, u.qualname) for u in units_of(old_source))
        return plan

    new_index = UnitIndex(units_of(new_source))
    touched, stray = _touched_by_added(new_index, change.added)
    if old_source is not None and change.old_path is not None and change.removed:
        _apply_removed(plan, change.old_path, change.removed, old_source, new_index, touched)
    _record_units(plan, change.new_path, touched, stray, new_source)
    return plan


def _touched_by_added(index: UnitIndex, added: set[int]) -> tuple[dict[str, Unit], list[int]]:
    """The units holding added lines, and the added lines outside every unit."""
    touched: dict[str, Unit] = {}
    stray: list[int] = []
    for line in sorted(added):
        unit = index.at(line)
        if unit is None:
            stray.append(line)
        else:
            touched.setdefault(unit.qualname, unit)
    return touched, stray


def _apply_removed(
    plan: Plan,
    old_path: str,
    removed: set[int],
    old_source: str,
    new_index: UnitIndex,
    touched: dict[str, Unit],
) -> None:
    by_name = {u.qualname: u for u in new_index.units}
    old_index = UnitIndex(units_of(old_source))
    for line in sorted(removed):
        old_unit = old_index.at(line)
        if old_unit is None:
            continue  # a removed module-level line; nothing left to test
        if old_unit.qualname in by_name:
            touched.setdefault(old_unit.qualname, by_name[old_unit.qualname])
        elif (old_path, old_unit.qualname) not in plan.deleted:
            plan.deleted.append((old_path, old_unit.qualname))


def _record_units(
    plan: Plan, path: str, touched: dict[str, Unit], stray: list[int], source: str
) -> None:
    module = module_of(path)
    for unit in sorted(touched.values(), key=lambda u: u.start):
        if unit.skip_reason:
            plan.unmutable.append((path, unit))
        else:
            plan.targets.append(Target(path, module, unit))
    source_lines = source.splitlines()
    code_lines = [n for n in stray if n <= len(source_lines) and source_lines[n - 1].strip()]
    plan.module_level.extend((path, a, b) for a, b in _runs(code_lines))


def _runs(lines: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for line in lines:
        if runs and line == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], line)
        else:
            runs.append((line, line))
    return runs


def merge_plans(plans: list[Plan]) -> Plan:
    merged = Plan()
    for p in plans:
        merged.targets += p.targets
        merged.unmutable += p.unmutable
        merged.module_level += p.module_level
        merged.deleted += p.deleted
    return merged


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #


class GitError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=env
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def collect_changes(repo: Path, base: str) -> tuple[str, list[FileChange]]:
    """The merge-base, and every changed package file against the working tree."""
    merge_base = _git(repo, "merge-base", base, "HEAD").strip()
    diff = _git(
        repo,
        "diff",
        "-U0",
        "--find-renames",
        "--no-color",
        "--no-ext-diff",
        merge_base,
        "--",
        PACKAGE_PREFIX,
    )
    changes = [c for c in parse_diff(diff) if _in_package(c)]
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", PACKAGE_PREFIX)
    for path in untracked.splitlines():
        if path.endswith(".py"):
            text = (repo / path).read_text(encoding="utf-8")
            changes.append(FileChange(None, path, added=set(range(1, len(text.splitlines()) + 1))))
    return merge_base, changes


def _in_package(change: FileChange) -> bool:
    path = change.new_path or change.old_path or ""
    return path.startswith(PACKAGE_PREFIX) and path.endswith(".py")


def plan_whole_files(repo: Path, paths: list[str]) -> Plan:
    """Every function of each file, as if the change had rewritten it."""
    plans = []
    for path in paths:
        if not (path.startswith(PACKAGE_PREFIX) and path.endswith(".py")):
            raise ValueError(f"{path} is not a {PACKAGE_PREFIX}**/*.py file")
        source = (repo / path).read_text(encoding="utf-8")
        every_line = set(range(1, len(source.splitlines()) + 1))
        plans.append(plan_file(FileChange(path, path, added=every_line), source, None))
    return merge_plans(plans)


def build_plan(repo: Path, merge_base: str, changes: list[FileChange]) -> Plan:
    plans = []
    for change in changes:
        new_source = (
            (repo / change.new_path).read_text(encoding="utf-8") if change.new_path else None
        )
        old_source = (
            _git(repo, "show", f"{merge_base}:{change.old_path}") if change.old_path else None
        )
        plans.append(plan_file(change, new_source, old_source))
    return merge_plans(plans)


# --------------------------------------------------------------------------- #
# run + report
# --------------------------------------------------------------------------- #


def describe_plan(plan: Plan, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    files = sorted({t.path for t in plan.targets})
    print(f"mutation: {len(plan.targets)} changed function(s) in {len(files)} file(s)", file=out)
    for t in plan.targets:
        print(f"  mutate  {t.path}:{t.unit.def_line} {t.unit.qualname}", file=out)
    for path, unit in plan.unmutable:
        print(f"  skip    {path}:{unit.def_line} {unit.qualname}: {unit.skip_reason}", file=out)
    for path, a, b in plan.module_level:
        where = f"{a}" if a == b else f"{a}-{b}"
        print(f"  skip    {path}:{where}: module- or class-level, no enclosing function", file=out)
    for path, name in plan.deleted:
        print(f"  deleted {path} {name}: removed by the change, nothing to mutate", file=out)


@dataclass(frozen=True)
class TestRings:
    """The changed modules' tests, relative to server/, nearest first."""

    #: files importing a changed module themselves (`select_direct`)
    direct: list[str]
    #: the rest of what the pre-push selector reaches through the graph
    #: (`--reach transitive` only)
    wider: list[str] = field(default_factory=list)
    #: target glob -> tests outside `direct` that call that function, a few
    #: calls out (`mutation_callers`; `--reach escalate` only)
    callers: dict[str, list[str]] = field(default_factory=dict)


def select_tests(repo: Path, plan: Plan, reach: str = "escalate") -> TestRings:
    """The rings ``reach`` needs. mutmut runs its stats pass over every selected
    file serially, so each ring is built only when a pass will run it."""
    paths = sorted({t.path for t in plan.targets})
    server = repo / SERVER_DIR

    def rel(tests: Iterable[Path]) -> list[str]:
        return sorted(p.relative_to(server).as_posix() for p in tests)

    direct = rel(select_pytest_targets.select_direct(repo, paths))
    near = set(direct)
    if reach == "transitive":
        wider = rel(select_pytest_targets.select(repo, paths))
        return TestRings(direct=direct, wider=[test for test in wider if test not in near])
    if reach == "direct":
        return TestRings(direct=direct)
    index = mutation_callers.build_index(server / "loregarden", server / "tests")
    callers = {
        t.glob: [
            test
            for test in rel(mutation_callers.caller_tests(index, _symbol(t)))
            if test not in near
        ]
        for t in plan.targets
    }
    return TestRings(direct=direct, callers=callers)


def _symbol(target: Target) -> mutation_callers.Symbol:
    """What a caller writes to reach the target: its name, or a method's name."""
    return mutation_callers.Symbol(target.module, target.unit.qualname.rpartition(".")[2])


def _union(lists: Iterable[list[str]]) -> list[str]:
    return sorted({test for tests in lists for test in tests})


@dataclass
class FunctionResult:
    target: Target
    mutants: list[dict] = field(default_factory=list)

    @property
    def failing(self) -> list[dict]:
        return [m for m in self.mutants if m["status"] in FAILING_STATUSES]


#: A survivor, or a mutant in a function no selected test runs: either way the
#: tests would not notice this function being wrong.
FAILING_STATUSES = frozenset({"survived", "no tests"})


def group_results(plan: Plan, mutants: list[dict]) -> list[FunctionResult]:
    by_glob = {t.glob[: -len("*")]: FunctionResult(t) for t in plan.targets}
    for mutant in mutants:
        prefix = mutant["name"].rpartition("__mutmut_")[0] + "__mutmut_"
        result = by_glob.get(prefix)
        if result is not None:
            result.mutants.append(mutant)
    return list(by_glob.values())


def report(results: list[FunctionResult], out: TextIO | None = None) -> int:
    out = out or sys.stdout
    total = sum(len(r.mutants) for r in results)
    counts: dict[str, int] = {}
    for r in results:
        for m in r.mutants:
            counts[m["status"]] = counts.get(m["status"], 0) + 1
    summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    print(f"\nmutation: {total} mutant(s): {summary or 'none'}", file=out)
    widened = sum(1 for r in results for m in r.mutants if m.get("killed_by") == "wider")
    if widened:
        print(
            f"  ({widened} killed only in pass 2, by tests that do not import the module)", file=out
        )
    for r in results:
        if not r.mutants:
            print(
                f"  {r.target.path}:{r.target.unit.def_line} {r.target.unit.qualname}: "
                "0 mutants (nothing mutmut can change)",
                file=out,
            )
    failing = [r for r in results if r.failing]
    if not failing:
        return EXIT_OK
    n = sum(len(r.failing) for r in failing)
    print(f"\nSURVIVORS: {n} mutant(s) the selected tests did not notice, by function:", file=out)
    for r in failing:
        print(
            f"\n{r.target.path}:{r.target.unit.def_line} {r.target.unit.qualname} "
            f"({len(r.failing)} of {len(r.mutants)} not killed)",
            file=out,
        )
        for m in r.failing:
            print(f"  {m['name']}  [{m['status']}]", file=out)
            for diff_line in (m.get("diff") or "").splitlines():
                print(f"    {diff_line}", file=out)
    return EXIT_SURVIVORS


def driver_env(environ: Mapping[str, str], repo: Path) -> dict[str, str]:
    """The driver's environment.

    ``no_proxy``: mutmut runs every pytest session in a forked child, and on
    macOS a forked child that asks the system for its proxies (``urllib``'s
    ``getproxies`` → ``_scproxy``, which any httpx client does) aborts the whole
    process (CPython bpo-33725). A stats run over the wider ring died that way
    in test_cli_local_main. With any proxy variable set, ``getproxies`` reads
    the environment and never asks the system; tests reach no real proxy.
    A proxy setting the caller already has is kept.
    """
    env = {k: v for k, v in environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env["LOREGARDEN_REPO_ROOT"] = str(repo)
    if not any(k.lower().endswith("_proxy") for k in env):
        env["no_proxy"] = "*"
    return env


def run_driver(
    repo: Path,
    targets: list[Target],
    globs: list[str],
    tests: list[str],
    workdir: Path,
    *,
    baseline_only: bool = False,
) -> tuple[int, dict | None]:
    workdir.mkdir(parents=True, exist_ok=True)
    plan_file_path = workdir / "plan.json"
    result_path = workdir / "result.json"
    plan_file_path.write_text(
        json.dumps(
            {
                "only_mutate": sorted({t.path[len(SERVER_DIR) + 1 :] for t in targets}),
                "globs": globs,
                "tests": tests,
            }
        ),
        encoding="utf-8",
    )
    cmd = [
        str(_SCRIPTS / "capacity-run.sh"),
        "mutation",
        "--footprint",
        "heavy",
        "--",
        sys.executable,
        str(_SCRIPTS / "mutmut_driver.py"),
        "--plan",
        str(plan_file_path),
        "--result",
        str(result_path),
        *(["--baseline-only"] if baseline_only else []),
    ]
    env = driver_env(os.environ, repo)
    sys.stdout.flush()  # our plan prints before mutmut's own output, not after it
    proc = subprocess.run(cmd, cwd=repo / SERVER_DIR, env=env, check=False)
    if not result_path.exists():
        return proc.returncode, None
    return proc.returncode, json.loads(result_path.read_text(encoding="utf-8"))


def _count(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, not {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base", default="origin/main", help="diff against this ref (default origin/main)"
    )
    parser.add_argument("--repo", type=Path, default=_SCRIPTS.parents[1])
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan and tests; run nothing"
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="mutate every function of this server/loregarden file instead of diffing (repeatable)",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="stop after the clean (unmutated) baseline run on mutmut's copy",
    )
    parser.add_argument(
        "--reach",
        choices=("escalate", "direct", "transitive"),
        default="escalate",
        help=(
            "escalate (default): direct importers first, then only the survivors against "
            "the tests that call their functions; direct: stop after the first pass; "
            "transitive: one pass over everything the import graph reaches"
        ),
    )
    parser.add_argument(
        "--pass2-cap",
        type=_count,
        default=DEFAULT_PASS2_CAP,
        metavar="N",
        help=f"skip pass 2 when more than N test files call the survivors (default {DEFAULT_PASS2_CAP})",
    )
    return parser


def _read_plan(args: argparse.Namespace, repo: Path) -> Plan | None:
    """The plan, or None (reported) when the change cannot be read."""
    try:
        if args.file:
            plan = plan_whole_files(repo, args.file)
            print(f"mutation: whole file(s) {', '.join(args.file)}")
            return plan
        merge_base, changes = collect_changes(repo, args.base)
        plan = build_plan(repo, merge_base, changes)
        print(f"mutation: base {args.base} (merge-base {merge_base[:10]}), working tree included")
        return plan
    except (GitError, SyntaxError, OSError, ValueError) as exc:
        print(f"mutation: cannot read the change against {args.base}: {exc}", file=sys.stderr)
        return None


def _announce_passes(first: list[str], second: list[str], reach: str) -> None:
    print(f"mutation: pass 1 runs {len(first)} test file(s) ({reach}):")
    for test in first:
        print(f"  {test}")
    if second:
        print(
            f"mutation: pass 2, for survivors only, draws on up to {len(second)} more test "
            "file(s) that call a changed function"
        )


def _run_passes(
    repo: Path,
    plan: Plan,
    first: list[str],
    second: PassTwo | None,
    baseline_only: bool,
    started: float,
) -> int:
    globs = [t.glob for t in plan.targets]
    with tempfile.TemporaryDirectory(prefix="loregarden-mutation-") as tmp:
        work = Path(tmp)
        code, result = run_driver(
            repo, plan.targets, globs, first, work / "pass1", baseline_only=baseline_only
        )
        if result is not None and result.get("baseline_only"):
            elapsed = time.monotonic() - started
            print(
                f"mutation: clean baseline green on mutmut's copy ({len(first)} test file(s), {elapsed:.0f}s)"
            )
            return EXIT_OK
        if second is not None:
            code, result = escalate(repo, plan, code, result, second, work / "pass2")
    return finish(plan, code, result, time.monotonic() - started)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = args.repo.resolve()
    started = time.monotonic()

    plan = _read_plan(args, repo)
    if plan is None:
        return EXIT_ERROR
    describe_plan(plan)
    if not plan.targets:
        print("mutation: no mutable function changed — nothing to mutate")
        return EXIT_OK

    try:
        rings = select_tests(repo, plan, args.reach)
    except select_pytest_targets.UnexaminableError as exc:
        print(f"mutation: cannot map the import graph: {exc}", file=sys.stderr)
        return EXIT_ERROR
    first, second = first_and_second_pass(rings, args.reach)
    if not first:
        print(
            "mutation: FAIL — no test file imports a changed module or calls a changed "
            "function (--reach transitive follows the whole import graph)",
            file=sys.stderr,
        )
        return EXIT_NO_TESTS
    pass_two = PassTwo(rings.callers, args.pass2_cap) if second else None
    _announce_passes(first, second, args.reach)
    if args.dry_run:
        for t in plan.targets:
            print(f"  glob {t.glob}")
        return EXIT_OK
    return _run_passes(repo, plan, first, pass_two, args.baseline_only, started)


def first_and_second_pass(rings: TestRings, reach: str) -> tuple[list[str], list[str]]:
    """The tests for each pass; pass 2's are the most it could draw on. With no
    direct importer, escalation starts on the callers."""
    if reach == "transitive":
        return rings.direct + rings.wider, []
    if reach == "direct":
        return rings.direct, []
    callers = _union(rings.callers.values())
    if not rings.direct:
        return callers, []
    return rings.direct, callers


@dataclass(frozen=True)
class PassTwo:
    #: target glob -> tests calling that function (`TestRings.callers`)
    callers: Mapping[str, list[str]]
    cap: int = DEFAULT_PASS2_CAP

    def tests_for(self, names: list[str]) -> list[str]:
        """The callers of every function a surviving mutant (or glob) lives in."""
        prefixes = {name.rpartition("__mutmut_")[0] for name in names}
        return _union(
            tests
            for glob, tests in self.callers.items()
            if glob.rpartition("__mutmut_")[0] in prefixes
        )


def escalate(
    repo: Path, plan: Plan, code: int, result: dict | None, pass_two: PassTwo, workdir: Path
) -> tuple[int, dict | None]:
    """Re-run pass 1's survivors — only those mutants — against the tests that
    call the functions they live in. Over ``pass_two.cap`` such files, or with
    none, pass 2 is skipped, says why, and pass 1's verdict stands.

    A mutant pass 2 kills is killed (marked ``killed_by: wider``). One pass 2
    leaves standing, or cannot reach (its tests run none of that code), stays a
    survivor. Pass 2 failing its own baseline fails the run: its tests are
    red on the unmutated copy, so no verdict from them can be trusted."""
    if result is None:
        return code, result
    phase = result["phase"]
    if phase == Phase.NO_COVERAGE:
        # The direct ring runs none of the changed code; every target goes wide.
        names = [t.glob for t in plan.targets]
        carried: list[dict] = []
    elif phase == Phase.DONE:
        carried = result["mutants"]
        names = [m["name"] for m in carried if m["status"] in FAILING_STATUSES]
        if not names:
            return code, result
    else:
        return code, result

    wider = pass_two.tests_for(names)
    if not wider:
        print(
            f"\nmutation: pass 2 skipped — no test outside pass 1 calls the surviving "
            f"function(s) within {mutation_callers.DEFAULT_HOPS} calls; pass 1's verdict stands"
        )
        return code, result
    if len(wider) > pass_two.cap:
        print(
            f"\nmutation: pass 2 skipped — {len(wider)} test file(s) call the surviving "
            f"function(s), over --pass2-cap {pass_two.cap}; pass 1's verdict stands"
        )
        return code, result
    print(
        f"\nmutation: pass 2 — {len(names)} survivor pattern(s) against {len(wider)} test file(s) "
        "that call them"
    )
    code2, result2 = run_driver(repo, plan.targets, names, wider, workdir)
    if result2 is None or result2["phase"] in (Phase.BASELINE_FAILED, Phase.ERROR):
        return code2, result2
    if result2["phase"] == Phase.NO_COVERAGE:
        print("mutation: pass 2 — the calling tests run none of the surviving code either")
        if phase == Phase.DONE:
            return code, result  # pass 1's survivors stand
        return code2, result2
    return 0, merge_passes(carried, result2["mutants"])


def merge_passes(first: list[dict], second: list[dict]) -> dict:
    """Pass 1's verdicts, with every mutant pass 2 killed marked killed."""
    by_name = {m["name"]: dict(m) for m in first}
    for mutant in second:
        if mutant["status"] in FAILING_STATUSES:
            # Still standing: keep pass 1's entry (and its diff) when there is one.
            by_name.setdefault(mutant["name"], mutant)
        else:
            by_name[mutant["name"]] = {**mutant, "killed_by": "wider"}
    return {"phase": Phase.DONE, "mutants": list(by_name.values())}


def finish(plan: Plan, driver_code: int, result: dict | None, elapsed: float) -> int:
    """Turn the driver's verdict into this script's exit code."""
    if driver_code == EXIT_CAPACITY_STALLED and result is None:
        print(
            "mutation: capacity-run.sh stopped waiting (exit 75); see its message", file=sys.stderr
        )
        return EXIT_CAPACITY_STALLED
    if result is None:
        print(
            f"mutation: ERROR — the mutmut driver exited {driver_code} without writing a result",
            file=sys.stderr,
        )
        return EXIT_ERROR
    phase = result["phase"]
    detail = result.get("detail", "")
    if phase == Phase.BASELINE_FAILED:
        print(
            f"mutation: FAIL — clean baseline failed on the unmutated copy: {detail}",
            file=sys.stderr,
        )
        failures = result.get("failures", [])
        if not failures:
            print(
                "mutation: no failing test was recorded (see mutmut's output above)",
                file=sys.stderr,
            )
        for failure in failures:
            print(f"--- first failure of a baseline session: {failure}", file=sys.stderr)
        return EXIT_BASELINE_FAILED
    if phase == Phase.NO_COVERAGE:
        print(
            f"mutation: FAIL — the selected tests run none of the changed functions: {detail}",
            file=sys.stderr,
        )
        return EXIT_NO_TESTS
    if phase != Phase.DONE:
        print(f"mutation: ERROR — mutmut did not finish ({phase}): {detail}", file=sys.stderr)
        return EXIT_ERROR
    mutants = result["mutants"]
    if not mutants:
        print(
            f"mutation: FAIL — {len(plan.targets)} function(s) changed but mutmut produced 0 mutants for them",
            file=sys.stderr,
        )
        return EXIT_NO_MUTANTS
    code = report(group_results(plan, mutants))
    print(f"mutation: wall time {elapsed:.0f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
