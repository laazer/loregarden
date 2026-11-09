"""Run workspace transition gate commands between workflow stages."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from loregarden.config import settings
from loregarden.core.terminal_text import strip_ansi
from loregarden.models.domain import (
    DockerFootprint,
    GateOutcome,
    OrchestrationRun,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services import orchestration_capacity
from loregarden.services.gate_capacity import GateCapacityUnavailable, acquire_gate_capacity
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.handoff_store import HANDOFF_SCRATCH_SUBDIR, export_for_gate
from loregarden.services.orchestration_profile import (
    DEFAULT_GATE_TIMEOUT_SECONDS,
    GatesConfig,
    OrchestrationProfile,
)
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

DEFAULT_TRANSITION_SCRIPT = "ci/scripts/run_workflow_transition_gates.py"

#: How much of a timed-out command's output is kept on its result: the end,
#: where a hung command's last words are.
TIMEOUT_OUTPUT_TAIL = 2048

#: `sysexits.h` EX_UNAVAILABLE, as raised by `.lefthook/scripts/
#: gate_python_guard.py` when the interpreter is too old to run the gate at all.
#: A gate that could not examine anything must not be reported as a gate that
#: found something — see `_run_command`. Kept in sync by
#: `tests/test_gate_python_guard.py`, which asserts the two constants agree.
GATE_EX_UNAVAILABLE = 69


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
    #: Commands that timed out once and were run again (the retry is recorded
    #: whether it then passed or not).
    timeout_retries: tuple[str, ...] = ()


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
    return _gate_context(
        workspace=workspace,
        ticket_id=ticket.id,
        external_id=ticket.external_id,
        from_stage=from_stage,
        to_stage=to_stage,
        repo_root=repo_root or resolve_workspace_root(workspace),
    )


#: Stand-in ticket identity for a gate evaluated outside any run — the Studio
#: preview and its "Test checks" button. A command keyed on the ticket sees
#: an id that matches nothing, which is the honest answer for a dry run.
SAMPLE_TICKET_ID = "sample-ticket-id"
SAMPLE_EXTERNAL_ID = "SAMPLE-1"


def sample_gate_context(
    workspace: Workspace, *, from_stage: str = "implement", to_stage: str = "verify"
) -> dict[str, str]:
    """Every placeholder a gate command may reference, filled for no ticket."""
    return _gate_context(
        workspace=workspace,
        ticket_id=SAMPLE_TICKET_ID,
        external_id=SAMPLE_EXTERNAL_ID,
        from_stage=from_stage,
        to_stage=to_stage,
        repo_root=resolve_workspace_root(workspace),
    )


def _gate_context(
    *,
    workspace: Workspace,
    ticket_id: str,
    external_id: str,
    from_stage: str,
    to_stage: str,
    repo_root: Path,
) -> dict[str, str]:
    transition = transition_name(from_stage, to_stage)
    return {
        "ticket_id": ticket_id,
        "external_id": external_id,
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


def resolved_transition_script(profile: OrchestrationProfile, workspace: Workspace) -> str:
    """The transition script a gate evaluation would run, relative to the
    workspace root, or "" when neither the configured nor the default path exists."""
    repo_root = resolve_workspace_root(workspace)
    script = _resolve_transition_script(profile.gates, repo_root)
    return str(script.relative_to(repo_root)) if script is not None else ""


def _host_load() -> str:
    """The host's 1-minute load average against its CPU count, in words."""
    try:
        one_minute = os.getloadavg()[0]
    except OSError:  # silent-ok: no load average on this platform; the message says so
        return "host load unknown"
    return f"load average {one_minute:.1f} on {os.cpu_count() or '?'} CPUs"


def _tail(output: bytes | str | None) -> str:
    """The last TIMEOUT_OUTPUT_TAIL characters of a killed command's partial output.

    `subprocess` hands partial output back as bytes on a timeout even in text
    mode, and a command killed mid-write can end inside a UTF-8 sequence.
    """
    if not output:
        return ""
    text = output if type(output) is str else output.decode("utf-8", errors="replace")
    return text.strip()[-TIMEOUT_OUTPUT_TAIL:]


