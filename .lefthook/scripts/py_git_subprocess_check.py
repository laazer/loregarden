#!/usr/bin/env python3
"""Keep git subprocess calls routed through the shared env-scrubbing helper.

GIT_DIR overrides `cwd`. A `subprocess.run(["git", ...], cwd=repo)` that passes
the ambient environment through therefore operates on whatever repository the
parent was bound to — not `repo`. That is not hypothetical: it was the root
cause of the pre-push worktree failures, and seven services carried the defect.

`loregarden.services.git_subprocess.run_git` scrubs the repo-binding variables
and is the one place allowed to build a raw git argv. This gate keeps it that
way, because the invariant is otherwise invisible — the broken call looks
exactly like the correct one at the call site.

`gh` is covered too: it resolves the repo by shelling out to git, so it inherits
the same binding.

Escape hatch: a call that passes an explicit `env=` is deliberate about the
child environment and is left alone. That is how `gh` calls pass
(`env=scrubbed_git_env()`), and it keeps the gate from blocking a genuinely
custom environment.

Usage: py_git_subprocess_check.py [staged files...]
"""

import ast
import sys
from pathlib import Path

# Commands that resolve a repository through git's environment.
_GUARDED_COMMANDS: frozenset[str] = frozenset({"git", "gh"})

# subprocess entry points that spawn a child process.
_SPAWN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    }
)

# The helper itself must build a raw git argv — it is the chokepoint.
_HELPER_SUFFIX = ("loregarden", "services", "git_subprocess.py")

_HELP = """
💡 Fix: route it through the shared helper.

    from loregarden.services.git_subprocess import run_git

    run_git(["status", "--porcelain"], cwd=repo, capture_output=True, text=True)

`run_git` is a thin subprocess.run passthrough — check/text/capture_output/timeout
all still mean what they mean — but it strips GIT_DIR, GIT_WORK_TREE and the other
repo-binding variables from the child, so `cwd` actually decides the repository.

Passing an explicit `env=` also satisfies this gate, for calls that are
deliberate about the child environment (e.g. `env=scrubbed_git_env()`).
"""


def _is_exempt(path: Path) -> bool:
    """The helper defines the chokepoint; tests legitimately drive git directly.

    Tests build throwaway repos in tmp_path and assert on real git behaviour —
    including a guard test that requires an *unscrubbed* call to prove GIT_DIR
    really does hijack one. conftest scrubs the ambient env for the suite.
    """
    parts = path.parts
    if "tests" in parts or path.name.startswith("test_"):
        return True
    return parts[-3:] == _HELPER_SUFFIX


def _spawn_call_name(func: ast.expr) -> str | None:
    """Return the subprocess entry point this call targets, if any."""
    # subprocess.run(...) / sp.run(...)
    if isinstance(func, ast.Attribute) and func.attr in _SPAWN_FUNCTIONS:
        return func.attr
    # run(...) after `from subprocess import run`
    if isinstance(func, ast.Name) and func.id in _SPAWN_FUNCTIONS:
        return func.id
    return None


def _guarded_command(node: ast.Call) -> str | None:
    """The guarded command this call spawns, if it is statically knowable.

    Handles the list form (`["git", "status"]`) and the shell-string form
    (`"git status"`). A command built from a variable is not statically
    knowable; those are skipped rather than guessed at, to keep the gate free
    of false positives.
    """
    if not node.args:
        return None

    first = node.args[0]

    if isinstance(first, ast.List) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            name = Path(head.value).name
            return name if name in _GUARDED_COMMANDS else None
        return None

    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        tokens = first.value.split()
        if tokens:
            name = Path(tokens[0]).name
            return name if name in _GUARDED_COMMANDS else None

    return None


def _has_explicit_env(node: ast.Call) -> bool:
    """True when the call decides the child environment itself."""
    return any(kw.arg == "env" for kw in node.keywords)


def violations_in(path: Path) -> list[tuple[int, str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        # Unreadable or mid-edit file: ruff and the test suite will speak up.
        return []

    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spawn = _spawn_call_name(node.func)
        if spawn is None:
            continue
        command = _guarded_command(node)
        if command is None or _has_explicit_env(node):
            continue
        found.append((node.lineno, command, spawn))
    return found


def main(argv: list[str]) -> int:
    failures: list[str] = []

    for raw in argv:
        path = Path(raw)
        if path.suffix != ".py" or _is_exempt(path):
            continue
        for lineno, command, spawn in violations_in(path):
            failures.append(f"   {path}:{lineno}: subprocess.{spawn}([{command!r}, ...]) — no env=")

    if not failures:
        return 0

    print("❌ Unscrubbed git subprocess call (inherits GIT_DIR):")
    print("   GIT_DIR overrides cwd, so this can operate on the wrong repository.")
    print()
    for failure in failures:
        print(failure)
    print(_HELP)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
