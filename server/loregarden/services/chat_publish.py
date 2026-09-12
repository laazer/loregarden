"""Land a chat turn's work, and say where it went either way.

An acting chat turn writes real code. Until this module existed nothing then
happened to it: no commit, no branch, no report — the edits simply sat in
whatever checkout the turn ran in, discoverable only by an operator who thought
to run `git status`. Two of them were found that way, a day later, on `main`.

So this does two things, and the second matters as much as the first:

1. Runs the workspace's configured publish steps, through the same
   `git_automation` chain a ticket's stage run uses.
2. Returns a note for the reply saying what happened — including, and
   especially, when the policy published nothing. A thread whose work is sitting
   uncommitted on a branch nobody has been told about is the same silent failure
   as the loose edits on `main`, just better hidden.

The two surfaces differ on the first point only. A Home chat thread owns its
branch and nothing else will ever publish it, so it publishes its own work. A
ticket triage turn writes into the ticket's worktree, where the ticket's next
stage commits it along with everything else on that branch — publishing there
would open a pull request out of a conversation. It gets the note alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    Ticket,
    Workspace,
    Worktree,
)
from loregarden.services.git_automation import (
    AutomationResult,
    publish_run,
    subject_for_chat_session,
)
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_run_root, resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnPublishOutcome:
    """What became of an acting turn's edits, in words an operator can act on."""

    #: Where the turn actually wrote. The shared workspace checkout when no
    #: worktree could be used, which is the case the note must not hide.
    repo_root: Path
    #: The branch the work is on, or "" when the turn ran in the shared checkout
    #: and is therefore on whatever the operator had checked out.
    branch: str
    #: Whether the turn left changes behind at all.
    dirty: bool
    #: The publish chain's report. None when nothing tried to publish — either
    #: the turn changed nothing, or this surface deliberately does not publish.
    automation: AutomationResult | None
    #: Set when this surface leaves publishing to something else, naming what.
    #: Distinct from an automation of None with no handler: "nobody published
    #: this" and "the ticket's pipeline will" need different advice.
    deferred_to: str = ""

    def as_note(self) -> str:
        """:meth:`summary` as a block to append to a reply, or "" if silent."""
        summary = self.summary()
        return f"\n\n---\n{summary}" if summary else ""

    def summary(self) -> str:
        """One sentence on where the work is, or "" when there is nothing to say.

        Empty only when the turn changed nothing — the one case where there is
        genuinely nothing to tell anyone. Separate from :meth:`as_note` because
        the Home rail delivers this as a message of its own rather than appended
        to a reply, and a leading `---` in a standalone message is a rule across
        an empty bubble.
        """
        if not self.dirty:
            return ""

        where = f"`{self.branch}`" if self.branch else f"`{self.repo_root}` (shared checkout)"
        if self.deferred_to:
            return f"**Work left on {where}**, for {self.deferred_to} to commit."

        if self.automation is None or not self.automation.steps:
            return (
                f"**Work left on {where}, uncommitted.** "
                "This workspace's git automation has `commit` off, so nothing was "
                "published. Commit it yourself, or turn the step on in the "
                "workspace's orchestration profile."
            )

        failure = self.automation.failure
        if failure:
            return (
                f"**Work is on {where}, but publishing stopped at "
                f"`{failure.step}`:** {failure.detail}"
            )

        steps = [step.step for step in self.automation.steps]
        if steps == ["commit"]:
            # The common case under a commit-only policy, and "published" would
            # overstate it: the branch is local, and a reader who believed
            # otherwise would go looking for a pull request that is not there.
            return f"**Committed to {where}.** Not pushed."

        done = ", ".join(f"`{step}`" for step in steps)
        line = f"**Published from {where}:** {done}."
        if self.automation.pr_url:
            line += f" {self.automation.pr_url}"
        return line


def _is_dirty(repo_root: Path) -> bool | None:
    """Whether `repo_root` has uncommitted changes. None if git could not say.

    Three-valued on purpose: "clean" and "unreadable" must not collapse, because
    "clean" is what suppresses the note that tells the operator where their work
    is.
    """
    status = run_git(
        ["status", "--porcelain"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        logger.warning(
            "Could not read git status in %s: %s",
            repo_root,
            (status.stderr or "").strip(),
        )
        return None
    return bool(status.stdout.strip())


@dataclass(frozen=True)
class _TurnCheckout:
    run: AgentRun
    repo_root: Path
    branch: str
    dirty: bool


def _inspect(session: Session, run_id: str, workspace: Workspace) -> _TurnCheckout | None:
    """Where this turn ran and whether it left anything, or None if it had no run."""
    run = session.get(AgentRun, run_id) if run_id else None
    if run is None:
        return None

    repo_root = resolve_run_root(session, run, resolve_workspace_root(workspace))
    worktree = session.get(Worktree, run.worktree_id) if run.worktree_id else None
    # An unreadable status counts as dirty: the note is the only thing that tells
    # the operator where to look, and staying quiet because git would not answer
    # is exactly the failure this module exists to prevent.
    return _TurnCheckout(
        run=run,
        repo_root=repo_root,
        branch=worktree.branch if worktree else "",
        dirty=_is_dirty(repo_root) is not False,
    )


def publish_chat_turn(
    session: Session,
    run_id: str,
    chat_session: BaxterChatSession,
    workspace: Workspace,
) -> TurnPublishOutcome | None:
    """Publish what a Home chat turn wrote, and report where it ended up.

    Returns None only when there is no run to publish from — an advisory turn,
    or an adapter that never opened one. Every other path returns an outcome,
    including the ones that published nothing.
    """
    checkout = _inspect(session, run_id, workspace)
    if checkout is None:
        return None
    if not checkout.dirty:
        return TurnPublishOutcome(
            repo_root=checkout.repo_root, branch=checkout.branch, dirty=False, automation=None
        )

    automation = publish_run(
        session,
        checkout.run,
        workspace,
        subject_for_chat_session(session, checkout.run, chat_session),
        resolve_git_automation(workspace),
    )
    if not automation.ok:
        logger.warning(
            "Chat thread %s could not publish: %s", chat_session.id, automation.as_dict()
        )
    return TurnPublishOutcome(
        repo_root=checkout.repo_root,
        branch=checkout.branch,
        dirty=True,
        automation=automation if automation.steps else None,
    )


def report_ticket_turn(
    session: Session,
    run_id: str,
    ticket: Ticket,
    workspace: Workspace,
) -> TurnPublishOutcome | None:
    """Report where a ticket triage turn's work landed, without publishing it.

    The ticket's pipeline owns that branch: its next stage commits everything on
    the worktree, so committing here would race it and opening a pull request
    would turn a conversation into one. The operator still has to be told the
    work exists and where — a triage turn that quietly leaves edits for a stage
    that may never run is the stranded-work failure in a different costume.
    """
    checkout = _inspect(session, run_id, workspace)
    if checkout is None:
        return None
    return TurnPublishOutcome(
        repo_root=checkout.repo_root,
        branch=checkout.branch,
        dirty=checkout.dirty,
        automation=None,
        deferred_to=f"ticket `{ticket.external_id}`'s next stage",
    )
