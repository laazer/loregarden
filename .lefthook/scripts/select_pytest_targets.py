#!/usr/bin/env python3
"""Which test files can the commits being pushed actually break?

The full suite is ~2950 tests and five minutes on an idle machine, considerably
worse on a busy one. Most pushes touch a handful of modules, and running
everything to learn that is the reason people reach for `--no-verify`. This
selects the test files that reach the changed modules through the import graph,
and runs only those — seconds, for a change to a leaf module.

Selection is not free of risk, so the design is biased hard toward running too
much:

* Anything it cannot map means the full suite. A changed `conftest.py`, a
  changed `pyproject.toml`, a non-Python file under `server/`, a file outside
  `server/`/`client/` — all of it falls back, because tests here read repo files
  (`.claude/`, `agent_context/`) that no import graph can see.
* Selecting *zero* tests means the full suite, not a fast pass. "Nothing reaches
  this module" is more often a hole in the graph than a fact about the code.
* A change under `client/` selects the server test files that name the client
  tree in a string constant (`REPO / "client"`, `"client/package.json"`). About
  fifty read it — the UI action catalog, the prod-shape fixtures, the gates — so
  a client-only diff is not "no server test can break". Before this it selected
  zero and so ran all of them, which made a one-component ticket's land
  verification a full-suite run that timed out (lg-milestone-that-2197).
  Package modules that name `client` are deliberately NOT followed: every one
  of them names it as a path inside *some workspace's* tree (agent scopes, the
  code map, the toolchain link), not to read this repo's client, and following
  them selected 529 of 691 files. A test that reads the client only through a
  helper is the miss this leaves, and CI's full run is what catches it.
* A change to agent prose (`agent_context/agents/`, `agent_context/skills/`,
  all markdown) selects the tests that name the `agent_context` tree, the tests
  that name the changed file, and the tests that import a package module that
  names it — directly, one hop. Followed transitively, any module that reads
  these files reaches ~530 of the 721 test files (every DB test reaches the
  migrations and the seed through `init_db`), which is the full suite under
  another name; the reader's own tests are where its handling of the file is
  pinned. A file is named by its path under `agent_context/` (role files are
  registered as `"agents/misc_agents/repair_v1.md"`) or by its basename (skills
  are found by globbing for `"SKILL.md"`). The rest of `agent_context/` (orchestration profiles,
  workflow seeds) is configuration that changes how runs behave, and still
  falls back.
* A change to a hook or repo script (`.lefthook/scripts/`, `scripts/`) selects
  the tests that name it, or name a script that runs or imports it, and the
  tests importing a package module that names one of those (one hop, as for
  prose). Tests reach these by path (`"server-tests.sh"`), by module name
  (`spec_from_file_location("py_organization_check", …)`, `f"{name}.py"`) or
  by `import`, never through the package graph, so before this every hook-only
  change ran the full suite: 32 minutes for a 20-line shell fix. A script no
  test names still selects zero, and zero still means everything.
* A test fixture (a non-Python file under `server/tests/`, such as a recorded
  `.json` or `.jsonl` payload) selects the tests that name it, and the tests
  importing a helper that names it. Tests open fixtures by name
  (`FIXTURES / "run_75d1f3_tool_calls.jsonl"`), so the import graph never sees
  them. A new fixture used to send its whole landing to the full suite: two
  fixture files, read by two test files, made lg-hive-office-2784's 31-file
  landing a full run of 8680 tests over 29 minutes. A fixture nothing names
  still selects zero, and zero still means everything.
* Markdown outside `agent_context/` (`CLAUDE.md`, `AGENTS.md`, the PR template,
  `docs/`) is read the same way, by name, so it takes the same path: the tests
  that name it, and the tests of any script that names it. A one-line doc fix
  beside a small code change used to cost the full suite.
* String constants naming a package module count as imports, so the MCP tool
  registry and other `importlib.import_module("loregarden.x")` dispatch does not
  slip through.

* `--nearest` (a push or a landing; either way a PR gets CI's full run) keeps the tests within
  `NEAREST_HOPS` imports of a changed module. 284 of the package's 1019 modules
  are one import cycle, so "reaches" is most of the suite for almost any service:
  a median of 611 of 835 files over the 472 commits to 2026-11-24 that changed a
  module and an existing test with it. 1207 of the 1278 tests changed there that
  the graph reaches at all were within two hops, at a median of 233 files. A
  changed module with no test that near keeps its nearest ones, and the tests
  that read the package's source (`rglob("*.py")`) or name the changed file are
  kept at any distance. A landing passes it too: a land verification is the
  first check of a ticket's work on its tree, not the last. Every landing on an
  integration branch opens or updates the tree's PR (`tree_open_pr`), and the
  merge queue runs CI's full suite on that PR merged onto main before anything
  reaches main. Without it, a 3-file change to `services/doctor.py` selected 636
  files, and lg-milestone-that-2820 was stopped at the 30-minute wall limit.

* `--budget N` (a push, or a landing with a larger N) caps the run at N files, closest first, and never exits
  2: where the selector would otherwise ask for everything (an unmappable path,
  nothing reachable, an empty diff, a module it cannot parse) it runs what the
  mappable part of the change reaches, or, when that is nothing, an even spread
  of N files across the sorted test files, so a `conftest.py` change still runs
  a sample. What it leaves out goes to stderr. On 2026-11-25 pre-push suites
  held this machine's capacity for 1-4 hours each, at load 60-77, while CI ran
  the same full suite in about 9 minutes on 4 shards; CI and the merge queue
  require that full run before anything lands, so a push only needs a fast
  first signal. A landing passes a larger one (300 by default, push-base.sh):
  uncapped, a change near `enums.py` selected ~640 files and ran into the
  30-minute wall limit, and a full-suite fallback cannot finish inside it.

What can still be missed: a test that reaches changed code through a data file,
a subprocess, or a name assembled at runtime. CI still runs the whole suite on
every PR, so the cost of a miss is a slower signal, never an unguarded merge.

Usage: select_pytest_targets.py --repo PATH --base REF [--nearest] [--budget N]
                                [--run-first-out FILE]
Prints one test path per line, the closest to the change first. Exit 2 means
"run the full suite" and the reason goes to stderr; with --budget it is never
returned. --run-first-out also writes,
in every case, the tests a full run should start with: the selection for the
part of the diff that can be narrowed.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# Before the sibling import. Below 3.11 a module using 3.10+ syntax (`match`)
# does not parse, so its imports are unknown and every narrowed run would fall
# back to the full suite. Refusing with EX_UNAVAILABLE says why in one line;
# server-tests.sh then runs everything with that line as the reason.
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from precommit_git_diff import (  # noqa: E402 - path set above
    UnexaminableError,
    UnexaminableFileError,
    parse_python_source,
)

EXIT_SELECTED = 0
EXIT_RUN_EVERYTHING = 2

PACKAGE = "loregarden"
CLIENT_DIR = "client"
CONTEXT_DIR = "agent_context"

#: Prose under `agent_context/`: read as text by the modules that name it, so
#: the tests reaching those modules are the ones a change can break.
NARROWABLE_CONTEXT_TREES = (f"{CONTEXT_DIR}/agents/", f"{CONTEXT_DIR}/skills/")

#: Hook and repo scripts: run or imported by path, so the tests that name a
#: script (or a script that reaches it) are the ones a change can break.
NARROWABLE_SCRIPT_DIRS = (".lefthook/scripts/", "scripts/")


#: Repo files tests reach by name, as they reach a script. Not how tests run:
#: lefthook.yml says what a push runs, and a suite cannot check that. 8 of the 15
#: landed changes that forced a full run in the 14 days to 2026-11-24 edited it.
NARROWABLE_NAMED_FILES = ("lefthook.yml", "Taskfile.yml")


def is_named_doc(path: str) -> bool:
    """Markdown read by name: anywhere but `agent_context/` (its own rule) and `client/`."""
    return path.endswith(".md") and not path.startswith((f"{CONTEXT_DIR}/", f"{CLIENT_DIR}/"))


#: How far `--nearest` follows imports back from a changed module: 1 is the tests
#: importing it, 2 the tests importing a module that does. See the module docstring.
NEAREST_HOPS = 2


def is_test_fixture(path: str) -> bool:
    """A data file tests open by name: anything under `server/tests/` but Python."""
    return path.startswith("server/tests/") and not path.endswith(".py")


#: Longest string constant kept as a possible script name; prose is longer.
_MAX_NAME_LENGTH = 200

#: Changing one of these changes how every test runs, so nothing narrower is honest.
#: `hermetic.gitconfig` is the global git config `conftest.py` gives every test.
_SHARED_TEST_FILES = frozenset(
    {"conftest.py", "factories.py", "worktree_helpers.py", "hermetic.gitconfig"}
)


def _run_git(args: list[str], repo: Path) -> str:
    """Git through a scrubbed environment: GIT_DIR beats cwd, and a pre-push hook
    in a worktree inherits it pointing at the main checkout."""
    env_blocklist = ("GIT_DIR", "GIT_WORK_TREE")
    env = {k: v for k, v in os.environ.items() if k not in env_blocklist}
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=env
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def changed_files(repo: Path, base: str) -> list[str]:
    out = _run_git(["diff", "--name-only", f"{base}...HEAD"], repo)
    return [line for line in out.splitlines() if line.strip()]


def full_suite_reason(paths: list[str]) -> str | None:
    """Why this change cannot be narrowed, if it cannot."""
    for path in paths:
        if path.startswith("client/"):
            continue  # jest's problem, not pytest's
        if path.startswith(NARROWABLE_CONTEXT_TREES + NARROWABLE_SCRIPT_DIRS):
            continue
        if is_named_doc(path) or path in NARROWABLE_NAMED_FILES:
            continue
        if not path.startswith("server/"):
            return f"{path} is outside server/ (tests read repo files no import graph sees)"
        if path.startswith("server/tests/") and Path(path).name in _SHARED_TEST_FILES:
            return f"{path} changes how every test runs"
        if is_test_fixture(path):
            continue
        if not path.endswith(".py"):
            return f"{path} is not Python (no import edge to follow)"
    return None


def module_name(path: Path, package_root: Path) -> str | None:
    """Dotted name for a file inside the package, e.g. loregarden.services.doctor."""
    try:
        rel = path.relative_to(package_root.parent)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _init_file(package: str) -> str:
    """The graph's name for a package's `__init__.py` alone, without what it re-exports."""
    return f"{package}.__init__"


#: package -> name it re-exports -> the modules that may define it
Exports = dict[str, dict[str, set[str]]]


def is_test_file(name: str) -> bool:
    """A module pytest collects tests from, rather than one a test imports."""
    return name.startswith("test_") and name.endswith(".py")


def helper_module(path: Path, test_root: Path) -> str:
    """`server/tests/sub/x.py` -> `tests.sub.x`, the name its importers use."""
    parts = path.relative_to(test_root).with_suffix("").parts
    return ".".join((test_root.name, *parts))


def helper_importers(graph: ImportGraph, helpers: frozenset[str]) -> dict[Path, int]:
    """Test files importing a changed helper: 1 directly, one more per helper between.

    Helpers import helpers (`history_helpers` builds on `factories`), so the
    changed set grows by importer until nothing new joins it.
    """
    selected: dict[Path, int] = {}
    reached = set(helpers)
    frontier = set(helpers)
    hops = 1
    while frontier:
        for test, names in graph.test_scripts.items():
            if test not in selected and names & frontier:
                selected[test] = hops
        frontier = {
            helper
            for helper, names in graph.helper_imports.items()
            if helper not in reached and names & frontier
        }
        reached |= frontier
        hops += 1
    return selected


def _defined_names(tree: ast.Module) -> set[str]:
    """What a module's own top-level statements bind, imports aside."""
    names: set[str] = set()
    for node in tree.body:
        if type(node) in (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef):
            names.add(node.name)
            continue
        targets = node.targets if type(node) is ast.Assign else []
        targets = [node.target] if type(node) is ast.AnnAssign else targets
        names.update(target.id for target in targets if type(target) is ast.Name)
    return names


