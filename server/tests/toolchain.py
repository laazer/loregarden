"""A test that needs this checkout's toolchain fails without it; it never skips.

`client/node_modules`, the dev venv, and node, npm, uv and git on PATH are what
this repository requires to be worked on (AGENTS.md, lefthook.yml). A test that
skipped without them reported "201 passed, 54 skipped" in a fresh worktree, and
nothing in that line says a gate's coverage was gone. Some sites failed only
when `CI` was set, which pre-push sets but an agent running pytest by hand does
not. Fail with the command that fixes it instead.

Genuinely optional host tools (tmux, ssh-keygen) still skip; they gate features
a machine may not have, not this checkout's setup.
`test_no_toolchain_skips.py` keeps new skips on these off the suite.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

CLIENT_MODULES = REPO_ROOT / "client" / "node_modules"
DEV_VENV = REPO_ROOT / "server" / ".venv"

NPM_CI = "run `npm ci` in client/"
UV_SYNC = "run `uv sync --extra dev` in server/"


def require_path(path: Path, fix: str) -> None:
    """Fail, naming ``fix``, unless ``path`` exists."""
    if not path.exists():
        pytest.fail(f"this test needs {path}; {fix}", pytrace=False)


def require_client_modules() -> None:
    require_path(CLIENT_MODULES, NPM_CI)


def require_dev_venv() -> None:
    require_path(DEV_VENV / "bin" / "ruff", UV_SYNC)


def require_commands(*names: str) -> None:
    """Fail unless every command in ``names`` is on PATH."""
    if missing := [name for name in names if shutil.which(name) is None]:
        pytest.fail(
            f"this test needs {', '.join(missing)} on PATH; this repository requires them",
            pytrace=False,
        )
