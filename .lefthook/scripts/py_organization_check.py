#!/usr/bin/env python3
"""Guardrails for Python code organization on staged files.

All checks are diff-scoped: a violation only fails the commit when it
overlaps lines this commit actually adds/modifies. Pre-existing debt
elsewhere in a touched file is reported nowhere and never blocks — the
guardrail is "don't make it worse", not "fix everything on sight". This
matters because loregarden already carries known debt (long functions,
long files, a few private-symbol imports); see server/pyproject.toml
[tool.pylint] and the py-pylint hook for the same policy applied there.
"""

import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

from precommit_git_diff import (
    git_diff_cached,
    git_diff_numstat,
    git_repo_root,
    parse_staged_additions,
)

MAX_FILE_LINES = 1500
MAX_CLASS_LINES = 1000
MIN_DUPLICATE_BODY_LINES = 8
MAX_INIT_LINES = 120

# Directories that never contribute catalog entries; pruned from the walk so we do not
# descend into them. server/.venv alone holds >1400 vendored .py files.
_CATALOG_PRUNE_DIRS: frozenset[str] = frozenset(
    {".venv", ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".pyinstaller"}
)

_FORBIDDEN_DYNAMIC_ACCESS: frozenset[str] = frozenset({"getattr", "setattr"})


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


def check_file(
    py_file: Path, touched_lines: Optional[Set[int]] = None, net_growing: bool = False
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
    if lines > MAX_FILE_LINES and net_growing:
        errors.append(
            f"{py_file}: module is {lines} lines (max {MAX_FILE_LINES}); split into smaller modules"
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

    duplicate_groups = find_duplicate_function_bodies(tree, content)
    for funcs in duplicate_groups:
        if not any(_span_touched(line, end, touched_lines) for _, line, end in funcs):
            continue
        refs = ", ".join(f"{name}@{line}" for name, line, _ in funcs)
        errors.append(
            f"{py_file}: duplicated function bodies detected ({refs}); extract shared helper to keep DRY"
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


def function_keys_for_file(py_file: Path) -> List[Tuple[Tuple[str, ...], str, int, int]]:
    """Returns (body_key, name, lineno, end_lineno) for eligible functions in a file."""
    if not py_file.exists():
        return []
    try:
        source = py_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return []

    lines = _split_source_lines(source)
    keys: List[Tuple[Tuple[str, ...], str, int, int]] = []
    for node in ast.walk(tree):
        key = function_body_key(node, lines)
        if key is None:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            keys.append((key, node.name, node.lineno, node.end_lineno or node.lineno))
    return keys


def build_codebase_catalog(changed_files: List[Path]) -> Dict[Tuple[str, ...], List[Tuple[str, str, int]]]:
    changed_set = {p.resolve() for p in changed_files if p.exists()}
    catalog: Dict[Tuple[str, ...], List[Tuple[str, str, int]]] = {}
    walk_root = Path("server") if Path("server").is_dir() else Path(".")
    for dirpath, dirnames, filenames in os.walk(walk_root):
        # Prune in place so os.walk does not descend into excluded trees.
        dirnames[:] = [d for d in dirnames if d not in _CATALOG_PRUNE_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            py_file = Path(dirpath) / filename
            resolved = py_file.resolve()
            if resolved in changed_set:
                continue
            for key, func_name, lineno, _end_lineno in function_keys_for_file(py_file):
                catalog.setdefault(key, []).append((py_file.as_posix(), func_name, lineno))
    return catalog


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


def main(argv: List[str]) -> int:
    candidates = [Path(arg) for arg in argv[1:] if arg.endswith(".py")]
    if not candidates:
        return 0

    repo = git_repo_root()
    additions_map: dict[str, Set[int]] = {}
    numstat_map: Dict[str, Tuple[int, int]] = {}
    if repo is not None:
        additions_map = {
            path: {ln for ln, _ in items}
            for path, items in parse_staged_additions(git_diff_cached(repo)).items()
        }
        numstat_map = git_diff_numstat(repo)

    touched_map: Dict[Path, Optional[Set[int]]] = {}
    all_errors: List[str] = []
    codebase_catalog = build_codebase_catalog(candidates)
    for path in candidates:
        rel = _repo_relative_posix(path, repo)
        touched: Optional[Set[int]] = additions_map.get(rel, set()) if repo is not None else None
        touched_map[path] = touched
        added, deleted = numstat_map.get(rel, (0, 0))
        net_growing = added > deleted
        all_errors.extend(check_file(path, touched_lines=touched, net_growing=net_growing))
    all_errors.extend(codebase_dry_errors(candidates, codebase_catalog, touched_map))

    if all_errors:
        print("pre-commit: Python organization check failed:")
        for err in all_errors:
            print(f" - {err}")
        return 1

    print("pre-commit: Python organization checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
