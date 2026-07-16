"""Run workspace transition gate commands between workflow stages."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket, WorkflowStageDef, Workspace
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.workspace_paths import resolve_workspace_root

logger = logging.getLogger(__name__)

DEFAULT_TRANSITION_SCRIPT = "ci/scripts/run_workflow_transition_gates.py"
GATE_TIMEOUT_SECONDS = 300

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Drop ANSI colour/cursor escape codes so lint/formatter output is readable
    when surfaced in the workflow pane or fed back to an agent as context."""
    return _ANSI_RE.sub("", text)


@dataclass(frozen=True)
class GateRunResult:
    ok: bool
    message: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class GateAutofixResult:
    ran: bool
    commands: list[str]
    output: str = ""


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
    except KeyError as exc:
        logger.warning(
            "gate command template references unknown placeholder %s; running it verbatim: %r",
            exc,
            template,
        )
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


def _is_undefined_transition(result: GateRunResult) -> bool:
    """True when the workspace transition-gate script rejected the transition
    *name* — i.e. it doesn't model this edge — rather than running a gate and
    failing it.

    The orchestrator emits one transition per stage edge as ``{from}_to_{to}``.
    A workspace whose gate script defines only a subset of those edges (or names
    its stages differently) must not wedge the whole workflow: an unmodeled edge
    means "no gate here", which is a pass, not a rejection. argparse's
    ``choices=`` rejection exits non-zero with "invalid choice" on stderr; a
    hand-rolled check typically prints "unknown transition". A genuine gate
    failure carries neither phrase, so it still blocks.
    """
    haystack = f"{result.stderr}\n{result.stdout}".lower()
    return "invalid choice" in haystack or "unknown transition" in haystack


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

    ran = 0

    # The workspace transition-gate script runs first, and tolerates transitions
    # it doesn't model: the orchestrator emits one edge per stage advance, but a
    # workspace may gate only some of them. An unmodeled edge is "no gate here"
    # (skip), not a rejection — see _is_undefined_transition. A real gate failure
    # still blocks.
    script = _resolve_transition_script(profile.gates, repo_root)
    if script is not None:
        script_command = format_gate_command(
            f"{sys.executable} {script.relative_to(repo_root)} "
            f"--ticket-id {context['external_id']} "
            f"--transition {context['transition']}",
            context,
        )
        result = _run_command(script_command, repo_root)
        if result.ok:
            ran += 1
        elif _is_undefined_transition(result):
            logger.info(
                "workspace transition script does not model transition %r; "
                "treating as no gate for this edge and continuing",
                context["transition"],
            )
        else:
            return result

    # Profile- and stage-configured gate commands (lint, static analysis, etc.)
    # are objective checks with no such notion of an "unmodeled" transition, so
    # any failure blocks.
    for template in collect_gate_commands(
        profile,
        from_stage=from_stage,
        to_stage=to_stage,
        stage_def=stage_def,
    ):
        command = format_gate_command(template, context)
        result = _run_command(command, repo_root)
        if not result.ok:
            return result
        ran += 1

    if ran == 0:
        return GateRunResult(ok=True, message="no gate commands configured")

    return GateRunResult(ok=True, message=f"passed {ran} gate command(s)")


def run_gate_autofix(
    profile: OrchestrationProfile,
    workspace: Workspace,
    ticket: Ticket,
    *,
    from_stage: str,
    to_stage: str,
    stage_def: WorkflowStageDef | None = None,
) -> GateAutofixResult:
    """Run the profile's mechanical fixer commands (ruff --fix, formatters, etc.)
    best-effort in the workspace root, after a transition gate failed. Fixers
    legitimately exit non-zero when unfixable issues remain, so their exit codes
    are ignored here — the caller re-runs the gate to decide whether the fix
    actually cleared it. Returns the (ANSI-stripped) combined output for logging.
    """
    if not profile.gates.enabled or not profile.gates.autofix_commands:
        return GateAutofixResult(ran=False, commands=[])

    repo_root = resolve_workspace_root(workspace)
    if not repo_root.is_dir():
        return GateAutofixResult(
            ran=False, commands=[], output=f"Workspace repo path does not exist: {repo_root}"
        )

    context = build_gate_context(
        workspace=workspace,
        ticket=ticket,
        from_stage=from_stage,
        to_stage=to_stage,
    )

    commands: list[str] = []
    chunks: list[str] = []
    for template in profile.gates.autofix_commands:
        command = format_gate_command(template, context)
        commands.append(command)
        result = _run_command(command, repo_root)
        body = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if not body and not result.ok:
            body = result.message
        if body:
            chunks.append(f"$ {command}\n{strip_ansi(body)}")

    return GateAutofixResult(ran=bool(commands), commands=commands, output="\n\n".join(chunks))
