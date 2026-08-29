#!/usr/bin/env python3
"""Guardrails for Python code organization, for humans and agents alike.

All checks are diff-scoped: a violation only fails when it overlaps lines the
change actually adds/modifies. Pre-existing debt elsewhere in a touched file is
reported nowhere and never blocks — the guardrail is "don't make it worse", not
"fix everything on sight". This matters because loregarden already carries known
debt (long functions, long files, a few private-symbol imports); see
server/pyproject.toml [tool.pylint] and the py-pylint hook for the same policy.

Two invocations, because the same rules have to reach both ways code enters a
workspace:

    # pre-commit (lefthook): explicit staged files, scoped to the index
    py_organization_check.py server/loregarden/foo.py …

    # orchestration gate: any workspace, scoped to what an agent just did
    py_organization_check.py --repo /path/to/workspace --scope worktree

Gate mode discovers its own file list from the diff (including untracked files,
which are the *most* important ones to read) and confines itself to the repo's
detected Python source root, mirroring the lefthook glob. Nothing here is
loregarden-specific: layout and the enum home are detected per repo, so every
workspace the control plane drives gets the same rules.
"""

import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

from precommit_git_diff import (
    DIFF_SCOPES,
    STAGED,
    GitScopeError,
    git_diff_cached,
    git_diff_numstat,
    git_repo_root,
    git_untracked_paths,
    parse_staged_additions,
    resolve_gate_scope,
)
from py_string_vocab import collect_enum_members, string_vocabulary_errors

MAX_FILE_LINES = 1500
# Test modules get a higher cap. A suite grows by accumulating cases against one
# surface, and splitting it because it crossed a line count scatters related
# coverage across files with no seam to justify the split. The cap still exists:
# past this, the module is testing too many surfaces, not too many cases.
MAX_TEST_FILE_LINES = 2500
MAX_CLASS_LINES = 1000
MIN_DUPLICATE_BODY_LINES = 8
MAX_INIT_LINES = 120
# Clustered mid-dot f-strings in one function ("a · {b}", " · {x} if x") are the
# same DRY smell as duplicate bodies — extract Dot / mid_dot instead of hand-rolling.
MIN_MID_DOT_FSTRINGS = 3
_MID_DOT = "·"

# Directories that never contribute catalog entries; pruned from the walk so we do not
# descend into them. server/.venv alone holds >1400 vendored .py files.
_CATALOG_PRUNE_DIRS: frozenset[str] = frozenset(
    {".venv", ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".pyinstaller"}
)

_FORBIDDEN_DYNAMIC_ACCESS: frozenset[str] = frozenset({"getattr", "setattr"})

# Builtin containers/scalars. `isinstance(payload, dict)` is not a type check, it is
# a schema check written by hand — 205 of this repo's 247 isinstance calls target one
# of these, all of them poking at an untyped payload a Pydantic model should own.
_PAYLOAD_SHAPE_TYPES: frozenset[str] = frozenset(
    {"dict", "list", "str", "int", "float", "bool", "tuple", "set", "bytes"}
)

_ALLOW_ISINSTANCE = "# py-org: allow-isinstance"


def _span_touched(start: int, end: int, touched_lines: Optional[Set[int]]) -> bool:
    """True if any line in [start, end] was added/modified in this diff."""
    if not touched_lines:
        return False
    return any(ln in touched_lines for ln in range(start, end + 1))


def class_span(node: ast.ClassDef) -> Optional[int]:
    start = node.lineno
    end = node.end_lineno
    if start is None or end is None:
        return None
    return end - start + 1


def _call_dynamic_access_name(func: ast.expr) -> Optional[str]:
    if isinstance(func, ast.Name) and func.id in _FORBIDDEN_DYNAMIC_ACCESS:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_DYNAMIC_ACCESS:
        return func.attr
    return None


def _is_test_path(py_file: Path) -> bool:
    return "tests" in py_file.parts or py_file.name.startswith("test_")


def dynamic_access_errors(
    py_file: Path, tree: ast.AST, touched_lines: Optional[Set[int]]
) -> List[str]:
    """Forbid getattr/setattr outside tests, on staged-added lines only."""
    if _is_test_path(py_file):
        return []
    errors: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_dynamic_access_name(node.func)
        if name is None:
            continue
        lineno = node.lineno
        if lineno is None or not _span_touched(lineno, lineno, touched_lines):
            continue
        errors.append(
            f"{py_file}:{lineno}: avoid `{name}(...)` outside tests; "
            "use explicit attributes, typing.Protocol, or structured APIs"
        )
    return errors


