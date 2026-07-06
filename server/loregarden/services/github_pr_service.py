"""Create GitHub pull requests for ticket approval flows."""

from __future__ import annotations

import json
import subprocess

from loregarden.models.domain import Artifact, Ticket, Workspace
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


def create_ticket_pull_request(session: Session, ticket: Ticket) -> dict:
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")

    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        raise ValueError("Workspace repo is not a git repository")

    branch = ticket.branch.strip()
    if not branch:
        raise ValueError("Set a branch on the ticket before opening a pull request")

    title = f"{ticket.external_id}: {ticket.title}"
    body = _build_pr_body(ticket)

    result = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--head",
            branch,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
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
        kind="pr",
        title=f"PR #{number}" if number else "Pull request",
        content_json=json.dumps(content),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return {"artifact_id": artifact.id, **content}
