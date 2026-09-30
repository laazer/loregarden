"""Read and write GitHub issues through the `gh` CLI.

The same transport `github_pr_service` uses for pull requests: `gh` owns the
credential, so loregarden never holds a token. Every call names its repository
with ``-R`` rather than letting `gh` infer it from the working directory, so a
link keeps pointing at the repo it was made against even if the workspace's
remote changes later.

Every failure raises `GithubIssueError` carrying `gh`'s own stderr — the caller
decides whether to surface it, record it on the link, or both. Nothing here
returns an empty answer for a failed call.
"""

from __future__ import annotations

import json
from pathlib import Path

from loregarden.models.domain import IssueClosure, IssueSnapshot
from loregarden.services.github_pr_service import run_gh
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_ISSUE_FIELDS = "number,title,body,state,stateReason,url,labels"


class GithubIssueError(ValueError):
    """A `gh` call failed, or answered with something that is not an issue."""


class GithubLabel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class GithubIssue(BaseModel):
    """An issue as `gh issue view/list --json` and the webhook payload spell it.

    Both shapes are accepted: `gh` says ``stateReason``/``url`` in upper case,
    the webhook says ``state_reason``/``html_url`` in lower case.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    number: int
    title: str
    body: str | None = None
    state: str
    state_reason: str | None = Field(default=None, alias="stateReason")
    url: str = ""
    html_url: str = ""
    labels: list[GithubLabel] = []

    @property
    def web_url(self) -> str:
        return self.html_url or self.url

    @property
    def closure(self) -> IssueClosure:
        # GitHub spells these OPEN/open and NOT_PLANNED/not_planned depending on
        # the API; lowercased, they are `IssueClosure`'s own values.
        if self.state.lower() == IssueClosure.OPEN:
            return IssueClosure.OPEN
        if (self.state_reason or "").lower() == IssueClosure.NOT_PLANNED:
            return IssueClosure.NOT_PLANNED
        return IssueClosure.COMPLETED

    def snapshot(self) -> IssueSnapshot:
        return IssueSnapshot(title=self.title, body=self.body or "", closure=self.closure)


def _gh(args: list[str], *, cwd: Path) -> str:
    result = run_gh(args, cwd=cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise GithubIssueError(f"gh {args[0]} {args[1]} failed: {detail or 'no output'}")
    return result.stdout


def _parse_issue(raw: str) -> GithubIssue:
    try:
        return GithubIssue.model_validate_json(raw)
    except ValidationError as exc:
        raise GithubIssueError(f"gh returned something that is not an issue: {exc}") from exc


def resolve_repo(cwd: Path) -> str:
    """The ``owner/name`` of the repository the workspace checkout points at."""
    out = _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=cwd)
    repo = out.strip()
    if "/" not in repo:
        raise GithubIssueError(f"gh repo view returned no repository: {out!r}")
    return repo


def get_issue(repo: str, number: int, *, cwd: Path) -> GithubIssue:
    return _parse_issue(
        _gh(["issue", "view", str(number), "-R", repo, "--json", _ISSUE_FIELDS], cwd=cwd)
    )


def list_open_issues(
    repo: str, *, cwd: Path, label: str = "", limit: int = 100
) -> list[GithubIssue]:
    args = [
        "issue",
        "list",
        "-R",
        repo,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        _ISSUE_FIELDS,
    ]
    if label:
        args += ["--label", label]
    raw = _gh(args, cwd=cwd)
    try:
        return [GithubIssue.model_validate(item) for item in json.loads(raw or "[]")]
    except (ValueError, ValidationError) as exc:
        raise GithubIssueError(f"gh issue list returned unparseable output: {exc}") from exc


def create_issue(repo: str, *, title: str, body: str, cwd: Path) -> tuple[int, str]:
    """Open an issue; returns its number and URL."""
    out = _gh(["issue", "create", "-R", repo, "--title", title, "--body", body], cwd=cwd)
    url = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if "/issues/" not in url:
        raise GithubIssueError(f"gh issue create returned no issue URL: {out!r}")
    number = url.rsplit("/issues/", 1)[-1].split("/", 1)[0]
    if not number.isdigit():
        raise GithubIssueError(f"gh issue create returned an unexpected URL: {url!r}")
    return int(number), url


def edit_issue(repo: str, number: int, *, cwd: Path, title: str | None, body: str | None) -> None:
    args = ["issue", "edit", str(number), "-R", repo]
    if title is not None:
        args += ["--title", title]
    if body is not None:
        args += ["--body", body]
    _gh(args, cwd=cwd)


def set_issue_closure(repo: str, number: int, closure: IssueClosure, *, cwd: Path) -> None:
    if closure is IssueClosure.OPEN:
        _gh(["issue", "reopen", str(number), "-R", repo], cwd=cwd)
        return
    reason = "not planned" if closure is IssueClosure.NOT_PLANNED else "completed"
    _gh(["issue", "close", str(number), "-R", repo, "--reason", reason], cwd=cwd)
