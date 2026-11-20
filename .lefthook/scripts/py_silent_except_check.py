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
from collections.abc import Sequence
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# Before any sibling import, and before this module's own annotations are
# evaluated: the gates need 3.11, and on an older interpreter they raise at
# import time and exit 1 — indistinguishable from a finding. Refuse with
# EX_UNAVAILABLE instead, so the runner reports "could not run" (657).
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from gate_unclassified import (  # noqa: E402 - sys.path is set up just above
    Outcome,
    Unclassified,
    unclassified_message,
)
from precommit_git_diff import (  # noqa: E402 - same
    GateInvocation,
    UnexaminableError,
    UnexaminableFileError,
    parse_gate_argv,
    parse_python_source,
    read_source_text,
    resolve_gate_scope,
)
from py_organization_check import python_files_in_scope  # noqa: E402 - same

#: Where the handler-body walk this gate's classifier rides on is wired. The one
#: field a producer cannot answer about itself, so it is written once, here,
#: rather than at each place a finding is built.
_REGISTERED_AT = "py_silent_except_check.violations_in"
_INFERRED_BY = "py_silent_except_check._paths_through"

# Catching these catches programmer errors too, so a silent body hides anything.
_BROAD_EXCEPTIONS: frozenset[str] = frozenset({"Exception", "BaseException"})

_ALLOW_MARKER = "# py-silent: allow"
#: `# silent-ok:` is the spelling shared with the TypeScript gate, so one idiom
#: covers both languages. Both are accepted; both need an argument.
_ALLOW_MARKERS: tuple[str, ...] = (_ALLOW_MARKER, "# silent-ok:")

#: A marker with nothing after it is a reflex, not a decision — and a reviewer
#: cannot verify a reason that was never written. The gate can only check that
#: one exists and is substantive; checking that it is *true* is the reviewer's job.
_MIN_WAIVER_REASON_CHARS = 12

#: debug/info sit below the default handler level, so a failure reported there is
#: reported nowhere. warning and above is what an operator actually sees.
_OBSERVED_LOG_LEVELS: frozenset[str] = frozenset(
    {"warning", "warn", "error", "exception", "critical", "fatal", "log"}
)
_QUIET_LOG_LEVELS: frozenset[str] = frozenset({"debug", "info"})

#: Names that push a failure somewhere a human will find it.
_SURFACING_CALLS: frozenset[str] = frozenset(
    {"emit_error", "emit_execution_update", "print", "print_exc", "capture_exception"}
)

#: Helpers that spawn a child process. `run`/`Popen` count only when qualified by
#: the subprocess module — `uvicorn.run(...)` is not a child process.
_PROCESS_HELPERS: frozenset[str] = frozenset({"run_git", "run_gh"})
_SUBPROCESS_SPAWNERS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)
_SUBPROCESS_MODULES: frozenset[str] = frozenset({"subprocess", "sp"})

#: Values a handler can return that the caller cannot tell from success.
_SUCCESS_SHAPED_CONSTANTS: tuple[object, ...] = (None, False, 0, "")

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

A swallow that is genuinely right waives on the `except` line — but the marker
alone waives nothing, because a reviewer cannot verify a reason nobody wrote:

    except OSError:  # py-silent: allow - cleanup on an already-failing path
    except OSError:  # silent-ok: best-effort warm; the next read repopulates

Both spellings work (`silent-ok:` is shared with the TypeScript gate), on the
handler line or in the comment block directly above it. The reason must say why
the failure is safe to lose: transient, retryable, or expected with a real
alternate path the user is still told about.
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
        if _statement_outcome(stmt) is Outcome.UNCLASSIFIED:
            # A construct no arm names still has to be answered here, or
            # `except Exception: match x: case _: pass` reads as a body that
            # reports something because this list has never met a `match`.
            # Vacuity is the trap: a statement-owner whose blocks could not be
            # read is NOT inert, rather than inert for want of evidence.
            blocks = _owned_blocks(stmt)
            if blocks and all(_is_inert_body(block) for block in blocks):
                continue
            return False
        return False
    return True


