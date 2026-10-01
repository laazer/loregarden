"""Run the organization guardrails against a workspace, on demand.

The same checks run automatically twice — pre-commit in each repo, and as a
transition gate on every stage. This is the third way in: an agent (or a human at
the CLI) asking "would the gate pass?" *before* spending a stage on the answer.

The checkers themselves live in ``.lefthook/scripts`` and are deliberately
stdlib-only, workspace-agnostic scripts rather than importable modules: they have
to run inside a git hook in a repo that has no loregarden venv. This service is
the thin adapter that resolves a workspace slug to a repo path and reports what
they said. Which gates exist, and how each one runs, come from
``workspace-gates.sh`` — the same dispatcher pre-commit and orchestration call —
so this cannot drift from what they enforce.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration as integration
from loregarden.services.workspace_integration import Installer, InstallerError, InstallState
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

CHECK_TIMEOUT_SECONDS = 300
#: The one list of workspace-agnostic gates; pre-commit and orchestration run it too.
GATES_DISPATCHER = "workspace-gates.sh"
#: The gates' "could not run" exit (EX_UNAVAILABLE), distinct from "found violations".
EX_UNAVAILABLE = 69
FAILURE_DETAIL_CHARS = 2000


class OrganizationAction(StrEnum):
    """What the caller wants done."""

    #: Report violations in the workspace's current changes. Read-only.
    CHECK = "check"
    #: Report whether the workspace's pre-commit hooks carry the managed block.
    HOOKS_STATUS = "hooks_status"
    #: Write/refresh that block. The only action that mutates another repo.
    INSTALL_HOOKS = "install_hooks"

    @classmethod
    def try_parse(cls, name: str) -> OrganizationAction | None:
        try:
            return cls(name)
        except ValueError:
            return None


class OrganizationScope(StrEnum):
    """Which diff the checks are scoped to; mirrors the checkers' --scope."""

    STAGED = "staged"
    WORKTREE = "worktree"
    BRANCH = "branch"

    @classmethod
    def try_parse(cls, name: str) -> OrganizationScope | None:
        try:
            return cls(name)
        except ValueError:
            return None


READ_ONLY_ACTIONS: frozenset[OrganizationAction] = frozenset(
    {OrganizationAction.CHECK, OrganizationAction.HOOKS_STATUS}
)


@dataclass(frozen=True)
class CheckerResult:
    checker: str
    ok: bool
    findings: list[str] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class OrganizationReport:
    action: OrganizationAction
    workspace_slug: str
    workspace_root: str
    ok: bool
    results: list[CheckerResult] = field(default_factory=list)

    def as_payload(self) -> dict:
        return {
            "action": self.action.value,
            "workspace_slug": self.workspace_slug,
            "workspace_root": self.workspace_root,
            "ok": self.ok,
            "finding_count": sum(len(r.findings) for r in self.results),
            "results": [
                {
                    "checker": r.checker,
                    "ok": r.ok,
                    "findings": r.findings,
                    "message": r.message,
                }
                for r in self.results
            ],
        }


def _scripts_dir() -> Path:
    return settings.repo_root / ".lefthook" / "scripts"


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT_SECONDS,
        check=False,
    )


def _parse_findings(stdout: str) -> list[str]:
    """The checkers print one ` - <finding>` line per violation."""
    return [line[3:].strip() for line in stdout.splitlines() if line.startswith(" - ")]


def _failure_message(completed: subprocess.CompletedProcess[str]) -> str:
    # All of it, capped: a crash's first line is often just node's loader frame,
    # with the actual cause ("Cannot find module …") a few lines below.
    detail = completed.stderr.strip()[:FAILURE_DETAIL_CHARS]
    verdict = (
        f"could not run (exit {completed.returncode})"
        if completed.returncode == EX_UNAVAILABLE
        else f"failed (exit {completed.returncode})"
    )
    return f"{verdict}: {detail}" if detail else verdict


