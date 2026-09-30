"""Find a module-level assignment in the domain package by name.

Two AC5 guards read ``WORKFLOW_WORK_ITEM_TYPES``'s source to prove it is spelled
out rather than derived from ``WorkItemType``. Both used to open ``enums.py``
directly and started failing the moment the assignment moved to
``work_item_types.py`` (enums.py was at the size cap). Searching the package
keeps the guard pointed at the definition wherever it lives.
"""

from __future__ import annotations

import ast
from pathlib import Path

from loregarden.models import domain as domain_pkg


def domain_assignment_source(name: str) -> str:
    """Return the unparsed right-hand side of ``name = ...`` in the domain package.

    Raises AssertionError when no module assigns it — the guard must fail loudly
    rather than pass over a definition it could not find.
    """
    package_root = Path(domain_pkg.__file__).resolve().parent
    for path in sorted(package_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    assert node.value is not None
                    return ast.unparse(node.value)
    raise AssertionError(f"{name} assignment not found under {package_root}")
