"""Run workspace transition gate commands between workflow stages."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import GateOutcome, Ticket, WorkflowStageDef, Workspace
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.handoff_store import HANDOFF_SCRATCH_SUBDIR, export_for_gate
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

DEFAULT_TRANSITION_SCRIPT = "ci/scripts/run_workflow_transition_gates.py"
GATE_TIMEOUT_SECONDS = 300

#: `sysexits.h` EX_UNAVAILABLE, as raised by `.lefthook/scripts/
#: gate_python_guard.py` when the interpreter is too old to run the gate at all.
#: A gate that could not examine anything must not be reported as a gate that
#: found something — see `_run_command`. Kept in sync by
#: `tests/test_gate_python_guard.py`, which asserts the two constants agree.
GATE_EX_UNAVAILABLE = 69

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Drop ANSI colour/cursor escape codes so lint/formatter output is readable
    when surfaced in the workflow pane or fed back to an agent as context."""
    return _ANSI_RE.sub("", text)


@dataclass(frozen=True)
class GateRunResult:
    ok: bool
    # Explicit terminal outcome so a gate that ran-and-passed is distinguishable
    # from one that never ran. Callers must not infer this from `ok`/`message`
    # alone — an empty message with ok=True used to collapse "passed" and
    # "skipped"/"disabled" into the same indistinguishable result (ticket 88).
    #   passed   — at least one real gate command ran and exited clean
    #   skipped  — gates on, but nothing was configured that actually runs
    #   disabled — gates turned off for this workspace entirely
    #   failed   — a gate ran and failed, or the evaluation could not proceed
    outcome: GateOutcome | None = None
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
    repo_root: Path | None = None,
) -> dict[str, str]:
    # Defaults to the shared checkout for callers with no ticket tree in hand;
    # the orchestration paths pass the ticket's worktree, which is where the
    # edits a gate is meant to judge actually are.
    repo_root = repo_root or resolve_workspace_root(workspace)
    transition = transition_name(from_stage, to_stage)
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "transition": transition,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "workspace_root": str(repo_root),
        "workspace_slug": workspace.slug,
        # Loregarden's own checkout, so a profile can invoke a check that ships
        # here and runs against any workspace — one copy of the rules rather
        # than a copy per repo that drifts.
        "loregarden_root": str(settings.repo_root),
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
    # A single malformed or unrunnable command entry (a typo'd Studio gate, a
    # checked-in script missing its execute bit) must degrade to a normal
    # "failed" result — never take down the whole evaluation with an unhandled
    # exception. shlex.split raises ValueError on unterminated quotes and yields
    # [] for a blank string (which subprocess.run would turn into an
    # IndexError); PermissionError and friends are OSError subclasses.
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message=f"malformed gate command: {exc}",
            command=command,
        )
    if not argv:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message="empty gate command",
            command=command,
        )
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            # GIT_DIR and friends override `cwd`, and every transition gate
            # resolves its own scope through git against the workspace it was
            # handed. An inherited binding aims them at another repository,
            # where they examine nothing and exit 0 — a pass over unread work.
            env=scrubbed_git_env(),
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        # FileNotFoundError (command not on PATH), PermissionError (file exists
        # but not executable), and other OS-level exec failures all land here.
        return GateRunResult(
            ok=False, outcome=GateOutcome.UNAVAILABLE, message=str(exc), command=command
        )
    except subprocess.TimeoutExpired:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message=f"Gate command timed out after {GATE_TIMEOUT_SECONDS}s",
            command=command,
        )

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode == GATE_EX_UNAVAILABLE:
        # The command ran and told us it could not do its job — an interpreter
        # too old to import the gate, so nothing was examined. Reporting that
        # as FAILED sends the autofix loop after a violation that was never
        # found; only UNAVAILABLE is true, and it is the outcome `_blocking`
        # deliberately preserves.
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message=stderr or stdout or "gate unavailable",
            command=command,
            stdout=stdout,
            stderr=stderr,
        )
    if completed.returncode != 0:
        detail = stderr or stdout or f"exit code {completed.returncode}"
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.FAILED,
            message=detail,
            command=command,
            stdout=stdout,
            stderr=stderr,
        )
    return GateRunResult(
        ok=True, outcome=GateOutcome.PASSED, command=command, stdout=stdout, stderr=stderr
    )


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


