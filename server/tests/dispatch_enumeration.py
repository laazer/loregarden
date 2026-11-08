"""The functions that start or finish a stage, found by reading the source.

Shared by the guards every dispatch must pass (`test_dependency_hold`,
`test_gate_dispatch_check`): each lists the functions it covers, and these
helpers find every function that would need to be on that list, so a new
dispatcher that skips a guard fails the suite instead of shipping.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "loregarden"

#: Every helper that writes a stage status, by the name it is called through.
_STATUS_WRITERS = frozenset({"set_stage_status", "_set_stage_status", "finalize_stage"})

#: Functions that set a stage RUNNING and are not dispatches, with why.
NOT_DISPATCHES: dict[tuple[str, str], str] = {
    ("agents/executors/permission_bridge.py", "_resume_after_approval"): (
        "puts the stage of a run that is already executing back to RUNNING once its "
        "approval is answered; the run was dispatched, and held, through start_run"
    ),
}


def _called_name(call: ast.Call) -> str:
    return getattr(call.func, "attr", getattr(call.func, "id", ""))


def _calls_in(node: ast.AST) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)  # py-org: allow-isinstance - AST node types
    ]


def _functions() -> list[tuple[str, ast.FunctionDef, str]]:
    """Every function in the package: (module path, node, module source)."""
    found: list[tuple[str, ast.FunctionDef, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef):  # py-org: allow-isinstance - AST node types
                found.append((str(path.relative_to(SOURCE_ROOT)), node, source))
    return found


def _sets_stage_running(node: ast.AST) -> bool:
    for call in _calls_in(node):
        if _called_name(call) not in _STATUS_WRITERS:
            continue
        values = [*call.args, *(keyword.value for keyword in call.keywords)]
        if any(getattr(value, "attr", "") == "RUNNING" for value in values):
            return True
    return False


def calls(node: ast.AST, name: str) -> bool:
    """Whether ``node`` calls something named ``name``."""
    return any(_called_name(call) == name for call in _calls_in(node))


def stage_starters() -> set[tuple[str, str]]:
    """Every (module, function) that sets a stage RUNNING."""
    return {
        (module, node.name)
        for module, node, source in _functions()
        if "RUNNING" in source and _sets_stage_running(node)
    }


def callers_of(name: str) -> set[tuple[str, str]]:
    """Every (module, function) that calls something named ``name``."""
    return {
        (module, node.name)
        for module, node, source in _functions()
        if name in source and node.name != name and calls(node, name)
    }


def function_node(module: str, function: str) -> ast.FunctionDef:
    """The definition of ``function`` in ``module`` (relative to the package)."""
    tree = ast.parse((SOURCE_ROOT / module).read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)  # py-org: allow-isinstance - AST node types
        and node.name == function
    )
