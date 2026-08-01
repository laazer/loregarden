"""Branch triage: inspect repo branches, detect weird states, capture diffs."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.artifact_service import (
    _git_base_ref,
    _resolve_upstream_ref,
    branch_diff_manifest,
    capture_branch_file_diff,
)
from loregarden.services.file_editor import _current_branch, _list_branches, _parse_worktrees
from loregarden.services.git_branch import resolve_ticket_branch, validate_branch_name
from loregarden.services.git_subprocess import run_git, scrubbed_git_env
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

STALE_DAYS = 30
RECENT_COMMIT_LIMIT = 8
COMMIT_SHA_RE = re.compile(r"^(?:HEAD|[0-9a-fA-F]{7,40})$")
AGENT_BRANCH_PREFIXES = ("loregarden/", "agent/")
PR_STATUS_TTL_SECONDS = 120
PR_STATUS_TERMINAL_TTL_SECONDS = 600
PR_STATUS_TERMINAL_STATES = ("closed", "merged")
PR_STATUS_MAX_WORKERS = 6
PR_LIST_LIMIT = 500
BRANCH_SCAN_MAX_WORKERS = 8
REF_FIELD_SEP = "\x1f"

_pr_status_cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}

_T = TypeVar("_T")
_R = TypeVar("_R")


def _map_parallel(fn: Callable[[_T], _R], items: Iterable[_T]) -> list[_R]:
    """Run `fn` over `items` on a small pool — every caller here shells out to git."""
    values = list(items)
    if not values:
        return []
    if len(values) == 1:
        return [fn(values[0])]
    with ThreadPoolExecutor(max_workers=min(BRANCH_SCAN_MAX_WORKERS, len(values))) as pool:
        return list(pool.map(fn, values))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_git(
        ["-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _is_git_repo(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


@dataclass(frozen=True)
class _BranchRef:
    """Everything one `refs/heads` entry contributes to the snapshot."""

    ahead: int
    behind: int
    last_commit: dict[str, str]
    upstream: str | None


def _unknown_branch_ref() -> _BranchRef:
    """Stand-in for a branch git did not report on, so the snapshot still lists it."""
    return _BranchRef(ahead=0, behind=0, last_commit={"date": "", "message": ""}, upstream=None)


def _remote_ref_names(repo_root: Path) -> set[str]:
    proc = _git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/remotes")
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}


def _upstream_from_ref_names(branch: str, configured: str, remote_names: set[str]) -> str | None:
    """`_resolve_upstream_ref` against an already-read ref list, so it costs no subprocess.

    The configured upstream is only honoured when the remote-tracking ref still
    exists: `branch.<name>.merge` outlives a deleted remote branch, and
    reporting a ref nothing resolves to would break the "vs remote" diff.
    """
    if configured and configured != "HEAD" and configured in remote_names:
        return configured
    for prefix in ("origin", "upstream"):
        candidate = f"{prefix}/{branch}"
        if candidate in remote_names:
            return candidate
    return None


def _branch_refs_batch(
    repo_root: Path, base: str, remote_names: set[str]
) -> dict[str, _BranchRef] | None:
    """Read ahead/behind, last commit and upstream for every branch in one git call.

    Returns None when git cannot count against `base` — an unresolvable base ref,
    or a git older than 2.41, which has no `ahead-behind` atom — so the caller can
    fall back to the per-branch commands.
    """
    fmt = REF_FIELD_SEP.join(
        (
            "%(refname:short)",
            "%(committerdate:iso-strict)",
            "%(upstream:short)",
            f"%(ahead-behind:{base})",
            "%(contents:subject)",
        )
    )
    proc = _git(repo_root, "for-each-ref", f"--format={fmt}", "refs/heads")
    if proc.returncode != 0:
        return None

    refs: dict[str, _BranchRef] = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split(REF_FIELD_SEP)
        if len(parts) != 5:
            continue
        name, date, configured, ahead_behind, subject = parts
        counts = ahead_behind.split()
        if len(counts) != 2 or not all(count.isdigit() for count in counts):
            return None
        refs[name] = _BranchRef(
            ahead=int(counts[0]),
            behind=int(counts[1]),
            last_commit={"date": date, "message": subject},
            upstream=_upstream_from_ref_names(name, configured, remote_names),
        )
    return refs


def _branch_refs_per_branch(
    repo_root: Path, base: str, branch_names: list[str]
) -> dict[str, _BranchRef]:
    """Fallback for `_branch_refs_batch`: one set of git calls per branch, in parallel."""

    def read(name: str) -> tuple[str, _BranchRef]:
        ahead, behind = _branch_ahead_behind(repo_root, base, name)
        return name, _BranchRef(
            ahead=ahead,
            behind=behind,
            last_commit=_branch_last_commit(repo_root, name),
            upstream=_resolve_upstream_ref(repo_root, name),
        )

    return dict(_map_parallel(read, branch_names))


def _branch_ahead_behind(repo_root: Path, base: str, branch: str) -> tuple[int, int]:
    """Raw commit counts. The squash-merge correction is applied by the caller."""
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
    return ahead, behind


def _branch_squash_merged(repo_root: Path, base: str, branch: str) -> bool:
    """Detect a branch whose commits were squashed and merged into base.

    Commit counts compare by SHA, so a branch that was squashed into a single
    commit on `base` still looks "ahead" even though its changes already landed
    — the squash commit has a different SHA than the branch's original commits.
    Treat the branch as merged if every file it touched since the merge-base is
    byte-identical between the branch tip and `base`. `base...branch` already
    diffs from the merge-base, so no separate merge-base lookup is needed.
    """
    files_proc = _git(repo_root, "diff", "--name-only", f"{base}...{branch}")
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


def _fetch_pr_status_live(repo_root: Path, branch: str) -> dict[str, Any] | None:
    """Look up the PR associated with a branch via the `gh` CLI (network call)."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "state,url,number,isDraft,title"],
            cwd=repo_root,
            # `gh` resolves the repo by shelling out to git, so an inherited
            # GIT_DIR would have it report the wrong repository's PRs.
            env=scrubbed_git_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        return None
    if not data:
        return None
    return _serialize_pr_row(data)


