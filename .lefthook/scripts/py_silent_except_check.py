#!/usr/bin/env python3
"""Refuse exception handlers that catch everything and say nothing.

A broad handler is not the problem — `except Exception` around an optional
enrichment step is often right. The problem is a broad handler whose body
*discards* the failure: `pass`, `return None`, `return False`, `value = None`.
The call then reports success it never had, and the defect surfaces later as
missing data with no trace back to the throw.

So the gate is narrow on purpose. A handler that logs, re-raises, records the
error on a result, or hands it back to the caller is left alone — the failure is
visible somewhere. Only an inert body is flagged: every statement in it is a
`pass`, a `continue`/`break`, or a return/assignment of a constant or empty
container, with the exception itself unused.

`contextlib.suppress(Exception)` is the same swallow with nicer syntax, so it is
covered too. `suppress(FileNotFoundError)` — a named, expected failure — is not.

Escape hatch: `# py-silent: allow` on the `except` (or `with suppress(...)`)
line, for a swallow that is genuinely correct and explained by a nearby comment
(best-effort cleanup on a path already failing, a cache warm that must not break
the request).

Two invocations, because the same rule has to reach both ways code enters a
workspace:

    # pre-commit (lefthook): explicit staged files, scoped to the index
    py_silent_except_check.py server/loregarden/foo.py …

    # orchestration gate: any workspace, scoped to what an agent just did
    py_silent_except_check.py --repo /path/to/workspace --scope worktree

Both are diff-scoped: a handler only fails when its `except` line is one the
change actually added or modified. Pre-existing swallows elsewhere in a touched
file are left alone — the rule is "don't add another", not "fix the tree on
sight", and a stage that blocks on debt an agent never wrote teaches the agent
to waive by reflex. Gate mode discovers its own file list from the diff,
including untracked files, which are the least-reviewed code in a run.

Nothing here is loregarden-specific: the Python source root is detected per
repo, so every workspace the control plane drives gets the same rule.

Usage: py_silent_except_check.py [files...] [--repo PATH] [--scope SCOPE] [--base REF]
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

from precommit_git_diff import (  # noqa: E402 - sys.path is set up just above
    DIFF_SCOPES,
    STAGED,
    WORKTREE,
    git_changed_paths,
    git_diff_cached,
    git_repo_root,
    git_untracked_paths,
    parse_staged_additions,
)
from py_organization_check import python_source_root  # noqa: E402 - same

# Catching these catches programmer errors too, so a silent body hides anything.
_BROAD_EXCEPTIONS: frozenset[str] = frozenset({"Exception", "BaseException"})

_ALLOW_MARKER = "# py-silent: allow"

_HELP = """
💡 Fix: make the failure visible, or narrow the catch.

    except Exception:                      # ❌ the caller sees success
        return None

    except Exception as exc:               # ✅ still recovers, leaves a trace
        logger.warning("pr status lookup failed for %s: %s", name, exc)
        return None

    except FileNotFoundError:              # ✅ an expected, named failure
        return None

Logging, re-raising, recording the error on the result, or returning something
built from `exc` all satisfy this gate — the point is that something downstream
can tell the difference between "nothing to report" and "nobody looked".

A swallow that is genuinely right (best-effort cleanup on an already-failing
path) waives with `# py-silent: allow` on the `except` line.
"""


def _is_exempt(path: Path) -> bool:
    """Tests assert on failure paths and build deliberate broken states."""
    parts = path.parts
    return "tests" in parts or path.name.startswith("test_")


def _exception_names(node: ast.expr | None) -> list[str]:
    """Names of the exception types a handler or suppress() call catches."""
    if node is None:
        return []
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    names: list[str] = []
    for item in items:
        if isinstance(item, ast.Attribute):
            names.append(item.attr)
        elif isinstance(item, ast.Name):
            names.append(item.id)
    return names


def _is_broad(node: ast.expr | None) -> bool:
    """A bare `except:` or one naming Exception/BaseException."""
    if node is None:
        return True
    return any(name in _BROAD_EXCEPTIONS for name in _exception_names(node))


def _is_inert_value(value: ast.expr | None) -> bool:
    """A constant or empty container — a value carrying nothing about the error."""
    if value is None or isinstance(value, ast.Constant):
        return True
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    return False


def _is_inert_body(body: list[ast.stmt]) -> bool:
    """True when nothing in the body records, reports, or re-raises the failure."""
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(stmt, ast.Return) and _is_inert_value(stmt.value):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # a docstring or bare `...`
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and _is_inert_value(stmt.value):
            continue
        return False
    return True


def _is_suppress_call(node: ast.expr) -> ast.Call | None:
    """The `contextlib.suppress(...)` call this context manager expression is."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return node if name == "suppress" else None


