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
- the failing jobs' log tails (`failure_log`), from `ci_checks`, and whether a
  failed job reached a verdict at all (`failed_job`, `merge_queue_failures`);
- a re-run of a run whose jobs did not (`rerun_failed_jobs`);
- the merge itself (`merge`), `pull_request_merge.squash_merge`, pinned to the
  staged head;
- a comment on the pull request (`comment`), where a PR admitted with no ticket
  is told why it left the queue.

Every read raises `GitHubReadError` when GitHub cannot be asked. None returns
an empty answer for a failure: "no checks yet" and "could not ask" stay apart
all the way to the entry (`MergeQueueWait`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol

from loregarden.models.domain import PullRequestCheckOutcome, PullRequestState
from loregarden.services.ci_checks import (
    GH_TIMEOUT_SECONDS,
    actions_job_id,
    check_name,
    failure_logs,
)
from loregarden.services.git_subprocess import run_gh
from loregarden.services.github_pr_listing import GitHubReadError, read_gh
from loregarden.services.merge_queue_failures import ActionsJob, ActionsRun
from loregarden.services.pull_request_merge import squash_merge
from loregarden.services.ticket_pull_request import GhCheck, check_outcome
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

_PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number url state title body headRefName headRefOid baseRefName baseRefOid
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

_COMMIT_CHECKS_QUERY = """
query($owner: String!, $name: String!, $oid: GitObjectID!) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) { ... on Commit {
      statusCheckRollup { contexts(first: 100) { nodes {
        ... on CheckRun { name status conclusion detailsUrl }
        ... on StatusContext { context state targetUrl }
      } } }
    } }
  }
}
"""

#: How GitHub refuses a rules or branch-protection read on a plan that has
#: neither (a private repository on the free plan). Its wording, not ours.
_PLAN_REFUSAL = "Upgrade to GitHub Pro or make this repository public"  # py-org: allow-string

#: GitHub caps a comment body at 65536 characters; leave room for its framing.
_COMMENT_LIMIT = 60000

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
    title: str = ""
    body: str = ""
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


class RequiredChecksUnavailable(GitHubReadError):
    """GitHub answered, and its answer is that this repository cannot require checks.

    Not a failure to ask: retrying gets the same refusal, so it must not be
    held as `GITHUB_UNREACHABLE` until the timeout (blobert#108 waited 91
    minutes on it, and the timeout then blamed its checks).
    """


class _CommitObject(BaseModel):
    status_check_rollup: _Rollup | None = Field(default=None, alias="statusCheckRollup")


class GatingChecks(BaseModel):
    """The checks the staged commit must pass, and where that list came from."""

    contexts: list[str]
    #: Empty when the base branch's own rules named them. Otherwise why they
    #: could not, and what stands in for them; the queue shows it, never hides it.
    fallback: str = ""


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
    #: What its author wrote. The staged commit carries them, since GitHub
    #: names a one-commit squash after its commit, and the body's `Ticket:`
    #: lines name the tickets the landing closes.
    title: str = ""
    body: str = ""


class QueueGitHub(Protocol):
    def required_contexts(self, base_branch: str) -> list[str]: ...

    def commit_checks(self, sha: str) -> list[GhCheck]: ...

    def read_pull_request(self, number: int) -> StagedPullRequest: ...

    def failure_log(self, checks: list[GhCheck]) -> str: ...

    def failed_job(self, url: str) -> ActionsJob | None: ...

    def actions_run(self, run_id: int) -> ActionsRun: ...

    def rerun_failed_jobs(self, run_id: int) -> str: ...

    def merge(self, number: int, head_sha: str) -> str: ...

    def open_pull_request(self, head_branch: str) -> tuple[int, str] | None: ...

    def comment(self, number: int, body: str) -> str: ...


class GhQueueGitHub:
    """`QueueGitHub` over the `gh` CLI, in one repository."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def required_contexts(self, base_branch: str) -> list[str]:
        try:
            raw = read_gh(
                self.repo_root,
                ["api", f"repos/{{owner}}/{{repo}}/rules/branches/{base_branch}"],
                GH_TIMEOUT_SECONDS,
            )
        except GitHubReadError as exc:
            if _PLAN_REFUSAL in str(exc):
                raise RequiredChecksUnavailable(
                    f"GitHub will not show {base_branch}'s rules on this plan "
                    "(a private repository on GitHub Free has no rulesets or branch protection)"
                ) from exc
            raise
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

    def commit_checks(self, sha: str) -> list[GhCheck]:
        raw = read_gh(
            self.repo_root,
            [
                "api",
                "graphql",
                "-F",
                "owner={owner}",
                "-F",
                "name={repo}",
                "-f",
                f"oid={sha}",
                "-f",
                f"query={_COMMIT_CHECKS_QUERY}",
            ],
            GH_TIMEOUT_SECONDS,
        )
        try:
            node = json.loads(raw)["data"]["repository"]["object"]
            if node is None:
                raise GitHubReadError(f"GitHub has no commit {sha[:12]}")
            commit = _CommitObject.model_validate(node)
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise GitHubReadError(f"unreadable checks for {sha[:12]}: {exc}") from exc
        rollup = commit.status_check_rollup
        return rollup.contexts.nodes if rollup is not None else []

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

    def failed_job(self, url: str) -> ActionsJob | None:
        """The Actions job behind a check's URL; None for a check that is not one."""
        job_id = actions_job_id(url)
        if job_id is None:
            return None
        raw = read_gh(
            self.repo_root,
            ["api", f"repos/{{owner}}/{{repo}}/actions/jobs/{job_id}"],
            GH_TIMEOUT_SECONDS,
        )
        try:
            return ActionsJob.model_validate_json(raw)
        except ValidationError as exc:
            raise GitHubReadError(f"unreadable job {job_id}: {exc}") from exc

    def actions_run(self, run_id: int) -> ActionsRun:
        raw = read_gh(
            self.repo_root,
            ["api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}"],
            GH_TIMEOUT_SECONDS,
        )
        try:
            return ActionsRun.model_validate_json(raw)
        except ValidationError as exc:
            raise GitHubReadError(f"unreadable run {run_id}: {exc}") from exc

    def rerun_failed_jobs(self, run_id: int) -> str:
        """Run a finished run's failed jobs again; "" when GitHub took it, else why not."""
        try:
            read_gh(
                self.repo_root,
                ["run", "rerun", str(run_id), "--failed"],
                GH_TIMEOUT_SECONDS,
            )
        except GitHubReadError as exc:
            return str(exc)
        return ""

    def merge(self, number: int, head_sha: str) -> str:
        return squash_merge(self.repo_root, number, head_sha)

    def comment(self, number: int, body: str) -> str:
        """Comment on PR ``number``; "" when posted, else why not."""
        try:
            proc = run_gh(
                ["pr", "comment", str(number), "--body", body[:_COMMENT_LIMIT]],
                cwd=self.repo_root,
                timeout=GH_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"gh did not run: {exc}"
        if proc.returncode == 0:
            return ""
        return (proc.stderr or proc.stdout or "").strip() or "gh pr comment failed"

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
        title=pr.title,
        body=pr.body,
    )


def gating_checks(github: QueueGitHub, base_branch: str, pr_head_sha: str) -> GatingChecks:
    """What the staged commit must pass: the base's required checks, or a named stand-in.

    Where GitHub cannot require checks at all, the stand-in is every check that
    reported on the pull request's own head before it was staged. Those are
    the repository's CI for this change, and naming them up front keeps the
    reason the rules are read at all: a check that has not reported on the
    staged commit yet is still waited for, not passed over. A head that
    reported nothing leaves nothing to gate on, and `contexts` comes back empty
    for the caller to refuse rather than land unverified.
    """
    try:
        return GatingChecks(contexts=github.required_contexts(base_branch))
    except RequiredChecksUnavailable as exc:
        names = sorted({check_name(check) for check in github.commit_checks(pr_head_sha)})
        stand_in = ", ".join(names) if names else "none"
        return GatingChecks(
            contexts=names,
            fallback=f"{exc}; gating on the checks the PR's head {pr_head_sha[:12]} "
            f"reported instead: {stand_in}",
        )


def gating_basis(gating: GatingChecks) -> str:
    """The stand-in, appended to what the entry says, so the operator reads it."""
    return f" ({gating.fallback})" if gating.fallback else ""


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


def read_gating(checks: list[GhCheck], required: list[str]) -> list[CheckReading]:
    """The required checks, then every other check that reported on the commit.

    GitHub lets a pull request merge with a failed check its base does not
    require, so the queue did too: #952 landed with Visual QA failing
    (2026-11-23), and main's own Visual QA failed until a fix landed 6.5 hours
    later. Visual QA cannot be required, because it runs only where a surface
    can have changed and a required check that never runs blocks the pull
    request for good. So what did run must pass, whatever the rules say.
    """
    others = sorted({check_name(check) for check in checks} - set(required))
    return read_required(checks, [*required, *others])


def unreported(checks: list[GhCheck], required: list[str]) -> list[str]:
    present = {check_name(check) for check in checks}
    return [name for name in required if name not in present]
