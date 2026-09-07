"""An absolute bound on a measured duration is a flake waiting for a slow machine.

`lg-workflow-integrity-668` was filed after a suite that had passed for months
failed wholesale on an unrelated branch. Its clock dimension was first attacked
by swapping `datetime.datetime` for a drifting subclass; that produced 48
"findings", every one an artifact of the instrument, because a subclass swap
cannot reach a module that already did `from datetime import datetime`. That
negative result is recorded on the ticket.

This is the replacement, and it targets a shape rather than simulating time.
`test_elapsed_ms_measures_the_briefing_and_not_the_telemetry_write` asserted
`elapsed_ms < 250`. The property it owned was that an injected 400ms write delay
was *excluded* from the figure; the absolute bound additionally asserted the
briefing itself was fast, which no criterion claimed. CI failed it at 364ms with
the exclusion working correctly.

So: find every assertion that bounds a measured duration from above by a bare
literal, and require each to be either fixed or named here with its reason.
Verified against the known defect — run over the pre-fix revision of that file,
the scan reports exactly that line.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).parent

#: Identifier fragments that mark a value as a *measured* quantity rather than a
#: configured one. A bound on `DISCOVERY_TIMEOUT_SECONDS` is an assertion about a
#: constant and cannot fail under load; a bound on `elapsed` can.
MEASURED = ("elapsed", "duration", "_ms", "seconds", "secs", "took", "latency", "runtime", "wall")

#: Bounds that must stay real, with the reason each one does. AC4 of the ticket:
#: named here so a future blanket clock fixture cannot disable them silently.
#:
#: Keyed by (file name, the comparison as `ast.unparse` renders it) rather than by
#: line number, so ordinary edits above them do not require touching this table.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "test_cli_run_timeout.py",
        "elapsed < 3",
    ): "Discriminates a kill at the 1s idle budget from one at the 4s hard cap. "
    "The bound is load-bearing: widening it past 4 destroys the distinction the "
    "test exists to make, so it cannot be relaxed, only kept honest.",
    (
        "test_cli_run_timeout.py",
        "3.5 < elapsed < 8",
    ): "Same pair of budgets from the other side — the lower bound separates the "
    "hard cap from the idle kill, the upper allows 2x the cap. Bounding a real "
    "subprocess is the behaviour under test; there is no clock to fake.",
    (
        "test_shutdown_drain.py",
        "report.waited_seconds < 2.0",
    ): "A zero-second window must not wait. Two orders of magnitude of margin on "
    "a wait whose correct value is 0.0, so load cannot plausibly reach it.",
    (
        "test_bulk_operations.py",
        "(stored - now).total_seconds() < 1",
    ): "Not a duration at all: `now` is the value the row was written with, so the "
    "difference is zero by construction. The slack absorbs SQLite's datetime "
    "round-trip precision, not elapsed time.",
}


def _identifier(node: ast.expr) -> str:
    """The name a value is known by, looking through calls, attributes and arithmetic."""
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=attr):
            return attr
        case ast.Subscript(value=inner) | ast.Call(func=inner):
            return _identifier(inner)
        case ast.BinOp(left=left, right=right):
            return _identifier(left) or _identifier(right)
        case _:
            return ""


def _is_literal_number(node: ast.expr) -> bool:
    """A bare numeric literal, including a signed or arithmetic one like `0.4 * 1000`."""
    match node:
        case ast.Constant(value=bool()):
            return False
        case ast.Constant(value=int() | float()):
            return True
        case ast.UnaryOp(operand=operand):
            return _is_literal_number(operand)
        case ast.BinOp(left=left, right=right):
            return _is_literal_number(left) and _is_literal_number(right)
        case _:
            return False


def _measures_duration(node: ast.expr) -> bool:
    """A measured quantity, as opposed to a configured one.

    An ALL_CAPS name is a constant by convention — `DISCOVERY_TIMEOUT_SECONDS >
    15.0` asserts a config value and no amount of load changes it. Flagging those
    would train readers to waive the check, which is how a guard stops guarding.
    """
    name = _identifier(node)
    if name.isupper():
        return False
    return any(fragment in name.lower() for fragment in MEASURED)


def _is_bounded_above(lesser: ast.expr, greater: ast.expr) -> bool:
    """Whether `lesser < greater` caps a measured duration with a literal ceiling."""
    return _measures_duration(lesser) and _is_literal_number(greater)


def _caps_a_duration(comparison: ast.Compare) -> bool:
    """Only an *upper* bound can fail on a slow machine.

    `elapsed > 1.0` asserts work happened and load only makes it truer; `elapsed
    < 3` is the one that breaks. Chains are walked pairwise, so the middle term
    of `3.5 < elapsed < 8` is correctly seen as capped by 8.
    """
    operands = [comparison.left, *comparison.comparators]
    for index, operator in enumerate(comparison.ops):
        left, right = operands[index], operands[index + 1]
        match operator:
            case ast.Lt() | ast.LtE() if _is_bounded_above(left, right):
                return True
            case ast.Gt() | ast.GtE() if _is_bounded_above(right, left):
                return True
    return False


def _bounds_in(source: str) -> list[str]:
    """Every `assert` that caps a measured duration with a numeric literal."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assert):  # py-org: allow-isinstance
            continue
        for comparison in ast.walk(node.test):
            if isinstance(comparison, ast.Compare) and _caps_a_duration(  # py-org: allow-isinstance
                comparison
            ):
                found.append(ast.unparse(comparison))
    return found


