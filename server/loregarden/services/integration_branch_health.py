"""Notice an integration branch that can no longer take its base, when the base moves.

Before this, a conflict between a tree's integration branch and `main` was
found only when something next cut a ticket from it. Nothing watched `main`,
so the first sign was a stage refusing to start, or a chat turn answering
"Baxter unavailable" (lg-durable-remote-335, after #560 changed a file the tree
had also changed). This sweep rides the reconcile timer and dry-runs the
refresh for every live tree, filing (and later closing) the inbox card in
`integration_conflict_card`.

The sweep itself writes nothing to git: `merge_preview` is a `merge-tree` and
an ancestry check. A conflicted tree is handed to the agent's merge turn
(`integration_merge_agent`) on a thread of its own, under the same switch and
per-tip budget a dispatch uses, and a card goes to a person only when no turn
may run or the turn does not resolve it. Before that, only a dispatch gave a
conflict a turn, so a tree with nothing to dispatch — every one the conflict had
already blocked — kept its card until a person merged by hand
(lg-run-durability-314, 2026-10-12: two import lines on each side).

The turn runs off the reconcile thread because it lasts as long as an agent
turn does; one conflicted tree must not hold up the pass for every other. It uses the same base ref the refresh does (the workspace profile's
`base_branch`, local), so what it reports is exactly what the next cut would
hit. Results are cached by the pair of tips, which fully determines them, so a
pass with nothing moved runs one `for-each-ref` per workspace and no merges.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from loregarden.core.state_machine import StateMachine
from loregarden.db.session import engine
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.drain import is_draining, refuse_reason
from loregarden.services.git_merge_noco import MergeOutcome, merge_preview, superseding_commit
from loregarden.services.git_subprocess import run_git
from loregarden.services.integration_conflict_card import (
    clear_integration_conflict,
    report_integration_conflict,
)
from loregarden.services.integration_merge_agent import merge_base_by_agent
from loregarden.services.integration_merge_budget import merge_turn_refusal
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: (integration tip, base tip) → the preview for that pair. A pair's answer
#: never changes, so this cannot go stale; it only saves the `merge-tree`.
_PREVIEWS: dict[tuple[str, str], MergeOutcome] = {}

#: (repo root, branch) of every merge turn this process has handed off and not
#: yet finished, so the next pass does not start a second one behind it.
_IN_FLIGHT: set[tuple[str, str]] = set()
_IN_FLIGHT_GUARD = threading.Lock()


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
        if cached.conflicted and superseding_commit(repo_root, branch_sha, base_sha):
            # The base already holds the branch's work (a squash merge); the next
            # cut moves the branch to the base tip instead of merging, so there
            # is nothing for a person to resolve.
            cached = MergeOutcome(ok=True, sha=base_sha, already_contained=True)
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
            refusal = merge_turn_refusal(session, workspace, root, branch_sha=branch_sha)
            if refusal:
                report_integration_conflict(
                    session,
                    root,
                    branch=branch,
                    base=base,
                    files=outcome.conflicted_files,
                    automatic=refusal,
                )
            else:
                schedule_merge_turn(
                    MergeTurn(
                        workspace.id, root.id, repo_root, branch, base, outcome.conflicted_files
                    )
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


@dataclass(frozen=True)
class MergeTurn:
    """A conflicted tree handed to the merge turn, by id: the turn opens its own session."""

    workspace_id: str
    root_id: str
    repo_root: Path
    branch: str
    base: str
    files: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.repo_root), self.branch)


def schedule_merge_turn(turn: MergeTurn) -> None:
    """Start ``turn`` on its own thread, unless one is already running for its branch.

    Inline under ``LOREGARDEN_SYNC_RUNS``, like every other background turn.
    Nothing starts while the process drains; the next process's sweep sees the
    same conflict and starts it there.
    """
    if is_draining():
        logger.warning("Not starting a merge turn for %s: %s", turn.branch, refuse_reason())
        return
    with _IN_FLIGHT_GUARD:
        if turn.key in _IN_FLIGHT:
            return
        _IN_FLIGHT.add(turn.key)
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        run_merge_turn(turn)
        return
    try:
        threading.Thread(
            target=run_merge_turn,
            args=(turn,),
            name=f"loregarden-integration-merge-{turn.branch}",
            daemon=True,
        ).start()
    except RuntimeError:
        _finished(turn)
        raise


def _finished(turn: MergeTurn) -> None:
    with _IN_FLIGHT_GUARD:
        _IN_FLIGHT.discard(turn.key)


def run_merge_turn(turn: MergeTurn) -> None:
    """Run the merge turn for ``turn``, then close its card or say why it stays open."""
    try:
        with Session(engine) as session:
            workspace = session.get(Workspace, turn.workspace_id)
            root = session.get(Ticket, turn.root_id)
            if workspace is None or root is None:
                logger.warning(
                    "Merge turn for %s skipped: workspace %s or root %s no longer exists",
                    turn.branch,
                    turn.workspace_id,
                    turn.root_id,
                )
                return
            resolution = merge_base_by_agent(
                session,
                workspace,
                root,
                repo_root=turn.repo_root,
                branch=turn.branch,
                base=turn.base,
                files=turn.files,
            )
            if resolution.ok:
                clear_integration_conflict(session, root, branch=turn.branch, base=turn.base)
            else:
                report_integration_conflict(
                    session,
                    root,
                    branch=turn.branch,
                    base=turn.base,
                    files=turn.files,
                    automatic=resolution.detail,
                )
    except Exception as exc:  # noqa: BLE001 — a thread's crash must reach the card, not vanish
        logger.exception("Merge turn for %s crashed", turn.branch)
        _report_crash(turn, exc)
    finally:
        _finished(turn)


def _report_crash(turn: MergeTurn, exc: Exception) -> None:
    """Put a crashed turn on the card, so the conflict does not look untried."""
    try:
        with Session(engine) as session:
            root = session.get(Ticket, turn.root_id)
            if root is not None:
                report_integration_conflict(
                    session,
                    root,
                    branch=turn.branch,
                    base=turn.base,
                    files=turn.files,
                    automatic=f"the merge turn crashed: {exc}",
                )
    except Exception:  # noqa: BLE001 — logged with traceback; the crash above is already logged too
        logger.exception("Could not record the crashed merge turn for %s", turn.branch)


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
