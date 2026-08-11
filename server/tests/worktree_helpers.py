"""Shared scaffolding for the worktree tests.

Every one of them needs the same two things — a throwaway repository with one
commit on `main`, and a way to ask which branch a directory is on — and four
copies of that is how the DRY gate starts failing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, name: str = "project") -> Path:
    """A repository on `main` with one commit, ready to cut worktrees from."""
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def head_branch(path: Path | str) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
