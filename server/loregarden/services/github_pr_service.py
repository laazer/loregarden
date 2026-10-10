"""Create GitHub pull requests for ticket approval flows."""

from __future__ import annotations

import json

from loregarden.models.domain import Artifact, ArtifactKind, Ticket, Workspace
from loregarden.services.git_subprocess import run_gh, run_git
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import subtree_root, target_branch_name
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session


def _build_pr_body(ticket: Ticket) -> str:
    lines = [
        f"## {ticket.title}",
        "",
        ticket.description.strip() or "_No description provided._",
        "",
    ]
    criteria = json.loads(ticket.acceptance_criteria_json or "[]")
    if criteria:
        lines.append("## Acceptance criteria")
        lines.extend(f"- {item}" for item in criteria)
        lines.append("")
    lines.extend(
        [
            "## Loregarden",
            f"- Ticket: `{ticket.external_id}`",
            f"- Workflow stage: `{ticket.workflow_stage_key or '—'}`",
            "",
            "_Opened from Loregarden approval workflow._",
        ]
    )
    return "\n".join(lines)


def _refuse_tree_member(session: Session, ticket: Ticket, workspace: Workspace) -> None:
    """A ticket inside a tree ships in its tree's PR, never one of its own.

    Its work lands on the tree's integration branch, which goes to the base as
    one PR when the root completes. A PR for the ticket's own branch carries
    the same commits a second time, so every conflict with the base has to be
    resolved twice (lg-durable-remote-336 was on both `integration/…-335` and
    its own PR #555).
    """
    target = target_branch_name(session, ticket, workspace)
    if target == resolve_orchestration_profile(workspace).git.base_branch:
        return
    root = subtree_root(session, ticket)
    raise ValueError(
        f"{ticket.external_id} lands on {target}; its work ships in that tree's pull request "
        f"when {root.external_id} completes. A PR for its own branch would duplicate it."
    )


def create_ticket_pull_request(session: Session, ticket: Ticket) -> dict:
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")

    if not (resolve_workspace_root(workspace) / ".git").exists():
        raise ValueError("Workspace repo is not a git repository")

    _refuse_tree_member(session, ticket, workspace)

    branch = ticket.branch.strip()
    if not branch:
        raise ValueError("Set a branch on the ticket before opening a pull request")

    # The ticket's commits are in its worktree, and the shared checkout is not
    # even on its branch any more. Pushing and opening the PR from anywhere
    # else publishes whatever that other tree happens to hold.
    repo_root = resolve_ticket_root(session, ticket, workspace)

    push = run_git(
        ["push", "-u", "origin", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        raise ValueError((push.stderr or push.stdout or "git push failed").strip())

    title = f"{ticket.external_id}: {ticket.title}"
    body = _build_pr_body(ticket)

    result = run_gh(
        ["pr", "create", "--title", title, "--body", body, "--head", branch],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "gh pr create failed").strip()
        raise ValueError(stderr)

    pr_url = result.stdout.strip().splitlines()[-1].strip()
    if not pr_url.startswith("http"):
        raise ValueError(f"Unexpected gh output: {result.stdout!r}")

    number = ""
    if "/pull/" in pr_url:
        number = pr_url.rsplit("/pull/", 1)[-1].split("/", 1)[0]

    content = {
        "url": pr_url,
        "number": number,
        "title": title,
        "branch": branch,
        "body": body,
    }

    artifact = Artifact(
        ticket_id=ticket.id,
        kind=ArtifactKind.PR,
        title=f"PR #{number}" if number else "Pull request",
        content_json=json.dumps(content),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return {"artifact_id": artifact.id, **content}
