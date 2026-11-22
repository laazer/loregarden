"""No test skips for want of this checkout's own toolchain; it fails instead.

A fresh worktree without `client/node_modules` reported "201 passed, 54
skipped", and a skip nobody reads is a pass. `tests/toolchain.py` fails with the
command that fixes it; this keeps new skips on the same things from coming back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# A registry check: runs on pre-commit (.lefthook/scripts/registry-checks.sh).
pytestmark = pytest.mark.registry

TESTS = Path(__file__).resolve().parent

#: What a skip may not be about: what `tests/toolchain.py` requires.
REQUIRED = ('"node"', '"npm"', '"npx"', '"uv"', '"git"', "node_modules", "npm ci", "uv sync")
_SKIPS = {"skip", "skipif", "importorskip"}


def toolchain_skips(source: str) -> list[int]:
    """Lines where a pytest skip mentions a required tool."""
    tree = ast.parse(source)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):  # py-org: allow-isinstance
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else ""  # py-org: allow-isinstance
        if name not in _SKIPS:
            continue
        text = ast.get_source_segment(source, node) or ""
        if any(word in text for word in REQUIRED):
            lines.append(node.lineno)
    return lines


def test_no_test_skips_for_the_toolchain_this_repo_requires():
    found = {
        path.name: lines
        for path in sorted(TESTS.rglob("*.py"))
        if path.name != Path(__file__).name
        and (lines := toolchain_skips(path.read_text(encoding="utf-8")))
    }

    assert not found, f"use tests/toolchain.py's require_* instead of skipping: {found}"


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ('pytest.mark.skipif(shutil.which("node") is None, reason="x")', True),
        ('pytest.skip("run `npm ci` in client/")', True),
        ("pytest.mark.skipif(not NODE_MODULES.is_dir(), reason='needs client/node_modules')", True),
        ('pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")', False),
        ('pytest.skip("create_all already enforces CHECK")', False),
    ],
)
def test_the_scan_tells_a_toolchain_skip_from_an_optional_one(source: str, flagged: bool):
    assert bool(toolchain_skips(source)) is flagged