def _package_exports(known: dict[str, Path], trees: dict[str, ast.Module]) -> Exports:
    """What each package's `__init__` re-exports, and from where.

    `loregarden.models.domain` star-imports every table and enum module, and 624
    of 836 test files import from it. Taken as one node, a change to any of those
    modules reached every one of them; a test that imports `Ticket` from it does
    not depend on an enum `Ticket`'s module never mentions.
    """
    exports: Exports = {}
    for name, path in known.items():
        if path.name != "__init__.py":
            continue
        sources = exports.setdefault(name, {})
        for node in trees[name].body:
            if type(node) is not ast.ImportFrom or node.module not in known:
                continue
            for alias in node.names:
                star = alias.name == "*"
                bound = _defined_names(trees[node.module]) if star else {alias.asname or alias.name}
                for export in bound:
                    sources.setdefault(export, set()).add(node.module)
    return exports


def _imported_modules(tree: ast.AST, exports: Exports) -> set[str]:
    """Package modules this file names — through imports and through strings.

    The string scan is what keeps `importlib.import_module("loregarden.mcp.x")`
    and the MCP tool registry from being invisible to the graph. A name imported
    from a package that re-exports it names the module defining it; one nobody is
    seen to define names the whole package.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(PACKAGE):
                defined = exports.get(node.module, {})
                for alias in node.names:
                    # `from loregarden.services import doctor` names a module too.
                    found.add(f"{node.module}.{alias.name}")
                    found.update(defined.get(alias.name, {node.module}))
                found.add(_init_file(node.module))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(f"{PACKAGE}."):
                found.add(node.value)
    return found


def _is_tree_path(value: str, tree: str) -> bool:
    """`tree`, `tree/…`, `…/tree/…` or `…/tree`: the path segment, so
    `"client_id"` and `"api client"` do not count."""
    return (
        value == tree
        or value.startswith(f"{tree}/")
        or f"/{tree}/" in value
        or value.endswith(f"/{tree}")
    )


def _is_client_path(value: str) -> bool:
    return _is_tree_path(value, CLIENT_DIR)


@dataclass
class _PathNames:
    """What a file's string constants say about the repo trees it reads.

    How a server test reads `client/` or `agent_context/`: by path, never by
    import, so the import graph cannot see it.
    """

    client: bool = False
    context_tree: bool = False
    #: constants that could name an `agent_context` markdown file
    markdown: frozenset[str] = frozenset()
    #: constants and bare imports that could name a script, by path or module name
    scripts: frozenset[str] = frozenset()
    #: globs Python files, so it reads source no import of its own names
    source: bool = False


class _PathFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.client = False
        self.context_tree = False
        self.markdown: set[str] = set()
        self.scripts: set[str] = set()
        self.source = False

    def visit_Call(self, node: ast.Call) -> None:
        called = node.func.attr if type(node.func) is ast.Attribute else ""
        if called in ("glob", "rglob"):
            patterns = [arg.value for arg in node.args if type(arg) is ast.Constant]
            self.source = self.source or any(str(value).endswith(".py") for value in patterns)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        # `import select_pytest_targets` after a sys.path insert names the script.
        self.scripts.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and not node.level:
            self.scripts.add(node.module)

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        # A literal's value is any of str/bytes/int/float/complex/None/Ellipsis:
        # a stdlib union this repo does not own, and only a str can be a path.
        if not isinstance(value, str):  # py-org: allow-isinstance
            return
        self.client = self.client or _is_client_path(value)
        self.context_tree = self.context_tree or _is_tree_path(value, CONTEXT_DIR)
        if value.endswith(".md"):
            self.markdown.add(value)
        if len(value) <= _MAX_NAME_LENGTH and value.strip() and not any(c.isspace() for c in value):
            self.scripts.add(value)


def _path_names(tree: ast.AST) -> _PathNames:
    finder = _PathFinder()
    finder.visit(tree)
    return _PathNames(
        client=finder.client,
        context_tree=finder.context_tree,
        markdown=frozenset(finder.markdown),
        scripts=frozenset(finder.scripts),
        source=finder.source,
    )


def names_context_file(constants: frozenset[str], rel: str) -> bool:
    """Does one of these constants name `rel`, a repo-relative `agent_context` file?

    By its whole path, by its path under `agent_context/` or any shorter
    suffix of it that keeps a directory, or by its basename.
    """
    basename = rel.rsplit("/", 1)[-1]
    for value in constants:
        if value == basename or value == rel:
            return True
        if "/" in value and rel.endswith(f"/{value.lstrip('/')}"):
            return True
    return False


def names_script(names: frozenset[str], rel: str) -> bool:
    """Does one of these constants or imports name `rel`, a repo-relative script?

    As `names_context_file` does, plus a Python script's module name: tests load
    gates with `spec_from_file_location("py_organization_check", …)`, build
    `f"{name}.py"` from a stem, or `import` the script after a sys.path insert.
    """
    if rel.endswith(".py") and rel.rsplit("/", 1)[-1].removesuffix(".py") in names:
        return True
    return names_context_file(names, rel)


def _script_files(repo: Path) -> dict[str, str]:
    """Every file under the script dirs, repo-relative, with its text."""
    found: dict[str, str] = {}
    for tree in NARROWABLE_SCRIPT_DIRS:
        root = repo / tree
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (
                UnicodeDecodeError
            ):  # silent-ok: a binary file cannot name a script; nothing to read
                continue
            found[path.relative_to(repo).as_posix()] = text
    return found


def _mentions(text: str, rel: str) -> bool:
    """Does a script's text run or import `rel`? By basename, or `import stem`."""
    basename = rel.rsplit("/", 1)[-1]
    if basename in text:
        return True
    if not basename.endswith(".py"):
        return False
    stem = re.escape(basename.removesuffix(".py"))
    return re.search(rf"^\s*(?:from\s+{stem}\s+import|import\s+{stem}\b)", text, re.M) is not None


