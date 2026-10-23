"""Scaffolding for the land-verification receipt tests (lg-milestone-that-1419).

The receipt is one row per *landing*, so every test here needs a repository, a
workspace and an integration-branch target set up the way `land_ticket` meets
them — and the census test needs a dozen of them in one function, each on its
own repository, because the paths it enumerates are mutually exclusive. That is
too much for a fixture and far too much to repeat per test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.git_automation_config import parse_override
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session, col, select
from tests.worktree_helpers import commit_on, git, make_repo

#: A command that fails the way a broken suite does: non-zero, and not the
#: "never ran" status.
FAILING_SUITE = "python3 -c 'import sys; print(\"FAILED tests/test_x.py::case_0\"); sys.exit(1)'"
PASSING_SUITE = "python3 -c 'print(\"42 passed\")'"


@dataclass(frozen=True)
class Landing:
    """One scenario's repository, workspace and ticket, ready to be landed."""

    repo: Path
    workspace: Workspace
    ticket: Ticket
    target: str
    tip: str


def build(
    session: Session,
    tmp_path: Path,
    name: str,
    *,
    commands: list[str] | None = None,
    parented: bool = True,
    with_branch: bool = True,
    repo: bool = True,
) -> Landing:
    """A ticket on its own branch, one commit past its integration branch.

    `commands` configures the ticket's `land_verify_commands` override, which is
    what arms the verification; `None` leaves the workspace unarmed, which is
    the DISABLED verdict. The flags turn the scenario into one of the
    non-landings: no repository, no branch, or a base-branch target.
    """
    root = make_repo(tmp_path, name) if repo else _bare_directory(tmp_path, name)
    workspace = Workspace(slug=name, name=name, repo_path=str(root))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    parent = None
    if parented:
        parent = Ticket(
            external_id=f"lg-ms-{name}",
            workspace_id=workspace.id,
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
        )
        session.add(parent)
        session.commit()
        session.refresh(parent)

    ticket = Ticket(
        external_id=f"lg-r-{name}",
        workspace_id=workspace.id,
        title=f"Ticket {name}",
        branch=f"loregarden/lg-r-{name}",
        parent_ticket_id=parent.id if parent else None,
        state=TicketState.IN_PROGRESS,
        # `land_ticket` is called from the terminal stage, so a real landing
        # always has a stage key — and `reportVerdict` needs one on the receipt.
        workflow_stage_key="done",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    target, tip = "", ""
    if repo:
        target = resolve_target_branch(session, ticket, workspace, repo_root=root)
        if with_branch:
            git(root, "branch", ticket.branch, target)
            tip = commit_on(root, ticket.branch, f"{name}.txt", "work\n")
    if commands is not None:
        configure(session, ticket, land_verify_commands=commands)
    return Landing(repo=root, workspace=workspace, ticket=ticket, target=target, tip=tip)


def _bare_directory(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def configure(session: Session, ticket: Ticket, **override) -> None:
    """Set the ticket's git-automation override, and prove it was not dropped.

    `parse_override` keeps only overridable fields of `GitAutomationConfig`, so
    a typo here would leave the land unarmed and every refusal test would pass
    by verifying nothing.
    """
    ticket.git_automation_json = json.dumps(override, sort_keys=True)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    kept = parse_override(ticket.git_automation_json)
    assert set(kept) == set(override), (
        f"the override was dropped: {sorted(set(override) - set(kept))} are not overridable "
        "fields of GitAutomationConfig, so nothing here is configured"
    )


def receipts(session: Session, ticket: Ticket) -> list[Artifact]:
    """Every land-verification receipt on the ticket, oldest first."""
    return list(
        session.exec(
            select(Artifact)
            .where(
                col(Artifact.ticket_id) == ticket.id,
                col(Artifact.kind) == ArtifactKind.LAND_VERIFICATION,
            )
            .order_by(col(Artifact.created_at), col(Artifact.id))
        ).all()
    )


def content(receipt: Artifact) -> dict:
    return json.loads(receipt.content_json or "{}")


def sha(cwd: Path, ref: str = "HEAD") -> str:
    return git(cwd, "rev-parse", ref).stdout.strip()


def ticket_tree(session: Session, landing: Landing) -> Path:
    """The ticket's own worktree, which is where uncommitted work is found."""
    run = AgentRun(
        run_code=f"r-{landing.ticket.external_id}",
        ticket_id=landing.ticket.id,
        workspace_id=landing.workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return resolve_execution_root(session, run, landing.ticket, landing.workspace)
