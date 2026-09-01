"""Shared scaffolding for the worktree tests.

Every one of them needs the same two things — a throwaway repository with one
commit on `main`, and a way to ask which branch a directory is on — and four
copies of that is how the DRY gate starts failing.
"""

from __future__ import annotations

import pathlib
import subprocess
from pathlib import Path

from loregarden.agents.mcp_context import (
    STAGE_REPORT_SECTION_TITLE,
    WORKFLOW_ENFORCEMENT_DOC_REL,
)


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
    # Seeded before the commit so the tree stays clean: an untracked
    # agent_context would make `git status --porcelain` non-empty and trip the
    # "checks do not write to the repository" invariant.
    seed_stage_report_contract(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def head_branch(path: Path | str) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def make_ticket(session, workspace, external_id: str = "LG-1", title: str = "Add the thing"):
    """A ticket on its own branch, which is all these tests need one for."""
    from loregarden.models.domain import Ticket

    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=title,
        branch=f"loregarden/{external_id.lower()}-{title.lower().replace(' ', '-')}",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def seed_stage_report_contract(repo_root) -> None:
    """Give a throwaway repo the workflow-enforcement doc a real workspace has.

    `DoctorCheck.STAGE_REPORT_CONTRACT` refuses to dispatch when this is missing,
    because a workspace without it hands every agent an empty report format and
    every stage then fails on a report it was never told how to write (the
    blobert 0-vs-3303 measurement). A fixture repo without it is not a neutral
    fixture — it is a workspace that could not actually run.
    """
    path = pathlib.Path(repo_root) / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    divider = "-" * 30
    path.write_text(
        f"intro\n\n{divider}\n{STAGE_REPORT_SECTION_TITLE}\n{divider}\n"
        "Report the stage with a status line and a summary.\n",
        encoding="utf-8",
    )
