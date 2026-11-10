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
  (`lefthook.yml`, `agent_context/`) that no import graph can see.
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
* String constants naming a package module count as imports, so the MCP tool
  registry and other `importlib.import_module("loregarden.x")` dispatch does not
  slip through.

What can still be missed: a test that reaches changed code through a data file,
a subprocess, or a name assembled at runtime. CI still runs the whole suite on
every PR, so the cost of a miss is a slower signal, never an unguarded merge.

Usage: select_pytest_targets.py --repo PATH --base REF
Prints one test path per line. Exit 2 means "run the full suite" and the reason
goes to stderr.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass
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

#: Changing one of these changes how every test runs, so nothing narrower is honest.
_SHARED_TEST_FILES = frozenset({"conftest.py", "factories.py", "worktree_helpers.py"})


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
        if path.startswith(NARROWABLE_CONTEXT_TREES):
            continue
        if not path.startswith("server/"):
            return f"{path} is outside server/ (tests read repo files no import graph sees)"
        if path.startswith("server/tests/") and Path(path).name in _SHARED_TEST_FILES:
            return f"{path} changes how every test runs"
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


def _imported_modules(tree: ast.AST) -> set[str]:
    """Package modules this file names — through imports and through strings.

    The string scan is what keeps `importlib.import_module("loregarden.mcp.x")`
    and the MCP tool registry from being invisible to the graph.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(PACKAGE):
                found.add(node.module)
                for alias in node.names:
                    # `from loregarden.services import doctor` names a module too.
                    found.add(f"{node.module}.{alias.name}")
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


class _PathFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.client = False
        self.context_tree = False
        self.markdown: set[str] = set()

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


def _path_names(tree: ast.AST) -> _PathNames:
    finder = _PathFinder()
    finder.visit(tree)
    return _PathNames(
        client=finder.client,
        context_tree=finder.context_tree,
        markdown=frozenset(finder.markdown),
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


def _parse(path: Path) -> ast.AST:
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
    #: module or test file -> the markdown paths its string constants name
    module_markdown: dict[str, frozenset[str]]
    test_markdown: dict[Path, frozenset[str]]


def build_graph(package_root: Path, test_root: Path) -> ImportGraph:
    known: dict[str, Path] = {}
    for py in package_root.rglob("*.py"):
        name = module_name(py, package_root)
        if name:
            known[name] = py

    module_edges: dict[str, set[str]] = {}
    module_markdown: dict[str, frozenset[str]] = {}
    for name, py in known.items():
        tree = _parse(py)
        module_edges[name] = {m for m in _imported_modules(tree) if m in known}
        module_markdown[name] = _path_names(tree).markdown

    test_edges: dict[Path, set[str]] = {}
    test_markdown: dict[Path, frozenset[str]] = {}
    client_tests: set[Path] = set()
    context_tests: set[Path] = set()
    for py in sorted(test_root.rglob("test_*.py")):
        tree = _parse(py)
        test_edges[py] = {m for m in _imported_modules(tree) if m in known}
        names = _path_names(tree)
        test_markdown[py] = names.markdown
        if names.client:
            client_tests.add(py)
        if names.context_tree:
            context_tests.add(py)

    return ImportGraph(
        module_edges=module_edges,
        test_edges=test_edges,
        client_tests=frozenset(client_tests),
        context_tests=frozenset(context_tests),
        module_markdown=module_markdown,
        test_markdown=test_markdown,
    )


def reachable(seeds: set[str], module_edges: dict[str, set[str]]) -> set[str]:
    """Every package module reachable from these, following imports forward."""
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(module_edges.get(current, ()))
    return seen


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


def classify(repo: Path, paths: list[str]) -> ChangeSet:
    package_root = repo / "server" / PACKAGE
    tests: set[Path] = set()
    modules: set[str] = set()
    client = False
    context_files: set[str] = set()
    for rel in paths:
        if rel.startswith(f"{CLIENT_DIR}/"):
            client = True
        elif rel.startswith(NARROWABLE_CONTEXT_TREES):
            context_files.add(rel)
        elif not rel.startswith("server/") or not rel.endswith(".py"):
            continue
        elif rel.startswith("server/tests/"):
            # A deleted test file cannot be run; its absence is not a gap.
            if (repo / rel).exists():
                tests.add(repo / rel)
        elif name := module_name(repo / rel, package_root):
            modules.add(name)
    return ChangeSet(
        tests=frozenset(tests),
        modules=frozenset(modules),
        client=client,
        context_files=frozenset(context_files),
    )


def select(repo: Path, paths: list[str]) -> list[Path]:
    """Test files that import a changed module, plus changed tests themselves,
    plus — when the client changed — the tests that read the client tree, plus
    — when agent prose changed — the tests that read it."""
    changes = classify(repo, paths)
    if not changes.modules and not changes.client and not changes.context_files:
        return sorted(changes.tests)

    graph = build_graph(repo / "server" / PACKAGE, repo / "server" / "tests")
    selected = set(changes.tests)
    if changes.client:
        selected |= graph.client_tests
    if changes.context_files:
        readers = _modules_naming(graph, changes.context_files)
        selected |= graph.context_tests | _tests_naming(graph, changes.context_files)
        selected |= {test for test, imports in graph.test_edges.items() if imports & readers}
    if changes.modules:
        selected |= {
            test_file
            for test_file, imports in graph.test_edges.items()
            if reachable(imports, graph.module_edges) & changes.modules
        }
    return sorted(selected)


def _modules_naming(graph: ImportGraph, files: frozenset[str]) -> set[str]:
    return {
        module
        for module, constants in graph.module_markdown.items()
        if any(names_context_file(constants, rel) for rel in files)
    }


def _tests_naming(graph: ImportGraph, files: frozenset[str]) -> set[Path]:
    return {
        test_file
        for test_file, constants in graph.test_markdown.items()
        if any(names_context_file(constants, rel) for rel in files)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()

    paths = changed_files(repo, args.base)
    if not paths:
        print(f"no diff against {args.base}", file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    reason = full_suite_reason(paths)
    if reason:
        print(reason, file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    try:
        selected = select(repo, paths)
    except UnexaminableError as exc:
        # A module whose imports are unknown may be the only path from a test
        # to the change, so no subset is honest.
        print(f"cannot map the import graph: {exc}", file=sys.stderr)
        return EXIT_RUN_EVERYTHING
    if not selected:
        # More often a hole in the graph than a module nothing tests.
        print("no test file reaches the changed modules", file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    for path in selected:
        print(path.relative_to(repo).as_posix())
    return EXIT_SELECTED


if __name__ == "__main__":
    raise SystemExit(main())
