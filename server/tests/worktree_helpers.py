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
from tests.repo_templates import from_template


def git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, name: str = "project") -> Path:
    """A repository on `main` with one commit, ready to cut worktrees from.

    A copy of one built once per process; see `tests/repo_templates.py`.
    """
    return from_template(tmp_path / name, "make_repo", _build_repo)


def _build_repo(root: Path) -> None:
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


def make_run(session, workspace, ticket, code: str):
    """A running implementer run on `ticket`, which is what a worktree hangs off."""
    from loregarden.models.domain import AgentRun, RunStatus

    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def make_tree_ticket(session, workspace, external_id, *, parent=None, kind=None, **fields):
    """An in-progress ticket with a branch named after it, for publish and landing tests."""
    from loregarden.models.domain import Ticket, TicketState, WorkItemType

    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=parent.id if parent else None,
        state=TicketState.IN_PROGRESS,
        work_item_type=kind or WorkItemType.TASK,
        **fields,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def commit_on(repo: Path, branch: str, filename: str, content: str) -> str:
    """One commit adding `filename` to `branch`, without disturbing the checkout.

    Goes through a throwaway worktree, or straight into the repository when
    `branch` is what it has checked out (git refuses a second checkout of it).
    Returns the new commit.
    """
    if branch == git(repo, "branch", "--show-current").stdout.strip():
        (repo / filename).parent.mkdir(parents=True, exist_ok=True)
        (repo / filename).write_text(content)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"{filename} on {branch}")
        return git(repo, "rev-parse", "HEAD").stdout.strip()
    tree = repo.parent / f"tmp-{branch.replace('/', '-')}"
    git(repo, "worktree", "add", "-q", str(tree), branch)
    (tree / filename).parent.mkdir(parents=True, exist_ok=True)
    (tree / filename).write_text(content)
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", f"{filename} on {branch}")
    sha = git(tree, "rev-parse", "HEAD").stdout.strip()
    git(repo, "worktree", "remove", "--force", str(tree))
    return sha


def make_orch_run(session, ticket):
    """A running orchestration on `ticket`, for driving `complete_orchestration`."""
    from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus

    run = OrchestrationRun(
        run_code=f"orch-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def at_terminal_stage(session, ticket):
    """Give `ticket` a two-stage workflow (work → done) parked at `done`, pending.

    The shape the orchestrator leaves a ticket in the instant before it derives
    `done`: `advance_stage` from here finds no route and finishes the workflow —
    which is where landing runs (768).
    """
    import json
    from uuid import uuid4

    from loregarden.models.domain import (
        StageStatus,
        WorkflowInstance,
        WorkflowStageDef,
        WorkflowTemplate,
    )
    from loregarden.services.workflow_state import initial_stages_json, set_stage_status

    stages = [
        WorkflowStageDef(key="work", name="Work", order=1, agent_id="backend_implementer"),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    template = WorkflowTemplate(
        slug=f"terminal-{uuid4()}",
        name="Terminal stage test template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "work", "to": "done", "when": "pass"}]),
    )
    session.add(template)
    session.commit()
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="done",
        stages_json=initial_stages_json(stages),
    )
    session.add(instance)
    session.commit()
    set_stage_status(ticket, instance, stages, "work", StageStatus.DONE)
    # The terminal stage is left PENDING: marking it done derives `done` on
    # the ticket, and that is the orchestrator's move, after landing.
    ticket.workflow_stage_key = "done"
    ticket.workflow_stage_status = StageStatus.PENDING
    session.add(ticket)
    session.add(instance)
    session.commit()
    session.refresh(ticket)
    return ticket


def blocking_text(session, ticket) -> str:
    """Everything a blocked ticket recorded: the inline issue plus its error artifact.

    `record_blocking_issue` keeps a short message inline and files a long one
    as an ERROR artifact behind a pointer, so a test that wants git's own words
    has to read both.
    """
    from loregarden.models.domain import Artifact, ArtifactKind
    from sqlmodel import select

    errors = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.ERROR)
    ).all()
    return " ".join([ticket.blocking_issues or "", *(e.content_json or "" for e in errors)])


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


def make_agent_stage_ticket(
    session,
    workspace,
    *,
    external_id: str,
    stage_key: str = "work",
    agent_id: str = "backend_implementer",
):
    """A ticket parked, PENDING, on one single-agent stage of a two-stage workflow.

    The shape both handoff boundaries need: a stage that resolves a CLI agent,
    so `start_stage_execution` opens a run and `render_stage_prompt` has
    something to render. `at_terminal_stage` is its sibling for the landing
    tests; this one leaves the ticket *before* the work rather than after it.
    """
    import json
    from uuid import uuid4

    from loregarden.models.domain import (
        StageStatus,
        Ticket,
        TicketState,
        WorkflowInstance,
        WorkflowStageDef,
        WorkflowTemplate,
        WorkItemType,
    )
    from loregarden.services.workflow_state import initial_stages_json

    stages = [
        WorkflowStageDef(
            key=stage_key, name="Work", order=1, stage_type="agent", agent_id=agent_id
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    template = WorkflowTemplate(
        slug=f"handoff-tpl-{uuid4()}",
        name="Handoff template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": stage_key, "to": "done", "when": "pass"}]),
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Hand this stage over",
        branch=f"loregarden/{external_id}",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
        workflow_stage_status=StageStatus.PENDING,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=stage_key,
            stages_json=initial_stages_json(stages),
        )
    )
    session.commit()
    return ticket


def cut_ticket_worktree(session, workspace, ticket, repo):
    """The ticket's worktree as an earlier stage would leave it: cut, on the
    ticket's branch, holding one commit of its own.

    Every handoff test starts from a tree that already exists, so that the
    refresh-on-*creation* path (`worktree_service._add_worktree`) cannot be what
    makes a "the tree was refreshed" assertion pass.
    """
    from pathlib import Path

    from loregarden.agents.executors.cli import CliAgentExecutor

    root, stopped = CliAgentExecutor(session)._prepare_tree(
        make_run(session, workspace, ticket, f"earlier-{ticket.external_id}"),
        ticket,
        workspace,
        workspace_root=Path(repo),
        skip_git_branch=False,
        advance_workflow=False,
    )
    assert stopped is None, stopped.stderr if stopped else None
    assert root != Path(repo), "fixture: the ticket must have its own worktree"
    (root / "own.txt").write_text("the ticket's own earlier stage\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "stage one")
    return root
