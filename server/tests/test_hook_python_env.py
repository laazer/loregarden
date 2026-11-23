"""Hooks run Python the way CI does, whatever the developer's shell exports.

This machine exports PYTHONDONTWRITEBYTECODE=1 and CI does not, so pre-push
passed a test that read a stale .pyc on CI (#936). Every hook script sources
`hook-noninteractive.sh`; it must clear what CI does not set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from tests.repo_paths import REPO_ROOT

HOOK_ENV = REPO_ROOT / ".lefthook" / "scripts" / "hook-noninteractive.sh"

#: Interpreter settings CI's workflow never sets, with a value that changes behaviour.
NOT_SET_ON_CI = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPYCACHEPREFIX": "/tmp/elsewhere",
    "PYTHONPATH": "/tmp/shadow",
    "PYTHONWARNINGS": "error",
    "PYTHONOPTIMIZE": "2",
    "PYTHONHASHSEED": "0",
    "PYTHONSAFEPATH": "1",
}

_PROBE = (
    "import json, os, sys; print(json.dumps({'flags': [sys.dont_write_bytecode, "
    "sys.flags.optimize, sys.flags.safe_path, sys.pycache_prefix], 'env': "
    "sorted(k for k in os.environ if k.startswith('PYTHON'))}))"
)


def _python_under_hook_env(extra: dict[str, str]) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")} | extra
    result = subprocess.run(
        ["bash", "-c", f'source "{HOOK_ENV}" && "{sys.executable}" -c "{_PROBE}"'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_the_hook_env_clears_what_ci_does_not_set():
    seen = _python_under_hook_env(NOT_SET_ON_CI)

    assert seen["env"] == []
    assert seen["flags"] == [False, 0, False, None]


@pytest.mark.parametrize(("name", "value"), sorted(NOT_SET_ON_CI.items()))
def test_each_setting_is_cleared_on_its_own(name: str, value: str):
    assert name not in _python_under_hook_env({name: value})["env"]
