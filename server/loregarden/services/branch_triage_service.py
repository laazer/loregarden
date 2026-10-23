"""Branch triage: inspect repo branches, detect weird states, capture diffs."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from loregarden.models.domain import PrListingState, Ticket, Workspace
from loregarden.services.artifact_service import (
    _resolve_upstream_ref,
    branch_diff_manifest,
    capture_branch_file_diff,
    git_base_ref,
)
from loregarden.services.branch_landed import landed_branches
from loregarden.services.checkout_guard import checkout_removal_refusal
from loregarden.services.file_editor import _current_branch, _list_branches, _parse_worktrees
from loregarden.services.git_branch import resolve_ticket_branch, validate_branch_name
from loregarden.services.git_subprocess import run_git
from loregarden.services.github_pr_listing import (
    GhPage,
    GitHubReadError,
    PullRequestStates,
    list_pull_requests,
)
from loregarden.services.publish_ref import branch_for_pr_head
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, Field, TypeAdapter
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

STALE_DAYS = 30
RECENT_COMMIT_LIMIT = 8
COMMIT_SHA_RE = re.compile(r"^(?:HEAD|[0-9a-fA-F]{7,40})$")
AGENT_BRANCH_PREFIXES = ("loregarden/", "agent/")
BRANCH_SCAN_MAX_WORKERS = 8
REF_FIELD_SEP = "\x1f"
#: The branch snapshot reads local git only. On 2026-10-21, at 223 branches and
#: 47 worktrees, it took 4.8s in-process (3.6s of it the squash-merge check over
#: 212 ahead branches) and 7-10s through the API. The budget is half a browser's
#: 30s navigation timeout; a test holds the snapshot to it with no remote call.
SNAPSHOT_BUDGET_SECONDS = 15.0
#: The whole listing, every page, in one `gh` process. 674 PRs read in
#: 3.2-4.6s on 2026-10-21; past this GitHub is stalling, and the page says
#: PR status is unavailable rather than waiting on it.
PR_LISTING_TIMEOUT_SECONDS = 25.0
PR_LISTING_TTL_SECONDS = 120

_PR_FIELDS = "number headRefName state isDraft url title"

_pr_listing_cache: dict[str, tuple[float, PrListing]] = {}

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
        logger.warning(
            "Unreadable rev-list counts for %s...%s (%r); reporting 0/0",
            base,
            branch,
            parts,
            exc_info=True,
        )
        return 0, 0
    return ahead, behind


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


class _GhPullRequest(BaseModel):
    number: int
    head_ref_name: str = Field(alias="headRefName")
    state: str
    is_draft: bool = Field(alias="isDraft")
    url: str
    title: str


_PR_PAGES = TypeAdapter(list[GhPage[_GhPullRequest]])


@dataclass(frozen=True)
class PrListing:
    """A repository's pull requests keyed by head branch, and whether that is all of them.

    ``state`` comes from GitHub's ``pageInfo.hasNextPage`` on the last page read,
    not from comparing a row count with the number asked for: a page holding
    exactly ``first`` rows says nothing about whether more remain.
    """

    state: PrListingState
    by_head: dict[str, dict[str, Any]]
    listed_count: int
    #: GitHub's own count, from the first page; None when no page was read.
    total_count: int | None
    reason: str = ""


def _serialize_pr(pr: _GhPullRequest) -> dict[str, Any]:
    return {
        "state": pr.state.lower(),
        "is_draft": pr.is_draft,
        "url": pr.url,
        "number": pr.number,
        "title": pr.title,
    }


def _pr_rank(pr: _GhPullRequest) -> tuple[int, int]:
    """Rank PRs sharing a head branch the way `gh pr view` picks one: open, then newest."""
    return (0 if pr.state.lower() == "open" else 1, -pr.number)


def _fetch_pr_listing(repo_root: Path) -> PrListing:
    """Every PR in the repository, and whether GitHub said that is all of them.

    Completeness is the last page's `hasNextPage`, which GitHub sets, and never
    the number of rows: a page holding exactly as many rows as were asked for
    says nothing about whether more remain.
    """
    try:
        listing = list_pull_requests(
            repo_root,
            _PR_FIELDS,
            _PR_PAGES,
            states=PullRequestStates.ALL,
            timeout_seconds=PR_LISTING_TIMEOUT_SECONDS,
        )
    except GitHubReadError as exc:
        return PrListing(PrListingState.UNAVAILABLE, {}, 0, None, str(exc))
    best: dict[str, _GhPullRequest] = {}
    for pr in listing.pull_requests:
        head = branch_for_pr_head(pr.head_ref_name)
        current = best.get(head)
        if head and (current is None or _pr_rank(pr) < _pr_rank(current)):
            best[head] = pr
    listed = len(listing.pull_requests)
    if listing.state is PrListingState.INCOMPLETE:
        return PrListing(
            PrListingState.INCOMPLETE,
            _serialize_heads(best),
            listed,
            listing.total_count,
            "GitHub reported more pages after the last one returned",
        )
    return PrListing(PrListingState.COMPLETE, _serialize_heads(best), listed, listing.total_count)


def _serialize_heads(best: dict[str, _GhPullRequest]) -> dict[str, dict[str, Any]]:
    return {head: _serialize_pr(pr) for head, pr in best.items()}


def _pr_listing(repo_root: Path) -> PrListing:
    """`_fetch_pr_listing`, served from a TTL cache when the cached read was complete.

    Only a complete listing is cached: a partial one is a failure to report,
    and the next request should try again rather than repeat it.
    """
    key = str(repo_root)
    now = time.monotonic()
    cached = _pr_listing_cache.get(key)
    if cached and now - cached[0] < PR_LISTING_TTL_SECONDS:
        return cached[1]
    listing = _fetch_pr_listing(repo_root)
    if listing.state is PrListingState.COMPLETE:
        _pr_listing_cache[key] = (now, listing)
    else:
        logger.warning(
            "PR listing for %s is %s: read %d of %s PRs (%s); branches outside it show PR "
            "status unknown",
            repo_root,
            listing.state.value,
            listing.listed_count,
            "unknown" if listing.total_count is None else listing.total_count,
            listing.reason,
        )
    return listing


def branch_pull_requests(workspace: Workspace) -> dict[str, Any]:
    """PR status for the workspace's local branches, and whether it is all of it.

    Separate from `branch_triage_snapshot` so the page paints from local git
    without waiting on GitHub. When ``state`` is ``complete``, a branch absent
    from ``pull_requests`` has no PR; otherwise its status is unknown.
    """
    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        listing = PrListing(PrListingState.UNAVAILABLE, {}, 0, None, "not a git repository")
        branch_names: list[str] = []
    else:
        listing = _pr_listing(repo_root)
        branch_names = _list_branches(repo_root)
    return {
        "workspace_slug": workspace.slug,
        "state": listing.state.value,
        "listed_count": listing.listed_count,
        "total_count": listing.total_count,
        "reason": listing.reason,
        "pull_requests": {
            name: listing.by_head[name] for name in branch_names if name in listing.by_head
        },
    }


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
            logger.warning(
                "Unreadable last-commit date %r on branch %s; skipping the stale check",
                last_commit_date,
                branch,
                exc_info=True,
            )

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


def resolve_branch_checkout(workspace: Workspace, branch: str) -> Path | None:
    """Checkout directory that owns ``branch``, if any.

    Prefers a linked worktree, then the primary repo when that branch is
    checked out there. Returns None when the branch is only a ref — callers
    that would write must refuse rather than editing some other active branch.
    """
    repo_root = resolve_workspace_root(workspace)
    for item in _parse_worktrees(repo_root):
        if item.get("branch") == branch:
            path = Path(item.get("path") or "")
            if path.is_dir():
                return path.resolve()
    if _current_branch(repo_root) == branch:
        return repo_root
    return None


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

    base = git_base_ref(repo_root) or "main"
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

    # Only branches that still look ahead can be squash-merged; `landed_branches`
    # recomputes only those whose answer the base or tip can have changed.
    squash_merged = landed_branches(
        repo_root, base, [name for name in branch_names if branch_refs[name].ahead > 0]
    )

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
                "squash_merged": name in squash_merged,
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


def worktree_paths_for_branch(repo_root: Path, branch: str) -> list[str]:
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


def _refuse_unsafe_removal(worktree_path: str) -> None:
    """Raise when removing the checkout would lose work (`checkout_guard`)."""
    refusal = checkout_removal_refusal(Path(worktree_path))
    if refusal:
        raise ValueError(f"Kept {worktree_path}: {refusal}")


def _remove_git_worktree(repo_root: Path, worktree_path: str) -> None:
    """Remove a checkout `_refuse_unsafe_removal` has cleared.

    Without `--force`: git then refuses a tree that changed since the check
    (or a locked one) on its own, a second guard rather than an override. A
    directory already gone is pruned, since `worktree remove` cannot find it.
    """
    if not Path(worktree_path).exists():
        proc = _git(repo_root, "worktree", "prune")
    else:
        proc = _git(repo_root, "worktree", "remove", worktree_path)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "worktree remove failed").strip()
        raise ValueError(detail)


def remove_branch_worktree(workspace: Workspace, branch: str, path: str) -> None:
    """Remove a single worktree linked to a branch, leaving the branch itself intact."""
    validate_branch_name(branch)
    repo_root = resolve_workspace_root(workspace)
    if not _is_git_repo(repo_root):
        raise ValueError("Workspace is not a git repository")

    worktree_paths = worktree_paths_for_branch(repo_root, branch)
    if path not in worktree_paths:
        raise ValueError(f"No worktree at '{path}' for branch '{branch}'")

    main_repo_path = str(repo_root.resolve())
    if path == main_repo_path:
        raise ValueError("Cannot remove the primary repository checkout")

    _refuse_unsafe_removal(path)
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

    worktree_paths = worktree_paths_for_branch(repo_root, branch)
    _refuse_branch_delete(repo_root, branch, worktree_paths, remove_worktrees=remove_worktrees)
    for path in worktree_paths:
        _remove_git_worktree(repo_root, path)
    return _git_delete_branch(repo_root, branch, worktree_paths, force=force)


_CHECKED_OUT = "Cannot delete the currently checked-out branch. Checkout another branch first."


def _refuse_branch_delete(
    repo_root: Path, branch: str, worktree_paths: list[str], *, remove_worktrees: bool
) -> None:
    """Every reason `delete_branch` would stop, raised before the first removal.

    A delete that stops halfway has already destroyed checkouts for a branch
    it then keeps, so nothing is removed until every worktree has passed.
    """
    main_repo_path = str(repo_root.resolve())
    if main_repo_path in worktree_paths:
        # The primary checkout has this branch out, which makes it the current one.
        raise ValueError(_CHECKED_OUT)
    base = git_base_ref(repo_root)
    if base and branch == base:
        raise ValueError("Cannot delete the base branch")
    if worktree_paths and not remove_worktrees:
        raise ValueError(_format_worktree_block_message(branch, worktree_paths))
    for path in worktree_paths:
        _refuse_unsafe_removal(path)


def _git_delete_branch(
    repo_root: Path, branch: str, worktree_paths: list[str], *, force: bool
) -> bool:
    proc = _git(repo_root, "branch", "-D" if force else "-d", branch)
    if proc.returncode == 0:
        return True
    detail = (proc.stderr or proc.stdout or "delete failed").strip()
    if "not found" in detail.lower():
        return False
    if "used by worktree" in detail.lower():
        match_paths = worktree_paths or worktree_paths_for_branch(repo_root, branch)
        if match_paths:
            raise ValueError(_format_worktree_block_message(branch, match_paths))
    raise ValueError(detail)
