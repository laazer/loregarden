"""Notice an integration branch that can no longer take its base, when the base moves.

Before this, a conflict between a tree's integration branch and `main` was
found only when something next cut a ticket from it. Nothing watched `main`,
so the first sign was a stage refusing to start, or a chat turn answering
"Baxter unavailable" (lg-durable-remote-335, after #560 changed a file the tree
had also changed). This sweep rides the reconcile timer and dry-runs the
refresh for every live tree, filing (and later closing) the inbox card in
`integration_conflict_card`.

It writes nothing to git: `merge_preview` is a `merge-tree` and an ancestry
check. It uses the same base ref the refresh does (the workspace profile's
`base_branch`, local), so what it reports is exactly what the next cut would
hit. Results are cached by the pair of tips, which fully determines them, so a
pass with nothing moved runs one `for-each-ref` per workspace and no merges.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_merge_noco import MergeOutcome, merge_preview
from loregarden.services.git_subprocess import run_git
from loregarden.services.integration_conflict_card import (
    clear_integration_conflict,
    report_integration_conflict,
)
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import INTEGRATION_PREFIX
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: (integration tip, base tip) → the preview for that pair. A pair's answer
#: never changes, so this cannot go stale; it only saves the `merge-tree`.
_PREVIEWS: dict[tuple[str, str], MergeOutcome] = {}


@dataclass(frozen=True)
class BranchHealth:
    branch: str
    root: str
    conflicted_files: tuple[str, ...]


def _tips(repo_root: Path, base: str) -> dict[str, str]:
    result = run_git(
        [
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            f"refs/heads/{INTEGRATION_PREFIX}",
            f"refs/heads/{base}",
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git for-each-ref failed in {repo_root}: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    tips: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, _, sha = line.rpartition(" ")
        tips[name] = sha
    return tips


def _roots(session: Session, workspace: Workspace, slugs: list[str]) -> dict[str, Ticket]:
    """Each slug's root ticket. `integration_branch_for` uses the external id,
    or the first 8 characters of the id when there is none."""
    found: dict[str, Ticket] = {}
    by_external = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace.id, col(Ticket.external_id).in_(slugs)
        )
    ).all()
    for ticket in by_external:
        found[ticket.external_id] = ticket
    for slug in slugs:
        if slug in found:
            continue
        by_id = session.exec(
            select(Ticket).where(
                Ticket.workspace_id == workspace.id, col(Ticket.id).startswith(slug)
            )
        ).first()
        if by_id is not None and not by_id.external_id.strip():
            found[slug] = by_id
    return found


def _preview(repo_root: Path, branch_sha: str, base_sha: str) -> MergeOutcome:
    key = (branch_sha, base_sha)
    cached = _PREVIEWS.get(key)
    if cached is None:
        cached = merge_preview(repo_root, branch_sha, base_sha)
        # A failed preview (git could not merge at all) is not cached: it may be
        # transient, and caching it would hide a recovery.
        if cached.ok or cached.conflicted:
            _PREVIEWS[key] = cached
    return cached


def check_workspace(session: Session, workspace: Workspace) -> list[BranchHealth]:
    """Preview every live tree's refresh in ``workspace``; file or close cards."""
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return []
    base = resolve_orchestration_profile(workspace).git.base_branch
    tips = _tips(repo_root, base)
    base_sha = tips.pop(base, "")
    if not base_sha or not tips:
        return []
    slugs = [name.removeprefix(INTEGRATION_PREFIX) for name in tips]
    roots = _roots(session, workspace, slugs)

    report: list[BranchHealth] = []
    for branch, branch_sha in sorted(tips.items()):
        root = roots.get(branch.removeprefix(INTEGRATION_PREFIX))
        # A branch with no root ticket here is not a tree this control plane
        # runs, and a finished tree's branch is never cut from again.
        if root is None or root.state in StateMachine.TERMINAL_TICKET_STATES:
            continue
        outcome = _preview(repo_root, branch_sha, base_sha)
        if outcome.conflicted:
            report_integration_conflict(
                session, root, branch=branch, base=base, files=outcome.conflicted_files
            )
        elif outcome.ok:
            clear_integration_conflict(session, root, branch=branch, base=base)
        else:
            logger.warning(
                "Could not preview %s taking %s in %s: %s",
                branch,
                base,
                repo_root,
                outcome.detail,
            )
            continue
        report.append(BranchHealth(branch, root.external_id, outcome.conflicted_files))
    return report


def check_integration_branches(session: Session) -> list[BranchHealth]:
    """The reconcile step: every active workspace. A workspace that fails is
    logged and the rest still run; the step itself raises only if every one did,
    so `reconcile_once` names it as failed."""
    workspaces = session.exec(select(Workspace).where(col(Workspace.archived_at).is_(None))).all()
    report: list[BranchHealth] = []
    failures = 0
    for workspace in workspaces:
        try:
            report.extend(check_workspace(session, workspace))
        except Exception:  # silent-ok: logged with traceback; one bad repo must not hide the others
            failures += 1
            logger.exception("Integration-branch check failed for workspace %s", workspace.slug)
    if workspaces and failures == len(workspaces):
        raise RuntimeError("integration-branch check failed for every workspace")
    return report
