"""Pull requests the merge queue admits by number, whose head branch someone else owns.

A tree's entry stages onto ``publish/<branch>``, a ref only loregarden writes,
from an integration branch in the workspace's own repository. A pull request
admitted by number (an interactive session's branch) is neither: its head may
have been pushed from another clone, so the commit is fetched before it can be
cut, and its author may push again while it waits, so a head the queue did not
stage is the author's newer work to adopt, never a stale ref to overwrite.

The fetched head is pinned under ``refs/loregarden/merge-queue/`` so the commit
stays reachable after the queue pushes its staged commit over the branch.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain import MergeQueueEntry
from loregarden.services.git_subprocess import run_git

PIN_PREFIX = "refs/loregarden/merge-queue/pr-"


def author_owns_head(entry: MergeQueueEntry) -> bool:
    """True for a PR admitted by number: its head branch is its author's, not the queue's."""
    return entry.ticket_id is None


def pin_ref(pr_number: int) -> str:
    return f"{PIN_PREFIX}{pr_number}"


def fetch_pull_head(repo_root: Path, pr_number: int) -> tuple[str, str]:
    """Fetch PR ``pr_number``'s head from origin into its pin; (sha, "") or ("", why not)."""
    ref = pin_ref(pr_number)
    fetched = run_git(
        ["fetch", "--quiet", "origin", f"+refs/pull/{pr_number}/head:{ref}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if fetched.returncode != 0:
        reason = (fetched.stderr or fetched.stdout or "git fetch failed").strip()
        return "", f"could not fetch PR #{pr_number}'s head from origin: {reason}"
    tip = run_git(
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if tip.returncode != 0:
        return "", f"fetched PR #{pr_number}'s head but cannot read it: {tip.stderr.strip()}"
    return tip.stdout.strip(), ""


def drop_pull_pin(repo_root: Path, pr_number: int) -> str:
    """Delete PR ``pr_number``'s pin once it has landed; "" when done or absent, else why not."""
    ref = pin_ref(pr_number)
    exists = run_git(
        ["show-ref", "--verify", "--quiet", ref],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if exists.returncode != 0:
        return ""
    deleted = run_git(
        ["update-ref", "-d", ref],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if deleted.returncode != 0:
        return f"could not delete {ref}: {(deleted.stderr or deleted.stdout).strip()}"
    return ""