def _checker_result(checker: str, argv: list[str]) -> CheckerResult:
    try:
        completed = _run(argv)
    except subprocess.TimeoutExpired:
        return CheckerResult(checker, ok=False, message=f"timed out after {CHECK_TIMEOUT_SECONDS}s")
    except OSError as exc:
        # Missing interpreter (no node on this machine) is a real answer, not a
        # crash: report it instead of failing the whole call.
        return CheckerResult(checker, ok=False, message=str(exc))
    ok = completed.returncode == 0
    return CheckerResult(
        checker,
        ok=ok,
        findings=_parse_findings(completed.stdout),
        message="" if ok else _failure_message(completed),
    )


def _gate_argv(scripts: Path, checker: str) -> list[str] | None:
    """How ``workspace-gates.sh`` runs a gate; ``None`` for a kind it does not know.

    Python goes through ``server_python.sh``: the checkers need >=3.11, and a bare
    ``python3`` is whatever the host PATH resolves (3.9 on macOS).
    """
    runners = {
        ".py": ["bash", str(scripts / "server_python.sh")],
        ".cjs": ["node"],
    }
    runner = runners.get(Path(checker).suffix)
    return None if runner is None else [*runner, str(scripts / checker)]


class GateListError(RuntimeError):
    """The dispatcher could not say which gates exist."""


def list_gates() -> list[str]:
    """The gates ``workspace-gates.sh --list`` names — the single list of them."""
    completed = _run(["bash", str(_scripts_dir() / GATES_DISPATCHER), "--list"])
    gates = completed.stdout.split()
    if completed.returncode != 0 or not gates:
        raise GateListError(
            f"{GATES_DISPATCHER} --list exited {completed.returncode} "
            f"with {len(gates)} gates: {completed.stderr.strip()}"
        )
    return gates


def check_workspace(workspace: Workspace, scope: OrganizationScope) -> list[CheckerResult]:
    root = resolve_workspace_root(workspace)
    scripts = _scripts_dir()
    common = ["--repo", str(root), "--scope", scope.value]
    try:
        gates = list_gates()
    except (GateListError, OSError, subprocess.TimeoutExpired) as exc:
        # An empty list here would read as "every gate passed".
        return [CheckerResult(GATES_DISPATCHER, ok=False, message=f"could not list gates: {exc}")]
    results = []
    for checker in gates:
        argv = _gate_argv(scripts, checker)
        results.append(
            CheckerResult(
                checker, ok=False, message=f"no runner for {checker!r} (unknown file type)"
            )
            if argv is None
            else _checker_result(checker, [*argv, *common])
        )
    return results


def hooks_result(workspace: Workspace, *, install: bool) -> CheckerResult:
    try:
        status = (
            integration.install(workspace, Installer.HOOKS)
            if install
            else integration.installer_status(workspace, Installer.HOOKS)
        )
    except InstallerError as exc:
        return CheckerResult("hooks", ok=False, message=str(exc))
    return CheckerResult("hooks", ok=status.state is InstallState.CURRENT, message=status.detail)


class UnknownWorkspaceError(ValueError):
    """Raised when a slug names no workspace — the caller's error, not a crash."""


def workspace_for_slug(session: Session, workspace_slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if workspace is None:
        raise UnknownWorkspaceError(f"no workspace with slug {workspace_slug!r}")
    return workspace


def run_organization_gate(
    workspace: Workspace,
    action: OrganizationAction,
    scope: OrganizationScope = OrganizationScope.WORKTREE,
) -> OrganizationReport:
    if action is OrganizationAction.CHECK:
        results = check_workspace(workspace, scope)
    else:
        results = [hooks_result(workspace, install=action is OrganizationAction.INSTALL_HOOKS)]
    return OrganizationReport(
        action=action,
        workspace_slug=workspace.slug,
        workspace_root=str(resolve_workspace_root(workspace)),
        ok=all(result.ok for result in results),
        results=results,
    )