def _isinstance_targets(node: ast.Call) -> List[str]:
    if len(node.args) < 2:
        return []
    target = node.args[1]
    elts = target.elts if isinstance(target, ast.Tuple) else [target]
    return [ast.unparse(elt) for elt in elts]


def isinstance_errors(
    py_file: Path, tree: ast.AST, content_lines: List[str], touched_lines: Optional[Set[int]]
) -> List[str]:
    """Forbid `isinstance(...)` outside tests, on staged lines only.

    Runtime type-switching is the dynamic-access smell one level up: the value's
    type is unknown because nothing typed it at the boundary. Two shapes, two
    fixes, so the message says which one applies.

    ``# py-org: allow-isinstance`` waives a line — for the places that genuinely
    inspect foreign objects (a third-party payload before it can be modelled, an
    ``__eq__``, a ``TypeDecorator``).
    """
    if _is_test_path(py_file):
        return []
    errors: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "isinstance"):
            continue
        lineno = node.lineno
        if lineno is None or not _span_touched(lineno, lineno, touched_lines):
            continue
        if _line_waives(content_lines, lineno, _ALLOW_ISINSTANCE):
            continue
        targets = _isinstance_targets(node)
        shown = ", ".join(targets) or "?"
        if any(t in _PAYLOAD_SHAPE_TYPES for t in targets):
            fix = (
                "this is a hand-rolled schema check; parse the payload into a Pydantic "
                "model at the boundary and pass the model around"
            )
        else:
            fix = (
                "dispatch on the type instead — polymorphism, a typing.Protocol, or a "
                "discriminated union the caller already knows"
            )
        errors.append(f"{py_file}:{lineno}: `isinstance(..., {shown})`; {fix}")
    return errors


def _line_waives(content_lines: List[str], lineno: int, marker: str) -> bool:
    if 1 <= lineno <= len(content_lines):
        return marker in content_lines[lineno - 1]
    return False


def check_file(
    py_file: Path,
    touched_lines: Optional[Set[int]] = None,
    net_growing: bool = False,
    catalogs: Optional["RepoCatalogs"] = None,
) -> List[str]:
    errors: List[str] = []

    if not py_file.exists():
        return errors

    try:
        content = py_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"{py_file}: not valid UTF-8 text")
        return errors

    lines = content.count("\n") + (0 if content.endswith("\n") else 1)
    # Whole-file caps only fire when this diff makes the file net longer — a
    # file that's already over the cap can still be freely edited/shrunk;
    # only growing it further is blocked.
    max_lines = MAX_TEST_FILE_LINES if _is_test_path(py_file) else MAX_FILE_LINES
    if lines > max_lines and net_growing:
        errors.append(
            f"{py_file}: module is {lines} lines (max {max_lines}); split into smaller modules"
        )

    try:
        tree = ast.parse(content, filename=str(py_file))
    except SyntaxError as exc:
        errors.append(f"{py_file}:{exc.lineno}: syntax error during organization checks: {exc.msg}")
        return errors

    errors.extend(init_module_minimal_errors(py_file, tree, lines, touched_lines, net_growing))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            span = class_span(node)
            if (
                span is not None
                and span > MAX_CLASS_LINES
                and _span_touched(node.lineno, node.end_lineno, touched_lines)
            ):
                errors.append(
                    f"{py_file}:{node.lineno}: class `{node.name}` is {span} lines "
                    f"(max {MAX_CLASS_LINES}); extract helper classes/modules"
                )
    errors.extend(private_import_errors(py_file, tree, touched_lines))
    errors.extend(dynamic_access_errors(py_file, tree, touched_lines))
    errors.extend(isinstance_errors(py_file, tree, content.splitlines(), touched_lines))
    errors.extend(mid_dot_fstring_errors(py_file, tree, touched_lines))
    errors.extend(
        string_vocabulary_errors(
            py_file,
            tree,
            content,
            touched_lines,
            catalogs.enums if catalogs else {},
            enum_home=catalogs.enum_home if catalogs else "",
        )
    )

    duplicate_groups = find_duplicate_function_bodies(tree, content)
    for funcs in duplicate_groups:
        if not any(_span_touched(line, end, touched_lines) for _, line, end in funcs):
            continue
        refs = ", ".join(f"{name}@{line}" for name, line, _ in funcs)
        errors.append(
            f"{py_file}: duplicated function bodies detected ({refs}); extract shared helper to keep DRY"
        )

    return errors


