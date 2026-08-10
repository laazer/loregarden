#!/usr/bin/env python3
"""Stringly-typed vocabulary guardrails, run as part of the py-organization gate.

loregarden already names its closed vocabularies: ``models/domain/enums.py`` and
``mcp/tool_ids.py`` hold ~100 enum members. The smell this catches is code that
ignores them — ``run.status == "failed"`` two lines under ``r.status ==
QueuePosition.STARTED`` — or invents an untyped vocabulary from scratch.

Four checks, in order of how much they know:

1. ``enum_literal_errors`` — the literal *is* a member value of an enum this file
   can already see (imports or defines). Highest confidence: the type exists and
   the code walked past it.
2. ``closed_set_errors`` — one expression tested against MIN_CLOSED_SET_LITERALS+
   distinct literals inline. A closed set with no name.
3. ``str_vocab_annotation_errors`` — a parameter or return named ``*_status`` /
   ``*_kind`` / ``mode`` / … annotated bare ``str``. The signature is where a
   vocabulary should become a type.
4. ``untyped_vocabulary_errors`` — the same literal used as a vocabulary value at
   MIN_VOCAB_SITES+ places in one module, and no enum anywhere defines it.

Every check is diff-scoped by the caller's ``touched_lines`` (see the gate's
module docstring: "don't make it worse", not "fix everything on sight"), skips
tests, and skips migrations — migration SQL must keep its literals frozen at the
values that were live when it ran, never follow a moving enum.

Escape hatch: ``# py-org: allow-string`` on the reported line. External
vocabularies that collide with ours need it — a GitHub check conclusion of
``"skipped"`` is not ``CIStatus.SKIPPED``, it is someone else's word that happens
to match.
"""

import ast
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Literals that are booleans wearing a costume; an enum is not the fix for these.
_BOOLISH: frozenset[str] = frozenset(
    {"", "0", "1", "true", "false", "yes", "no", "on", "off", "y", "n", "t", "f"}
)

# Below this length a literal is an abbreviation or a punctuation token, not a
# vocabulary member worth a type.
MIN_VOCAB_LITERAL_LEN = 3

# An inline `x in {...}` / `x == a or x == b` reaching this many distinct literals
# is a closed set. Measured against server/: 4 flags 5 sites, all real; 3 starts
# pulling in argument-parsing sets that are already fine as constants.
MIN_CLOSED_SET_LITERALS = 4

# The same literal compared at this many places in one module is a vocabulary,
# whether or not anyone declared one.
MIN_VOCAB_SITES = 3

# Operands that hold free-form text: a literal compared against them is a
# substring or identity check, not a vocabulary test. `name`/`key`/`id` are
# deliberately absent — McpTool proves those are exactly where vocabularies live.
_PROSE_OPERANDS: frozenset[str] = frozenset(
    {
        "text", "content", "message", "body", "logs", "log", "stdout", "stderr",
        "output", "prompt", "description", "raw", "source", "line", "path",
        "file", "filename", "url",
    }
)

# Parameter/function names that carry a vocabulary rather than free text.
_VOCAB_SUFFIXES: Tuple[str, ...] = ("status", "state", "kind", "type", "mode", "phase")

# ...except these, whose vocabulary is defined by a spec we do not own.
_VOCAB_NAME_EXEMPTIONS: frozenset[str] = frozenset(
    {"content_type", "mime_type", "media_type"}
)

_BARE_STR_ANNOTATIONS: frozenset[str] = frozenset(
    {"str", "str|None", "None|str", "Optional[str]"}
)

_ALLOW_COMMENT = "# py-org: allow-string"


def _is_test_path(py_file: Path) -> bool:
    return "tests" in py_file.parts or py_file.name.startswith("test_")


def _is_migration_path(py_file: Path) -> bool:
    return py_file.name.startswith("migrations") and py_file.parent.name == "db"


def is_exempt_path(py_file: Path) -> bool:
    """Tests pin wire values on purpose; migrations must freeze theirs."""
    return _is_test_path(py_file) or _is_migration_path(py_file)


# --------------------------------------------------------------------------- #
# Enum catalog
# --------------------------------------------------------------------------- #


