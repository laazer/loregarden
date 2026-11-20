"""The organization gate's `isinstance` detector.

205 of this repo's 247 `isinstance` calls target `dict`/`str`/`list` — schema
checks written by hand against payloads nothing modelled at the boundary. Pin
what the gate catches, which fix each shape is told to apply, and the waiver
that keeps genuinely foreign objects committable.
"""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"
_CHECKER_PATH = _SCRIPTS / "py_organization_check.py"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_script("py_organization_check")


#: The label every unclassified finding opens with, from the criterion's own
#: words ("unclassified construct at <file>:<line>"). Spelled literally here
#: rather than imported from `.lefthook/scripts/gate_unclassified.py` so that
#: this file's existing coverage does not go dark while that module is being
#: written; `test_gate_unclassified.py` pins the two to the same string.
UNCLASSIFIED_LABEL = "unclassified construct at"


def _errors(source: str, *, path: str = "service.py", touched: set[int] | None = None) -> list[str]:
    tree = ast.parse(source)
    lines = source.splitlines()
    if touched is None:
        touched = set(range(1, len(lines) + 2))
    return checker.isinstance_errors(Path(path), tree, lines, touched)


PAYLOAD_CHECK = """
def read(payload):
    if not isinstance(payload, dict):
        return None
    return payload.get("id")
"""

DOMAIN_CHECK = """
def render(part):
    if isinstance(part, LogLine):
        return part.text
    return str(part)
"""


def test_flags_payload_shape_check_toward_pydantic():
    errors = _errors(PAYLOAD_CHECK)
    assert len(errors) == 1
    assert "Pydantic" in errors[0]


def test_flags_domain_type_check_toward_polymorphism():
    errors = _errors(DOMAIN_CHECK)
    assert len(errors) == 1
    assert "polymorphism" in errors[0]


def test_reports_every_target_in_a_tuple():
    errors = _errors("def f(x):\n    return isinstance(x, (dict, list))\n")
    assert "dict, list" in errors[0]


def test_only_staged_lines_fail():
    assert _errors(PAYLOAD_CHECK, touched={2}) == []
    assert _errors(PAYLOAD_CHECK, touched={3}) != []


def test_tests_may_assert_on_types():
    assert _errors(PAYLOAD_CHECK, path="tests/test_payloads.py") == []


def test_allow_comment_waives_the_line():
    source = """
def read(payload):
    if not isinstance(payload, dict):  # py-org: allow-isinstance
        return None
    return payload
"""
    assert _errors(source) == []


# --------------------------------------------------------------------------- #
# The borrowed label (lg-code-knowledge-1084, AC2)
#
# `_isinstance_targets` returns [] when the call has fewer than two arguments,
# a splatted target, or a target that is not a readable type reference. Today
# `isinstance_errors` prints ``isinstance(..., ?)`` and takes the TYPE-SWITCH
# arm's advice — "dispatch on the type instead" — as though it had established
# that the target was not a payload-shape check. It established nothing. That
# borrowed advice is the defect: the gate must say it could not classify the
# construct, and name it, instead of flattening it into a neighbouring arm.
# --------------------------------------------------------------------------- #

UNREADABLE_TARGETS = {
    "no target at all": "def f(x):\n    return isinstance(x)\n",
    "a splatted target": "def f(x, types):\n    return isinstance(x, *types)\n",
    "a computed target": "def f(x, y):\n    return isinstance(x, type(y))\n",
    "a subscripted target": "def f(x, table):\n    return isinstance(x, table['k'])\n",
    "a splat inside a tuple": "def f(x, rest):\n    return isinstance(x, (dict, *rest))\n",
}

READABLE_TARGETS = {
    "a payload shape": PAYLOAD_CHECK,
    "a domain type": DOMAIN_CHECK,
    "a dotted type": "def f(x):\n    return isinstance(x, models.Ticket)\n",
    "a tuple of types": "def f(x):\n    return isinstance(x, (dict, list))\n",
}


def _unclassified(errors: list[str]) -> list[str]:
    return [e for e in errors if UNCLASSIFIED_LABEL in e]


@pytest.mark.parametrize("name", sorted(UNREADABLE_TARGETS))
def test_an_unreadable_target_is_named_unclassified(name):
    errors = _errors(UNREADABLE_TARGETS[name])

    assert len(errors) == 1, f"{name}: {errors}"
    assert _unclassified(errors) == errors, f"{name}: {errors}"
    assert "isinstance" in errors[0]


@pytest.mark.parametrize("name", sorted(UNREADABLE_TARGETS))
def test_an_unreadable_target_borrows_neither_arms_advice(name):
    """Taking one arm's advice is a claim the gate did not earn."""
    message = _errors(UNREADABLE_TARGETS[name])[0]

    assert "Pydantic" not in message
    assert "polymorphism" not in message
    assert "?" not in message, "`isinstance(..., ?)` reads as a known answer"


@pytest.mark.parametrize("name", sorted(UNREADABLE_TARGETS))
def test_the_unclassified_finding_names_the_call_s_own_file_and_line(name):
    """Anchored at the `isinstance` call's own lineno, which the existing scope
    filter already admits — the gate does not widen what it examines."""
    message = _errors(UNREADABLE_TARGETS[name], path="src/pkg/mod.py")[0]
    assert message.startswith("src/pkg/mod.py:2:")


@pytest.mark.parametrize("name", sorted(READABLE_TARGETS))
def test_a_readable_target_still_takes_its_named_arm(name):
    """AC7a: the constructs the gate DOES name keep today's verdict."""
    errors = _errors(READABLE_TARGETS[name])

    assert len(errors) == 1, f"{name}: {errors}"
    assert _unclassified(errors) == [], f"{name}: {errors}"