def _line_waives(lines: list[str], lineno: int) -> bool:
    if 1 <= lineno <= len(lines):
        return _ALLOW_MARKER in lines[lineno - 1]
    return False


def violations_in(path: Path) -> list[tuple[int, str]]:
    """(line, what-was-caught) for every silent broad catch in the file."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        # Unreadable or mid-edit file: ruff and the test suite will speak up.
        return []

    lines = source.splitlines()
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if not _is_broad(node.type) or not _is_inert_body(node.body):
                continue
            caught = ", ".join(_exception_names(node.type)) or "bare except"
            lineno = node.lineno
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            lineno, caught = 0, ""
            for item in node.items:
                call = _is_suppress_call(item.context_expr)
                if call is None:
                    continue
                broad = [
                    n
                    for n in _exception_names(ast.Tuple(elts=list(call.args)))
                    if n in _BROAD_EXCEPTIONS
                ]
                if broad:
                    lineno, caught = call.lineno, f"suppress({', '.join(broad)})"
                    break
            if not lineno:
                continue
        else:
            continue

        if _line_waives(lines, lineno):
            continue
        found.append((lineno, caught))

    return sorted(found)


@dataclass(frozen=True)
class Invocation:
    """How this run was asked to scope itself.

    Two callers: lefthook passes staged file paths and nothing else; an
    orchestration gate passes ``--repo``/``--scope`` and no file list, because it
    is judging whatever an agent just did to a workspace it does not enumerate.
    """

    files: list[Path]
    repo: Path | None
    diff_scope: str
    base_ref: str
    label: str


def parse_argv(argv: list[str]) -> Invocation:
    files: list[Path] = []
    repo_arg: str | None = None
    diff_scope = STAGED
    base_ref = "main"
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--repo" and index + 1 < len(argv):
            repo_arg, index = argv[index + 1], index + 2
        elif arg == "--scope" and index + 1 < len(argv):
            diff_scope, index = argv[index + 1], index + 2
        elif arg == "--base" and index + 1 < len(argv):
            base_ref, index = argv[index + 1], index + 2
        else:
            if arg.endswith(".py"):
                files.append(Path(arg))
            index += 1
    if diff_scope not in DIFF_SCOPES:
        diff_scope = STAGED
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def _gate_candidates(invocation: Invocation, repo: Path) -> list[Path]:
    """Changed Python files under the repo's own source root.

    Mirrors the lefthook glob: without it the gate grades build tooling and
    AST-walking scripts — this file included — by rules written for application
    code.
    """
    source_root = python_source_root(repo).resolve()
    changed = (
        repo / rel
        for rel in git_changed_paths(repo, invocation.diff_scope, invocation.base_ref)
        if rel.endswith(".py")
    )
    return [path for path in changed if source_root in path.resolve().parents]


def _all_line_numbers(path: Path) -> set[int]:
    try:
        return set(range(1, len(path.read_text(encoding="utf-8").splitlines()) + 1))
    except (OSError, UnicodeDecodeError):
        return set()


def _repo_relative_posix(path: Path, repo: Path | None) -> str:
    if repo is None:
        return path.as_posix()
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str]) -> int:
    invocation = parse_argv(argv)
    repo = invocation.repo
    candidates = invocation.files
    if not candidates and repo is not None:
        # Gate mode: no explicit file list, so the diff itself says what to read.
        candidates = _gate_candidates(invocation, repo)
    candidates = [path for path in candidates if path.suffix == ".py" and not _is_exempt(path)]
    if not candidates:
        return 0

    additions_map: dict[str, set[int]] = {}
    untracked: set[str] = set()
    if repo is not None:
        diff = git_diff_cached(repo, invocation.diff_scope, invocation.base_ref)
        additions_map = {
            path: {ln for ln, _ in items} for path, items in parse_staged_additions(diff).items()
        }
        if invocation.diff_scope == WORKTREE:
            untracked = set(git_untracked_paths(repo))

    failures: list[str] = []
    for path in candidates:
        rel = _repo_relative_posix(path, repo)
        touched: set[int] | None = additions_map.get(rel, set()) if repo is not None else None
        if rel in untracked:
            # Nothing in the diff to scope against: the whole file is new.
            touched = _all_line_numbers(path)
        for lineno, caught in violations_in(path):
            if touched is not None and lineno not in touched:
                continue
            failures.append(
                f"   {path}:{lineno}: `{caught}` with nothing logged, raised, or recorded"
            )

    if not failures:
        if invocation.label == "gate":
            # A stage transition should log that the check ran, not just that it
            # did not fail — a gate that prints nothing reads like a gate that
            # never executed.
            print("gate: silent-exception check passed.")
        return 0

    print(f"{invocation.label}: ❌ Silently caught exception:")
    print("   A broad catch with an inert body reports success the code never had.")
    print()
    for failure in failures:
        print(failure)
    print(_HELP)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
