"""Python extraction over the stdlib `ast` (lg-code-knowledge-281).

The bytes go to `ast.parse` rather than a decoded string: `ast` honours PEP
263 coding declarations and skips a UTF-8 BOM, and a decode to `str` guesses
UTF-8 and reports a real latin-1 module — or any module with a BOM — as
unparseable.

Four shapes are recorded, which is what the ticket names: classes,
module-level functions, methods inside a class body, and module-level
constants. `ast` is walked ONE level into a class body, so a function nested
inside a function is not recorded and neither is a class-level assignment; a
closure is none of the four kinds, and a caller asking where something is
defined does not mean a local.
"""

from __future__ import annotations

import ast

from loregarden.models.domain.symbol_map_enums import SymbolKind
from loregarden.services.symbol_map.values import ExtractedSymbol

#: Both function node types, matched by exact type rather than `isinstance`:
#: `ast` nodes are final, and an exact match cannot widen by accident.
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

_SUFFIXES = (".py", ".pyi")


def module_path(path: str) -> str:
    """A repo-relative path as the dotted module path 481 v4 section 2 names.

    Separators become dots, the extension is dropped, and a trailing
    `.__init__` goes with it, so `pkg/__init__.py` is `pkg` and not
    `pkg.__init__`.
    """
    stem = path
    for suffix in _SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    dotted = stem.replace("/", ".").replace("\\", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return dotted


def _constant_name(node: ast.stmt) -> str:
    """The bound name of a module-level assignment, or "" if it is not one.

    A single `Name` target only. Tuple unpacking binds several names at one
    line and attribute targets bind nothing new, so neither is a definition
    site a caller can be sent to.
    """
    if type(node) is ast.AnnAssign and type(node.target) is ast.Name:
        return node.target.id
    if type(node) is ast.Assign and len(node.targets) == 1:
        target = node.targets[0]
        if type(target) is ast.Name:
            return target.id
    return ""


def python_symbols(path: str, data: bytes) -> list[ExtractedSymbol]:
    """Every class, function, method and module constant in *data*.

    Raises `SyntaxError` or `ValueError` for source this interpreter cannot
    parse; `extract` turns that into a recorded status.
    """
    tree = ast.parse(data)
    module = module_path(path)
    found: list[ExtractedSymbol] = []
    for node in tree.body:
        if type(node) is ast.ClassDef:
            found.append(
                ExtractedSymbol(
                    name=node.name,
                    qualified_name=f"{module}.{node.name}",
                    kind=SymbolKind.CLASS,
                    # `lineno` is the `class`/`def` line and never a decorator
                    # above it; a line taken from the node's source segment or
                    # from `decorator_list[0]` is off by as many lines as the
                    # definition carries decorators.
                    line=node.lineno,
                )
            )
            for child in node.body:
                if type(child) in _FUNCTION_NODES:
                    found.append(
                        ExtractedSymbol(
                            name=child.name,
                            qualified_name=f"{module}.{node.name}.{child.name}",
                            kind=SymbolKind.METHOD,
                            line=child.lineno,
                            parent=node.name,
                        )
                    )
        elif type(node) in _FUNCTION_NODES:
            found.append(
                ExtractedSymbol(
                    name=node.name,
                    qualified_name=f"{module}.{node.name}",
                    kind=SymbolKind.FUNCTION,
                    line=node.lineno,
                )
            )
        else:
            name = _constant_name(node)
            if name:
                found.append(
                    ExtractedSymbol(
                        name=name,
                        qualified_name=f"{module}.{name}",
                        kind=SymbolKind.CONSTANT,
                        line=node.lineno,
                    )
                )
    return found
