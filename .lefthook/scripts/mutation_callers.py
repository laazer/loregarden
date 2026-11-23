"""Pass 2's tests: the ones that call what survived pass 1, a few calls out.

The import graph answers "can this test reach the module?". For a widely
imported module that is most of the suite: on #939 the selector's transitive
ring for ``mcp_context``/``doctor`` was 566 of 808 test files, reached through
chains of up to nine modules. mutmut's stats run took them serially for 2h36m
and killed none of the 11 survivors, because a test that imports something
that imports the module need not call the changed function. So pass 2 follows
names instead of imports:

- hop 0: tests that name a surviving function and import its module, or that
  name it as a dotted string (``mock.patch("loregarden.x.func")``);
- hop n: the same for every function that names a hop n-1 function, in its own
  module or in a module importing that one. A module-level statement naming one
  (a registry dict, a re-export) passes it on under the names it binds; a
  class-level one, under the class's name.

Names are matched as written, not resolved. A different function with the same
name in an importing module is followed too (the wide side). A call reaching
the function only through dispatch (an HTTP route, the MCP registry's string
lookup, ``getattr``) is not followed: its survivor stands and is reported, which
is the safe side.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import select_pytest_targets
from precommit_git_diff import UnexaminableFileError, parse_python_source

#: Call hops followed past the surviving function itself. Each hop is one more
#: function between a test and the mutant, and the mutant's effect is thinner by
#: then; three keeps a widely called helper from pulling in the suite again.
DEFAULT_HOPS = 3


@dataclass(frozen=True)
class Symbol:
    """A name a caller writes to reach something defined in ``module``."""

    module: str
    name: str


@dataclass(frozen=True)
class Binder:
    """A function, a method, or a module/class-level statement: the names a
    caller uses to reach it, and the names its own source mentions."""

    binds: frozenset[str]
    mentions: frozenset[str]


class _Mentions(ast.NodeVisitor):
    """Every name a piece of source writes: bare names, attributes, the names an
    import brings in, and the ``loregarden.…`` dotted strings it spells out."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.dotted: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self.names.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.names.add(node.attr)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        self.names.add(node.name.rpartition(".")[2])

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if type(value) is str and value.startswith(f"{select_pytest_targets.PACKAGE}."):
            self.dotted.add(value)


def mentions_of(node: ast.AST) -> _Mentions:
    found = _Mentions()
    found.visit(node)
    return found


class _Bound(ast.NodeVisitor):
    """The module-level names one statement binds. Attribute and subscript
    targets bind nothing a caller could import."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_alias(self, node: ast.alias) -> None:
        self.names.add(node.asname or node.name.partition(".")[0])

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.names |= _target_names(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.names |= _target_names(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.names |= _target_names(node.target)


class _TargetNames(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self.names.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """`obj.x = …` binds nothing at module level."""

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """`table[k] = …` binds nothing at module level."""


def _target_names(target: ast.AST) -> set[str]:
    found = _TargetNames()
    found.visit(target)
    return found.names


class _Binders(ast.NodeVisitor):
    """A module's top level and one level into its classes, as binders."""

    def __init__(self) -> None:
        self.binders: list[Binder] = []
        self._class: str | None = None

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._add({node.name}, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._class is not None:
            self._add({self._class}, node)
            return
        self._class = node.name
        self._add({node.name}, ast.Module(body=node.bases + node.decorator_list, type_ignores=[]))
        for statement in node.body:
            self.visit(statement)
        self._class = None

    def generic_visit(self, node: ast.AST) -> None:
        """Any other top-level or class-level statement."""
        if self._class is not None:
            self._add({self._class}, node)
            return
        bound = _Bound()
        bound.visit(node)
        if bound.names:
            self._add(bound.names, node)

    def _add(self, binds: set[str], node: ast.AST) -> None:
        self.binders.append(Binder(frozenset(binds), frozenset(mentions_of(node).names)))


def binders_of(tree: ast.AST) -> list[Binder]:
    collector = _Binders()
    collector.visit(tree)
    return collector.binders


def _parse(path: Path) -> ast.Module:
    try:
        return parse_python_source(path.read_text(encoding="utf-8"), path)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise UnexaminableFileError(
            f"{path}: could not read the names it uses ({type(exc).__name__}: {exc})"
        ) from exc


@dataclass(frozen=True)
class TestNames:
    imports: frozenset[str]
    names: frozenset[str]
    dotted: frozenset[str]

    def names_symbol(self, symbol: Symbol) -> bool:
        spelled = f"{symbol.module}.{symbol.name}"
        if any(d == spelled or d.startswith(f"{spelled}.") for d in self.dotted):
            return True
        return symbol.name in self.names and symbol.module in self.imports


@dataclass(frozen=True)
class NameIndex:
    #: module -> its binders
    binders: dict[str, list[Binder]]
    #: module -> package modules importing it, itself included
    importers: dict[str, frozenset[str]]
    tests: dict[Path, TestNames]

    def callers(self, symbol: Symbol) -> set[Symbol]:
        """What a caller writes to reach each binder that names ``symbol``."""
        found: set[Symbol] = set()
        for module in self.importers.get(symbol.module, ()):
            for binder in self.binders.get(module, ()):
                if symbol.name in binder.mentions:
                    found |= {Symbol(module, name) for name in binder.binds}
        return found

    def tests_naming(self, symbols: Iterable[Symbol]) -> set[Path]:
        wanted = list(symbols)
        return {
            path for path, names in self.tests.items() if any(names.names_symbol(s) for s in wanted)
        }


def build_index(package_root: Path, test_root: Path) -> NameIndex:
    graph = select_pytest_targets.build_graph(package_root, test_root)
    binders: dict[str, list[Binder]] = {}
    importers: dict[str, set[str]] = {name: {name} for name in graph.module_edges}
    for py in package_root.rglob("*.py"):
        name = select_pytest_targets.module_name(py, package_root)
        if name and name in graph.module_edges:
            binders[name] = binders_of(_parse(py))
            for imported in graph.module_edges[name]:
                importers.setdefault(imported, {imported}).add(name)
    tests: dict[Path, TestNames] = {}
    for path, imports in graph.test_edges.items():
        found = mentions_of(_parse(path))
        tests[path] = TestNames(frozenset(imports), frozenset(found.names), frozenset(found.dotted))
    return NameIndex(
        binders=binders,
        importers={k: frozenset(v) for k, v in importers.items()},
        tests=tests,
    )


def caller_tests(index: NameIndex, symbol: Symbol, hops: int = DEFAULT_HOPS) -> set[Path]:
    """Tests that name ``symbol``, or a function at most ``hops`` calls above it."""
    seen = {symbol}
    frontier = {symbol}
    for _ in range(hops):
        frontier = {c for s in frontier for c in index.callers(s)} - seen
        if not frontier:
            break
        seen |= frontier
    return index.tests_naming(seen)