def gates_can_run(profile: OrchestrationProfile, workspace: Workspace) -> bool:
    """True when this profile would actually execute *something* that gates a
    transition — i.e. `gates.enabled` AND either a real (non-blank) gate command
    is configured or the workspace's transition script resolves.

    `gates.enabled=True` with nothing runnable gates nothing; reporting that as
    "enabled" (as the raw flag did) shows green in the Gates editor for a config
    that in fact lets every transition through. This is the honest predicate.
    """
    if not profile.gates.enabled:
        return False
    if any(command.strip() for command in profile.gates.commands):
        return True
    repo_root = resolve_workspace_root(workspace)
    return _resolve_transition_script(profile.gates, repo_root) is not None


def _blocking(result: GateRunResult) -> GateRunResult:
    """Stamp a failed command with the outcome that decides who handles it.

    A command that could not run keeps UNAVAILABLE; anything else that failed is
    a real gate failure. Collapsing the two — which `replace(result,
    outcome="failed")` did unconditionally — is what sent a hung `npx` to an
    agent to "fix".
    """
    if result.outcome is GateOutcome.UNAVAILABLE:
        return result
    return replace(result, outcome=GateOutcome.FAILED)


def run_transition_gates(
    session: Session,
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
        return GateRunResult(ok=True, outcome=GateOutcome.DISABLED, message="gates disabled")

    # The tree the stage just wrote in. Running gates in the shared checkout
    # would lint a copy of the repo that has none of the ticket's changes —
    # every gate would pass on work it never saw.
    repo_root = resolve_ticket_root(session, ticket, workspace)
    if not repo_root.is_dir():
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.FAILED,
            message=f"Workspace repo path does not exist: {repo_root}",
        )

    context = build_gate_context(
        workspace=workspace,
        ticket=ticket,
        from_stage=from_stage,
        to_stage=to_stage,
        repo_root=repo_root,
    )

    ran = 0

    # The workspace transition-gate script runs first, and tolerates transitions
    # it doesn't model: the orchestrator emits one edge per stage advance, but a
    # workspace may gate only some of them. An unmodeled edge is "no gate here"
    # (skip), not a rejection — see _is_undefined_transition. A real gate failure
    # still blocks.
    script = _resolve_transition_script(profile.gates, repo_root)
    if script is not None:
        # The handoff lives in the database, so point the workspace's gates at an
        # exported tree rather than the repo's tracked checkpoints. The export mirrors
        # the ticket's checkpoint dir first, because the todo gate reads the same
        # `--checkpoints-dir` and its artifact is still a committed file.
        export_for_gate(session, workspace, ticket)
        script_command = format_gate_command(
            f"{sys.executable} {script.relative_to(repo_root)} "
            f"--ticket-id {context['external_id']} "
            f"--transition {context['transition']} "
            f"--checkpoints-dir {HANDOFF_SCRATCH_SUBDIR}",
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
            return _blocking(result)

    # Profile- and stage-configured gate commands (lint, static analysis, etc.)
    # are objective checks with no such notion of an "unmodeled" transition, so
    # any failure blocks.
    for template in collect_gate_commands(
        profile,
        from_stage=from_stage,
        to_stage=to_stage,
        stage_def=stage_def,
    ):
        # A blank/whitespace-only entry (a Studio user or hand-edited YAML saving
        # ["", "  "]) gates nothing — skip it so it neither crashes the run nor
        # inflates the "ran" count that lets an operator tell real gates apart.
        if not template.strip():
            continue
        command = format_gate_command(template, context)
        result = _run_command(command, repo_root)
        if not result.ok:
            return _blocking(result)
        ran += 1

    if ran == 0:
        return GateRunResult(
            ok=True, outcome=GateOutcome.SKIPPED, message="no gate commands configured"
        )

    return GateRunResult(
        ok=True, outcome=GateOutcome.PASSED, message=f"passed {ran} gate command(s)"
    )


def run_gate_autofix(
    session: Session,
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

    # Same tree the gate ran in, or the fixer edits files nobody is checking.
    repo_root = resolve_ticket_root(session, ticket, workspace)
    if not repo_root.is_dir():
        return GateAutofixResult(
            ran=False, commands=[], output=f"Workspace repo path does not exist: {repo_root}"
        )

    context = build_gate_context(
        workspace=workspace,
        ticket=ticket,
        from_stage=from_stage,
        to_stage=to_stage,
        repo_root=repo_root,
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
