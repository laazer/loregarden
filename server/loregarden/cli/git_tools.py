"""`loregarden git …` — git chores across the workspaces loregarden manages.

    loregarden git change-pr --repo PATH --branch B --title T -- <command> {worktree} …
    loregarden git change-pr --all-workspaces --branch B --title T -- <command> …

`change-pr` runs a command inside a fresh worktree of each repository and opens a
pull request with whatever it changed, leaving the repository's own checkout
alone. See `services/change_pr.py` for the steps. Refreshing every workspace's
hook block is one invocation (`task workspace:hooks:pr`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loregarden.cli.errors import UsageError
from loregarden.db.session import engine
from loregarden.models.domain import Workspace
from loregarden.services.change_pr import (
    ChangeOutcome,
    ChangePrError,
    ChangeRequest,
    open_change_pr,
)
from loregarden.services.git_subprocess import run_gh
from loregarden.services.workspace_integration import is_loregarden, primary_checkout
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select


def _workspace_repos() -> list[Path]:
    """Every live workspace's root except loregarden's own repository."""
    checkout = primary_checkout()
    with Session(engine) as session:
        workspaces = session.exec(
            select(Workspace).where(col(Workspace.archived_at).is_(None)).order_by(Workspace.slug)
        ).all()
        return [
            resolve_workspace_root(workspace)
            for workspace in workspaces
            if checkout is None or not is_loregarden(workspace, checkout)
        ]


def _gh_token(user: str | None) -> str | None:
    if not user:
        return None
    result = run_gh(["auth", "token", "--user", user], cwd=Path.cwd())
    if result.returncode != 0:
        raise UsageError(f"no gh token for {user!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def _change_pr(args: argparse.Namespace) -> str:
    command = args.change_command[1:] if args.change_command[:1] == ["--"] else args.change_command
    if not command:
        raise UsageError("give the change command after `--`")
    if bool(args.repo) == args.all_workspaces:
        raise UsageError("pass --repo (one or more) or --all-workspaces, not both")
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
    repos = [Path(repo).resolve() for repo in args.repo] if args.repo else _workspace_repos()
    token = _gh_token(args.gh_user)

    failures: list[str] = []
    for repo in repos:
        request = ChangeRequest(repo, args.branch, args.title, body, command)
        try:
            result = open_change_pr(request, gh_token=token)
        except ChangePrError as exc:
            failures.append(str(repo))
            print(f"failed: {repo}: {exc}", flush=True)
            continue
        if result.outcome is ChangeOutcome.OPENED:
            print(f"opened: {repo} {result.detail}", flush=True)
        else:
            print(f"no change: {repo} ({result.detail.splitlines()[0]})", flush=True)
    sys.stdout.flush()
    if failures:
        raise ChangePrError(f"{len(failures)} of {len(repos)} failed: {', '.join(failures)}")
    return ""


def register(sub: argparse._SubParsersAction) -> None:
    """Add `git change-pr` to the root CLI's `git` group."""
    change = sub.add_parser(
        "change-pr",
        help="Run a command in a fresh worktree of each repo and open a PR with what it changed.",
    )
    change.add_argument(
        "--repo", action="append", default=[], help="Repository root; repeat for several."
    )
    change.add_argument(
        "--all-workspaces",
        action="store_true",
        help="Every live workspace in the database, except loregarden itself.",
    )
    change.add_argument("--branch", required=True, help="New branch name, the same in every repo.")
    change.add_argument("--title", required=True, help="PR title and commit subject.")
    body = change.add_mutually_exclusive_group()
    body.add_argument("--body", default="", help="PR body and commit message body.")
    body.add_argument("--body-file", help="Read the PR body from this file.")
    change.add_argument(
        "--gh-user",
        help="Open PRs as this logged-in gh account, when the active one cannot push.",
    )
    # Not `command`: the `git` group's subparsers already own that dest.
    change.add_argument(
        "change_command",
        metavar="command",
        nargs=argparse.REMAINDER,
        help="After `--`: the command to run in the worktree; {worktree} is its path.",
    )
    change.set_defaults(run=_change_pr)
