"""Check the environment traps instead of remembering them.

Every check here corresponds to a failure this control plane has actually had,
and to a paragraph in CLAUDE.md or an agent memory file written afterwards so the
next person would remember. That is the part worth replacing: the knowledge is
mechanical, the diagnosis is a shell command, and the remediation is one line.
Prose telling an agent to remember something is the weakest possible enforcement.

Two rules hold for every check. None of them writes to the repository, fetches
from a remote, or reads the value of a credential — a diagnostic that changes
what it is diagnosing is not one. And none of them raises: an exception inside a
check becomes a FAIL for that check alone, because a doctor that dies on its
first surprise is worse than no doctor.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from loregarden.config import resolved_database_path, settings
from loregarden.models.domain import (
    DoctorCheck,
    DoctorFinding,
    DoctorStatus,
    PortabilityState,
    Ticket,
    Workspace,
)
from loregarden.services.git_subprocess import GIT_LOCATION_ENV_VARS, run_git
from sqlmodel import Session, select

#: Checks cheap and decisive enough to run before every agent dispatch. The rest
#: are informational, and a doctor that adds a second to every stage gets turned
#: off. See `services.handoff_boundary` for the sibling pre-dispatch check.
DISPATCH_PREFLIGHT_CHECKS = (
    DoctorCheck.GIT_CORE_BARE,
    DoctorCheck.GIT_ENV_LEAK,
    DoctorCheck.REPO_HAS_COMMIT,
)


def _ok(check: DoctorCheck, finding: str) -> DoctorFinding:
    return DoctorFinding(check=check, status=DoctorStatus.PASS, finding=finding)


def check_git_core_bare(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """`core.bare` set true in a checkout that has a working tree.

    Agent worktree creation has left this behind, and the symptom is unrelated to
    the cause: every work-tree operation afterwards fails with an exit-128
    checkout error naming a path that is perfectly fine.
    """
    proc = run_git(
        ["config", "--local", "--get", "core.bare"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        return _ok(DoctorCheck.GIT_CORE_BARE, "core.bare is not set on this checkout.")
    return DoctorFinding(
        check=DoctorCheck.GIT_CORE_BARE,
        status=DoctorStatus.FAIL,
        finding=f"core.bare=true in {repo_root}, which has a working tree.",
        remediation="git config --local core.bare false",
    )


def check_git_env_leak(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """GIT_DIR / GIT_WORK_TREE in the ambient environment.

    They beat `cwd`, so a leaked pair silently points every git call at another
    repository. `run_git` scrubs them for this process's own calls; what this
    catches is the environment a *subprocess* would inherit — an agent shelling
    out to git itself, or a hook running under one.
    """
    leaked = sorted(name for name in GIT_LOCATION_ENV_VARS if os.environ.get(name))
    if not leaked:
        return _ok(DoctorCheck.GIT_ENV_LEAK, "No git location variables in the environment.")
    return DoctorFinding(
        check=DoctorCheck.GIT_ENV_LEAK,
        status=DoctorStatus.FAIL,
        finding=f"{', '.join(leaked)} set in the environment; they override cwd for git.",
        remediation=(
            f"unset {' '.join(leaked)} — or run through .lefthook/scripts/"
            "hook-noninteractive.sh, which unsets them for hooks."
        ),
    )


def check_repo_has_commit(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """A repository with no commit. Several helpers here assume HEAD resolves."""
    proc = run_git(
        ["rev-parse", "--verify", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return _ok(DoctorCheck.REPO_HAS_COMMIT, "HEAD resolves.")
    return DoctorFinding(
        check=DoctorCheck.REPO_HAS_COMMIT,
        status=DoctorStatus.FAIL,
        finding=f"{repo_root} has no commit, or is not a git repository.",
        remediation="git init && git commit --allow-empty -m 'initial'",
    )


def check_db_resolution(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """The database this process resolved, and whether anything is in it.

    A worktree resolves `data/loregarden.db` relative to itself, finds an empty
    file or none, and answers every ticket query with a silent zero — which reads
    as "no such ticket" rather than "wrong database".
    """
    path = resolved_database_path()
    if not path.is_file():
        return DoctorFinding(
            check=DoctorCheck.DB_RESOLUTION,
            status=DoctorStatus.FAIL,
            finding=f"No database at {path}.",
            remediation=(
                f"Point LOREGARDEN_REPO_ROOT at the main checkout (currently {settings.repo_root})."
            ),
        )
    if not session.exec(select(Ticket).limit(1)).first():
        return DoctorFinding(
            check=DoctorCheck.DB_RESOLUTION,
            status=DoctorStatus.WARN,
            finding=f"The database at {path} holds no tickets.",
            remediation=(
                "Expected? If not, this is probably a worktree resolving its own empty "
                f"copy — set LOREGARDEN_REPO_ROOT to the main checkout "
                f"(currently {settings.repo_root})."
            ),
        )
    return _ok(DoctorCheck.DB_RESOLUTION, f"Database at {path} has tickets.")


def check_backend_reload_sentinel(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """Backend sources newer than the reload sentinel.

    WARN rather than FAIL: it only bites when a dev server is up, and it is not
    this code's business whether one is. When it does bite it costs a debugging
    session, because the fix under test is stale code behaving exactly as the bug
    it was meant to remove.
    """
    server_root = repo_root / "server"
    sentinel = server_root / ".self-improve-restart"
    if not server_root.is_dir():
        return _ok(DoctorCheck.BACKEND_RELOAD_SENTINEL, "No server tree; not applicable.")
    if not sentinel.exists():
        return DoctorFinding(
            check=DoctorCheck.BACKEND_RELOAD_SENTINEL,
            status=DoctorStatus.WARN,
            finding="No reload sentinel; a running dev server would not pick up .py edits.",
            remediation=f"touch {sentinel}",
        )

    sentinel_mtime = sentinel.stat().st_mtime
    newer = next(
        (
            source
            for source in server_root.rglob("*.py")
            if ".venv" not in source.parts and source.stat().st_mtime > sentinel_mtime
        ),
        None,
    )
    if newer is None:
        return _ok(
            DoctorCheck.BACKEND_RELOAD_SENTINEL, "Sentinel is newer than every backend source."
        )
    return DoctorFinding(
        check=DoctorCheck.BACKEND_RELOAD_SENTINEL,
        status=DoctorStatus.WARN,
        finding=f"{newer.relative_to(repo_root)} is newer than the reload sentinel.",
        remediation=f"touch {sentinel}",
    )


def check_cli_credentials(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """Whether a credential *source* exists for the agent CLIs.

    Presence only — no value is read, logged, or returned. A missing one makes
    the CLI exit reporting "not logged in", which reads as a bug in the code that
    spawned it rather than as an expired session.
    """
    sources = {
        "claude": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())
        or (settings.repo_root / "data" / ".claude-oauth-token").is_file(),
        "cursor": bool(os.environ.get("CURSOR_API_KEY", "").strip()),
    }
    present = sorted(name for name, found in sources.items() if found)
    if present:
        return _ok(
            DoctorCheck.CLI_CREDENTIALS, f"Credential source present for: {', '.join(present)}."
        )
    return DoctorFinding(
        check=DoctorCheck.CLI_CREDENTIALS,
        status=DoctorStatus.WARN,
        finding="No credential source found for any agent CLI.",
        remediation=(
            "claude setup-token, saved to data/.claude-oauth-token — or export "
            "CLAUDE_CODE_OAUTH_TOKEN before launching the backend."
        ),
    )


def portability_state(repo_root: Path) -> PortabilityState:
    """Where the branch stands against its upstream, from refs already on disk.

    Deliberately without a fetch: a diagnostic that touches the network answers a
    different question each time it runs, and a slow one gets switched off.
    """
    upstream = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if upstream.returncode != 0:
        return PortabilityState.LOCAL_ONLY

    counts = run_git(
        ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if counts.returncode != 0:
        return PortabilityState.LOCAL_ONLY

    ahead_raw, _, behind_raw = counts.stdout.strip().partition("\t")
    ahead, behind = int(ahead_raw or 0), int(behind_raw or 0)
    if ahead and behind:
        return PortabilityState.REMOTE_DIVERGED
    if ahead:
        return PortabilityState.PUSH_REQUIRED
    return PortabilityState.REMOTE_READY


def check_git_portability(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """Report, not verdict. PUSH_REQUIRED is the normal state mid-ticket, and
    only REMOTE_DIVERGED needs a decision from anyone."""
    state = portability_state(repo_root)
    if state is PortabilityState.REMOTE_DIVERGED:
        return DoctorFinding(
            check=DoctorCheck.GIT_PORTABILITY,
            status=DoctorStatus.WARN,
            finding="The branch is both ahead of and behind its upstream.",
            remediation="Rebase or merge before pushing; a push will be rejected as is.",
        )
    return _ok(DoctorCheck.GIT_PORTABILITY, f"Branch is {state.value}.")


CHECKS: dict[DoctorCheck, Callable[[Session, Workspace, Path], DoctorFinding]] = {
    DoctorCheck.GIT_CORE_BARE: check_git_core_bare,
    DoctorCheck.GIT_ENV_LEAK: check_git_env_leak,
    DoctorCheck.REPO_HAS_COMMIT: check_repo_has_commit,
    DoctorCheck.DB_RESOLUTION: check_db_resolution,
    DoctorCheck.BACKEND_RELOAD_SENTINEL: check_backend_reload_sentinel,
    DoctorCheck.CLI_CREDENTIALS: check_cli_credentials,
    DoctorCheck.GIT_PORTABILITY: check_git_portability,
}


def run_checks(
    session: Session,
    workspace: Workspace,
    repo_root: Path,
    *,
    checks: tuple[DoctorCheck, ...] | None = None,
) -> list[DoctorFinding]:
    """Run `checks` (all of them by default) against `repo_root`.

    A check that raises becomes a FAIL naming the exception, and the rest still
    run. Reporting six results and one broken check beats reporting a traceback.
    """
    selected = checks if checks is not None else tuple(CHECKS)
    findings: list[DoctorFinding] = []
    for check in selected:
        try:
            findings.append(CHECKS[check](session, workspace, repo_root))
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the others
            findings.append(
                DoctorFinding(
                    check=check,
                    status=DoctorStatus.FAIL,
                    finding=f"Check raised {type(exc).__name__}: {exc}",
                    remediation="This is a bug in the check itself, not in the workspace.",
                )
            )
    return findings
