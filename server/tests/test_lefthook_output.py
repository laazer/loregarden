"""Hooks print quietly, and no session setting can make them examine nothing.

The quiet output used to live in `lefthook-agent.yml`, picked by a
LEFTHOOK_CONFIG that `.claude/settings.json` set for every Claude Code session.
That variable reaches every repository the session commits in, and lefthook
resolves it against each one's root. In a repository without that file, lefthook
printed `Config file ... not found!`, ran no hook and exited 0, so a commit in
another workspace passed gates it never ran.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

# A registry check: runs on pre-commit (.lefthook/scripts/registry-checks.sh).
pytestmark = pytest.mark.registry

REPO = Path(__file__).resolve().parents[2]


def test_a_passing_gate_is_quiet_and_a_failing_one_prints_its_output():
    config = yaml.safe_load((REPO / "lefthook.yml").read_text(encoding="utf-8"))

    assert set(config["output"]) == {"summary", "failure"}


def test_no_session_setting_redirects_lefthook():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))

    assert not [name for name in settings.get("env", {}) if name.startswith("LEFTHOOK")]


def test_the_shim_for_older_sessions_is_lefthook_yml_and_nothing_else():
    shim = yaml.safe_load((REPO / "lefthook-agent.yml").read_text(encoding="utf-8"))

    assert shim == {"extends": ["lefthook.yml"]}
