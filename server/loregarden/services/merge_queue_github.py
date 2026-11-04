"""What the merge queue asks GitHub, behind one seam the loop can be tested through.

Three questions and one action:

- which checks the base branch requires (`required_contexts`) — read from the
  branch's own rules, not inferred from the checks that happen to have run,
  because a required check that has not reported yet is absent from the
  commit's rollup and a rollup over what is present would pass without it;
- what GitHub holds for the staged pull request (`read_pull_request`): its
  head and base commits, the head commit's *tree*, its checks, and once merged
  the merge commit and its tree — so the tree-equality assertion compares
  GitHub's own readings, not ours;
- the failing jobs' log tails (`failure_log`), from `ci_checks`;
- the merge itself (`merge`), `pull_request_merge.squash_merge`, pinned to the
  staged head.

Every read raises `GitHubReadError` when GitHub cannot be asked. None returns
an empty answer for a failure: "no checks yet" and "could not ask" stay apart
all the way to the entry (`MergeQueueWait`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from loregarden.models.domain import PullRequestCheckOutcome, PullRequestState
from loregarden.services.ci_checks import GH_TIMEOUT_SECONDS, check_name, failure_logs
from loregarden.services.github_pr_listing import GitHubReadError, read_gh
from loregarden.services.pull_request_merge import squash_merge
from loregarden.services.ticket_pull_request import GhCheck, check_outcome
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

_PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number url state headRefName headRefOid baseRefName baseRefOid
      mergeCommit { oid tree { oid } }
      commits(last: 1) { nodes { commit { oid tree { oid }
        statusCheckRollup { contexts(first: 100) { nodes {
          ... on CheckRun { name status conclusion detailsUrl }
          ... on StatusContext { context state targetUrl }
        } } }
      } } }
    }
  }
}
"""

#: GitHub's pull request states, which we do not own.
_STATE = {
    "OPEN": PullRequestState.OPEN,  # py-org: allow-string
    "MERGED": PullRequestState.MERGED,  # py-org: allow-string
    "CLOSED": PullRequestState.CLOSED,  # py-org: allow-string
}


class _Oid(BaseModel):
    oid: str


class _Commit(BaseModel):
    oid: str
    tree: _Oid
    status_check_rollup: _Rollup | None = Field(alias="statusCheckRollup")


class _Contexts(BaseModel):
    nodes: list[GhCheck]


class _Rollup(BaseModel):
    contexts: _Contexts


class _CommitNode(BaseModel):
    commit: _Commit


class _Commits(BaseModel):
    nodes: list[_CommitNode]


class _MergeCommit(BaseModel):
    oid: str
    tree: _Oid


class _GhPullRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: int
    url: str
    state: str
    head_branch: str = Field(alias="headRefName")
    head_sha: str = Field(alias="headRefOid")
    base_branch: str = Field(alias="baseRefName")
    base_sha: str = Field(alias="baseRefOid")
    merge_commit: _MergeCommit | None = Field(alias="mergeCommit")
    commits: _Commits


_Commit.model_rebuild()


class _OpenPull(BaseModel):
    number: int
    url: str


_OpenPulls = TypeAdapter(list[_OpenPull])


class CheckReading(BaseModel):
    """One check on the staged commit, as the entry records it."""

    name: str
    outcome: PullRequestCheckOutcome
    url: str = ""


class StagedPullRequest(BaseModel):
    """GitHub's reading of the queue's pull request at one moment."""

    number: int
    url: str
    state: PullRequestState
    head_branch: str
    head_sha: str
    #: The tree of the commit GitHub would merge — what the checks ran on.
    head_tree: str
    base_branch: str
    #: The base tip GitHub compares the head against.
    base_sha: str
    merge_commit: str = ""
    merge_tree: str = ""
    checks: list[GhCheck] = Field(default_factory=list)


class QueueGitHub(Protocol):
    def required_contexts(self, base_branch: str) -> list[str]: ...

    def read_pull_request(self, number: int) -> StagedPullRequest: ...

    def failure_log(self, checks: list[GhCheck]) -> str: ...

    def merge(self, number: int, head_sha: str) -> str: ...

    def open_pull_request(self, head_branch: str) -> tuple[int, str] | None: ...


