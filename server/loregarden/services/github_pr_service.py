"""Create GitHub pull requests for ticket approval flows."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loregarden.models.domain import Artifact, Ticket, Workspace
from loregarden.services.artifact_service import _git_base_ref
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select


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


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _gh(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], cwd=repo_root, capture_output=True, text=True)


def _has_commits_ahead_of_base(repo_root: Path, branch: str) -> bool:
    base = _git_base_ref(repo_root)
    if not base:
        # No resolvable base ref to compare against — don't block PR creation on this check.
        return True
    result = _git(repo_root, "rev-list", "--count", f"{base}..{branch}")
    return result.returncode != 0 or result.stdout.strip() != "0"


def _branch_pushed_and_current(repo_root: Path, branch: str) -> bool:
    """Whether origin/<branch> exists and already has every local commit on <branch>."""
    remote = _git(repo_root, "rev-parse", "--verify", f"origin/{branch}")
    if remote.returncode != 0:
        return False
    ahead = _git(repo_root, "rev-list", "--count", f"origin/{branch}..{branch}")
    return ahead.returncode == 0 and ahead.stdout.strip() == "0"


def _push_branch(repo_root: Path, branch: str) -> None:
    result = _git(repo_root, "push", "-u", "origin", branch)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "git push failed").strip()
        raise ValueError(f"Could not push branch '{branch}' to origin: {stderr}")


def _find_existing_pr(repo_root: Path, branch: str) -> dict | None:
    result = _gh(
        repo_root,
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "all",
        "--json",
        "number,url,title,state,body",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        items = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return items[0] if items else None


def _attempt_squash_merge(repo_root: Path, number: str) -> dict:
    """Try to squash-merge the PR immediately.

    A blocked merge (conflicts, required checks/reviews not satisfied) is an expected,
    recoverable outcome here — not an error in the Open PR flow — so it's surfaced as
    ``needs_triage`` (the branch will show up with its blocking issue in Branch Triage)
    rather than raised as a ValueError.
    """
    if not number:
        return {"merged": False, "needs_triage": False, "reason": None}

    view = _gh(repo_root, "pr", "view", number, "--json", "mergeable,mergeStateStatus,state")
    if view.returncode == 0:
        try:
            info = json.loads(view.stdout)
        except json.JSONDecodeError:
            info = {}
        if info.get("state") == "MERGED":
            return {"merged": True, "needs_triage": False, "reason": None}
        if info.get("mergeable") == "CONFLICTING":
            return {
                "merged": False,
                "needs_triage": True,
                "reason": "Merge conflicts with the base branch — resolve in Branch Triage.",
            }

    result = _gh(repo_root, "pr", "merge", number, "--squash", "--delete-branch")
    if result.returncode == 0:
        return {"merged": True, "needs_triage": False, "reason": None}

    stderr = (result.stderr or result.stdout or "gh pr merge failed").strip()
    return {"merged": False, "needs_triage": True, "reason": stderr}


def create_ticket_pull_request(session: Session, ticket: Ticket) -> dict:
    """Open (or reuse) the ticket's PR, then try to squash-merge it immediately.

    Idempotent: safe to call again to retry a blocked merge — it reuses the existing PR
    instead of erroring, and updates the ticket's single "pr" artifact in place rather than
    accumulating a new one per call.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")

    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        raise ValueError("Workspace repo is not a git repository")

    branch = ticket.branch.strip()
    if not branch:
        raise ValueError("Set a branch on the ticket before opening a pull request")

    if not _has_commits_ahead_of_base(repo_root, branch):
        raise ValueError(
            f"Branch '{branch}' has no commits ahead of the base branch — nothing to open a pull request for."
        )

    if not _branch_pushed_and_current(repo_root, branch):
        _push_branch(repo_root, branch)

    existing = _find_existing_pr(repo_root, branch)
    created = existing is None

    if existing is not None:
        pr_url = existing["url"]
        number = str(existing["number"])
        title = existing["title"]
        body = existing.get("body") or ""
        pr_state = existing.get("state", "OPEN")
    else:
        title = f"{ticket.external_id}: {ticket.title}"
        body = _build_pr_body(ticket)
        result = _gh(repo_root, "pr", "create", "--title", title, "--body", body, "--head", branch)
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "gh pr create failed").strip()
            raise ValueError(stderr)

        pr_url = result.stdout.strip().splitlines()[-1].strip()
        if not pr_url.startswith("http"):
            raise ValueError(f"Unexpected gh output: {result.stdout!r}")

        number = ""
        if "/pull/" in pr_url:
            number = pr_url.rsplit("/pull/", 1)[-1].split("/", 1)[0]
        pr_state = "OPEN"

    if pr_state == "MERGED":
        merge_result = {"merged": True, "needs_triage": False, "reason": None}
    elif pr_state == "OPEN":
        merge_result = _attempt_squash_merge(repo_root, number)
    else:
        # CLOSED (not merged) — leave as-is, don't attempt to merge a closed PR.
        merge_result = {"merged": False, "needs_triage": False, "reason": None}

    content = {
        "url": pr_url,
        "number": number,
        "title": title,
        "branch": branch,
        "body": body,
        "created": created,
        **merge_result,
    }

    artifact = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "pr")
    ).first()
    if artifact is None:
        artifact = Artifact(ticket_id=ticket.id, kind="pr")
    artifact.title = f"PR #{number}" if number else "Pull request"
    artifact.content_json = json.dumps(content)

    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return {"artifact_id": artifact.id, **content}