def _is_suppress_call(node: ast.expr) -> ast.Call | None:
    """The `contextlib.suppress(...)` call this context manager expression is."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return node if name == "suppress" else None


def _waiver_reason(line: str) -> str | None:
    """The justification on this line, if it carries either marker."""
    for marker in _ALLOW_MARKERS:
        if marker in line:
            return line.split(marker, 1)[1].lstrip(" -:\t").strip()
    return None


def _waiver_start(lines: list[str], lineno: int) -> int:
    """Walk up over the comment block above, where a multi-line reason lives."""
    first = lineno
    while first > 1 and lines[first - 2].strip().startswith("#"):
        first -= 1
    return first


def _line_waives(lines: list[str], lineno: int, end_lineno: int | None = None) -> bool:
    """True when a substantive reason is written on or just above the construct."""
    last = min(end_lineno or lineno, len(lines))
    for i in range(_waiver_start(lines, lineno), last + 1):
        reason = _waiver_reason(lines[i - 1])
        if reason is not None and len(reason) >= _MIN_WAIVER_REASON_CHARS:
            return True
    return False


def _thin_waiver_line(lines: list[str], lineno: int, end_lineno: int | None = None) -> int | None:
    """Where a marker was written with no real reason behind it."""
    last = min(end_lineno or lineno, len(lines))
    for i in range(_waiver_start(lines, lineno), last + 1):
        reason = _waiver_reason(lines[i - 1])
        if reason is not None and len(reason) < _MIN_WAIVER_REASON_CHARS:
            return i
    return None


# The checks below walk `ast`, whose node types are a closed, foreign hierarchy
# with no protocol to dispatch on — `isinstance` is the only way to read them,
# which is why each of these lines carries the organization gate's waiver.
def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):  # py-org: allow-isinstance
        return func.attr
    if isinstance(func, ast.Name):  # py-org: allow-isinstance
        return func.id
    return None


def _spawns_process(node: ast.Call) -> bool:
    """A call that runs a child process whose exit code could go unread."""
    name = _call_name(node)
    if name is None:
        return False
    if name in _PROCESS_HELPERS:
        return True
    if name not in _SUBPROCESS_SPAWNERS:
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)  # py-org: allow-isinstance
        and isinstance(func.value, ast.Name)  # py-org: allow-isinstance
        and func.value.id in _SUBPROCESS_MODULES
    )


def _annotation_is_optional(annotation: ast.expr | None) -> bool:
    """Whether a signature already says this call can fail to produce a value."""
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant):  # py-org: allow-isinstance
        if annotation.value is None:
            return True
        if isinstance(annotation.value, str):  # py-org: allow-isinstance
            try:
                return _annotation_is_optional(ast.parse(annotation.value, mode="eval").body)
            except SyntaxError:
                # silent-ok: an unparseable annotation is mypy's finding, not this
                # gate's; treating it as non-Optional only widens what we grade
                return False
        return False
    if isinstance(annotation, ast.BinOp) and isinstance(  # py-org: allow-isinstance
        annotation.op, ast.BitOr
    ):  # py-org: allow-isinstance
        return any(
            isinstance(side, ast.Constant) and side.value is None  # py-org: allow-isinstance
            for side in (annotation.left, annotation.right)
        )
    if isinstance(annotation, ast.Subscript):  # py-org: allow-isinstance
        base = annotation.value
        if isinstance(base, ast.Attribute):  # py-org: allow-isinstance
            return base.attr == "Optional"
        if isinstance(base, ast.Name):  # py-org: allow-isinstance
            return base.id == "Optional"
        return False
    return False


def _handler_owners(tree: ast.AST) -> dict[int, ast.AST]:
    """Map each handler to the innermost function containing it."""
    owners: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):  # py-org: allow-isinstance
            for inner in ast.walk(node):
                if isinstance(inner, ast.ExceptHandler):  # py-org: allow-isinstance
                    # ast.walk is breadth-first, so the innermost def writes last.
                    owners[id(inner)] = node
    return owners


_CONTROL_FLOW_STMTS = (ast.If, ast.Try, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)
#: The arms that end a path: it leaves loudly, or it terminates.
_TERMINATING_STMTS = (ast.Raise, ast.Return, ast.Break, ast.Continue)
_SCOPE_STMTS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _statement_observes(stmt: ast.stmt) -> bool:
    """Whether this one statement reports the failure at a level someone sees.

    Nested defs are skipped: a logger call inside a closure declared in the
    handler runs when that closure is called, which may be never.
    """
    if isinstance(stmt, _SCOPE_STMTS):  # py-org: allow-isinstance
        return False
    for node in ast.walk(stmt):
        if isinstance(node, _SCOPE_STMTS) and node is not stmt:  # py-org: allow-isinstance
            continue
        if not isinstance(node, ast.Call):  # py-org: allow-isinstance
            continue
        name = _call_name(node)
        if name in _OBSERVED_LOG_LEVELS or name in _SURFACING_CALLS:
            return True
    return False


def _nested_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """The statement lists a compound statement can run, named explicitly.

    Spelled out per node type rather than reached for with `getattr`: the shapes
    are fixed by the grammar, and a typo'd attribute name would silently return
    nothing — a path this analysis then reports as observed.
    """
    blocks: list[list[ast.stmt]] = []
    if isinstance(stmt, ast.Try):  # py-org: allow-isinstance
        blocks.extend([stmt.body, stmt.orelse, stmt.finalbody])
        blocks.extend(nested.body for nested in stmt.handlers)
    elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):  # py-org: allow-isinstance
        blocks.extend([stmt.body, stmt.orelse])
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):  # py-org: allow-isinstance
        blocks.append(stmt.body)
    return [block for block in blocks if block]


def _owns_statements(stmt: ast.stmt) -> bool:
    """Whether this statement runs statements of its own.

    Read off the tree rather than from a list of node types, so a compound
    statement a later Python adds is recognized as a subject instead of being
    read as a plain statement — which is the defect this answers, one version on.

    Deliberately not `"body" in type(stmt)._fields`: that finds `TryStar` and
    MISSES `Match`, whose fields are `(subject, cases)` with the statements
    hanging off `match_case.body`. Helper nodes (`match_case`, `ExceptHandler`)
    are looked through, one level, which is as deep as the grammar puts a
    statement list beneath a statement.
    """
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.stmt):  # py-org: allow-isinstance
            return True
        if isinstance(child, ast.expr):  # py-org: allow-isinstance
            # A lambda body or a comprehension is an expression, not a path
            # through the handler.
            continue
        if any(
            isinstance(inner, ast.stmt)  # py-org: allow-isinstance
            for inner in ast.iter_child_nodes(child)
        ):
            return True
    return False


def _statement_outcome(stmt: ast.stmt) -> Outcome:
    """Which of the three answers the handler-body walk has for this statement.

    The arm table of `_paths_through`, read as a classification rather than as
    control flow, so the case it has no arm for has a name:

    * CLASSIFIED — `Raise`, `Return`/`Break`/`Continue`, `If`, and the rest of
      `_CONTROL_FLOW_STMTS`: a named arm fires and says which bodies the path
      can enter.
    * NOT_APPLICABLE — a nested `def`/`class` (declared on this path, run on
      another, which is why `_statement_observes` already skips it), and a plain
      statement, which owns no statements for the walk to miss.
    * UNCLASSIFIED — it owns statements of its own and no arm names it. On
      CPython 3.11 that is exactly `TryStar` and `Match`.
    """
    if isinstance(stmt, _SCOPE_STMTS):  # py-org: allow-isinstance
        return Outcome.NOT_APPLICABLE
    if isinstance(stmt, _TERMINATING_STMTS):  # py-org: allow-isinstance
        return Outcome.CLASSIFIED
    if isinstance(stmt, _CONTROL_FLOW_STMTS):  # py-org: allow-isinstance
        return Outcome.CLASSIFIED
    if _owns_statements(stmt):
        return Outcome.UNCLASSIFIED
    return Outcome.NOT_APPLICABLE


def _owned_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Every statement list an UNCLASSIFIED statement can run.

    `_nested_blocks` names the blocks of the control-flow arms explicitly and
    deliberately. This is the other half: the blocks of a statement no arm
    names, read off the tree, so a construct the gate cannot grade is not also
    read as reporting the failure. A statement-owner this returns nothing for is
    treated as NOT inert by the caller rather than vacuously inert.
    """
    blocks: list[list[ast.stmt]] = []
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.expr):  # py-org: allow-isinstance
            continue
        if isinstance(child, ast.stmt):  # py-org: allow-isinstance
            blocks.append([child])
            continue
        nested = [
            inner
            for inner in ast.iter_child_nodes(child)
            if isinstance(inner, ast.stmt)  # py-org: allow-isinstance
        ]
        if nested:
            blocks.append(nested)
    return blocks