def _joined_str_has_mid_dot(node: ast.JoinedStr) -> bool:
    """True when an f-string hard-codes the mid-dot separator in a constant piece."""
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and _MID_DOT in value.value:
            return True
    return False


def _mid_dot_sites_in_function(fn: ast.AST) -> List[int]:
    """Line numbers of f-strings that hard-code the mid-dot separator."""
    return [
        node.lineno or 0
        for node in ast.walk(fn)
        if isinstance(node, ast.JoinedStr) and _joined_str_has_mid_dot(node) and node.lineno
    ]


def mid_dot_fstring_errors(
    py_file: Path, tree: ast.AST, touched_lines: Optional[Set[int]]
) -> List[str]:
    """Flag functions that hand-roll several mid-dot labels instead of using Dot.

    Diff-scoped: only fails when at least one mid-dot f-string overlaps the
    staged lines. ``Dot`` / ``mid_dot`` join via ``" · ".join(...)`` (a Call on
    a Constant), so they do not self-trigger.
    """
    if _is_test_path(py_file):
        return []
    if py_file.name == "dot_line.py":
        return []

    errors: List[str] = []
    for node in tree.body:
        funcs: List[ast.AST] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.append(item)
        for fn in funcs:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            sites = _mid_dot_sites_in_function(fn)
            if len(sites) < MIN_MID_DOT_FSTRINGS:
                continue
            if not any(ln in (touched_lines or set()) for ln in sites):
                continue
            errors.append(
                f"{py_file}:{fn.lineno}: function `{fn.name}` hand-rolls {len(sites)} "
                f"mid-dot labels (min {MIN_MID_DOT_FSTRINGS}); use "
                f"`loregarden.dot_line.Dot` / `mid_dot` instead of f-strings with ` · `"
            )
    return errors


