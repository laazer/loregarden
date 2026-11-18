"""Static import reading, shared by the suites that assert a package's seams.

`services/code_signals/` and `services/symbol_map/` both ship an import
boundary as an acceptance criterion — the reducers know nothing about a
database, the dispatcher knows nothing about the filesystem, no module in the
package can reach the orchestrator. Both asked the same question of a module's
source, so the walk lives here rather than once per suite.
"""

from __future__ import annotations

import ast


def imported_names(source: str) -> set[str]:
    """Every dotted name `source` imports, from both import forms.

    `import a.b` contributes `a.b`; `from a.b import c` contributes `a.b`.
    A caller testing a boundary matches on the name and on the name plus a
    dot, so a package and its submodules are both caught.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):  # py-org: allow-isinstance - ast node dispatch
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance - ast node dispatch
            if node.module:
                names.add(node.module)
    return names
