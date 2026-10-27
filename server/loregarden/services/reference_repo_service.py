"""Reference repos — third-party checkouts the ticket studio scoper reads from.

The recurring workflow this serves: you find a repo that solves part of the problem
you are about to scope, and you want the parts of it that are useful here. The repo
is cloned shallowly into a cache outside every workspace (never into the project
checkout, which the orchestrator would sweep into a commit), recorded per workspace
so the same clone is reused by every later session, and handed to the scoper as an
extra readable directory.

Clones are treated as read-only source material: nothing here writes into one beyond
`clone`/`fetch`/`reset --hard`, and the paths are confined to the cache root.
"""

from __future__ import annotations

import fcntl
import logging
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from loregarden.config import settings
from loregarden.models.domain import (
    ReferenceRepo,
    ReferenceRepoCreate,
    ReferenceRepoView,
    Workspace,
)
from loregarden.services.git_subprocess import run_git
from sqlmodel import Session, select

# Hosts are matched loosely (any git host works), but each path segment must be a
# plain name: no traversal, no shell-significant characters, nothing that could turn
# into a flag when it reaches git's argv.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SSH_RE = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+)$")
_ALLOWED_SCHEMES = ("https", "http", "ssh")

#: A `.git` lock younger than this may belong to a git command someone is running
#: in the clone by hand (an agent's `git status` takes `index.lock` for a moment),
#: so it is left for git to report. Every loregarden writer is excluded by the
#: refresh lock; only those outside callers need the grace.
_STALE_LOCK_GRACE_SECONDS = 60

logger = logging.getLogger(__name__)


class ReferenceRepoError(ValueError):
    """A reference repo could not be parsed, cloned, or refreshed."""


@dataclass(frozen=True)
class ParsedRepoUrl:
    url: str
    host: str
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.host}/{self.owner}/{self.name}"


def cache_root() -> Path:
    configured = (settings.reference_repo_cache_dir or "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".loregarden" / "reference_repos"
    )
    return root.resolve()


def parse_repo_url(raw: str) -> ParsedRepoUrl:
    """Normalize a clone URL into host/owner/name, rejecting anything unsafe.

    Accepts `https://host/owner/repo(.git)` and `git@host:owner/repo(.git)`. Longer
    paths (GitLab subgroups) collapse into the owner segment so the slug stays three
    parts wide.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise ReferenceRepoError("Repository URL is required")
    if url.startswith("-"):
        raise ReferenceRepoError("Invalid repository URL")

    ssh = _SSH_RE.match(url)
    if ssh:
        host = ssh.group("host")
        path = ssh.group("path")
    else:
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ReferenceRepoError(
                "Repository URL must be an https or ssh clone URL (e.g. "
                "https://github.com/owner/repo)"
            )
        if not parsed.hostname:
            raise ReferenceRepoError("Invalid repository URL: no host")
        # Credentials in the URL would be persisted in the database and in git's
        # remote config; make the caller use their normal git credential helper.
        if parsed.username or parsed.password:
            raise ReferenceRepoError("Remove credentials from the repository URL")
        host = parsed.hostname
        path = parsed.path

    segments = [segment for segment in path.strip("/").split("/") if segment]
    if len(segments) < 2:
        raise ReferenceRepoError("Repository URL must include an owner and a repo name")
    segments[-1] = re.sub(r"\.git$", "", segments[-1])

    for segment in [host, *segments]:
        # "." and ".." pass the character class but are traversal once they reach a path.
        if segment in (".", "..") or not _SEGMENT_RE.match(segment):
            raise ReferenceRepoError(f"Unsupported characters in repository URL: {segment}")

    return ParsedRepoUrl(
        url=url,
        host=host,
        owner="-".join(segments[:-1]),
        name=segments[-1],
    )


def clone_path_for(parsed: ParsedRepoUrl) -> Path:
    """Where this repo's clone lives, confined to the cache root.

    Every segment is already validated, so the resolve() check is a belt-and-braces
    guard against a future caller reaching this with an unvalidated slug.
    """
    root = cache_root()
    path = (root / parsed.host / parsed.owner / parsed.name).resolve()
    if root not in path.parents:
        raise ReferenceRepoError("Refusing to place a clone outside the reference repo cache")
    return path


def is_cloned(path: Path | str) -> bool:
    return (Path(path) / ".git").exists()


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    result = run_git(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=settings.reference_repo_clone_timeout,
    )
    if result.returncode != 0:
        # The whole message: git's last line alone ("remove the file manually to
        # continue.") drops the line naming which file.
        detail = (result.stderr or result.stdout or "").strip()
        raise ReferenceRepoError(detail or f"git {args[0]} failed")
    return (result.stdout or "").strip()


@contextmanager
def _refresh_lock(path: Path) -> Iterator[None]:
    """Hold the clone's refresh lock: one clone/fetch/reset per repo at a time.

    The survey feeder, `add_repo` and `sync_repo` all refresh the same clone, and two
    fetches at once leave one failing on the other's `.git/*.lock`. `flock` is
    released by the kernel when its holder dies, so a crashed refresh never strands
    it. The lock file sits beside the clone, not in it, so it survives the clone
    being cleared.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / f"{path.name}.refresh-lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _clear_stale_git_locks(path: Path) -> None:
    """Remove `.git` locks no live refresh can own. Call only under `_refresh_lock`.

    A git killed mid-command (a server restart, a timeout) leaves its lock behind and
    every later command on the clone refuses until someone deletes it by hand.
    """
    git_dir = path / ".git"
    candidates = [*git_dir.glob("*.lock"), *(git_dir / "refs").rglob("*.lock")]
    cutoff = time.time() - _STALE_LOCK_GRACE_SECONDS
    for lock in candidates:
        try:
            if lock.stat().st_mtime > cutoff:
                continue
            lock.unlink()
        except FileNotFoundError:  # silent-ok: its owner finished and removed it; nothing is stale
            continue
        except OSError as exc:
            raise ReferenceRepoError(f"Could not remove stale git lock {lock}: {exc}") from exc
        logger.warning("Removed stale git lock %s left by an interrupted git command", lock)