def _serialize_pr_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": str(row.get("state", "")).lower(),
        "is_draft": bool(row.get("isDraft")),
        "url": row.get("url", ""),
        "number": row.get("number"),
        "title": row.get("title", ""),
    }


def _pr_row_rank(row: dict[str, Any]) -> tuple[int, int]:
    """Rank PRs sharing a head branch the way `gh pr view` picks one: open, then newest."""
    state = str(row.get("state", "")).lower()
    try:
        number = int(row.get("number") or 0)
    except (TypeError, ValueError):
        number = 0
    return (0 if state == "open" else 1, -number)


def _fetch_pr_list(repo_root: Path) -> tuple[dict[str, dict[str, Any]], bool]:
    """Every PR in one `gh` call, keyed by head branch.

    Returns the mapping plus whether the listing is exhaustive. A truncated page
    (or a failed call) means a branch missing from the mapping may still have a
    PR, so the caller falls back to a per-branch lookup for those rather than
    reporting "no PR".
    """
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--limit",
                str(PR_LIST_LIMIT),
                "--json",
                "headRefName,state,url,number,isDraft,title",
            ],
            cwd=repo_root,
            # `gh` resolves the repo by shelling out to git, so an inherited
            # GIT_DIR would have it report the wrong repository's PRs.
            env=scrubbed_git_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}, False
    if proc.returncode != 0:
        return {}, False
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return {}, False
    if not isinstance(rows, list):
        return {}, False

    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        head = str(row.get("headRefName") or "")
        if not head:
            continue
        current = best.get(head)
        if current is None or _pr_row_rank(row) < _pr_row_rank(current):
            best[head] = row
    return {head: _serialize_pr_row(row) for head, row in best.items()}, len(rows) < PR_LIST_LIMIT


def _pr_status_ttl(value: dict[str, Any] | None) -> int:
    """Closed/merged PRs are done changing, so cache them far longer than open ones."""
    if value and value.get("state") in PR_STATUS_TERMINAL_STATES:
        return PR_STATUS_TERMINAL_TTL_SECONDS
    return PR_STATUS_TTL_SECONDS


def _branch_pr_statuses(repo_root: Path, branches: list[str]) -> dict[str, dict[str, Any] | None]:
    """Resolve PR status for each branch, serving from a TTL cache where possible."""
    now = time.monotonic()
    results: dict[str, dict[str, Any] | None] = {}
    stale: list[str] = []

    for name in branches:
        cached = _pr_status_cache.get((str(repo_root), name))
        if cached and now - cached[0] < _pr_status_ttl(cached[1]):
            results[name] = cached[1]
        else:
            stale.append(name)

    if not stale:
        return results

    listed, exhaustive = _fetch_pr_list(repo_root)
    unresolved: list[str] = []
    for name in stale:
        if name in listed:
            value = listed[name]
        elif exhaustive:
            value = None
        else:
            unresolved.append(name)
            continue
        _pr_status_cache[(str(repo_root), name)] = (now, value)
        results[name] = value

    if not unresolved:
        return results

    with ThreadPoolExecutor(max_workers=min(PR_STATUS_MAX_WORKERS, len(unresolved))) as pool:
        future_map = {
            pool.submit(_fetch_pr_status_live, repo_root, name): name for name in unresolved
        }
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                value = future.result()
            except Exception:
                value = None
            _pr_status_cache[(str(repo_root), name)] = (now, value)
            results[name] = value

    return results


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

    main_repo_path = str(repo_root.resolve())
    branch_worktrees = [item for item in worktrees if item.get("branch")]
    dirty_by_path = dict(
        _map_parallel(
            lambda path: (path, _worktree_dirty(path)),
            sorted({item["path"] for item in branch_worktrees}),
        )
    )
    worktrees_by_branch: dict[str, list[dict[str, Any]]] = {}
    for item in branch_worktrees:
        worktrees_by_branch.setdefault(item["branch"], []).append(
            {
                "path": item["path"],
                "label": item["label"],
                "dirty": dirty_by_path.get(item["path"], False),
                "is_primary": item["path"] == main_repo_path,
            }
        )

    remote_names = _remote_ref_names(repo_root)
    read_refs = _branch_refs_batch(repo_root, base, remote_names)
    if read_refs is None:
        read_refs = _branch_refs_per_branch(repo_root, base, branch_names)
    branch_refs = {name: read_refs.get(name) or _unknown_branch_ref() for name in branch_names}

    # Only branches that still look ahead can be squash-merged, and the check is
    # two git calls each — so it runs on the pool, over that subset alone.
    squash_merged = {
        name
        for name, merged in _map_parallel(
            lambda name: (name, _branch_squash_merged(repo_root, base, name)),
            [name for name in branch_names if branch_refs[name].ahead > 0],
        )
        if merged
    }

    pr_statuses = _branch_pr_statuses(repo_root, [name for name in branch_names if name != base])

    branches: list[dict[str, Any]] = []
    issue_count = 0

    for name in branch_names:
        ref = branch_refs[name]
        ahead = 0 if name in squash_merged else ref.ahead
        behind = ref.behind
        last = ref.last_commit
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

        upstream = ref.upstream
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
                "pr": pr_statuses.get(name),
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