def scripts_reaching(repo: Path, changed: frozenset[str]) -> set[str]:
    """The changed scripts plus every script that runs or imports one, transitively.

    A test of `py-review.sh` exercises the Python gate it calls, so a change to
    that gate must select it.
    """
    texts = _script_files(repo)
    reached = set(changed)
    frontier = list(changed)
    while frontier:
        current = frontier.pop()
        for rel, text in texts.items():
            if rel not in reached and _mentions(text, current):
                reached.add(rel)
                frontier.append(rel)
    return reached


def _parse(path: Path) -> ast.Module:
    """The module's AST, or `UnexaminableFileError` naming why there is none.

    This used to return ``None`` for a file it could not read or parse, and the
    graph recorded that module as importing nothing. A test reaching a changed
    module only through it was then not selected: a hole in the graph read as a
    fact about the code, which is the narrowing this selector exists to avoid.
    """
    try:
        return parse_python_source(path.read_text(encoding="utf-8"), path)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise UnexaminableFileError(
            f"{path}: could not read its imports ({type(exc).__name__}: {exc})"
        ) from exc


@dataclass(frozen=True)
class ImportGraph:
    """The package and its tests as edges, plus what each names by path."""

    #: module -> package modules it imports
    module_edges: dict[str, set[str]]
    #: test file -> package modules it imports
    test_edges: dict[Path, set[str]]
    #: test files that name the client tree themselves
    client_tests: frozenset[Path]
    #: test files that name the `agent_context` tree themselves
    context_tests: frozenset[Path]
    #: test files that read the package's source themselves
    source_tests: frozenset[Path]
    #: module or test file -> the markdown paths its string constants name
    module_markdown: dict[str, frozenset[str]]
    test_markdown: dict[Path, frozenset[str]]
    #: module or test file -> what could name a script (see `_PathNames.scripts`)
    module_scripts: dict[str, frozenset[str]]
    test_scripts: dict[Path, frozenset[str]]
    #: test helper (`tests.repo_paths`) -> the modules it imports, by name
    helper_imports: dict[str, frozenset[str]] = field(default_factory=dict)


