"""The organization gate's `isinstance` detector.

205 of this repo's 247 `isinstance` calls target `dict`/`str`/`list` — schema
checks written by hand against payloads nothing modelled at the boundary. Pin
what the gate catches, which fix each shape is told to apply, and the waiver
that keeps genuinely foreign objects committable.
"""

import ast
import importlib.util
from pathlib import Path

_CHECKER_PATH = (
    Path(__file__).resolve().parents[2] / ".lefthook" / "scripts" / "py_organization_check.py"
)


def _load_checker():
    spec = importlib.util.spec_from_file_location("py_organization_check", _CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


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