def _unpushed_shas(repo_root: Path, branch: str, upstream: str) -> set[str]:
    proc = _git(repo_root, "rev-list", f"{upstream}..{branch}")
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}


def branch_activity(
    workspace: Workspace,
    branch: str,
    *,
    limit: int = RECENT_COMMIT_LIMIT,
) -> dict[str, Any]:
    """Recent commits on a branch, each marked as pushed or not.

    Commits are the only branch history git records, so this is what "recent
    activity" can honestly show: a push is inferred from whether the commit is
    reachable from the upstream ref. Test runs, stage completions, and other
    agent events leave no trace in the repository and are absent by design
    rather than by omission.
    """
    validate_branch_name(branch)
    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        return {"branch": branch, "upstream": None, "commits": []}

    upstream = _resolve_upstream_ref(repo_root, branch)
    # With no upstream nothing has been pushed; an empty unpushed set would
    # otherwise read as "every commit is on the remote".
    unpushed = _unpushed_shas(repo_root, branch, upstream) if upstream else None

    proc = _git(
        repo_root, "log", f"-{max(1, limit)}", "--format=%H%x1f%h%x1f%cI%x1f%an%x1f%s", branch
    )
    if proc.returncode != 0:
        return {"branch": branch, "upstream": upstream, "commits": []}

    commits: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        sha, short_sha, date, author, message = parts
        commits.append(
            {
                "sha": sha,
                "short_sha": short_sha,
                "date": date,
                "author": author,
                "message": message,
                "pushed": unpushed is not None and sha not in unpushed,
            }
        )
    return {"branch": branch, "upstream": upstream, "commits": commits}


def commit_snapshot(workspace: Workspace, sha: str) -> dict[str, Any]:
    """Read one commit by SHA without accepting arbitrary git revisions."""
    if not COMMIT_SHA_RE.fullmatch(sha):
        raise ValueError("Commit ref must be HEAD or a 7 to 40 character hexadecimal SHA")

    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        raise ValueError("Workspace is not a git repository")

    proc = _git(
        repo_root,
        "show",
        "--no-patch",
        "--format=%H%x00%h%x00%cI%x00%an%x00%s%x00%b",
        sha,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise ValueError("Commit not found")
    fields = proc.stdout.rstrip("\n").split("\x00", 5)
    if len(fields) != 6:
        raise ValueError("Commit metadata is unavailable")
    full_sha, short_sha, date, author, message, body = fields

    stats = _git(repo_root, "show", "--numstat", "--format=", full_sha)
    files_changed = 0
    insertions = 0
    deletions = 0
    if stats.returncode == 0:
        for line in (stats.stdout or "").splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            files_changed += 1
            if parts[0].isdigit():
                insertions += int(parts[0])
            if parts[1].isdigit():
                deletions += int(parts[1])

    remote_refs = _git(repo_root, "branch", "-r", "--contains", full_sha)
    return {
        "sha": full_sha,
        "short_sha": short_sha,
        "date": date,
        "author": author,
        "message": message,
        "body": body.strip(),
        "pushed": remote_refs.returncode == 0 and bool((remote_refs.stdout or "").strip()),
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
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


def remove_branch_worktree(workspace: Workspace, branch: str, path: str) -> None:
    """Remove a single worktree linked to a branch, leaving the branch itself intact."""
    validate_branch_name(branch)
    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        raise ValueError("Workspace is not a git repository")

    worktree_paths = _worktree_paths_for_branch(repo_root, branch)
    if path not in worktree_paths:
        raise ValueError(f"No worktree at '{path}' for branch '{branch}'")

    main_repo_path = str(repo_root.resolve())
    if path == main_repo_path:
        raise ValueError("Cannot remove the primary repository checkout")

    _remove_git_worktree(repo_root, path)


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