def build_graph(package_root: Path, test_root: Path) -> ImportGraph:
    known: dict[str, Path] = {}
    for py in package_root.rglob("*.py"):
        name = module_name(py, package_root)
        if name:
            known[name] = py

    trees = {name: _parse(py) for name, py in known.items()}
    exports = _package_exports(known, trees)
    nodes = known.keys() | {_init_file(name) for name in exports}
    module_edges: dict[str, set[str]] = {}
    module_markdown: dict[str, frozenset[str]] = {}
    module_scripts: dict[str, frozenset[str]] = {}
    for name, tree in trees.items():
        module_edges[name] = _imported_modules(tree, exports) & nodes
        names = _path_names(tree)
        module_markdown[name] = names.markdown
        module_scripts[name] = names.scripts

    test_edges: dict[Path, set[str]] = {}
    test_markdown: dict[Path, frozenset[str]] = {}
    test_scripts: dict[Path, frozenset[str]] = {}
    client_tests: set[Path] = set()
    context_tests: set[Path] = set()
    source_tests: set[Path] = set()
    for py in sorted(test_root.rglob("test_*.py")):
        tree = _parse(py)
        test_edges[py] = _imported_modules(tree, exports) & nodes
        names = _path_names(tree)
        test_markdown[py] = names.markdown
        test_scripts[py] = names.scripts
        if names.client:
            client_tests.add(py)
        if names.context_tree:
            context_tests.add(py)
        if names.source:
            source_tests.add(py)

    helper_imports = {
        helper_module(py, test_root): _path_names(_parse(py)).scripts
        for py in sorted(test_root.rglob("*.py"))
        if not is_test_file(py.name)
    }

    return ImportGraph(
        module_edges=module_edges,
        test_edges=test_edges,
        client_tests=frozenset(client_tests),
        context_tests=frozenset(context_tests),
        source_tests=frozenset(source_tests),
        module_markdown=module_markdown,
        test_markdown=test_markdown,
        module_scripts=module_scripts,
        test_scripts=test_scripts,
        helper_imports=helper_imports,
    )