def _unclassified_subjects(body: list[ast.stmt]) -> list[ast.stmt]:
    """The statements this walk reaches that `_statement_outcome` cannot grade.

    Exactly as wide as the walk it rides on, and no wider: the same bodies, and
    the same `_nested_blocks`. It does not descend into a nested `def` or
    `class` (whose statements do not run on the handler's path) nor into the
    `try` the handler guards, so a construct the path analysis never reads is
    NOT_APPLICABLE and produces nothing.
    """
    found: list[ast.stmt] = []
    for stmt in body:
        outcome = _statement_outcome(stmt)
        if outcome is Outcome.UNCLASSIFIED:
            found.append(stmt)
            continue
        if outcome is Outcome.NOT_APPLICABLE:
            continue
        if isinstance(stmt, ast.If):  # py-org: allow-isinstance
            blocks = [branch for branch in (stmt.body, stmt.orelse) if branch]
        else:
            blocks = _nested_blocks(stmt)
        for block in blocks:
            found.extend(_unclassified_subjects(block))
    return found


def _paths_through(body: list[ast.stmt], observed: bool) -> tuple[bool, set[bool]]:
    """Walk a statement list. Returns (a quiet exit was found, states falling through).

    `observed` is whether the failure has already been reported on this path. The
    point of tracking it per-path rather than per-handler is that a conditional
    `raise` only covers the branch that takes it: a handler that re-raises when
    the error is fatal still returns quietly for every other error, and that
    second path is the one nobody sees.
    """
    quiet = False
    states = {observed}
    for stmt in body:
        if not states:
            break  # everything before this terminated; the rest is unreachable
        nxt: set[bool] = set()
        for state in states:
            if isinstance(stmt, ast.Raise):  # py-org: allow-isinstance
                continue  # this path leaves loudly and never falls through
            if isinstance(stmt, (ast.Return, ast.Break, ast.Continue)):  # py-org: allow-isinstance
                quiet = quiet or not state
                continue  # terminates the path either way
            if isinstance(stmt, ast.If):  # py-org: allow-isinstance
                for branch in (stmt.body, stmt.orelse):
                    if branch:
                        found, out = _paths_through(branch, state)
                        quiet = quiet or found
                        nxt |= out
                    else:
                        nxt.add(state)  # the missing else falls straight through
                continue
            if isinstance(stmt, _CONTROL_FLOW_STMTS):  # py-org: allow-isinstance
                # Loops may not run and handlers may not fire, so the surrounding
                # path survives alongside whatever the nested bodies do.
                nxt.add(state)
                for block in _nested_blocks(stmt):
                    found, out = _paths_through(block, state)
                    quiet = quiet or found
                    nxt |= out
                continue
            nxt.add(state or _statement_observes(stmt))
        states = nxt
    return quiet, states