def test_no_unreviewed_absolute_bound_on_a_measured_duration():
    """Every such bound is either gone or named in ALLOWED with its reason."""
    unreviewed: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for expression in _bounds_in(path.read_text()):
            if (path.name, expression) not in ALLOWED:
                unreviewed.append(f"{path.name}: {expression}")

    assert not unreviewed, (
        "These assertions bound a measured duration by an absolute literal, so they "
        "pass on an idle machine and fail under load:\n  "
        + "\n  ".join(unreviewed)
        + "\n\nAssert the property you mean instead — usually by comparing two measured "
        "quantities, which scales with the machine — or add an entry to ALLOWED in "
        f"{Path(__file__).name} saying why this one must stay real."
    )


def test_the_scan_finds_the_defect_it_was_built_from():
    """A control. Without it, a scan that silently matched nothing would look identical
    to a clean suite — which is how the DNS hole survived: the guard ran and found
    nothing because it was pointed at the wrong layer."""
    regressed = (
        "def test_x():\n    _assemble(session)\n    assert _rows(db, run.id)[0].elapsed_ms < 250\n"
    )
    assert _bounds_in(regressed) == ["_rows(db, run.id)[0].elapsed_ms < 250"]


def test_a_configured_constant_is_not_a_measured_duration():
    """`DISCOVERY_TIMEOUT_SECONDS > 15.0` asserts a config value, and no amount of
    load changes it. Flagging it would train readers to waive the check."""
    assert _bounds_in("def test_x():\n    assert DISCOVERY_TIMEOUT_SECONDS > 15.0\n") == []


def test_a_lower_bound_is_not_flagged():
    """Load only makes `elapsed > 1.0` truer. Flagging it would bury the bounds
    that can actually fail among ones that cannot."""
    assert _bounds_in("def test_x():\n    assert elapsed > 1.0\n") == []


def test_the_middle_of_a_chain_is_seen_as_capped():
    """`3.5 < elapsed < 8` caps `elapsed` at 8 even though it is neither operand
    of a single comparison — the chain has to be walked pairwise."""
    assert _bounds_in("def test_x():\n    assert 3.5 < elapsed < 8\n") == ["3.5 < elapsed < 8"]


def test_the_allowlist_has_no_stale_entries():
    """An entry that no longer matches anything is fiction, and fiction in a
    waiver table is how the table stops being read. If a bound was fixed or
    rewritten, its reason goes with it."""
    flagged = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        flagged.update((path.name, expression) for expression in _bounds_in(path.read_text()))

    stale = sorted(set(ALLOWED) - flagged)
    assert not stale, (
        "These ALLOWED entries match no assertion any more — delete them:\n  "
        + "\n  ".join(f"{name}: {expression}" for name, expression in stale)
    )