def _importers(graph: ImportGraph) -> dict[str, set[str]]:
    importers: dict[str, set[str]] = {}
    for name, imported in graph.module_edges.items():
        for target in imported:
            importers.setdefault(target, set()).add(name)
    return importers


def _hops_to(graph: ImportGraph, importers: dict[str, set[str]], module: str) -> dict[Path, int]:
    """Every test file that reaches ``module``, and over how many imports: 1 imports it."""
    distance = {module: 0}
    frontier = [module]
    while frontier:
        following = []
        for current in frontier:
            for name in importers.get(current, ()):
                if name not in distance:
                    distance[name] = distance[current] + 1
                    following.append(name)
        frontier = following
    hops = {}
    for test, imported in graph.test_edges.items():
        reached = [distance[name] for name in imported if name in distance]
        if reached:
            hops[test] = min(reached) + 1
    return hops


def module_hops(graph: ImportGraph, modules: frozenset[str], limit: int | None) -> dict[Path, int]:
    """The test files within ``limit`` imports of a changed module, and how near each is.

    Per module, so one with no test that near keeps its nearest ones: its
    neighbours in the diff being well covered says nothing about it.
    """
    nearest: dict[Path, int] = {}
    importers = _importers(graph)
    for module in modules:
        hops = _hops_to(graph, importers, module)
        if not hops:
            continue
        within = max(limit, min(hops.values())) if limit is not None else max(hops.values())
        for test, count in hops.items():
            if count <= within:
                nearest[test] = min(count, nearest.get(test, count))
    return nearest