def _run_command(
    command: str,
    cwd: Path,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = DEFAULT_GATE_TIMEOUT_SECONDS,
) -> GateRunResult:
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
            env={**scrubbed_git_env(), **(extra_env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        # FileNotFoundError (command not on PATH), PermissionError (file exists
        # but not executable), and other OS-level exec failures all land here.
        return GateRunResult(
            ok=False, outcome=GateOutcome.UNAVAILABLE, message=str(exc), command=command
        )
    except subprocess.TimeoutExpired as exc:
        # Not UNAVAILABLE: the tool exists and was working. Under load a probe
        # that answers in 4ms idle blew a 5s budget, and "could not run" sent
        # the operator looking for a broken install (lg-milestone-that-2487).
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.TIMEOUT,
            message=f"Gate command timed out after {timeout}s ({_host_load()})",
            command=command,
            stdout=_tail(exc.stdout),
            stderr=_tail(exc.stderr),
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

    A command that could not run keeps UNAVAILABLE, and one that ran out of
    time keeps TIMEOUT; anything else that failed is a real gate failure.
    Collapsing them — which `replace(result, outcome="failed")` did
    unconditionally — is what sent a hung `npx` to an agent to "fix".
    """
    if result.outcome is not None and result.outcome.reached_no_verdict:
        return result
    return replace(result, outcome=GateOutcome.FAILED)


def _outer_parent_lease_id(session: Session, ticket: Ticket) -> str | None:
    outer = orchestration_capacity.enclosing_parent_lease(session, ticket)
    return outer.id if outer is not None else None


@dataclass(frozen=True)
class _ParentCapacity:
    """The run's standing claim for this transition, or the refusal instead."""

    lease_id: str | None = None
    refusal: GateRunResult | None = None


def _parent_lease_for_gates(
    session: Session,
    profile: OrchestrationProfile,
    workspace: Workspace,
    ticket: Ticket,
    *,
    orch_run: OrchestrationRun | None,
) -> _ParentCapacity:
    """Find or take this run's parent host lease before the gate claims a child.

    A refusal is returned as UNAVAILABLE rather than raised or skipped: a gate
    that cannot find out whether it has capacity must not report PASSED, and
    `gate_recovery` routes UNAVAILABLE to a person because no agent can fix a
    busy machine.

    `parent_capacity_footprint: none` means this run takes no parent and its
    gates claim top-level leases, as they did before 1923 — still metered, just
    paying the line once per transition. It is NOT a way to run gate commands
    holding nothing.
    """
    footprint = profile.gates.parent_capacity_footprint
    if (
        orch_run is None
        or footprint is DockerFootprint.NONE
        or profile.gates.capacity_footprint is DockerFootprint.NONE
        or not settings.docker_capacity_enabled
    ):
        return _ParentCapacity()
    try:
        reservation = orchestration_capacity.acquire_or_find_parent(
            session,
            orch_run=orch_run,
            ticket=ticket,
            workspace=workspace,
            footprint=footprint,
            outer_parent_lease_id=_outer_parent_lease_id(session, ticket),
        )
    except Exception as exc:  # noqa: BLE001 - reported as UNAVAILABLE; see docstring
        logger.exception("Could not read the capacity ledger for run %s", orch_run.run_code)
        return _ParentCapacity(
            refusal=GateRunResult(
                ok=False,
                outcome=GateOutcome.UNAVAILABLE,
                message=(
                    "Gates did not run: the capacity ledger could not be read, so whether "
                    f"this run holds host capacity is unknown — {exc}"
                ),
            )
        )
    if not reservation.granted:
        return _ParentCapacity(
            refusal=GateRunResult(
                ok=False,
                outcome=GateOutcome.UNAVAILABLE,
                message=f"Gates did not run: {reservation.message}",
            )
        )
    # A child left behind by a killed transition would refuse every later gate
    # on this run for the life of the run: `reserve_child` refuses rather than
    # queues, and the reaper extends an expired child while its parent is held.
    orchestration_capacity.release_leaked_gate_children(session, reservation.lease_id)
    # Excess booking is the intended mode, not a mis-sizing: a 0.5-cpu parent
    # lends what it can to a 1.0-cpu SERVICE child and books the rest ahead of
    # the queue. A parent that fully covered its children is unaffordable on this
    # machine, so the trade is deliberate and bounded — at most 0.5 standing plus
    # 0.5 excess per in-flight ticket (lg-machine-resource-1923 R7/R8).
    return _ParentCapacity(lease_id=reservation.lease_id)


def run_transition_gates(
    session: Session,
    profile: OrchestrationProfile,
    workspace: Workspace,
    ticket: Ticket,
    *,
    from_stage: str,
    to_stage: str,
    stage_def: WorkflowStageDef | None = None,
    orch_run: OrchestrationRun | None = None,
) -> GateRunResult:
    """Run configured gate commands after *from_stage* completes and before *to_stage*.

    `orch_run` is how the run-scoped parent capacity lease arrives
    (lg-machine-resource-1923). The parent is acquired *here*, lazily on the
    first transition gate of the run, so a failure to acquire has a
    `GateRunResult` to report through rather than being a silent skip; the
    gate's own claim is then a child of it and never queues. With no run — a
    land verification, a standalone transition — gates take a top-level claim
    and wait in line exactly as they did before.
    """
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

    script = _resolve_transition_script(profile.gates, repo_root)
    # A blank/whitespace-only entry (a Studio user or hand-edited YAML saving
    # ["", "  "]) gates nothing — dropped here so it neither crashes the run nor
    # inflates the "ran" count that lets an operator tell real gates apart.
    templates = [
        template
        for template in collect_gate_commands(
            profile,
            from_stage=from_stage,
            to_stage=to_stage,
            stage_def=stage_def,
        )
        if template.strip()
    ]
    if script is None and not templates:
        return GateRunResult(
            ok=True, outcome=GateOutcome.SKIPPED, message="no gate commands configured"
        )

    parent = _parent_lease_for_gates(session, profile, workspace, ticket, orch_run=orch_run)
    if parent.refusal is not None:
        return parent.refusal
    try:
        lease = acquire_gate_capacity(
            session,
            profile.gates.capacity_footprint,
            ticket=ticket,
            workspace=workspace,
            transition=context["transition"],
            # Every command is bounded by its timeout and retried at most once,
            # so this covers the whole run without a heartbeat.
            ttl_seconds=_lease_ttl(profile.gates, templates),
            parent_lease_id=parent.lease_id,
            orchestration_run_id=orch_run.id if orch_run is not None else None,
        )
    except GateCapacityUnavailable as exc:
        return GateRunResult(ok=False, outcome=GateOutcome.UNAVAILABLE, message=str(exc))
    try:
        return _run_gate_commands(
            session,
            ticket,
            repo_root,
            context,
            script,
            templates,
            env=lease.env,
            gates=profile.gates,
        )
    finally:
        lease.release(session)


def _lease_ttl(gates: GatesConfig, templates: list[str]) -> int:
    """Seconds the gate lease must outlive: every command twice, plus the script twice."""
    budget = sum(gates.timeout_for(template) for template in templates)
    return 2 * (budget + DEFAULT_GATE_TIMEOUT_SECONDS) + DEFAULT_GATE_TIMEOUT_SECONDS


def _run_once_more_on_timeout(
    command: str, cwd: Path, *, env: dict[str, str], timeout: int
) -> GateRunResult:
    """Run a gate command, and once more if it timed out.

    A timeout on a loaded host is often only slowness; a second attempt costs
    one more budget and spares a person a block that clears by itself. A
    command that could not run (UNAVAILABLE) or that failed is not retried:
    neither will change on its own.
    """
    first = _run_command(command, cwd, extra_env=env, timeout=timeout)
    if first.outcome is not GateOutcome.TIMEOUT:
        return first
    logger.warning("gate command timed out, retrying once: %s — %s", command, first.message)
    second = _run_command(command, cwd, extra_env=env, timeout=timeout)
    if second.outcome is GateOutcome.TIMEOUT:
        second = replace(second, message=f"{second.message}, on its retry too")
    return replace(second, timeout_retries=(command,))


def _run_gate_commands(
    session: Session,
    ticket: Ticket,
    repo_root: Path,
    context: dict[str, str],
    script: Path | None,
    templates: list[str],
    *,
    env: dict[str, str],
    gates: GatesConfig,
) -> GateRunResult:
    """Run the transition script, then each configured command, stopping at the first failure."""
    ran = 0
    retried: list[str] = []

    # The workspace transition-gate script runs first, and tolerates transitions
    # it doesn't model: the orchestrator emits one edge per stage advance, but a
    # workspace may gate only some of them. An unmodeled edge is "no gate here"
    # (skip), not a rejection — see _is_undefined_transition. A real gate failure
    # still blocks.
    if script is not None:
        # The handoff lives in the database, so point the workspace's gates at an
        # exported tree rather than the repo's tracked checkpoints. The export mirrors
        # the ticket's checkpoint dir first, because the todo gate reads the same
        # `--checkpoints-dir` and its artifact is still a committed file.
        export_for_gate(session, ticket, repo_root=repo_root)
        script_command = format_gate_command(
            f"{sys.executable} {script.relative_to(repo_root)} "
            f"--ticket-id {context['external_id']} "
            f"--transition {context['transition']} "
            f"--checkpoints-dir {HANDOFF_SCRATCH_SUBDIR}",
            context,
        )
        result = _run_once_more_on_timeout(
            script_command, repo_root, env=env, timeout=DEFAULT_GATE_TIMEOUT_SECONDS
        )
        retried += result.timeout_retries
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
    for template in templates:
        command = format_gate_command(template, context)
        result = _run_once_more_on_timeout(
            command, repo_root, env=env, timeout=gates.timeout_for(template)
        )
        retried += result.timeout_retries
        if not result.ok:
            return replace(_blocking(result), timeout_retries=tuple(retried))
        ran += 1

    if ran == 0:
        return GateRunResult(
            ok=True,
            outcome=GateOutcome.SKIPPED,
            message="no gate commands configured",
            timeout_retries=tuple(retried),
        )

    message = f"passed {ran} gate command(s)"
    if retried:
        message += f"; {len(retried)} passed only on a retry after timing out"
    return GateRunResult(
        ok=True, outcome=GateOutcome.PASSED, message=message, timeout_retries=tuple(retried)
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


@dataclass(frozen=True)
class GateDryRun:
    template: str
    command: str
    result: GateRunResult
    duration_ms: int


def dry_run_gate_commands(
    workspace: Workspace,
    templates: list[str],
    *,
    from_stage: str,
    to_stage: str,
) -> list[GateDryRun]:
    """Run draft gate commands in the workspace's shared checkout, every one of
    them, so an operator sees each command's verdict before a ticket does.

    Unlike `run_transition_gates` this does not stop at the first failure — the
    point is to see them all — and it runs no transition script, which needs a
    real ticket's exported handoff to judge anything. Blank entries are
    dropped, as the real run drops them.
    """
    repo_root = resolve_workspace_root(workspace)
    repo_exists = repo_root.is_dir()
    context = sample_gate_context(workspace, from_stage=from_stage, to_stage=to_stage)
    runs: list[GateDryRun] = []
    for template in templates:
        if not template.strip():
            continue
        command = format_gate_command(template, context)
        started = time.monotonic()
        result = (
            _run_command(command, repo_root)
            if repo_exists
            else GateRunResult(
                ok=False,
                outcome=GateOutcome.UNAVAILABLE,
                message=f"Workspace repo path does not exist: {repo_root}",
                command=command,
            )
        )
        elapsed = int((time.monotonic() - started) * 1000)
        runs.append(
            GateDryRun(template=template, command=command, result=result, duration_ms=elapsed)
        )
    return runs
