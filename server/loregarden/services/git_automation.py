"""Land a finished run's work, as far as its workspace allows.

Commit, push, open a PR, enable auto-merge — each its own switch, run in that
order and stopped at the first one that is off (see
``git_automation_config.enabled_steps`` for why the order is a chain rather
than four independent choices).

The point is unattended operation: a queue that runs overnight should not stop
at "the work is done, now come and press some buttons". So every step reports
what it did rather than raising, and a step that cannot proceed ends the
pipeline with a reason attached to the run instead of taking the run down with
it. The work is already committed by then; the failure is about publishing it.

Existing single-ticket operations (``commit_and_push_ticket_branch``,
``create_ticket_pull_request``) stay where they are — they run in the workspace
checkout on a human's request, which is a different thing from a run publishing
its own worktree.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    Ticket,
    Workspace,
    Worktree,
)
from loregarden.services.git_automation_config import enabled_steps, resolve_git_automation
from loregarden.services.git_branch import (
    chat_session_branch,
    resolve_ticket_branch,
    validate_branch_name,
)
from loregarden.services.git_subprocess import run_git, scrubbed_git_env
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.workspace_paths import resolve_run_root, resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str = ""


@dataclass
class AutomationResult:
    """What the pipeline managed to do, in order."""

    steps: list[StepResult] = field(default_factory=list)
    pr_url: str = ""

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    @property
    def failure(self) -> StepResult | None:
        return next((step for step in self.steps if not step.ok), None)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "pr_url": self.pr_url,
            "steps": [{"step": s.step, "ok": s.ok, "detail": s.detail} for s in self.steps],
        }


@dataclass(frozen=True)
class PublishSubject:
    """What a run's work is published *as*.

    The pipeline used to read these four strings off a ``Ticket``, which made
    "has a ticket" a precondition for publishing at all — so a Home chat thread,
    which does real work and has no ticket, had no way to commit and left its
    edits loose in the operator's checkout. Naming the fields the steps actually
    need is what lets both owners through the same chain.
    """

    branch: str
    commit_message: str
    pr_title: str
    pr_body: str


def subject_for_ticket(session: Session, run: AgentRun, ticket: Ticket) -> PublishSubject:
    """How a ticket's work is published — unchanged from when this was inline."""
    return PublishSubject(
        branch=_branch_for_run(session, run, ticket),
        commit_message=f"{ticket.external_id}: {ticket.title}",
        pr_title=f"{ticket.external_id}: {ticket.title}",
        pr_body="\n".join(
            [
                ticket.description.strip() or "_No description._",
                "",
                f"- Ticket: `{ticket.external_id}`",
                f"- Workflow stage: `{ticket.workflow_stage_key or '—'}`",
                "",
                "_Opened automatically by the Loregarden queue._",
            ]
        ),
    )


def subject_for_chat_session(
    session: Session, run: AgentRun, chat_session: BaxterChatSession
) -> PublishSubject:
    """How a Home chat thread's work is published.

    The commit message names the conversation rather than the turn: a thread
    commits once per acting turn onto one branch, and a message that repeated
    the turn's text would make `git log` a transcript.
    """
    title = chat_session.title.strip() or "Home chat"
    return PublishSubject(
        branch=_branch_for_chat_run(session, run, chat_session),
        commit_message=f"chat: {title}",
        pr_title=f"chat: {title}",
        pr_body="\n".join(
            [
                f"Work from the Home chat thread **{title}**.",
                "",
                f"- Chat session: `{chat_session.id}`",
                f"- Run: `{run.run_code}`",
                "",
                "_Opened automatically by the Loregarden chat rail._",
            ]
        ),
    )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return run_git(args, cwd=cwd, capture_output=True, text=True)


def _fail_text(result: subprocess.CompletedProcess, fallback: str) -> str:
    return ((result.stderr or result.stdout) or fallback).strip()


def _commit(repo_root: Path, subject: PublishSubject) -> StepResult:
    add = _git(["add", "-A"], repo_root)
    if add.returncode != 0:
        return StepResult("commit", False, _fail_text(add, "git add failed"))

    message = subject.commit_message
    commit = _git(["commit", "-m", message], repo_root)
    if commit.returncode != 0:
        combined = f"{commit.stdout}\n{commit.stderr}".lower()
        if "nothing to commit" in combined:
            # Not a failure. A stage that only read the codebase, or whose work
            # a previous stage already committed, has nothing of its own — and
            # stopping the pipeline here would block a PR that should open.
            return StepResult("commit", True, "nothing to commit")
        return StepResult("commit", False, _fail_text(commit, "git commit failed"))
    return StepResult("commit", True, message)


def _push(repo_root: Path, branch: str) -> StepResult:
    push = _git(["push", "-u", "origin", branch], repo_root)
    if push.returncode != 0:
        return StepResult("push", False, _fail_text(push, "git push failed"))
    return StepResult("push", True, f"pushed {branch}")