def _has_quiet_exit(handler: ast.ExceptHandler) -> bool:
    """Some way out of this handler reports nothing at warning level or above."""
    quiet, falling = _paths_through(handler.body, False)
    # Falling off the end of the handler is an exit too.
    return quiet or (False in falling)


def _quiet_only(handler: ast.ExceptHandler) -> bool:
    """Some exit from this handler accounts for the failure only at debug/info."""
    has_quiet_log = any(
        _call_name(node) in _QUIET_LOG_LEVELS
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)  # py-org: allow-isinstance
    )
    return has_quiet_log and _has_quiet_exit(handler)


def _is_success_shaped_value(value: ast.expr | None) -> bool:
    """A returned value the caller cannot tell from a real, successful answer.

    Narrower than `_is_inert_value`: `return "unavailable"` carries something,
    `return ""` does not.
    """
    if value is None:
        return True
    if isinstance(value, ast.Constant):  # py-org: allow-isinstance
        return value.value in _SUCCESS_SHAPED_CONSTANTS
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):  # py-org: allow-isinstance
        return not value.elts
    if isinstance(value, ast.Dict):  # py-org: allow-isinstance
        return not value.keys
    return False


def _returns_success_shape(handler: ast.ExceptHandler, owner: ast.AST | None) -> bool:
    """The handler hands back a value the caller cannot tell from a real answer.

    An Optional return type is exempt: `-> Action | None` returning None is the
    documented contract, and a type checker forces every caller to handle it.
    That is the difference between it and `-> list[str]` returning `[]` on a
    corrupt file, where an empty answer and a broken one are the same value.
    """
    returns = [
        n
        for n in ast.walk(handler)
        if isinstance(n, ast.Return)  # py-org: allow-isinstance
    ]  # py-org: allow-isinstance
    if not returns:
        return False
    # Path-aware for the same reason as _quiet_only: a `raise` on one branch
    # says nothing about the branch that returns instead.
    if not _has_quiet_exit(handler):
        return False
    # Only a reference inside the *returned value* means the error reached the
    # caller. `if fatal(exc): raise` reads the exception to make a decision and
    # then returns the empty answer anyway, which is the shape this catches.
    if handler.name and any(
        node.id == handler.name
        for ret in returns
        for node in ast.walk(ret)
        if isinstance(node, ast.Name)  # py-org: allow-isinstance
    ):
        return False  # the exception itself is carried into the result
    inert = [n for n in returns if _is_success_shaped_value(n.value)]
    if len(inert) != len(returns):
        return False
    # Only claim this when the signature proves the caller cannot tell. An
    # unannotated `except FileNotFoundError: return None` is an expected, named
    # failure — the case this gate has always deliberately left alone — and an
    # annotated `-> X | None` says so in the type. What is left is the shape that
    # actually lies: `-> list[str]` returning `[]`, `-> bool` returning False.
    if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):  # py-org: allow-isinstance
        return False
    if owner.returns is None or _annotation_is_optional(owner.returns):
        return False
    return True