class GhQueueGitHub:
    """`QueueGitHub` over the `gh` CLI, in one repository."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def required_contexts(self, base_branch: str) -> list[str]:
        raw = read_gh(
            self.repo_root,
            ["api", f"repos/{{owner}}/{{repo}}/rules/branches/{base_branch}"],
            GH_TIMEOUT_SECONDS,
        )
        try:
            rules = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubReadError(f"unreadable branch rules for {base_branch}: {exc}") from exc
        contexts: list[str] = []
        for rule in rules:
            if rule.get("type") != "required_status_checks":  # py-org: allow-string
                continue
            for check in rule.get("parameters", {}).get("required_status_checks", []):
                contexts.append(check["context"])
        return sorted(set(contexts))

    def read_pull_request(self, number: int) -> StagedPullRequest:
        raw = read_gh(
            self.repo_root,
            [
                "api",
                "graphql",
                "-F",
                "owner={owner}",
                "-F",
                "name={repo}",
                "-F",
                f"number={number}",
                "-f",
                f"query={_PR_QUERY}",
            ],
            GH_TIMEOUT_SECONDS,
        )
        try:
            node = json.loads(raw)["data"]["repository"]["pullRequest"]
            if node is None:
                raise GitHubReadError(f"GitHub has no pull request #{number}")
            pr = _GhPullRequest.model_validate(node)
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise GitHubReadError(f"unreadable answer for PR #{number}: {exc}") from exc
        return staged_pull_request(pr)

    def failure_log(self, checks: list[GhCheck]) -> str:
        logs = failure_logs(self.repo_root, checks)
        return "\n\n".join(part for part in (logs.text, *logs.errors) if part)

    def merge(self, number: int, head_sha: str) -> str:
        return squash_merge(self.repo_root, number, head_sha)

    def open_pull_request(self, head_branch: str) -> tuple[int, str] | None:
        """The open PR whose head is ``head_branch``; None when there is none.

        Raises `GitHubReadError` when `gh` cannot answer, so "no PR" and
        "could not ask" stay two answers.
        """
        raw = read_gh(
            self.repo_root,
            ["pr", "list", "--head", head_branch, "--state", "open", "--json", "number,url"],
            GH_TIMEOUT_SECONDS,
        )
        try:
            listed = _OpenPulls.validate_json(raw or "[]")
        except ValidationError as exc:
            raise GitHubReadError(f"unreadable PR list for {head_branch}: {exc}") from exc
        return (listed[0].number, listed[0].url) if listed else None


def staged_pull_request(pr: _GhPullRequest) -> StagedPullRequest:
    head = pr.commits.nodes[0].commit if pr.commits.nodes else None
    if head is not None and head.oid != pr.head_sha:
        # `commits(last: 1)` and `headRefOid` read at different instants
        # disagree only when the head moved between them. Reporting the
        # older commit's tree as the head's would be the mismatch the queue
        # exists to refuse, so this is a failure to read, not an answer.
        raise GitHubReadError(
            f"PR #{pr.number}'s head moved while it was being read "
            f"({head.oid[:12]} vs {pr.head_sha[:12]}); read again"
        )
    rollup = head.status_check_rollup if head is not None else None
    return StagedPullRequest(
        number=pr.number,
        url=pr.url,
        state=_STATE.get(pr.state, PullRequestState.OPEN),
        head_branch=pr.head_branch,
        head_sha=pr.head_sha,
        head_tree=head.tree.oid if head is not None else "",
        base_branch=pr.base_branch,
        base_sha=pr.base_sha,
        merge_commit=pr.merge_commit.oid if pr.merge_commit else "",
        merge_tree=pr.merge_commit.tree.oid if pr.merge_commit else "",
        checks=rollup.contexts.nodes if rollup is not None else [],
    )


def read_required(checks: list[GhCheck], required: list[str]) -> list[CheckReading]:
    """Each required context's outcome on the commit; PENDING for one not yet reported.

    A required check absent from the rollup is not passing — it has not run —
    so it reads PENDING with no URL, and `unreported` names it.
    """
    by_name = {check_name(check): check for check in checks}
    readings: list[CheckReading] = []
    for name in required:
        check = by_name.get(name)
        if check is None:
            readings.append(CheckReading(name=name, outcome=PullRequestCheckOutcome.PENDING))
            continue
        readings.append(
            CheckReading(
                name=name,
                outcome=check_outcome(check),
                url=check.details_url or check.target_url,
            )
        )
    return readings


def unreported(checks: list[GhCheck], required: list[str]) -> list[str]:
    present = {check_name(check) for check in checks}
    return [name for name in required if name not in present]