def init_module_minimal_errors(
    py_file: Path,
    tree: ast.AST,
    lines: int,
    touched_lines: Optional[Set[int]],
    net_growing: bool = False,
) -> List[str]:
    errors: List[str] = []
    if py_file.name != "__init__.py":
        return errors

    if lines > MAX_INIT_LINES and net_growing:
        errors.append(
            f"{py_file}: __init__.py is {lines} lines (max {MAX_INIT_LINES}); "
            "keep package __init__ minimal (imports/re-exports only)"
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _span_touched(node.lineno, node.lineno, touched_lines):
                continue
            errors.append(
                f"{py_file}:{node.lineno}: avoid defining {type(node).__name__.replace('Def', '').lower()} in __init__.py; "
                "move behavior to a module and re-export symbols here"
            )
    return errors


def private_import_errors(
    py_file: Path, tree: ast.AST, touched_lines: Optional[Set[int]]
) -> List[str]:
    errors: List[str] = []
    is_test_file = _is_test_path(py_file)
    if is_test_file:
        return errors
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            if not _span_touched(node.lineno, node.lineno, touched_lines):
                continue
            for alias in node.names:
                imported = alias.name
                if imported.startswith("_") and not imported.startswith("__"):
                    errors.append(
                        f"{py_file}:{node.lineno}: imports private symbol `{imported}`; "
                        "depend on a public API instead (or promote it to a public symbol before reuse)"
                    )
        if isinstance(node, ast.Import):
            if not _span_touched(node.lineno, node.lineno, touched_lines):
                continue
            for alias in node.names:
                module_name = alias.name.rsplit(".", 1)[-1]
                if module_name.startswith("_") and not module_name.startswith("__"):
                    errors.append(
                        f"{py_file}:{node.lineno}: imports private module `{alias.name}`; "
                        "depend on a public API instead (or promote it to a public module before reuse)"
                    )
    return errors


def _split_source_lines(source: str) -> List[str]:
    """Split source into lines the way the parser does (keepends; \\r \\n \\r\\n only).

    Faithful copy of CPython's private ``ast._splitlines_no_ff`` so we can split a
    file's source ONCE and reuse it, instead of ``ast.get_source_segment`` re-splitting
    the whole file for every statement node (the previous O(statements x file_size) cost).
    """
    idx = 0
    lines: List[str] = []
    next_line = ""
    n = len(source)
    while idx < n:
        c = source[idx]
        next_line += c
        idx += 1
        if c == "\r" and idx < n and source[idx] == "\n":
            next_line += "\n"
            idx += 1
        if c in "\r\n":
            lines.append(next_line)
            next_line = ""
    if next_line:
        lines.append(next_line)
    return lines


def _source_segment_from_lines(lines: List[str], node: ast.AST) -> Optional[str]:
    """Reproduce ``ast.get_source_segment(source, node)`` (padded=False) from pre-split
    ``lines`` (as produced by ``_split_source_lines``). Byte-for-byte identical output;
    only the whole-source re-split per call is eliminated."""
    end_lineno = getattr(node, "end_lineno", None)
    end_col_offset = getattr(node, "end_col_offset", None)
    if end_lineno is None or end_col_offset is None:
        return None
    lineno = node.lineno - 1
    end = end_lineno - 1
    col_offset = node.col_offset
    if end == lineno:
        return lines[lineno].encode()[col_offset:end_col_offset].decode()
    first = lines[lineno].encode()[col_offset:].decode()
    last = lines[end].encode()[:end_col_offset].decode()
    middle = lines[lineno + 1:end]
    return "".join([first, *middle, last])


def normalized_body_lines(lines: List[str], node: ast.AST) -> List[str]:
    segment = _source_segment_from_lines(lines, node) or ""
    out: List[str] = []
    for raw in segment.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(" ".join(line.split()))
    return out


def find_duplicate_function_bodies(
    tree: ast.AST, source: str
) -> List[List[tuple[str, int, int]]]:
    """Returns groups of (name, lineno, end_lineno) with identical normalized bodies."""
    buckets: dict[tuple[str, ...], List[tuple[str, int, int]]] = {}
    lines = _split_source_lines(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_lines: List[str] = []
        for stmt in node.body:
            body_lines.extend(normalized_body_lines(lines, stmt))
        if len(body_lines) < MIN_DUPLICATE_BODY_LINES:
            continue
        key = tuple(body_lines)
        buckets.setdefault(key, []).append((node.name, node.lineno, node.end_lineno or node.lineno))
    return [group for group in buckets.values() if len(group) > 1]


def function_body_key(node: ast.AST, lines: List[str]) -> Optional[Tuple[str, ...]]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    body_lines: List[str] = []
    for stmt in node.body:
        body_lines.extend(normalized_body_lines(lines, stmt))
    if len(body_lines) < MIN_DUPLICATE_BODY_LINES:
        return None
    return tuple(body_lines)


_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def _project_root_for(py_file: Path, repo_root: Path) -> Path:
    """Nearest ancestor that declares a Python project, bounded by the repo root."""
    resolved = repo_root.resolve()
    current = py_file.resolve().parent
    while True:
        if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
            return current
        if current == resolved or resolved not in current.parents:
            return py_file.resolve().parent
        current = current.parent


def python_source_roots(repo_root: Path, changed_files: Optional[List[Path]] = None) -> List[Path]:
    """Which subtrees the cross-file catalogs should walk.

    loregarden nests its package under ``server/``; other workspaces put it at the
    top, under ``src/``, or somewhere no convention predicts. When no known layout
    matches, walk the *project* each changed file belongs to (nearest pyproject/
    setup marker) instead of the repo root: blobert is a monorepo of 12,004 .py
    files, where a repo-root walk cost 26s per gate run and a DRY catalog spanning
    unrelated subprojects answered no useful question anyway.
    """
    for candidate in ("server", "src", "backend", "app"):
        path = repo_root / candidate
        if path.is_dir() and any(path.rglob("*.py")):
            return [path]
    if not changed_files:
        return [repo_root]
    roots: List[Path] = []
    for py_file in changed_files:
        root = _project_root_for(py_file, repo_root)
        # Drop any root already covered by a shallower one.
        if not any(root == kept or kept in root.parents for kept in roots):
            roots = [kept for kept in roots if root not in kept.parents]
            roots.append(root)
    return roots or [repo_root]


def python_source_root(repo_root: Path, changed_files: Optional[List[Path]] = None) -> Path:
    """The single root used for "is this file in scope" tests (gate mode filtering)."""
    roots = python_source_roots(repo_root, changed_files)
    return roots[0] if len(roots) == 1 else repo_root


def python_files_in_scope(
    repo: Optional[Path], candidates: Sequence[Path], discovered: bool = True
) -> List[Path]:
    """The Python files a gate should read, from a run's candidate paths.

    A ``discovered`` list came from a diff, so it is confined to the repo's own
    Python source root — mirroring the lefthook glob, without which a gate
    grades build tooling and AST-walking scripts by rules written for
    application code. An explicit list was already scoped by its caller.

    Shared by every Python gate: this filter decides half of whether a run
    examined anything, so it does not get reimplemented per gate.
    """
    python = [path for path in candidates if path.suffix == ".py"]
    if repo is None or not discovered:
        return python
    source_root = python_source_root(repo).resolve()
    return [path for path in python if source_root in path.resolve().parents]


def _read_and_parse(py_file: Path) -> Optional[Tuple[str, ast.AST]]:
    if not py_file.exists():
        return None
    try:
        source = py_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    try:
        return source, ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return None


def function_keys_for_file(py_file: Path) -> List[Tuple[Tuple[str, ...], str, int, int]]:
    """Returns (body_key, name, lineno, end_lineno) for eligible functions in a file."""
    parsed = _read_and_parse(py_file)
    if parsed is None:
        return []
    source, tree = parsed
    return function_keys_from_tree(tree, source)


def function_keys_from_tree(
    tree: ast.AST, source: str
) -> List[Tuple[Tuple[str, ...], str, int, int]]:
    lines = _split_source_lines(source)
    keys: List[Tuple[Tuple[str, ...], str, int, int]] = []
    for node in ast.walk(tree):
        key = function_body_key(node, lines)
        if key is None:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            keys.append((key, node.name, node.lineno, node.end_lineno or node.lineno))
    return keys


@dataclass(frozen=True)
class RepoCatalogs:
    duplicates: Dict[Tuple[str, ...], List[Tuple[str, str, int]]]
    enums: Dict[str, Dict[str, str]]
    #: The module that already holds most of this repo's enums, so "add one" can
    #: point somewhere real in whichever workspace is being checked rather than
    #: naming loregarden's own.
    enum_home: str = ""


def build_repo_catalogs(
    changed_files: List[Path], repo_root: Optional[Path] = None
) -> RepoCatalogs:
    """One walk, three answers: duplicate-body keys, str-enum member values, enum home.

    They share a walk because parsing the tree twice is the expensive half of this
    hook. The DRY catalog excludes the changed files (a function cannot duplicate
    itself); the enum catalog includes them — an enum added in this very commit is
    still the type the rest of the commit should be using.
    """
    changed_set = {p.resolve() for p in changed_files if p.exists()}
    catalog: Dict[Tuple[str, ...], List[Tuple[str, str, int]]] = {}
    enum_catalog: Dict[str, Dict[str, str]] = {}
    enum_density: Dict[str, int] = {}
    root = repo_root or Path(".")
    for walk_root in python_source_roots(root, changed_files):
        for dirpath, dirnames, filenames in os.walk(walk_root):
            # Prune in place so os.walk does not descend into excluded trees.
            dirnames[:] = [d for d in dirnames if d not in _CATALOG_PRUNE_DIRS]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                py_file = Path(dirpath) / filename
                parsed = _read_and_parse(py_file)
                if parsed is None:
                    continue
                source, tree = parsed
                before = sum(len(v) for v in enum_catalog.values())
                collect_enum_members(tree, enum_catalog)
                added_members = sum(len(v) for v in enum_catalog.values()) - before
                if added_members > 0:
                    enum_density[_repo_relative_posix(py_file, root.resolve())] = added_members
                if py_file.resolve() in changed_set:
                    continue
                for key, func_name, lineno, _end in function_keys_from_tree(tree, source):
                    catalog.setdefault(key, []).append((py_file.as_posix(), func_name, lineno))
    enum_home = max(enum_density, key=lambda path: enum_density[path], default="")
    return RepoCatalogs(catalog, enum_catalog, enum_home)


def codebase_dry_errors(
    changed_files: List[Path],
    catalog: Dict[Tuple[str, ...], List[Tuple[str, str, int]]],
    touched_map: Dict[Path, Optional[Set[int]]],
) -> List[str]:
    errors: List[str] = []
    for py_file in changed_files:
        touched = touched_map.get(py_file)
        for key, func_name, lineno, end_lineno in function_keys_for_file(py_file):
            if not _span_touched(lineno, end_lineno, touched):
                continue
            matches = catalog.get(key, [])
            if not matches:
                continue
            refs = ", ".join(f"{path}:{name}@{line}" for path, name, line in matches[:3])
            errors.append(
                f"{py_file}:{lineno}: function `{func_name}` duplicates existing code ({refs}); reuse existing logic to keep DRY"
            )
    return errors


def _repo_relative_posix(py_file: Path, repo: Optional[Path]) -> str:
    if repo is None:
        return py_file.as_posix()
    try:
        return py_file.resolve().relative_to(repo).as_posix()
    except ValueError:
        return py_file.as_posix()


def _all_line_numbers(path: Path) -> Set[int]:
    try:
        return set(range(1, len(path.read_text(encoding="utf-8").splitlines()) + 1))
    except (OSError, UnicodeDecodeError):
        return set()


@dataclass(frozen=True)
class Invocation:
    """How this run was asked to scope itself.

    Two callers: lefthook passes staged file paths and nothing else; an
    orchestration gate passes ``--repo``/``--scope`` and no file list, because it
    is judging whatever an agent just did to a workspace it does not enumerate.
    """

    files: List[Path]
    repo: Optional[Path]
    diff_scope: str
    base_ref: str
    label: str


def parse_argv(argv: List[str]) -> Invocation:
    files: List[Path] = []
    repo_arg: Optional[str] = None
    diff_scope = STAGED
    base_ref = "main"
    rest = argv[1:]
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--repo" and index + 1 < len(rest):
            repo_arg, index = rest[index + 1], index + 2
        elif arg == "--scope" and index + 1 < len(rest):
            diff_scope, index = rest[index + 1], index + 2
        elif arg == "--base" and index + 1 < len(rest):
            base_ref, index = rest[index + 1], index + 2
        else:
            if arg.endswith(".py"):
                files.append(Path(arg))
            index += 1
    if diff_scope not in DIFF_SCOPES:
        diff_scope = STAGED
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def main(argv: List[str]) -> int:
    invocation = parse_argv(argv)
    try:
        return _check(invocation)
    except GitScopeError as exc:
        # A scope the gate could not resolve is not a scope it examined.
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


def _check(invocation: Invocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=python_files_in_scope,
    )
    repo = run.repo
    candidates = run.files
    if not candidates:
        return 0

    additions_map: dict[str, Set[int]] = {}
    numstat_map: Dict[str, Tuple[int, int]] = {}
    untracked: Set[str] = set()
    if repo is not None:
        diff = git_diff_cached(repo, run.diff_scope, run.base_ref)
        additions_map = {
            path: {ln for ln, _ in items} for path, items in parse_staged_additions(diff).items()
        }
        numstat_map = git_diff_numstat(repo, run.diff_scope, run.base_ref)
        if run.scope.includes_untracked:
            untracked = set(git_untracked_paths(repo))

    touched_map: Dict[Path, Optional[Set[int]]] = {}
    all_errors: List[str] = []
    catalogs = build_repo_catalogs(candidates, repo)
    for path in candidates:
        rel = _repo_relative_posix(path, repo)
        touched: Optional[Set[int]] = additions_map.get(rel, set()) if repo is not None else None
        if rel in untracked:
            # Nothing in the diff to scope against: the whole file is new.
            touched = _all_line_numbers(path)
        touched_map[path] = touched
        added, deleted = numstat_map.get(rel, (0, 0))
        net_growing = added > deleted
        all_errors.extend(
            check_file(
                path,
                touched_lines=touched,
                net_growing=net_growing,
                catalogs=catalogs,
            )
        )
    all_errors.extend(codebase_dry_errors(candidates, catalogs.duplicates, touched_map))

    if all_errors:
        print(f"{invocation.label}: Python organization check failed:")
        for err in all_errors:
            print(f" - {err}")
        return 1

    print(f"{invocation.label}: Python organization checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
