"""Whether a path may be moved to the Trash, decided on the path itself.

The only module that answers that question, and it answers it by **refusal**:
every check below names something that would be lost, and a path is allowed
only when none fires. A check that cannot be completed refuses too. An
unprovable premise is not a satisfied one (the rule `worktree_lifecycle`
already lives by).

In order:

1. **Shape.** Absolute, normalized, exists, not a symlink, no symlinked
   component, inside a scanned root and not the root itself. Anything outside
   what the person can see in the picture is never touched.
2. **Protected.** The home directory, `/`, every workspace checkout, every
   repository's main working tree, loregarden's own checkout and its data
   directory: never the path, never anything containing one.
3. **Inside `.git`.** An object store is never trimmed by hand.
4. **In use.** Any process whose working directory is inside the path (an
   agent, a shell, a Claude session in that worktree).
5. **Git.** A linked worktree goes only when `worktree_lifecycle.checkout_loss`
   finds nothing (no uncommitted or untracked change, no commit only HEAD
   reaches), no agent run owns it, and nothing git ignores in it looks
   irreplaceable (`.env`, a database, a key). Inside a working tree, a path may
   hold no tracked file and no untracked one: only what git ignores.
6. **Outside git**, only a dependency, build or cache folder, by its own name
   or an ancestor's.

Even then it goes to the Trash, not away: recoverable until the person empties it.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.config import settings
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    REGENERABLE_KINDS,
    DiskCleanupMethod,
    DiskKind,
    Ticket,
    Worktree,
    WorktreeState,
)
from loregarden.services.checkout_guard import checkout_removal_refusal, find_precious, named
from loregarden.services.disk_usage.roots import ScanPlan, WorktreeRef, list_worktrees
from loregarden.services.disk_usage.scanner import kind_for_name
from loregarden.services.disk_usage.store import data_dir, database_path
from loregarden.services.git_subprocess import run_git
from pydantic import BaseModel, Field
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


class Verdict(BaseModel):
    path: str
    kind: DiskKind
    allowed: bool
    method: DiskCleanupMethod | None = None
    #: Why it is refused. Empty exactly when `allowed`.
    reasons: list[str] = Field(default_factory=list)
    #: What the person should know before confirming an allowed one.
    notes: list[str] = Field(default_factory=list)
    #: `dev:ino:mtime_ns` at assessment; execute refuses if it changed.
    fingerprint: str = ""
    worktree_repo: str = ""


@dataclass
class SafetyContext:
    """Everything the checks consult, read once per preview or execute."""

    plan: ScanPlan
    protected: list[Path]
    loregarden_data: Path
    #: pid, command and working directory of every visible process, or None
    #: when they could not be listed (which refuses everything).
    process_cwds: list[tuple[str, str, Path]] | None
    worktree_rows: dict[Path, Worktree] = field(default_factory=dict)
    ticket_states: dict[str, object] = field(default_factory=dict)


def list_process_cwds() -> list[tuple[str, str, Path]] | None:
    """Every process's working directory, via lsof; None when lsof cannot answer.

    lsof exits 1 when it could not inspect *some* process, while still listing
    the rest, so the exit code alone does not decide: output decides.
    """
    try:
        proc = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fpcn"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("lsof could not list process working directories: %s", exc)
        return None
    if not proc.stdout.strip():
        logger.warning("lsof listed no processes (exit %s): %s", proc.returncode, proc.stderr)
        return None
    found: list[tuple[str, str, Path]] = []
    pid = command = ""
    for line in proc.stdout.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid, command = value, ""
        elif tag == "c":
            command = value
        elif tag == "n" and value.startswith("/"):
            found.append((pid, command, Path(value)))
    return found


def build_context(session: Session, plan: ScanPlan) -> SafetyContext:
    main_trees = [ref.path for ref in plan.worktrees.values() if ref.is_main]
    data = data_dir().resolve()
    protected = [
        Path("/"),
        Path.home().resolve(),
        settings.repo_root.resolve(),
        data,
        database_path().resolve(),
        *plan.workspace_roots,
        *main_trees,
    ]
    rows: dict[Path, Worktree] = {}
    for row in session.exec(select(Worktree).where(Worktree.state == WorktreeState.ACTIVE)).all():
        if row.worktree_path:
            rows[Path(row.worktree_path).resolve()] = row
    ticket_ids = {row.ticket_id for row in rows.values() if row.ticket_id}
    states = {}
    if ticket_ids:
        for ticket in session.exec(select(Ticket).where(Ticket.id.in_(ticket_ids))).all():
            states[ticket.id] = ticket.state
    return SafetyContext(
        plan=plan,
        protected=protected,
        loregarden_data=data,
        process_cwds=list_process_cwds(),
        worktree_rows=rows,
        ticket_states=states,
    )


def fingerprint(st: os.stat_result) -> str:
    return f"{st.st_dev}:{st.st_ino}:{st.st_mtime_ns}"


def _git_lines(args: list[str], cwd: Path) -> list[str] | None:
    proc = run_git(args, cwd=str(cwd), check=False, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        logger.warning("git %s in %s failed: %s", " ".join(args), cwd, (proc.stderr or "").strip())
        return None
    return [line for line in proc.stdout.split("\0" if "-z" in args else "\n") if line]


def regenerable_kind(path: Path, root: Path) -> DiskKind | None:
    """The kind a dependency, build or cache folder at or above `path` gives it."""
    if path == root:
        return None
    for part in reversed(path.relative_to(root).parts):
        kind = kind_for_name(part)
        if kind in REGENERABLE_KINDS:
            return kind
    return None


class _Refused(Exception):
    def __init__(self, *reasons: str) -> None:
        super().__init__(reasons[0])
        self.reasons = list(reasons)


def _check_shape(path_text: str) -> tuple[Path, os.stat_result]:
    path = Path(path_text)
    if not path.is_absolute() or os.path.normpath(path_text) != path_text:
        raise _Refused("Not an absolute, normalized path.")
    try:
        st = path.lstat()
    except FileNotFoundError:
        raise _Refused("It no longer exists.") from None
    except OSError as exc:
        raise _Refused(f"Could not read it: {exc.strerror or exc}.") from None
    if path.is_symlink():
        raise _Refused("It is a symlink; removing it would not free what it points at.")
    if os.path.realpath(path) != str(path):
        raise _Refused("Its path runs through a symlinked folder.")
    return path, st


def _scan_root_for(ctx: SafetyContext, path: Path) -> Path:
    for root in ctx.plan.roots:
        if path == root.path:
            ref = ctx.plan.worktrees.get(path)
            if ref is not None and not ref.is_main:
                # A lone worktree outside its checkout is scanned as its own root.
                return root.path
            raise _Refused("It is a scanned folder itself. Remove it from the scan instead.")
        if path.is_relative_to(root.path):
            return root.path
    raise _Refused("It is outside every scanned folder.")


def _check_protected(ctx: SafetyContext, path: Path) -> None:
    for guarded in ctx.protected:
        if guarded == path or guarded.is_relative_to(path):
            raise _Refused(f"It is or contains {guarded}, which cleanup never touches.")
    if path.is_relative_to(ctx.loregarden_data):
        raise _Refused("It is loregarden's own data. Its runs and database live there.")
    if ".git" in path.parts:
        raise _Refused("It is inside a git object store.")


def _check_in_use(ctx: SafetyContext, path: Path) -> None:
    if ctx.process_cwds is None:
        raise _Refused("Could not list running processes, so cannot tell whether one is using it.")
    users = [
        f"{command or 'process'} (pid {pid})"
        for pid, command, cwd in ctx.process_cwds
        if cwd == path or cwd.is_relative_to(path)
    ]
    if users:
        raise _Refused(f"In use: {named(users)} is working inside it.")


def _owning_row_refusal(ctx: SafetyContext, path: Path) -> str | None:
    row = ctx.worktree_rows.get(path)
    if row is None:
        return None
    if row.chat_session_id:
        return "A Home chat thread owns this worktree. Delete the thread to release it."
    if (
        row.ticket_id
        and ctx.ticket_states.get(row.ticket_id) in StateMachine.TERMINAL_TICKET_STATES
    ):
        return None
    return "An unfinished ticket's run owns this worktree."


def _assess_worktree(ctx: SafetyContext, path: Path, ref: WorktreeRef) -> Verdict:
    if ref.is_main:
        raise _Refused("It is the repository's main checkout.")
    owned = _owning_row_refusal(ctx, path)
    if owned:
        raise _Refused(owned)
    refusal = checkout_removal_refusal(path)
    if refusal:
        raise _Refused(refusal)
    branch = ref.branch or "its detached HEAD"
    return Verdict(
        path=str(path),
        kind=DiskKind.WORKTREE,
        allowed=True,
        method=DiskCleanupMethod.TRASH_WORKTREE,
        notes=[
            f"Branch {branch} keeps every commit. Only the checkout goes.",
            f"Afterwards git forgets the worktree ({ref.repo_root}).",
        ],
        worktree_repo=str(ref.repo_root),
    )


def _discover_worktree(path: Path) -> WorktreeRef | None:
    """The worktree at `path` when its repository is not a registered workspace.

    A `.git` file marks a linked worktree or a submodule. Asked of git itself,
    from inside: `git worktree list` names the main working tree first, so a
    submodule (its own main tree) comes back `is_main` and is refused.
    """
    if not (path / ".git").is_file():
        return None
    refs = list_worktrees(path, "")
    if refs is None:
        raise _Refused("git could not say which repository this checkout belongs to.")
    found = next((ref for ref in refs if ref.path == path), None)
    if found is None or not refs:
        return found
    # Asked from inside the worktree, so the repository is the main tree git named first.
    return WorktreeRef(found.path, refs[0].path, found.branch, found.is_main, "")


def _containing_tree(ctx: SafetyContext, path: Path) -> Path | None:
    known = [p for p in ctx.plan.worktrees if path.is_relative_to(p)]
    if known:
        return max(known, key=lambda p: len(p.parts))
    probe = run_git(
        ["rev-parse", "--show-toplevel"],
        cwd=str(path if path.is_dir() else path.parent),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return Path(probe.stdout.strip()).resolve()
    return None


def _assess_in_tree(path: Path, tree: Path, root: Path, kind: DiskKind) -> Verdict:
    rel = str(path.relative_to(tree))
    tracked = _git_lines(["ls-files", "-z", "--", rel], tree)
    if tracked is None:
        raise _Refused("git could not list the tracked files here.")
    if tracked:
        raise _Refused(f"{len(tracked)} file(s) here are tracked by git. Remove them through git.")
    untracked = _git_lines(
        ["status", "--porcelain", "--untracked-files=all", "-z", "--", rel], tree
    )
    if untracked is None:
        raise _Refused("git could not list the untracked files here.")
    if untracked:
        raise _Refused(f"{len(untracked)} untracked file(s) here exist nowhere else.")
    notes = ["git ignores everything here."]
    if regenerable_kind(path, root) is None:
        hits, finished = find_precious(path)
        if not finished:
            raise _Refused("Too many files to check for irreplaceable ones.")
        if hits:
            raise _Refused(f"It holds files nothing regenerates: {named(hits)}.")
    else:
        notes.append("A package manager or build puts it back.")
    return Verdict(
        path=str(path), kind=kind, allowed=True, method=DiskCleanupMethod.TRASH, notes=notes
    )


def assess(ctx: SafetyContext, path_text: str) -> Verdict:
    """The verdict on one path. Never raises for a refusal; it says so."""
    kind = DiskKind.DIRECTORY
    try:
        path, st = _check_shape(path_text)
        kind = DiskKind.DIRECTORY if path.is_dir() else DiskKind.FILE
        root = _scan_root_for(ctx, path)
        kind = regenerable_kind(path, root) or kind
        _check_protected(ctx, path)
        _check_in_use(ctx, path)
        ref = ctx.plan.worktrees.get(path) or _discover_worktree(path)
        if ref is not None:
            verdict = _assess_worktree(ctx, path, ref)
        else:
            tree = _containing_tree(ctx, path)
            if tree is not None:
                verdict = _assess_in_tree(path, tree, root, kind)
            elif kind in REGENERABLE_KINDS:
                verdict = Verdict(
                    path=str(path),
                    kind=kind,
                    allowed=True,
                    method=DiskCleanupMethod.TRASH,
                    notes=["A package manager, build or app puts it back."],
                )
            else:
                raise _Refused(
                    "Not in git and not a dependency, build or cache folder, so loregarden "
                    "cannot tell what it holds. Delete it yourself if you are sure."
                )
        verdict.fingerprint = fingerprint(st)
        return verdict
    except _Refused as refused:
        return Verdict(path=path_text, kind=kind, allowed=False, reasons=refused.reasons)
