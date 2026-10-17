"""Where a workspace's repository lives on its host, for the dashboard's repo link.

Read from git (`origin`'s URL) rather than `gh`, so the link still works when
`gh` cannot answer, which is the moment the PR table needs it most.
"""

from __future__ import annotations

import re
import subprocess

from loregarden.models.domain import Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel

GIT_TIMEOUT_SECONDS = 10
#: `git@host:owner/repo(.git)` and `ssh://git@host/owner/repo(.git)`.
_SCP_LIKE = re.compile(r"^(?:ssh://)?[\w.-]+@(?P<host>[\w.-]+)[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTP = re.compile(r"^https?://(?:[^@/]+@)?(?P<host>[\w.:-]+)/(?P<path>.+?)(?:\.git)?/?$")


class WorkspaceRemote(BaseModel):
    workspace_slug: str
    repo_root: str
    #: `origin`'s URL as git has it; "" when there is none.
    remote_url: str
    #: The browsable https URL, or "" when the remote is not one we can read.
    web_url: str
    #: Why there is no `web_url`; "" when there is one.
    error: str


def web_url_for(remote_url: str) -> str:
    """``https://host/owner/repo`` for an ssh or https remote, else ""."""
    for pattern in (_SCP_LIKE, _HTTP):
        match = pattern.match(remote_url.strip())
        if match:
            return f"https://{match.group('host')}/{match.group('path')}"
    return ""


def workspace_remote(workspace: Workspace) -> WorkspaceRemote:
    root = resolve_workspace_root(workspace)

    def result(remote_url: str = "", error: str = "") -> WorkspaceRemote:
        web_url = web_url_for(remote_url) if remote_url else ""
        if remote_url and not web_url:
            error = f"origin is {remote_url!r}, which is not a URL this page can link to"
        return WorkspaceRemote(
            workspace_slug=workspace.slug,
            repo_root=str(root),
            remote_url=remote_url,
            web_url=web_url,
            error=error,
        )

    try:
        proc = run_git(
            ["remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result(error=f"could not run git in {root}: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"git remote get-url exited {proc.returncode}"
        return result(error=detail)
    return result(remote_url=(proc.stdout or "").strip())