def collect_enum_members(tree: ast.AST, catalog: Dict[str, Dict[str, str]]) -> None:
    """Add every ``str``-valued enum member in ``tree`` to ``catalog``.

    Shape is ``{member_value: {EnumName: "EnumName.MEMBER"}}`` — one value can
    belong to several enums ("failed" belongs to nine here), and the caller needs
    the class names to decide which one the file in hand actually meant.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_is_enum_base(base) for base in node.bases):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            value = stmt.value
            if not isinstance(target, ast.Name):
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            catalog.setdefault(value.value, {})[node.name] = f"{node.name}.{target.id}"


def _is_enum_base(base: ast.expr) -> bool:
    name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
    return name.endswith("Enum")


def _visible_enum_names(tree: ast.AST) -> Set[str]:
    """Enum classes this module can reference by name: imported or defined here."""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


# --------------------------------------------------------------------------- #
# Vocabulary-position literals
# --------------------------------------------------------------------------- #


def _const_str(expr: ast.expr) -> Optional[str]:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _collection_literals(expr: ast.expr) -> List[str]:
    if not isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return []
    return [lit for lit in (_const_str(elt) for elt in expr.elts) if lit is not None]


def _operand_tail(expr: ast.expr) -> Optional[str]:
    """The identifier a comparison is really about: ``run.status`` -> ``status``."""
    if isinstance(expr, ast.Attribute):
        return expr.attr.lower()
    if isinstance(expr, ast.Name):
        return expr.id.lower()
    if isinstance(expr, ast.Call):
        return _operand_tail(expr.func)
    return None


def _match_case_literals(case: ast.match_case) -> List[str]:
    pattern = case.pattern
    patterns = pattern.patterns if isinstance(pattern, ast.MatchOr) else [pattern]
    out: List[str] = []
    for sub in patterns:
        if isinstance(sub, ast.MatchValue):
            lit = _const_str(sub.value)
            if lit is not None:
                out.append(lit)
    return out


def vocabulary_literals(tree: ast.AST) -> Iterable[Tuple[int, str]]:
    """``(lineno, literal)`` for literals used as a closed-vocabulary value.

    Equality against an expression, membership in a literal collection, or a
    ``match`` case. Notably *not* ``"failed" in logs`` — that is a substring
    search, and reading it as a vocabulary test is how this check would start
    flagging log parsing.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            yield from _compare_literals(node)
        elif isinstance(node, ast.match_case):
            for lit in _match_case_literals(node):
                yield node.pattern.lineno, lit


def _compare_literals(node: ast.Compare) -> Iterable[Tuple[int, str]]:
    left = node.left
    for op, comparator in zip(node.ops, node.comparators):
        if isinstance(op, (ast.Eq, ast.NotEq)):
            for lit, other in ((_const_str(comparator), left), (_const_str(left), comparator)):
                if lit is not None and _operand_tail(other) not in _PROSE_OPERANDS:
                    yield node.lineno, lit
        elif isinstance(op, (ast.In, ast.NotIn)) and _operand_tail(left) not in _PROSE_OPERANDS:
            for lit in _collection_literals(comparator):
                yield node.lineno, lit
        left = comparator


def _is_vocabulary_literal(literal: str) -> bool:
    return literal not in _BOOLISH and len(literal) >= MIN_VOCAB_LITERAL_LEN


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def enum_literal_errors(
    py_file: Path,
    tree: ast.AST,
    touched_lines: Optional[Set[int]],
    enum_catalog: Dict[str, Dict[str, str]],
) -> List[Tuple[int, str]]:
    """Literals that duplicate a member of an enum this file already sees."""
    visible = _visible_enum_names(tree)
    errors: List[Tuple[int, str]] = []
    for lineno, literal in vocabulary_literals(tree):
        if not _is_vocabulary_literal(literal) or not _touched(lineno, touched_lines):
            continue
        members = {
            name: member
            for name, member in enum_catalog.get(literal, {}).items()
            if name in visible
        }
        if not members:
            continue
        suggestion = ", ".join(sorted(members.values())[:3])
        errors.append(
            (
                lineno,
                f"{py_file}:{lineno}: compares against the literal {literal!r}, "
                f"which is already {suggestion}; use the enum member, not the string",
            )
        )
    return errors


def _module_constant_lines(tree: ast.AST) -> Set[int]:
    """Lines covered by a module-level ``UPPER_CASE = ...`` binding.

    A set of literals bound to a named constant has already been given a name,
    which is the fix this check asks for.
    """
    lines: Set[int] = set()
    for stmt in getattr(tree, "body", []):
        target: Optional[str] = None
        value: Optional[ast.expr] = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            if isinstance(stmt.targets[0], ast.Name):
                target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target, value = stmt.target.id, stmt.value
        if target is None or value is None or not target.isupper():
            continue
        for node in ast.walk(value):
            lineno = getattr(node, "lineno", None)
            if lineno is not None:
                lines.add(lineno)
    return lines


def _inline_literal_set(node: ast.AST) -> Set[str]:
    literals: Set[str] = set()
    if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
        for comparator in node.comparators:
            literals.update(_collection_literals(comparator))
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            if isinstance(value, ast.Compare) and all(
                isinstance(op, (ast.Eq, ast.NotEq)) for op in value.ops
            ):
                for comparator in value.comparators:
                    lit = _const_str(comparator)
                    if lit is not None:
                        literals.add(lit)
    elif isinstance(node, ast.Match):
        for case in node.cases:
            literals.update(_match_case_literals(case))
    return literals