@dataclass(frozen=True)
class ChangeSet:
    """A diff's paths, sorted by what each one asks of the server tests."""

    #: changed test files that still exist (a deleted one cannot be run)
    tests: frozenset[Path]
    #: changed package modules, dotted
    modules: frozenset[str]
    #: anything under `client/` changed
    client: bool
    #: changed agent prose, repo-relative (see `NARROWABLE_CONTEXT_TREES`)
    context_files: frozenset[str]
    #: changed files tests reach only by name: hook and repo scripts
    #: (`NARROWABLE_SCRIPT_DIRS`) and markdown outside `agent_context/` (`is_named_doc`)
    script_files: frozenset[str] = frozenset()
    #: changed test helpers (`tests.repo_paths`): run their importers, not them
    test_helpers: frozenset[str] = frozenset()
    #: changed test fixtures, repo-relative (`is_test_fixture`)
    fixture_files: frozenset[str] = frozenset()


def _classify_test_path(repo: Path, rel: str, tests: set[Path], helpers: set[str]) -> None:
    """A test file runs itself; a helper runs what imports it.

    pytest collects nothing from a helper and fails the run (exit 5), and what
    a helper can break is every test importing it, deleted or not. A deleted
    test file cannot be run; its absence is not a gap.
    """
    if not is_test_file(Path(rel).name):
        helpers.add(helper_module(repo / rel, repo / "server" / "tests"))
    elif (repo / rel).exists():
        tests.add(repo / rel)


def classify(repo: Path, paths: list[str]) -> ChangeSet:
    package_root = repo / "server" / PACKAGE
    tests: set[Path] = set()
    modules: set[str] = set()
    client = False
    context_files: set[str] = set()
    script_files: set[str] = set()
    test_helpers: set[str] = set()
    fixture_files: set[str] = set()
    for rel in paths:
        if rel.startswith(f"{CLIENT_DIR}/"):
            client = True
        elif is_test_fixture(rel):
            # A deleted fixture still names the tests that opened it.
            fixture_files.add(rel)
        elif rel.startswith(NARROWABLE_CONTEXT_TREES):
            context_files.add(rel)
        elif (
            rel.startswith(NARROWABLE_SCRIPT_DIRS)
            or is_named_doc(rel)
            or rel in NARROWABLE_NAMED_FILES
        ):
            # A deleted script still names what used to call it.
            script_files.add(rel)
        elif not rel.startswith("server/") or not rel.endswith(".py"):
            continue
        elif rel.startswith("server/tests/"):
            _classify_test_path(repo, rel, tests, test_helpers)
        elif name := module_name(repo / rel, package_root):
            modules.add(name)
            if rel.endswith("/__init__.py"):
                modules.add(_init_file(name))
    return ChangeSet(
        tests=frozenset(tests),
        modules=frozenset(modules),
        client=client,
        context_files=frozenset(context_files),
        script_files=frozenset(script_files),
        test_helpers=frozenset(test_helpers),
        fixture_files=frozenset(fixture_files),
    )


def select(repo: Path, paths: list[str]) -> list[Path]:
    """Test files that import a changed module, plus changed tests themselves,
    plus — when the client changed — the tests that read the client tree, plus
    — when agent prose changed — the tests that read it, plus — when a script
    changed — the tests that name it or a script reaching it."""
    changes = classify(repo, paths)
    if not _needs_graph(changes):
        return sorted(changes.tests)
    graph = build_graph(repo / "server" / PACKAGE, repo / "server" / "tests")
    return sorted(_select(repo, changes, graph))


