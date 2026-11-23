"""Pass 2's test selection: tests that call a surviving function, not every
test the import graph reaches (`.lefthook/scripts/mutation_callers.py`)."""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
mc = importlib.import_module("mutation_callers")

PACKAGE = {
    "__init__.py": "",
    "core.py": """
        def target():
            return 1

        def bystander():
            return 2

        class Runner:
            def run_it(self):
                return 3
    """,
    "helpers.py": """
        from loregarden import core

        def helper():
            return core.target()

        def drive():
            return core.Runner().run_it()
    """,
    "outer.py": """
        from loregarden import helpers

        def outer():
            return helpers.helper()
    """,
    "registry.py": """
        from loregarden import core

        TOOLS = {"target": core.target}
    """,
    "elsewhere.py": """
        from loregarden import core

        def unrelated():
            return core.bystander()
    """,
}

TESTS = {
    "test_direct.py": "from loregarden.core import target\n\ndef test_it():\n    assert target()\n",
    "test_helper.py": "from loregarden import helpers\n\ndef test_it():\n    helpers.helper()\n",
    "test_outer.py": "from loregarden import outer\n\ndef test_it():\n    outer.outer()\n",
    "test_patch.py": (
        "from unittest import mock\n\n"
        "def test_it():\n    with mock.patch('loregarden.core.target'):\n        pass\n"
    ),
    "test_tools.py": "from loregarden import registry\n\ndef test_it():\n    registry.TOOLS\n",
    "test_unrelated.py": "from loregarden import elsewhere\n\ndef test_it():\n    elsewhere.unrelated()\n",
    "test_drive.py": "from loregarden import helpers\n\ndef test_it():\n    helpers.drive()\n",
}


@pytest.fixture
def index(tmp_path: Path):
    package = tmp_path / "loregarden"
    package.mkdir()
    for name, source in PACKAGE.items():
        (package / name).write_text(textwrap.dedent(source), encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, source in TESTS.items():
        (tests / name).write_text(source, encoding="utf-8")
    return mc.build_index(package, tests)


def _names(paths) -> set[str]:
    return {p.name for p in paths}


def test_callers_up_the_chain_are_followed_and_mere_importers_are_not(index):
    selected = _names(mc.caller_tests(index, mc.Symbol("loregarden.core", "target"), hops=3))
    assert selected == {
        "test_direct.py",
        "test_helper.py",
        "test_outer.py",
        "test_patch.py",
        "test_tools.py",
    }


def test_hops_bound_how_far_up_the_chain_it_goes(index):
    symbol = mc.Symbol("loregarden.core", "target")
    assert _names(mc.caller_tests(index, symbol, hops=0)) == {"test_direct.py", "test_patch.py"}
    assert "test_outer.py" not in _names(mc.caller_tests(index, symbol, hops=1))
    assert "test_outer.py" in _names(mc.caller_tests(index, symbol, hops=2))


def test_a_method_is_followed_through_attribute_calls(index):
    selected = _names(mc.caller_tests(index, mc.Symbol("loregarden.core", "run_it")))
    assert selected == {"test_drive.py"}


def test_a_function_nothing_calls_selects_no_test(index):
    lonely = mc.Symbol("loregarden.outer", "outer")
    assert _names(mc.caller_tests(index, lonely)) == {"test_outer.py"}
    assert mc.caller_tests(index, mc.Symbol("loregarden.registry", "missing")) == set()