def clone_or_refresh(parsed: ParsedRepoUrl) -> tuple[Path, str, str]:
    """Ensure a current shallow clone exists; return (path, default_branch, head_sha)."""
    path = clone_path_for(parsed)
    with _refresh_lock(path):
        if is_cloned(path):
            _clear_stale_git_locks(path)
            _run(["fetch", "--depth", "1", "origin", "HEAD"], cwd=path)
            _run(["reset", "--hard", "FETCH_HEAD"], cwd=path)
        else:
            # A leftover directory from a failed clone would make git refuse; clear it.
            if path.exists():
                shutil.rmtree(path)
            _run(["clone", "--depth", "1", "--single-branch", "--", parsed.url, str(path)])

        branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
        head = _run(["rev-parse", "HEAD"], cwd=path)
    return path, branch, head


def _view(repo: ReferenceRepo, workspace_slug: str) -> ReferenceRepoView:
    return ReferenceRepoView(
        id=repo.id,
        workspace_slug=workspace_slug,
        url=repo.url,
        slug=repo.slug,
        name=repo.name,
        local_path=repo.local_path,
        default_branch=repo.default_branch,
        head_sha=repo.head_sha,
        notes=repo.notes,
        cloned=is_cloned(repo.local_path) if repo.local_path else False,
        last_synced_at=repo.last_synced_at,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
    )


class ReferenceRepoService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _workspace(self, slug: str) -> Workspace:
        ws = self.session.exec(select(Workspace).where(Workspace.slug == slug)).first()
        if not ws:
            raise ReferenceRepoError(f"Workspace not found: {slug}")
        return ws

    def list_repos(self, *, workspace_slug: str) -> list[ReferenceRepoView]:
        ws = self._workspace(workspace_slug)
        rows = self.session.exec(
            select(ReferenceRepo)
            .where(ReferenceRepo.workspace_id == ws.id)
            .order_by(ReferenceRepo.created_at.asc())
        ).all()
        return [_view(row, ws.slug) for row in rows]

    def get_many(self, repo_ids: list[str]) -> list[ReferenceRepo]:
        """The rows for these ids, in the order given, skipping ids that no longer exist."""
        rows: list[ReferenceRepo] = []
        for repo_id in repo_ids:
            row = self.session.get(ReferenceRepo, repo_id)
            if row:
                rows.append(row)
        return rows

    def views_for(self, repo_ids: list[str]) -> list[ReferenceRepoView]:
        views: list[ReferenceRepoView] = []
        for row in self.get_many(repo_ids):
            ws = self.session.get(Workspace, row.workspace_id)
            views.append(_view(row, ws.slug if ws else ""))
        return views

    def add_repo(self, body: ReferenceRepoCreate) -> ReferenceRepoView:
        ws = self._workspace(body.workspace_slug)
        parsed = parse_repo_url(body.url)

        existing = self.session.exec(
            select(ReferenceRepo)
            .where(ReferenceRepo.workspace_id == ws.id)
            .where(ReferenceRepo.slug == parsed.slug)
        ).first()

        path, branch, head = clone_or_refresh(parsed)
        now = datetime.now(timezone.utc)

        row = existing or ReferenceRepo(workspace_id=ws.id, slug=parsed.slug)
        row.url = parsed.url
        row.name = parsed.name
        row.local_path = str(path)
        row.default_branch = branch
        row.head_sha = head
        row.last_synced_at = now
        row.updated_at = now
        if body.notes.strip():
            row.notes = body.notes.strip()
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _view(row, ws.slug)

    def sync_repo(self, repo_id: str) -> ReferenceRepoView:
        row = self.session.get(ReferenceRepo, repo_id)
        if not row:
            raise ReferenceRepoError("Reference repo not found")
        parsed = parse_repo_url(row.url)
        path, branch, head = clone_or_refresh(parsed)
        row.local_path = str(path)
        row.default_branch = branch
        row.head_sha = head
        row.last_synced_at = datetime.now(timezone.utc)
        row.updated_at = row.last_synced_at
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        ws = self.session.get(Workspace, row.workspace_id)
        return _view(row, ws.slug if ws else "")

    def delete_repo(self, repo_id: str, *, remove_clone: bool = False) -> None:
        row = self.session.get(ReferenceRepo, repo_id)
        if not row:
            raise ReferenceRepoError("Reference repo not found")
        if remove_clone and row.local_path:
            path = Path(row.local_path).resolve()
            # Only ever delete inside the cache; a hand-edited row must not turn
            # this into an arbitrary recursive delete.
            if cache_root() in path.parents and is_cloned(path):
                shutil.rmtree(path, ignore_errors=True)
        self.session.delete(row)
        self.session.commit()
