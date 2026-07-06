"""Run workspace transition gate commands between workflow stages."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket, WorkflowStageDef, Workspace
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.workspace_paths import resolve_workspace_root

DEFAULT_TRANSITION_SCRIPT = "ci/scripts/run_workflow_transition_gates.py"
GATE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class GateRunResult:
    ok: bool
    message: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""


def transition_name(from_stage: str, to_stage: str) -> str:
    return f"{from_stage}_to_{to_stage}"


def build_gate_context(
    *,
    workspace: Workspace,
    ticket: Ticket,
    from_stage: str,
    to_stage: str,
) -> dict[str, str]:
    repo_root = resolve_workspace_root(workspace)
    transition = transition_name(from_stage, to_stage)
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "transition": transition,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "workspace_root": str(repo_root),
        "workspace_slug": workspace.slug,
    }


def format_gate_command(template: str, context: dict[str, str]) -> str:
    try:
        return template.format(**context)
    except KeyError:
        return template


def _resolve_transition_script(gates: GatesConfig, repo_root: Path) -> Path | None:
    candidates: list[Path] = []
    if gates.transition_script.strip():
        candidates.append(repo_root / gates.transition_script.strip())
    candidates.append(repo_root / DEFAULT_TRANSITION_SCRIPT)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _run_command(command: str, cwd: Path) -> GateRunResult:
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        return GateRunResult(ok=False, message=str(exc), command=command)
    except subprocess.TimeoutExpired:
        return GateRunResult(
            ok=False,
            message=f"Gate command timed out after {GATE_TIMEOUT_SECONDS}s",
            command=command,
        )

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout or f"exit code {completed.returncode}"
        return GateRunResult(
            ok=False,
            message=detail,
            command=command,
            stdout=stdout,
            stderr=stderr,
        )
    return GateRunResult(ok=True, command=command, stdout=stdout, stderr=stderr)


def collect_gate_commands(
    profile: OrchestrationProfile,
    *,
    from_stage: str,
    to_stage: str,
    stage_def: WorkflowStageDef | None = None,
) -> list[str]:
    if not profile.gates.enabled:
        return []

    commands: list[str] = list(profile.gates.commands)
    if stage_def and stage_def.gate_commands:
        commands.extend(stage_def.gate_commands)
    return commands


def run_transition_gates(
    profile: OrchestrationProfile,
    workspace: Workspace,
    ticket: Ticket,
    *,
    from_stage: str,
    to_stage: str,
    stage_def: WorkflowStageDef | None = None,
) -> GateRunResult:
    """Run configured gate commands after *from_stage* completes and before *to_stage*."""
    if not profile.gates.enabled:
        return GateRunResult(ok=True, message="gates disabled")

    repo_root = resolve_workspace_root(workspace)
    if not repo_root.is_dir():
        return GateRunResult(ok=False, message=f"Workspace repo path does not exist: {repo_root}")

    context = build_gate_context(
        workspace=workspace,
        ticket=ticket,
        from_stage=from_stage,
        to_stage=to_stage,
    )

    commands: list[str] = []
    script = _resolve_transition_script(profile.gates, repo_root)
    if script is not None:
        commands.append(
            f"{sys.executable} {script.relative_to(repo_root)} "
            f"--ticket-id {context['external_id']} "
            f"--transition {context['transition']}"
        )

    for template in collect_gate_commands(
        profile,
        from_stage=from_stage,
        to_stage=to_stage,
        stage_def=stage_def,
    ):
        commands.append(format_gate_command(template, context))

    if not commands:
        return GateRunResult(ok=True, message="no gate commands configured")

    for raw in commands:
        command = format_gate_command(raw, context)
        result = _run_command(command, repo_root)
        if not result.ok:
            return result

    return GateRunResult(ok=True, message=f"passed {len(commands)} gate command(s)")
