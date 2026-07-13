"""Branch triage: inspect repo branches, detect weird states, capture diffs."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.artifact_service import (
    _git_base_ref,
    _resolve_upstream_ref,
    branch_diff_manifest,
    capture_branch_file_diff,
)
from loregarden.services.file_editor import _current_branch, _list_branches, _parse_worktrees
from loregarden.services.git_branch import resolve_ticket_branch, validate_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

STALE_DAYS = 30
AGENT_BRANCH_PREFIXES = ("loregarden/", "agent/")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _is_git_repo(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def _branch_ahead_behind(repo_root: Path, base: str, branch: str) -> tuple[int, int]:
    proc = _git(repo_root, "rev-list", "--left-right", "--count", f"{base}...{branch}")
    if proc.returncode != 0:
        return 0, 0
    parts = (proc.stdout or "").strip().split()
    if len(parts) != 2:
        return 0, 0
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except ValueError:
        return 0, 0

    if ahead > 0 and _branch_squash_merged(repo_root, base, branch):
        ahead = 0

    return ahead, behind


def _merge_base(repo_root: Path, base: str, branch: str) -> str | None:
    proc = _git(repo_root, "merge-base", base, branch)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _branch_squash_merged(repo_root: Path, base: str, branch: str) -> bool:
    """Detect a branch whose commits were squashed and merged into base.

    `rev-list --left-right --count` compares commits by SHA, so a branch that
    was squashed into a single commit on `base` still looks "ahead" even
    though its changes already landed — the squash commit has a different SHA
    than the branch's original commits. Treat the branch as merged if every
    file it touched since the merge-base is byte-identical between the branch
    tip and `base`.
    """
    merge_base = _merge_base(repo_root, base, branch)
    if not merge_base:
        return False

    files_proc = _git(repo_root, "diff", "--name-only", f"{merge_base}..{branch}")
    if files_proc.returncode != 0:
        return False
    changed_files = [line for line in (files_proc.stdout or "").splitlines() if line.strip()]
    if not changed_files:
        return True

    diff_proc = _git(repo_root, "diff", "--quiet", base, branch, "--", *changed_files)
    if diff_proc.returncode not in (0, 1):
        return False
    return diff_proc.returncode == 0


def _branch_last_commit(repo_root: Path, branch: str) -> dict[str, str]:
    proc = _git(repo_root, "log", "-1", "--format=%cI|%s", branch)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return {"date": "", "message": ""}
    raw = proc.stdout.strip()
    if "|" in raw:
        date, message = raw.split("|", 1)
    else:
        date, message = raw, ""
    return {"date": date, "message": message}


def _worktree_dirty(worktree_path: str) -> bool:
    proc = _git(Path(worktree_path), "status", "--porcelain")
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def _ticket_branch_map(session: Session, workspace_id: str) -> dict[str, list[dict[str, str]]]:
    tickets = session.exec(select(Ticket).where(Ticket.workspace_id == workspace_id)).all()
    by_branch: dict[str, list[dict[str, str]]] = {}
    for ticket in tickets:
        branch = resolve_ticket_branch(ticket)
        if not branch:
            continue
        by_branch.setdefault(branch, []).append(
            {
                "id": ticket.id,
                "external_id": ticket.external_id,
                "title": ticket.title,
                "state": ticket.state,
            }
        )
    return by_branch


def _detect_issues(
    *,
    branch: str,
    base: str,
    is_current: bool,
    ahead: int,
    behind: int,
    dirty: bool,
    worktree_count: int,
    linked_tickets: list[dict[str, str]],
    last_commit_date: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    if dirty:
        issues.append(
            {
                "code": "dirty",
                "severity": "high",
                "message": "Uncommitted changes in a worktree on this branch",
            }
        )

    if worktree_count > 1:
        issues.append(
            {
                "code": "multiple_worktrees",
                "severity": "medium",
                "message": f"{worktree_count} worktrees checked out on this branch",
            }
        )

    if ahead > 0 and behind > 0:
        issues.append(
            {
                "code": "diverged",
                "severity": "high",
                "message": f"Diverged from {base}: {ahead} ahead, {behind} behind",
            }
        )
    elif behind > 0:
        issues.append(
            {
                "code": "behind_base",
                "severity": "medium",
                "message": f"{behind} commit(s) behind {base}",
            }
        )

    if not linked_tickets:
        prefix_match = any(branch.startswith(prefix) for prefix in AGENT_BRANCH_PREFIXES)
        if prefix_match or branch != base:
            severity = "high" if prefix_match else "low"
            issues.append(
                {
                    "code": "no_ticket",
                    "severity": severity,
                    "message": "No work item linked to this branch",
                }
            )

    if last_commit_date:
        try:
            committed = datetime.fromisoformat(last_commit_date.replace("Z", "+00:00"))
            if committed.tzinfo is None:
                committed = committed.replace(tzinfo=timezone.utc)
            cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
            if committed < cutoff and branch != base:
                issues.append(
                    {
                        "code": "stale",
                        "severity": "low",
                        "message": f"No commits in the last {STALE_DAYS} days",
                    }
                )
        except ValueError:
            pass

    if is_current and linked_tickets and ahead == 0 and behind == 0 and not dirty:
        # Healthy current branch — no extra noise
        pass

    return issues


def _branch_diff_options(
    repo_root: Path,
    *,
    base: str,
    branch: str,
    is_current: bool,
    upstream: str | None,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = [
        {"mode": "base", "label": f"vs {base}", "ref": base},
    ]
    if upstream:
        options.append({"mode": "remote", "label": f"vs {upstream}", "ref": upstream})
    if is_current:
        options.append({"mode": "unstaged", "label": "Unstaged changes", "ref": "working tree"})
        options.append({"mode": "uncommitted", "label": "Uncommitted changes", "ref": "HEAD"})
    return options


def branch_triage_snapshot(session: Session, workspace: Workspace) -> dict[str, Any]:
    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        return {
            "workspace_id": workspace.id,
            "workspace_slug": workspace.slug,
            "base_branch": "",
            "current_branch": "",
            "branches": [],
            "issue_count": 0,
        }

    base = _git_base_ref(repo_root) or "main"
    current = _current_branch(repo_root)
    branch_names = _list_branches(repo_root)
    worktrees = _parse_worktrees(repo_root)
    ticket_map = _ticket_branch_map(session, workspace.id)

    worktrees_by_branch: dict[str, list[dict[str, str]]] = {}
    for item in worktrees:
        branch_name = item.get("branch") or ""
        if not branch_name:
            continue
        worktrees_by_branch.setdefault(branch_name, []).append(
            {
                "path": item["path"],
                "label": item["label"],
                "dirty": _worktree_dirty(item["path"]),
            }
        )

    branches: list[dict[str, Any]] = []
    issue_count = 0

    for name in branch_names:
        ahead, behind = _branch_ahead_behind(repo_root, base, name)
        last = _branch_last_commit(repo_root, name)
        linked = ticket_map.get(name, [])
        wt_list = worktrees_by_branch.get(name, [])
        dirty = any(wt["dirty"] for wt in wt_list)
        is_current = (
            name == current
            and any(
                wt["path"] == str(repo_root.resolve())
                for wt in worktrees
                if wt.get("branch") == name
            )
            or name == current
        )

        issues = _detect_issues(
            branch=name,
            base=base,
            is_current=is_current,
            ahead=ahead,
            behind=behind,
            dirty=dirty,
            worktree_count=len(wt_list),
            linked_tickets=linked,
            last_commit_date=last["date"],
        )
        if issues:
            issue_count += 1

        upstream = _resolve_upstream_ref(repo_root, name)
        diff_options = _branch_diff_options(
            repo_root,
            base=base,
            branch=name,
            is_current=is_current,
            upstream=upstream,
        )

        branches.append(
            {
                "name": name,
                "is_current": is_current,
                "is_base": name == base,
                "ahead": ahead,
                "behind": behind,
                "dirty": dirty,
                "upstream": upstream,
                "diff_options": diff_options,
                "worktrees": wt_list,
                "linked_tickets": linked,
                "last_commit": last,
                "issues": issues,
            }
        )

    branches.sort(
        key=lambda item: (
            0 if item["issues"] else 1,
            -len(item["issues"]),
            item["name"].lower(),
        )
    )

    return {
        "workspace_id": workspace.id,
        "workspace_slug": workspace.slug,
        "base_branch": base,
        "current_branch": current,
        "branches": branches,
        "issue_count": issue_count,
    }


def _validate_diff_file_path(file_path: str) -> None:
    path = file_path.strip()
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError(f"Invalid file path: {file_path!r}")


def branch_diff_snapshot(
    workspace: Workspace,
    branch: str,
    *,
    base: str | None = None,
    mode: str = "base",
    file_path: str | None = None,
) -> dict[str, Any] | None:
    validate_branch_name(branch)
    allowed = {"base", "remote", "unstaged", "uncommitted"}
    if mode not in allowed:
        raise ValueError(f"Invalid diff mode: {mode}")
    if file_path:
        _validate_diff_file_path(file_path)
        return capture_branch_file_diff(workspace, branch, file_path, base=base, mode=mode)
    return branch_diff_manifest(workspace, branch, base=base, mode=mode)


def _branch_exists(repo_root: Path, branch: str) -> bool:
    proc = _git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return proc.returncode == 0


def _worktree_paths_for_branch(repo_root: Path, branch: str) -> list[str]:
    paths: list[str] = []
    for item in _parse_worktrees(repo_root):
        if item.get("branch") == branch:
            path = (item.get("path") or "").strip()
            if path:
                paths.append(path)
    return paths


def _format_worktree_block_message(branch: str, worktree_paths: list[str]) -> str:
    if len(worktree_paths) == 1:
        return (
            f"Cannot delete branch '{branch}' — it is checked out in worktree "
            f"{worktree_paths[0]}. Remove the worktree first, or delete with "
            "remove_worktrees enabled."
        )
    joined = ", ".join(worktree_paths)
    return (
        f"Cannot delete branch '{branch}' — it is checked out in {len(worktree_paths)} "
        f"worktrees: {joined}. Remove them first, or delete with remove_worktrees enabled."
    )


def _remove_git_worktree(repo_root: Path, worktree_path: str) -> None:
    proc = _git(repo_root, "worktree", "remove", "--force", worktree_path)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "worktree remove failed").strip()
        raise ValueError(detail)


def delete_branch(
    workspace: Workspace,
    branch: str,
    *,
    force: bool = False,
    remove_worktrees: bool = False,
) -> bool:
    """Delete a local branch. Returns True if the branch was removed, False if already gone."""
    validate_branch_name(branch)
    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        raise ValueError("Workspace is not a git repository")

    if not _branch_exists(repo_root, branch):
        return False

    current = _current_branch(repo_root)
    main_repo_path = str(repo_root.resolve())
    worktree_paths = _worktree_paths_for_branch(repo_root, branch)

    if branch == current and main_repo_path in worktree_paths:
        raise ValueError(
            "Cannot delete the currently checked-out branch. Checkout another branch first."
        )

    if worktree_paths and not remove_worktrees:
        raise ValueError(_format_worktree_block_message(branch, worktree_paths))

    if worktree_paths and remove_worktrees:
        for path in worktree_paths:
            if path == main_repo_path and branch == current:
                raise ValueError(
                    "Cannot delete the currently checked-out branch. Checkout another branch first."
                )
            _remove_git_worktree(repo_root, path)

    base = _git_base_ref(repo_root)
    if base and branch == base:
        raise ValueError("Cannot delete the base branch")

    args = ["branch", "-D" if force else "-d", branch]
    proc = _git(repo_root, *args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "delete failed").strip()
        if "not found" in detail.lower():
            return False
        if "used by worktree" in detail.lower():
            match_paths = worktree_paths or _worktree_paths_for_branch(repo_root, branch)
            if match_paths:
                raise ValueError(_format_worktree_block_message(branch, match_paths))
        raise ValueError(detail)
    return True
