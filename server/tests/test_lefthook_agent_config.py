"""The agent sessions' lefthook config may change how gates print, never which run.

`.claude/settings.json` sets LEFTHOOK_CONFIG for every Claude Code session in
this repo. Whatever that file says is the gate set those sessions commit under,
so a config that dropped or overrode a gate would be a hook bypass nobody typed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

# A registry check: runs on pre-commit (.lefthook/scripts/registry-checks.sh).
pytestmark = pytest.mark.registry

REPO = Path(__file__).resolve().parents[2]


def _agent_config_name() -> str:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return settings["env"]["LEFTHOOK_CONFIG"]


def test_agent_sessions_get_the_full_gate_set_with_quiet_output():
    config = yaml.safe_load((REPO / _agent_config_name()).read_text(encoding="utf-8"))

    # Every gate comes from lefthook.yml, and nothing else is set but output.
    assert config == {"extends": ["lefthook.yml"], "output": config["output"]}
    # A failing gate still prints what failed; only success is quiet.
    assert "failure" in config["output"]
    assert "summary" in config["output"]