def _gh(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # `gh` resolves its repository through git, so an inherited GIT_DIR would
    # aim these at whatever repo the parent process was pointed at.
    return subprocess.run(
        ["gh", *args],
        cwd=cwd,
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
    )


def _existing_pr_url(repo_root: Path, branch: str) -> str:
    view = _gh(["pr", "view", branch, "--json", "url", "--jq", ".url"], repo_root)
    if view.returncode != 0:
        return ""
    url = view.stdout.strip()
    return url if url.startswith("http") else ""


def _open_pr(repo_root: Path, subject: PublishSubject, base: str) -> tuple[StepResult, str]:
    # A rerun of a stage on the same branch must not fail the pipeline just
    # because the PR it would open is already open.
    existing = _existing_pr_url(repo_root, subject.branch)
    if existing:
        return StepResult("open_pr", True, f"already open: {existing}"), existing

    result = _gh(
        [
            "pr",
            "create",
            "--title",
            subject.pr_title,
            "--body",
            subject.pr_body,
            "--head",
            subject.branch,
            "--base",
            base,
        ],
        repo_root,
    )
    if result.returncode != 0:
        return StepResult("open_pr", False, _fail_text(result, "gh pr create failed")), ""

    url = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    if not url.startswith("http"):
        return StepResult("open_pr", False, f"unexpected gh output: {result.stdout!r}"), ""
    return StepResult("open_pr", True, url), url


def _auto_merge(repo_root: Path, pr_url: str) -> StepResult:
    if not pr_url:
        return StepResult("auto_merge", False, "no pull request to merge")

    # --auto queues the merge behind the PR's required checks rather than
    # merging now. That is the point: the queue should not land work that CI
    # has not passed, and it should not sit waiting for CI either.
    result = _gh(["pr", "merge", pr_url, "--auto", "--squash"], repo_root)
    if result.returncode != 0:
        detail = _fail_text(result, "gh pr merge failed")
        return StepResult("auto_merge", False, detail)
    return StepResult("auto_merge", True, "auto-merge enabled")


def publish_run(
    session: Session,
    run: AgentRun,
    workspace: Workspace,
    subject: PublishSubject,
    config: GitAutomationConfig,
) -> AutomationResult:
    """Run the configured publish steps for a finished run's checkout.

    Owner-agnostic: everything owner-specific is already decided in ``subject``.
    :func:`run_git_automation` is the ticket entry point and
    ``chat_publish.publish_chat_turn`` the conversational one.
    """
    result = AutomationResult()
    if not config.commit:
        return result

    workspace_root = resolve_workspace_root(workspace)
    repo_root = resolve_run_root(session, run, workspace_root)
    if not (repo_root / ".git").exists():
        result.steps.append(StepResult("resolve", False, f"not a git repository: {repo_root}"))
        return result

    try:
        validate_branch_name(subject.branch)
    except ValueError as exc:
        result.steps.append(StepResult("resolve", False, str(exc)))
        return result

    # enabled_steps owns the chain. Re-deriving it here with a ladder of
    # `if not config.x: return` is how the two drift apart.
    for step in enabled_steps(config):
        if step == "commit":
            result.steps.append(_commit(repo_root, subject))
        elif step == "push":
            result.steps.append(_push(repo_root, subject.branch))
        elif step == "open_pr":
            pr_step, pr_url = _open_pr(repo_root, subject, config.base_branch)
            result.steps.append(pr_step)
            result.pr_url = pr_url
        elif step == "auto_merge":
            result.steps.append(_auto_merge(repo_root, result.pr_url))

        # A step that failed makes every later one meaningless — an unpushed
        # branch has no PR to open, and an unopened PR has nothing to merge.
        if not result.ok:
            return result

    return result


def run_git_automation(
    session: Session,
    run: AgentRun,
    ticket: Ticket,
    config: GitAutomationConfig | None = None,
) -> AutomationResult:
    """Run the configured publish steps for a finished ticket run."""
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        result = AutomationResult()
        result.steps.append(StepResult("resolve", False, "workspace not found"))
        return result

    return publish_run(
        session,
        run,
        workspace,
        subject_for_ticket(session, run, ticket),
        config or resolve_git_automation(workspace, ticket),
    )


def _branch_for_run(session: Session, run: AgentRun, ticket: Ticket) -> str:
    """The branch this run's work is on.

    A run in a worktree is on the branch that worktree was created with, which
    is not necessarily the ticket's branch — the ticket may have been retargeted
    since. Pushing the ticket's branch from a worktree checked out on another
    one pushes the wrong commits.
    """
    if run.worktree_id:
        worktree = session.get(Worktree, run.worktree_id)
        if worktree and worktree.branch:
            return worktree.branch
    return resolve_ticket_branch(ticket)


def _branch_for_chat_run(session: Session, run: AgentRun, chat_session: BaxterChatSession) -> str:
    """Same, for a chat thread: the tree's branch beats the derived name.

    A thread renamed after its first acting turn derives a different branch than
    the one its worktree is checked out on, and pushing the derived name from
    that tree pushes commits that are not there.
    """
    if run.worktree_id:
        worktree = session.get(Worktree, run.worktree_id)
        if worktree and worktree.branch:
            return worktree.branch
    return chat_session_branch(chat_session)