def _unclassified_violations(node: ast.ExceptHandler, path: Path) -> list[tuple[int, str]]:
    """The unclassified-construct findings for one handler's body, if any."""
    return [
        (
            # Anchored at the handler's own lineno — the anchor the
            # touched-line filter in `_check` already accepts — while the
            # message names the construct's own site. Anchoring at the
            # construct would silently change what this gate examines.
            node.lineno,
            unclassified_message(
                Unclassified(
                    construct=f"ast.{type(subject).__name__}",
                    path=str(path),
                    line=subject.lineno,
                    inferred_by=_INFERRED_BY,
                    registered_at=_REGISTERED_AT,
                )
            ),
        )
        for subject in _unclassified_subjects(node.body)
    ]


def _swallow_violation(node: ast.ExceptHandler, owner: ast.AST | None) -> tuple[int, str] | None:
    """The one swallow finding for a handler's own shape, if it has one."""
    names = ", ".join(_exception_names(node.type)) or "bare except"
    if _is_broad(node.type) and _is_inert_body(node.body):
        return (node.lineno, f"`{names}` with nothing logged, raised, or recorded")
    if _quiet_only(node):
        return (
            node.lineno,
            f"`{names}` reported only at debug/info, which is below the default "
            "handler level; log it at warning or above, or surface it",
        )
    if _returns_success_shape(node, owner):
        return (
            node.lineno,
            f"`{names}` returns a value indistinguishable from success; return a "
            "failure-carrying result (ok=False / error=…), raise, or log at warning+",
        )
    return None


def _violations_for_handler(
    node: ast.ExceptHandler,
    lines: list[str],
    owners: dict[int, ast.AST | None],
    path: Path,
) -> list[tuple[int, str]]:
    """Every finding anchored on one `except` handler, in one place."""
    end = node.end_lineno or node.lineno
    thin = _thin_waiver_line(lines, node.lineno, end)
    if thin is not None:
        return [
            (
                thin,
                "a waiver marker with no real reason; say why the failure is safe to "
                "swallow — transient, retryable, or expected with a surfaced "
                f"alternate path — in at least {_MIN_WAIVER_REASON_CHARS} characters",
            )
        ]
    if _line_waives(lines, node.lineno, end):
        return []

    found = _unclassified_violations(node, path)
    swallow = _swallow_violation(node, owners.get(id(node)))
    if swallow is not None:
        found.append(swallow)
    return found


