"""The per-process repo templates copy only what a repository is, not what a git process holds."""

from __future__ import annotations

from pathlib import Path

from tests.repo_templates import copy_repo
from tests.worktree_helpers import git


def test_copy_leaves_behind_a_lock_a_git_process_holds_in_the_template(tmp_path: Path):
    source = tmp_path / "template"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(
        source,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "seed",
    )
    # What a still-running `git maintenance run --auto --detach` leaves mid-copy.
    (source / ".git" / "objects" / "maintenance.lock").write_text("")
    (source / ".git" / "index.lock").write_text("")

    destination = tmp_path / "copy"
    copy_repo(source, destination)

    assert not (destination / ".git" / "objects" / "maintenance.lock").exists()
    assert not (destination / ".git" / "index.lock").exists()
    assert git(destination, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