def select_direct(repo: Path, paths: list[str]) -> list[Path]:
    """Test files that import a changed package module themselves — the nearest
    ring of `select`, and the "closeness 1" tier of `run_first`.

    For callers that pay per test rather than per run: mutation testing runs
    every selected test once per mutant's stats pass, and a change to a widely
    imported module reaches most of the suite transitively (691 of ~720 files
    for `git_subprocess.py`). A test that reaches the module only through
    another one is the miss this leaves; mutation testing reports that as a
    surviving mutant, which is the safe direction."""
    changes = classify(repo, paths)
    if not changes.modules:
        return []
    graph = build_graph(repo / "server" / PACKAGE, repo / "server" / "tests")
    return sorted(test for test, imports in graph.test_edges.items() if imports & changes.modules)


def _needs_graph(changes: ChangeSet) -> bool:
    return bool(
        changes.modules
        or changes.client
        or changes.context_files
        or changes.script_files
        or changes.test_helpers
        or changes.fixture_files
    )


def _select(
    repo: Path, changes: ChangeSet, graph: ImportGraph, limit: int | None = None
) -> dict[Path, int]:
    """Each selected test file and its distance from the change: 0 is changed itself."""
    selected = dict.fromkeys(_other_readers(repo, changes, graph), NEAREST_HOPS)
    selected |= module_hops(graph, changes.modules, limit)
    for test, hops in helper_importers(graph, changes.test_helpers).items():
        selected[test] = min(hops, selected.get(test, hops))
    return selected | dict.fromkeys(changes.tests, 0)


def _other_readers(repo: Path, changes: ChangeSet, graph: ImportGraph) -> set[Path]:
    """The tests that read a changed file that is not a package module."""
    selected: set[Path] = set()
    if changes.client:
        selected |= graph.client_tests
    if changes.context_files:
        readers = _modules_naming(graph, changes.context_files)
        selected |= graph.context_tests | _tests_naming(graph, changes.context_files)
        selected |= {test for test, imports in graph.test_edges.items() if imports & readers}
    if changes.script_files:
        selected |= _tests_reaching_scripts(graph, scripts_reaching(repo, changes.script_files))
    if changes.fixture_files:
        selected |= _tests_reading_fixtures(graph, changes.fixture_files)
    return selected


def _tests_reading_fixtures(graph: ImportGraph, fixtures: frozenset[str]) -> set[Path]:
    """Tests naming one of these fixtures, and tests importing a helper that names one."""
    naming = {
        test
        for test, names in graph.test_scripts.items()
        if any(names_context_file(names, rel) for rel in fixtures)
    }
    helpers = frozenset(
        helper
        for helper, names in graph.helper_imports.items()
        if any(names_context_file(names, rel) for rel in fixtures)
    )
    return naming | helper_importers(graph, helpers).keys()


def _source_readers(changes: ChangeSet, graph: ImportGraph) -> dict[Path, int]:
    """Tests that read package source as text: every one that globs it, at
    `NEAREST_HOPS`, and those naming a changed module's file, as if importing it."""
    if not changes.modules:
        return {}
    named = {
        test
        for test, names in graph.test_scripts.items()
        if any(names_context_file(names, rel) for rel in _module_files(changes.modules))
    }
    return dict.fromkeys(graph.source_tests, NEAREST_HOPS) | dict.fromkeys(named, 1)


def _module_files(modules: frozenset[str]) -> list[str]:
    """Repo-relative. A package's `__init__.py` is left out: every package has one."""
    own = (name for name in modules if not name.endswith(".__init__"))
    return [f"server/{name.replace('.', '/')}.py" for name in own]


def _modules_naming(graph: ImportGraph, files: frozenset[str]) -> set[str]:
    return {
        module
        for module, constants in graph.module_markdown.items()
        if any(names_context_file(constants, rel) for rel in files)
    }


def _tests_reaching_scripts(graph: ImportGraph, scripts: set[str]) -> set[Path]:
    """Tests naming one of these scripts, and tests importing a module that names one."""
    readers = {
        module
        for module, names in graph.module_scripts.items()
        if any(names_script(names, rel) for rel in scripts)
    }
    return {
        test_file
        for test_file, names in graph.test_scripts.items()
        if any(names_script(names, rel) for rel in scripts) or graph.test_edges[test_file] & readers
    }


def _tests_naming(graph: ImportGraph, files: frozenset[str]) -> set[Path]:
    return {
        test_file
        for test_file, constants in graph.test_markdown.items()
        if any(names_context_file(constants, rel) for rel in files)
    }


