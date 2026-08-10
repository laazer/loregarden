"""Run the organization guardrails against a workspace, on demand.

The same checks run automatically twice — pre-commit in each repo, and as a
transition gate on every stage. This is the third way in: an agent (or a human at
the CLI) asking "would the gate pass?" *before* spending a stage on the answer.

The checkers themselves live in ``.lefthook/scripts`` and are deliberately
stdlib-only, workspace-agnostic scripts rather than importable modules: they have
to run inside a git hook in a repo that has no loregarden venv. This service is
the thin adapter that resolves a workspace slug to a repo path and reports what
they said.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

CHECK_TIMEOUT_SECONDS = 300


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


def _checker_result(checker: str, argv: list[str]) -> CheckerResult:
    try:
        completed = _run(argv)
    except subprocess.TimeoutExpired:
        return CheckerResult(checker, ok=False, message=f"timed out after {CHECK_TIMEOUT_SECONDS}s")
    except OSError as exc:
        # Missing interpreter (no node on this machine) is a real answer, not a
        # crash: report it instead of failing the whole call.
        return CheckerResult(checker, ok=False, message=str(exc))
    findings = _parse_findings(completed.stdout)
    return CheckerResult(
        checker,
        ok=completed.returncode == 0,
        findings=findings,
        message=""
        if completed.returncode == 0
        else (completed.stderr.strip() or "").split("\n")[0],
    )


def check_workspace(workspace: Workspace, scope: OrganizationScope) -> list[CheckerResult]:
    root = resolve_workspace_root(workspace)
    scripts = _scripts_dir()
    common = ["--repo", str(root), "--scope", scope.value]
    return [
        _checker_result("python", ["python3", str(scripts / "py_organization_check.py"), *common]),
        _checker_result(
            "typescript", ["node", str(scripts / "ts_organization_check.cjs"), *common]
        ),
    ]


def hooks_result(workspace: Workspace, *, install: bool) -> CheckerResult:
    root = resolve_workspace_root(workspace)
    argv = [str(settings.repo_root / "scripts" / "install-workspace-hooks.sh")]
    if not install:
        argv.append("--check")
    argv.append(str(root))
    try:
        completed = _run(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckerResult("hooks", ok=False, message=str(exc))
    detail = (completed.stdout.strip() or completed.stderr.strip()).split("\n")[0]
    return CheckerResult("hooks", ok=completed.returncode == 0, message=detail)


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
