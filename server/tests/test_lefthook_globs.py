"""lefthook globs read `**/` as zero directories or more.

lefthook's default matcher reads `**/` as one directory or more, so a gate on
`P/**/S` silently skipped every file directly in `P`. Measured against the tree
before `glob_matcher: doublestar` was set: client-dup and registry-checks
skipped client/src/App.tsx and main.tsx, registry-checks skipped
server/tests/conftest.py, every TS gate skipped client/vite.config.ts, and
pre-push client-tests skipped client/package.json and package-lock.json, so a
dependency-only push ran no jest. Each skip printed "no files for inspection",
which reads like a pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# A registry check: runs on pre-commit (.lefthook/scripts/registry-checks.sh).
pytestmark = pytest.mark.registry

REPO = Path(__file__).resolve().parents[2]


def test_globs_use_the_doublestar_matcher():
    config = yaml.safe_load((REPO / "lefthook.yml").read_text(encoding="utf-8"))
    assert config.get("glob_matcher") == "doublestar"