def _violation_for_spawn_call(node: ast.Expr, lines: list[str]) -> tuple[int, str] | None:
    """The discarded-exit-code finding for one bare call expression, if any."""
    call = node.value
    if not isinstance(call, ast.Call) or not _spawns_process(call):  # py-org: allow-isinstance
        return None
    checked = any(
        kw.arg == "check"
        and isinstance(kw.value, ast.Constant)  # py-org: allow-isinstance
        and kw.value.value is True  # py-org: allow-isinstance
        for kw in call.keywords
    )
    if checked:
        return None
    end = node.end_lineno or node.lineno
    if _line_waives(lines, node.lineno, end):
        return None
    return (
        node.lineno,
        "a process result discarded with check=True not set, so a non-zero exit "
        "passes unnoticed; keep the result and test returncode, or pass check=True",
    )


def _violation_for_suppress_with(
    node: ast.With | ast.AsyncWith, lines: list[str]
) -> tuple[int, str] | None:
    """The broad-suppress finding for one `with` statement, if it has one."""
    lineno, caught = 0, ""
    for item in node.items:
        call = _is_suppress_call(item.context_expr)
        if call is None:
            continue
        broad = [
            n for n in _exception_names(ast.Tuple(elts=list(call.args))) if n in _BROAD_EXCEPTIONS
        ]
        if broad:
            lineno, caught = call.lineno, f"suppress({', '.join(broad)})"
            break
    if not lineno:
        return None
    if _line_waives(lines, lineno):
        return None
    return (lineno, f"`{caught}` with nothing logged, raised, or recorded")


def violations_in(path: Path, *, repo: Path | None) -> list[tuple[int, str]]:
    """(line, what-was-caught) for every silent broad catch in the file.

    ``repo`` is the boundary `read_source_text` grades against, required and
    keyword-only for the same reason it is there: a default would let this read
    site drop the check while the other one kept it.
    """
    source = read_source_text(path, repo=repo)
    try:
        tree = parse_python_source(source, path)
    except SyntaxError as exc:
        # "Ruff will speak up" was an assumption about a *different* run. This
        # one graded the file and found nothing, which is not the same as the
        # file being clean, so it says so instead of returning an empty list.
        raise UnexaminableFileError(
            f"{path}:{exc.lineno}: this run could not parse it, so it cannot be "
            f"reported clean ({exc.msg})"
        ) from exc

    lines = source.splitlines()
    found: list[tuple[int, str]] = []
    owners = _handler_owners(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):  # py-org: allow-isinstance
            found.extend(_violations_for_handler(node, lines, owners, path))
        elif isinstance(node, ast.Expr) and isinstance(  # py-org: allow-isinstance
            node.value, ast.Call
        ):  # py-org: allow-isinstance
            violation = _violation_for_spawn_call(node, lines)
            if violation is not None:
                found.append(violation)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            violation = _violation_for_suppress_with(node, lines)
            if violation is not None:
                found.append(violation)

    return sorted(found)


def _graded_files(repo: Path | None, candidates: Sequence[Path], discovered: bool) -> list[Path]:
    """The Python files in scope that this gate has an opinion about.

    The language and source-root filtering is shared with the organization
    gate; the exemptions are this gate's own.
    """
    in_scope = python_files_in_scope(repo, candidates, discovered)
    return [path for path in in_scope if not _is_exempt(path)]


def main(argv: list[str]) -> int:
    invocation = parse_gate_argv(argv)
    try:
        return _check(invocation)
    except UnexaminableError as exc:
        # One handler for the one invariant: a scope this run could not resolve,
        # and a file it could not read, are both things it did not examine.
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


def _check(invocation: GateInvocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=_graded_files,
    )
    candidates = run.files
    if not candidates:
        return 0

    failures: list[str] = []
    for path in candidates:
        touched = run.touched_lines(path)
        for lineno, caught in violations_in(path, repo=run.repo):
            if touched is not None and lineno not in touched:
                continue
            failures.append(f"   {path}:{lineno}: {caught}")

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