def test_an_unreadable_target_is_still_diff_scoped():
    source = UNREADABLE_TARGETS["a splatted target"]
    assert _errors(source, touched={1}) == []
    assert _errors(source, touched={2}) != []


def test_an_unreadable_target_is_still_waivable():
    source = "def f(x, types):\n    return isinstance(x, *types)  # py-org: allow-isinstance\n"
    assert _errors(source) == []


def test_tests_may_still_hold_an_unreadable_target():
    source = UNREADABLE_TARGETS["a splatted target"]
    assert _errors(source, path="tests/test_payloads.py") == []


# --------------------------------------------------------------------------- #
# AC2 — the shapes the criterion's own list only gestures at
#
# "fewer than two args, a splat, a non-literal target" is a description of a
# space, not an enumeration of it. These are the corners of that space that the
# five cases above do not reach, and each one is a call the gate recognizes as
# its subject (an `isinstance()` on an admitted line) and cannot read a target
# out of. Flattening any of them into an arm is the same borrowed label.
# --------------------------------------------------------------------------- #

MORE_UNREADABLE_TARGETS = {
    "no arguments at all": "def f():\n    return isinstance()\n",
    "a splatted subject": "def f(args):\n    return isinstance(*args)\n",
    "a keyword-splatted target": "def f(x, kw):\n    return isinstance(x, **kw)\n",
    "a target built by a call": "def f(x):\n    return isinstance(x, tuple(known()))\n",
    "a target read off self": "def f(self, x):\n    return isinstance(x, self.kinds[0])\n",
}


@pytest.mark.parametrize("name", sorted(MORE_UNREADABLE_TARGETS))
def test_a_further_unreadable_shape_is_named_unclassified(name):
    errors = _errors(MORE_UNREADABLE_TARGETS[name])

    assert len(errors) == 1, f"{name}: {errors}"
    assert _unclassified(errors) == errors, f"{name}: {errors}"


@pytest.mark.parametrize("name", sorted(MORE_UNREADABLE_TARGETS))
def test_a_further_unreadable_shape_borrows_neither_arms_advice(name):
    message = _errors(MORE_UNREADABLE_TARGETS[name])[0]

    assert "Pydantic" not in message
    assert "polymorphism" not in message


def test_the_gate_does_not_crash_on_a_call_shape_python_would_refuse_at_runtime():
    """`isinstance()` and `isinstance(*args)` are parseable and would raise at
    runtime. A gate that raises instead of reporting is the file-level failure
    this ticket's sibling already names, arriving through the construct-level
    door — and `main` would exit non-zero with a traceback, not a finding.
    """
    for source in ("def f():\n    return isinstance()\n", "def f(a):\n    return isinstance(*a)\n"):
        assert len(_errors(source)) == 1, source


def test_each_unreadable_call_gets_its_own_finding():
    """Two calls, two findings: a detector that reports once per file loses the
    second, which is this ticket's defect one level up."""
    source = "def f(x, types, table):\n    a = isinstance(x, *types)\n    return isinstance(x, table['k'])\n"
    errors = _errors(source)

    assert len(errors) == 2, errors
    assert _unclassified(errors) == errors
    assert {e.split(":")[1] for e in errors} == {"2", "3"}


def test_a_readable_and_an_unreadable_call_each_keep_their_own_verdict():
    """The arms must not contaminate each other within one file."""
    source = (
        "def f(payload, types):\n"
        "    if not isinstance(payload, dict):\n"
        "        return None\n"
        "    return isinstance(payload, *types)\n"
    )
    errors = _errors(source)

    assert len(errors) == 2, errors
    assert len(_unclassified(errors)) == 1, errors
    assert any("Pydantic" in e for e in errors), errors


def test_the_same_source_reports_the_same_findings_twice():
    """Determinism: a finding whose text shifts between runs cannot be waived or
    reproduced from the message a developer was shown."""
    source = UNREADABLE_TARGETS["a splat inside a tuple"]
    assert _errors(source) == _errors(source)


# --------------------------------------------------------------------------- #
# AC8 — the arm is measured against the live tree, on THIS gate too
#
# The silent-except side has `test_the_live_tree_holds_no_unclassifiable_
# constructs`; this is its missing twin, and it is the one that matters more:
# the tree holds 398 `isinstance()` calls, so a recognizer written wide here
# fails every workspace's next commit. Measured at this stage: of those 398, 0
# have a target that is not an `ast.Name`/`ast.Attribute` (alone or in a tuple),
# so the expected count is 0 and this is a live guard from the first implement
# round rather than after it.
# --------------------------------------------------------------------------- #


def test_the_live_tree_holds_no_unclassifiable_isinstance_calls():
    repo_root = Path(__file__).resolve().parents[2]
    # Named explicitly rather than `server/`: `server/.venv` is thousands of
    # third-party files written for other interpreters, and walking it would
    # measure something other than this repository.
    trees = [
        repo_root / "server" / "loregarden",
        repo_root / ".lefthook" / "scripts",
        repo_root / "scripts",
    ]

    offenders: list[str] = []
    for tree in trees:
        for py in sorted(tree.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            source = py.read_text(encoding="utf-8")
            lines = source.splitlines()
            errors = checker.isinstance_errors(
                py, ast.parse(source), lines, set(range(1, len(lines) + 2))
            )
            offenders.extend(_unclassified(errors))

    assert offenders == [], f"{len(offenders)} unclassified; a count in the hundreds is the gate"
