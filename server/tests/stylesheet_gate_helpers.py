"""Throwaway repos for the stylesheet gates' tests (motion, type scale).

Both gates read the workspace's root stylesheet to decide whether its tokens
exist, so each test repo is a bare git repo with an optional client/src/index.css.
"""

import os
import subprocess
from pathlib import Path


def clean_env() -> dict[str, str]:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=clean_env())


def build_client_repo(root: Path, root_stylesheet: str | None) -> None:
    """A committed repo with client/src/, and index.css when a stylesheet is given."""
    root.mkdir()
    git(root, "init", "-q", "-b", "main", ".")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "client" / "src").mkdir(parents=True)
    (root / "client" / "src" / ".keep").write_text("")
    if root_stylesheet is not None:
        (root / "client" / "src" / "index.css").write_text(root_stylesheet)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