def closed_set_errors(
    py_file: Path, tree: ast.AST, touched_lines: Optional[Set[int]]
) -> List[Tuple[int, str]]:
    """One expression tested against a whole inline vocabulary."""
    constant_lines = _module_constant_lines(tree)
    errors: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or lineno in constant_lines or not _touched(lineno, touched_lines):
            continue
        literals = _inline_literal_set(node)
        if literals & _BOOLISH or len(literals) < MIN_CLOSED_SET_LITERALS:
            continue
        shown = ", ".join(repr(lit) for lit in sorted(literals)[:4])
        errors.append(
            (
                lineno,
                f"{py_file}:{lineno}: tests one value against {len(literals)} inline "
                f"literals ({shown}, …); name the vocabulary — an enum, or a module "
                f"constant if it is not a domain type",
            )
        )
    return errors


def _is_vocabulary_name(name: str) -> bool:
    lowered = name.lower().rstrip("_")
    if lowered in _VOCAB_NAME_EXEMPTIONS:
        return False
    return any(lowered == suffix or lowered.endswith(f"_{suffix}") for suffix in _VOCAB_SUFFIXES)


def _bare_str_annotation(annotation: Optional[ast.expr]) -> bool:
    if annotation is None:
        return False
    return ast.unparse(annotation).replace(" ", "") in _BARE_STR_ANNOTATIONS


def str_vocab_annotation_errors(
    py_file: Path, tree: ast.AST, touched_lines: Optional[Set[int]]
) -> List[Tuple[int, str]]:
    """Signatures that carry a vocabulary as bare ``str``."""
    errors: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if not _is_vocabulary_name(arg.arg) or not _bare_str_annotation(arg.annotation):
                continue
            if not _touched(arg.lineno, touched_lines):
                continue
            errors.append(
                (
                    arg.lineno,
                    f"{py_file}:{arg.lineno}: `{node.name}` takes `{arg.arg}: str`; a "
                    f"vocabulary parameter needs an enum (or `Literal[...]` for a set "
                    f"this repo does not own)",
                )
            )
        if (
            _is_vocabulary_name(node.name)
            and _bare_str_annotation(node.returns)
            and _touched(node.lineno, touched_lines)
        ):
            errors.append(
                (
                    node.lineno,
                    f"{py_file}:{node.lineno}: `{node.name}` returns bare `str`; return "
                    f"the enum so callers cannot invent a value",
                )
            )
    return errors


def untyped_vocabulary_errors(
    py_file: Path,
    tree: ast.AST,
    touched_lines: Optional[Set[int]],
    enum_catalog: Dict[str, Dict[str, str]],
    enum_home: str = "",
) -> List[Tuple[int, str]]:
    """A literal used as a vocabulary value all over one module, with no enum anywhere."""
    sites: Dict[str, Set[int]] = {}
    for lineno, literal in vocabulary_literals(tree):
        if _is_vocabulary_literal(literal) and literal not in enum_catalog:
            sites.setdefault(literal, set()).add(lineno)

    errors: List[Tuple[int, str]] = []
    for literal, linenos in sorted(sites.items()):
        if len(linenos) < MIN_VOCAB_SITES:
            continue
        if not any(_touched(lineno, touched_lines) for lineno in linenos):
            continue
        first = min(linenos)
        where = ", ".join(str(lineno) for lineno in sorted(linenos)[:5])
        # Point at wherever *this* repo keeps its enums; the gate runs against
        # every workspace the control plane drives, not just loregarden.
        home = f" — add one to {enum_home}" if enum_home else ""
        errors.append(
            (
                first,
                f"{py_file}:{first}: {literal!r} is compared at {len(linenos)} sites "
                f"(lines {where}) and no enum defines it; this vocabulary has no type"
                f"{home}",
            )
        )
    return errors


def _touched(lineno: int, touched_lines: Optional[Set[int]]) -> bool:
    """Matches the gate's ``_span_touched``: no diff information means no finding.

    The hook only knows what is staged when it can reach git. Reporting
    everything in that case would turn a missing repo into a wall of errors.
    """
    return bool(touched_lines) and lineno in touched_lines


def _suppressed(lineno: int, source_lines: Sequence[str]) -> bool:
    if 1 <= lineno <= len(source_lines):
        return _ALLOW_COMMENT in source_lines[lineno - 1]
    return False


def string_vocabulary_errors(
    py_file: Path,
    tree: ast.AST,
    content: str,
    touched_lines: Optional[Set[int]],
    enum_catalog: Dict[str, Dict[str, str]],
    enum_home: str = "",
) -> List[str]:
    """Run every vocabulary check, dropping the ones the author waived."""
    if is_exempt_path(py_file):
        return []
    source_lines = content.splitlines()
    found: List[Tuple[int, str]] = [
        *enum_literal_errors(py_file, tree, touched_lines, enum_catalog),
        *closed_set_errors(py_file, tree, touched_lines),
        *str_vocab_annotation_errors(py_file, tree, touched_lines),
        *untyped_vocabulary_errors(py_file, tree, touched_lines, enum_catalog, enum_home),
    ]
    return [message for lineno, message in found if not _suppressed(lineno, source_lines)]