def run_first(repo: Path, paths: list[str], limit: int | None = None) -> list[Path]:
    """The selection for the part of the diff that can be narrowed, closest first.

    What a narrowed run runs, in the order it runs it, and what a full run caused
    by one unmappable file (`pyproject.toml`) runs first: such a diff still holds
    changes the graph maps, and the tests nearest those are the likeliest to fail.

    Closest first, because "reaches" is often most of the suite: the changed
    tests lead, then the files importing a changed module or naming its file,
    then one import further out at a time. In path order a failure one import
    away surfaced 23 minutes into a push that had waited 100 for its turn.
    Empty when nothing reaches the change: a test that reads every source file
    is no evidence that one does.
    """
    narrowable = [path for path in paths if full_suite_reason([path]) is None]
    if not narrowable:
        return []
    changes = classify(repo, narrowable)
    if not _needs_graph(changes):
        return sorted(changes.tests)
    graph = build_graph(repo / "server" / PACKAGE, repo / "server" / "tests")
    selected = _select(repo, changes, graph, limit)
    if not selected:
        return []
    for test, hops in _source_readers(changes, graph).items():
        selected[test] = min(hops, selected.get(test, hops))
    return sorted(selected, key=lambda test: (selected[test], test))


def _write_run_first(out: Path | None, repo: Path, tests: list[Path]) -> None:
    if out is not None:
        out.write_text(
            "".join(f"{test.relative_to(repo).as_posix()}\n" for test in tests),
            encoding="utf-8",
        )


def spread(tests: list[Path], count: int) -> list[Path]:
    """`count` files evenly across `tests` sorted, first included: every k-th.

    Deterministic, so a re-push after a fix runs the same files again.
    """
    ordered = sorted(tests)
    if count >= len(ordered):
        return ordered
    return [ordered[i * len(ordered) // count] for i in range(count)]


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return number


def _budgeted(repo: Path, selected: list[Path], budget: int, why: str | None) -> list[Path]:
    """At most `budget` files, and on stderr what that left out and why.

    `why` is the reason a run without a budget would have been the full suite.
    """
    if why:
        print(f"{why}; not running the full suite on a push (CI does)", file=sys.stderr)
    if not selected:
        everything = sorted((repo / "server" / "tests").rglob("test_*.py"))
        sample = spread(everything, budget)
        print(
            f"budget: nothing narrower to run, so an even sample of {len(sample)} "
            f"of {len(everything)} test files; CI runs the rest",
            file=sys.stderr,
        )
        return sample
    kept = selected[:budget]
    if len(kept) < len(selected):
        print(
            f"budget: running the closest {len(kept)} of {len(selected)} selected "
            f"test files; left {len(selected) - len(kept)} for CI, the first being "
            f"{selected[budget].relative_to(repo).as_posix()}",
            file=sys.stderr,
        )
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--run-first-out",
        type=Path,
        default=None,
        help="also write the tests a full run should start with (see run_first), one per line",
    )
    parser.add_argument(
        "--nearest",
        action="store_true",
        help=f"keep only the tests within {NEAREST_HOPS} imports of the change (a push)",
    )
    parser.add_argument(
        "--budget",
        type=_positive_int,
        default=None,
        help="print at most N files, closest first, and never ask for the full suite (a push)",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    selected, why = _selection(repo, args.base, args.nearest, args.run_first_out)
    if args.budget is not None:
        selected = _budgeted(repo, selected, args.budget, why)
    elif why:
        print(why, file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    for path in selected:
        print(path.relative_to(repo).as_posix())
    return EXIT_SELECTED


def _selection(
    repo: Path, base: str, nearest: bool, run_first_out: Path | None
) -> tuple[list[Path], str | None]:
    """The tests to run first, closest first, and why the run should be the
    full suite instead, if it should."""
    paths = changed_files(repo, base)
    if not paths:
        _write_run_first(run_first_out, repo, [])
        return [], f"no diff against {base}"

    reason = full_suite_reason(paths)
    try:
        selected = run_first(repo, paths, NEAREST_HOPS if nearest else None)
    except UnexaminableError as exc:
        # A module whose imports are unknown may be the only path from a test
        # to the change, so no subset is honest — not even as an order.
        _write_run_first(run_first_out, repo, [])
        prefix = f"{reason}\n" if reason else ""
        return [], f"{prefix}cannot map the import graph: {exc}"
    _write_run_first(run_first_out, repo, selected)
    if reason:
        return selected, reason
    if not selected:
        # More often a hole in the graph than a module nothing tests.
        return [], "no test file reaches the changed modules"
    return selected, None


if __name__ == "__main__":
    raise SystemExit(main())
