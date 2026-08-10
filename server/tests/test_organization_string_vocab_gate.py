"""The organization gate's stringly-typed vocabulary detectors.

loregarden names ~100 enum members and then compares against raw strings anyway
(`r.status == QueuePosition.STARTED` on one line, `r.status == "failed"` on the
next). Pin both halves: the abuse each detector must catch, and the legitimate
string use it must leave alone — external vocabularies, prose comparisons, and
sets that already carry a name.
"""

import ast
import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"


def _load_module(name: str):
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


vocab = _load_module("py_string_vocab")

ENUMS = """
from enum import Enum

class RunStatus(str, Enum):
    FAILED = "failed"
    RUNNING = "running"

class McpTool(str, Enum):
    GET_TICKET = "loregarden_get_ticket"
"""


def _catalog(*sources: str) -> dict:
    catalog: dict = {}
    for source in sources or (ENUMS,):
        vocab.collect_enum_members(ast.parse(source), catalog)
    return catalog


def _errors(source: str, *, path: str = "service.py", touched: set[int] | None = None) -> list[str]:
    """Run every detector. ``touched=None`` stages every line of the snippet."""
    if touched is None:
        touched = set(range(1, len(source.splitlines()) + 2))
    return vocab.string_vocabulary_errors(
        Path(path), ast.parse(source), source, touched, _catalog()
    )


# --------------------------------------------------------------------------- #
# 1. literal that an in-scope enum already names
# --------------------------------------------------------------------------- #

FLAGGED_ENUM_LITERAL = """
from loregarden.models.domain.enums import RunStatus

def count_failures(runs):
    return sum(1 for r in runs if r.status == "failed")
"""

CLEAN_ENUM_MEMBER = """
from loregarden.models.domain.enums import RunStatus

def count_failures(runs):
    return sum(1 for r in runs if r.status == RunStatus.FAILED)
"""

# The enum exists but this module never imported it, so the literal belongs to
# some other vocabulary — a GitHub conclusion, a CLI event name.
CLEAN_ENUM_NOT_IN_SCOPE = """
def summarize(payload):
    return payload["conclusion"] == "failed"
"""


def test_flags_literal_that_a_visible_enum_already_names():
    errors = _errors(FLAGGED_ENUM_LITERAL)
    assert len(errors) == 1
    assert "RunStatus.FAILED" in errors[0]


def test_allows_the_enum_member_itself():
    assert _errors(CLEAN_ENUM_MEMBER) == []


def test_allows_literal_when_no_matching_enum_is_in_scope():
    assert _errors(CLEAN_ENUM_NOT_IN_SCOPE) == []


def test_flags_membership_in_a_literal_collection():
    source = """
from loregarden.mcp.tool_ids import McpTool

def is_read_tool(name):
    return name in ("loregarden_get_ticket",)
"""
    assert "McpTool.GET_TICKET" in _errors(source)[0]


def test_substring_search_is_not_a_vocabulary_test():
    # `"failed" in logs` scans prose; reading it as a vocabulary test is how this
    # detector would start flagging log parsing.
    source = """
from loregarden.models.domain.enums import RunStatus

def looks_broken(logs):
    return "failed" in logs
"""
    assert _errors(source) == []


def test_prose_operand_is_not_a_vocabulary_test():
    source = """
from loregarden.models.domain.enums import RunStatus

def is_marker(message):
    return message == "failed"
"""
    assert _errors(source) == []


# --------------------------------------------------------------------------- #
# 2. inline closed set
# --------------------------------------------------------------------------- #


def test_flags_inline_closed_set():
    source = """
def is_stage_kind(kind):
    return kind in {"agent", "classify", "gate", "parallel", "verify"}
"""
    errors = _errors(source)
    assert len(errors) == 1
    assert "5 inline literals" in errors[0]


def test_allows_a_closed_set_bound_to_a_named_constant():
    source = """
STAGE_KINDS = {"agent", "classify", "gate", "parallel", "verify"}

def is_stage_kind(value):
    return value in STAGE_KINDS
"""
    assert _errors(source) == []


def test_allows_boolean_spellings():
    source = """
def as_bool(value):
    return value in {"1", "on", "true", "yes"}
"""
    assert _errors(source) == []


# --------------------------------------------------------------------------- #
# 3. bare `str` on a vocabulary signature
# --------------------------------------------------------------------------- #


def test_flags_vocabulary_parameter_typed_str():
    source = """
def settle(run_id: str, status: str) -> None:
    return None
"""
    errors = _errors(source)
    assert len(errors) == 1
    assert "`status`" in errors[0] or "status: str" in errors[0]


def test_allows_literal_annotation_for_a_foreign_vocabulary():
    source = """
from typing import Literal

def get_diff(mode: Literal["base", "unstaged"]) -> str:
    return mode
"""
    assert _errors(source) == []


def test_allows_content_type_which_is_not_our_vocabulary():
    source = """
def render(body: str, content_type: str) -> bytes:
    return body.encode()
"""
    assert _errors(source) == []


def test_flags_vocabulary_return_typed_str():
    source = """
def meter_status() -> str:
    return "ok"
"""
    assert "returns bare `str`" in _errors(source)[0]


# --------------------------------------------------------------------------- #
# 4. vocabulary with no enum anywhere
# --------------------------------------------------------------------------- #

UNTYPED_VOCABULARY = """
def route(stage):
    if stage.kind == "classify":
        return 1
    if stage.kind == "classify":
        return 2
    return 3 if stage.kind == "classify" else 4
"""


def test_flags_repeated_untyped_vocabulary():
    errors = _errors(UNTYPED_VOCABULARY)
    assert len(errors) == 1
    assert "no enum defines it" in errors[0]


def test_two_sites_are_not_yet_a_vocabulary():
    source = """
def route(stage):
    if stage.kind == "classify":
        return 1
    return 2 if stage.kind == "classify" else 3
"""
    assert _errors(source) == []


def test_literal_with_an_enum_elsewhere_is_left_to_the_enum_detector():
    # "running" is a RunStatus member but this module cannot see the enum; the
    # untyped-vocabulary detector must not double-report it.
    source = """
def route(run):
    if run.phase == "running":
        return 1
    if run.phase == "running":
        return 2
    return 3 if run.phase == "running" else 4
"""
    assert _errors(source) == []


# --------------------------------------------------------------------------- #
# scoping and escape hatch
# --------------------------------------------------------------------------- #


def test_only_staged_lines_fail():
    # Line 5 holds the violation; staging line 2 alone must not block.
    assert _errors(FLAGGED_ENUM_LITERAL, touched={2}) == []
    assert _errors(FLAGGED_ENUM_LITERAL, touched={5}) != []


def test_tests_may_pin_wire_values():
    assert _errors(FLAGGED_ENUM_LITERAL, path="tests/test_runs.py") == []


def test_migrations_freeze_their_literals():
    assert _errors(FLAGGED_ENUM_LITERAL, path="server/loregarden/db/migrations.py") == []


def test_allow_comment_waives_the_line():
    source = """
from loregarden.models.domain.enums import RunStatus

def count_failures(runs):
    return sum(1 for r in runs if r.status == "failed")  # py-org: allow-string
"""
    assert _errors(source) == []
